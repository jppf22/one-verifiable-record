"""Same-fact gate: decide which vector-search candidates state the SAME fact.

Vector similarity alone cannot separate "born 11 March 1712" from "died 27 October
1772" (both ~0.87 for the same person), so alignment proposes several candidates per
claim. Before this gate every candidate was graded with its own LLM call: ~400 calls
for one page, most rejected by the account's 40 requests/minute limit, and every
candidate that did get graded ("Claim B is about his death, not his birth") was shown
as a verdict.

Now, for page X and each counterpart page:
1. Candidates with the same property (verdicts._same_fact: same Wikidata property or
   predicate) are accepted without the LLM and graded as before (deterministic first).
2. The rest go to ONE batched LLM call per counterpart (up to ``gate_batch_size`` claims
   of X, each with its ``gate_max_candidates`` nearest candidates). The model picks, per
   claim, the single candidate about the same fact (values may differ: that is the
   conflict we want) and grades the pair in the same answer.
3. Each counterpart claim is matched to at most one claim of X, and vice versa. So a
   comparison row is one fact, not a chain of loosely related sentences.
4. Rejected candidates get verdict 'different_fact' (kept, so alignment does not propose
   them again; never shown as verdicts). If the LLM is unavailable the candidates stay
   'unmatched' (hidden, retried on the next re-check). An error is never stored as a
   verdict.
"""
import llm
from config import settings
from db import execute
from pages import get_page
from verdicts import (SOURCE_DETERMINISTIC, SOURCE_LLM, _side, _same_fact,
                      compute_confidence, deterministic_verdict, fetch_pairs,
                      page_links_where)

LANG_NAMES = {"fi": "Finnish", "sv": "Swedish", "en": "English"}
GATE_VERDICTS = {"supported", "contradicted", "unverifiable"}

SYSTEM = """You align atomic factual claims between two language editions of the same Wikipedia article.

For each claim A, you get up to 4 candidate claims B from the other language edition. Pick the ONE candidate that states the SAME FACT as A, or null if none does.

SAME FACT means: same subject AND same property or event. Examples: both give the person's birth date; both describe the first marriage; both say what something is named after; both give the population in the same year. The VALUES MAY DIFFER: if A says born 11 March and B says born 2 March, that is the same fact (birth date) with a conflicting value, and you MUST match it.

NOT the same fact:
- Different property or event, even if the dates or numbers are equal or the topic is related (birth date vs death date; joining an organisation vs being appointed to an office in the same year; a population figure vs an area figure).
- Different subject (the person vs a relative; the city vs its region).
- Different instance of a repeatable event (first marriage vs second marriage; one office vs another office; population in 2010 vs population in 2020).

Rules:
- Each B id may be used for at most one A claim. If two A claims could use the same B (e.g. B gives both birth date and birthplace, and A has separate birth-date and birthplace claims), give B to the A claim that matches B's main fact (the first property B states) and set the other A to null. Before replying, check that no b_id appears twice.
- Only use B ids listed under that A claim.
- Return every A claim exactly once, in the given order.

For each matched pair, give a verdict:
- "supported": the values agree (allow for translation, rounding, different detail levels, or one side being more specific without conflict).
- "contradicted": the values are incompatible for the same fact (different date, number, place, or person).
- "unverifiable": same fact, but you cannot tell whether the values agree.
Give confidence (0 to 1) that both the match and the verdict are correct, and a short rationale in English (max 20 words) naming the property and the two values.
For null matches, set verdict to null and give a short rationale.

Reply with JSON only, in this exact shape:
{"matches": [{"a_id": <int>, "b_id": <int or null>, "verdict": "supported" | "contradicted" | "unverifiable" | null, "confidence": <number 0-1>, "rationale": "<string>"}]}"""


def _same_subject(a: dict, b: dict) -> bool:
    """Loose subject check for the no-LLM path: "Daniel Tilas" = "Daniel Olofsson Tilas",
    but "Daniel Tilas" != "Olof Tilas" (his father). Unknown subjects pass."""
    sa, sb = (a.get("subject") or "").lower().split(), (b.get("subject") or "").lower().split()
    if not sa or not sb:
        return True
    return sa[0] == sb[0] or sa == sb


def _user_prompt(title: str, lang_a: str, lang_b: str, groups: list) -> str:
    blocks = []
    for g in groups:
        lines = [f"A{g['here']['id']}: {g['here']['statement_text']}"]
        lines += [f"  - B{c['other']['id']}: {c['other']['statement_text']}" for c in g["cands"]]
        blocks.append("\n".join(lines))
    return (f"Article subject: {title}\n"
            f"Claims A are from the {LANG_NAMES.get(lang_a, lang_a)} edition. "
            f"Candidates B are from the {LANG_NAMES.get(lang_b, lang_b)} edition.\n"
            'Ids: use the number after "A" or "B" as an integer.\n\n' + "\n\n".join(blocks))


def _as_int(v, prefix):
    if isinstance(v, str):
        v = v.strip().lstrip(prefix)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _validate(reply: dict, groups: list) -> dict:
    """{here_id: match}: drop ids not offered, enforce one B per A by confidence."""
    offered = {g["here"]["id"]: {c["other"]["id"] for c in g["cands"]} for g in groups}
    out = {}
    for m in reply.get("matches") or []:
        a, b = _as_int(m.get("a_id"), "A"), _as_int(m.get("b_id"), "B")
        if a not in offered:
            continue
        if b not in offered[a]:
            b = None
        try:
            conf = max(0.0, min(1.0, float(m.get("confidence"))))
        except (TypeError, ValueError):
            conf = None
        verdict = m.get("verdict") if m.get("verdict") in GATE_VERDICTS else "unverifiable"
        out[a] = {"b": b, "verdict": verdict, "confidence": conf,
                  "rationale": str(m.get("rationale") or "")[:500]}
    used = set()
    for a, m in sorted(out.items(), key=lambda kv: -(kv[1]["confidence"] or 0)):
        if m["b"] is None:
            continue
        if m["b"] in used:
            m["b"], m["rationale"] = None, "candidate already matched to another claim"
        else:
            used.add(m["b"])
    return out


def _write_verdict(c: dict, verdict, source, relation, confidence, rationale) -> None:
    p = c["pair"]
    a, b = _side(p, "a"), _side(p, "b")
    vconf = compute_confidence(a, b, source, p.get("similarity"), confidence)
    execute(
        """UPDATE claim_links
           SET verdict = %s, verdict_source = %s, relation = %s, candidate_confidence = %s,
               verdict_confidence = %s, rationale = %s,
               evidence = CONCAT(%s, ' || ', %s), computed_at = CURRENT_TIMESTAMP
           WHERE id = %s AND verdict = 'unmatched'""",
        (verdict, source, relation, confidence, vconf, rationale,
         a["statement_text"], b["statement_text"], p["link_id"]),
    )


def _reject(c: dict, source: str, rationale: str) -> None:
    _write_verdict(c, "different_fact", source, "different", None, rationale)


def gate_page(page_id: int) -> dict:
    """Gate every 'unmatched' link between page X and its counterparts.

    Returns {"to_grade": [link ids accepted without the LLM, still 'unmatched'],
             "graded": [{id, before, after, changed}], "rejected": n, "deferred": n,
             "llm_calls": n}.
    """
    page = get_page(page_id)
    result = {"to_grade": [], "graded": [], "rejected": 0, "deferred": 0, "llm_calls": 0}
    if not page:
        return result
    where, params = page_links_where(page_id, "cl.verdict = 'unmatched'")
    pairs = fetch_pairs(where, params)
    if not pairs:
        return result

    # Orient each pair: "here" is the side on page X (one page per language in the set).
    groups: dict = {}
    for p in pairs:
        a, b = _side(p, "a"), _side(p, "b")
        here, other = (a, b) if a["lang"] == page["lang"] else (b, a)
        g = groups.setdefault((here["id"], other["lang"]), {"here": here, "cands": []})
        g["cands"].append({"pair": p, "other": other})

    llm_queue: dict = {}  # other lang -> [group]
    used: dict = {}       # other lang -> counterpart claim ids already matched
    matched_here = set()  # (here id, other lang) that already have a same-fact match
    # Graded same-fact links count as matches, so a candidate can't steal their claims.
    graded_where, graded_params = page_links_where(
        page_id, "(cl.verdict IN ('supported','contradicted','possible_match','possible_conflict')"
                 " OR (cl.verdict = 'unverifiable' AND cl.relation = 'same'))"
                 " AND cl.verdict_source <> 'deterministic-fallback'")
    for p in fetch_pairs(graded_where, graded_params):
        a, b = _side(p, "a"), _side(p, "b")
        here, other = (a, b) if a["lang"] == page["lang"] else (b, a)
        used.setdefault(other["lang"], set()).add(other["id"])
        matched_here.add((here["id"], other["lang"]))
    for (here_id, lang), g in sorted(groups.items()):
        g["cands"].sort(key=lambda c: -(c["pair"].get("similarity") or 0))
        if (here_id, lang) in matched_here:
            for c in g["cands"]:
                _reject(c, SOURCE_DETERMINISTIC, f"already matched to a claim on the {lang} page")
                result["rejected"] += 1
            continue
        exact = [c for c in g["cands"] if _same_fact(g["here"], c["other"])
                 and _same_subject(g["here"], c["other"])
                 and c["other"]["id"] not in used.get(lang, set())]
        if exact:
            keep = exact[0]
            used.setdefault(lang, set()).add(keep["other"]["id"])
            result["to_grade"].append(keep["pair"]["link_id"])
            for c in g["cands"]:
                if c is not keep:
                    _reject(c, SOURCE_DETERMINISTIC, "another candidate states the same property")
                    result["rejected"] += 1
            continue
        cap = settings.gate_max_candidates
        for c in g["cands"][cap:]:
            _reject(c, SOURCE_DETERMINISTIC, f"not among the {cap} nearest candidates")
            result["rejected"] += 1
        g["cands"] = g["cands"][:cap]
        llm_queue.setdefault(lang, []).append(g)

    size = max(1, settings.gate_batch_size)
    for lang, lang_groups in llm_queue.items():
        for i in range(0, len(lang_groups), size):
            chunk = lang_groups[i:i + size]
            # a counterpart claim already taken by an exact match can't be reused
            for g in chunk:
                g["cands"] = [c for c in g["cands"] if c["other"]["id"] not in used.get(lang, set())]
            chunk = [g for g in chunk if g["cands"]]
            if not chunk:
                continue
            try:
                result["llm_calls"] += 1
                reply = llm.chat_json(SYSTEM, _user_prompt(page["title"], page["lang"], lang, chunk),
                                      max_tokens=60 * len(chunk) + 300)
            except llm.LLMUnavailable:
                result["deferred"] += sum(len(g["cands"]) for g in chunk)
                continue  # left 'unmatched': hidden now, gated on the next re-check
            matches = _validate(reply, chunk)
            for g in chunk:
                m = matches.get(g["here"]["id"]) or {"b": None, "rationale": "no answer for this claim"}
                for c in g["cands"]:
                    if c["other"]["id"] != m["b"]:
                        _reject(c, SOURCE_LLM, m["rationale"] if m["b"] is None
                                else "the model matched a different candidate")
                        result["rejected"] += 1
                        continue
                    used.setdefault(lang, set()).add(m["b"])
                    det = deterministic_verdict(g["here"], c["other"])
                    if det:
                        _write_verdict(c, det, SOURCE_DETERMINISTIC, "same", 1.0,
                                       "canonical value comparison")
                    else:
                        _write_verdict(c, m["verdict"], SOURCE_LLM, "same", m["confidence"],
                                       m["rationale"])
                    result["graded"].append({"id": c["pair"]["link_id"], "before": "unmatched",
                                             "after": det or m["verdict"], "changed": True})
    return result
