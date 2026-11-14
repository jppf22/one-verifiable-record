"""Confidence-aware claim grading, cheapest evidence first:

1. deterministic — dates and numbers compared on canonical ``value_normalized``
2. LLM — cross-language entailment via the Verda Inference API

Every UPDATE to claim_links writes a new row version (SYSTEM VERSIONING), so
the full evolution of every verdict stays queryable in SQL.
"""
import json

import llm

from config import settings
from db import execute, placeholders, query, query_one
from pages import comparison_set
from predicates import same as same_predicate, same_subject

NUMERIC_TOLERANCE = 1e-9
VALID_VERDICTS = {
    "supported", "contradicted", "possible_match", "possible_conflict",
    "unverifiable", "definition_mismatch",
}
VALID_RELATIONS = {"same", "related", "different", "unknown"}

# verdict_source values. 'deterministic-fallback' marks a verdict produced
# without the LLM because the Verda API was unavailable, so a guess is never
# presented as a model output. 'human' marks a reviewer override (Q1).
SOURCE_DETERMINISTIC = "deterministic"
SOURCE_LLM = "llm"
SOURCE_FALLBACK = "deterministic-fallback"
SOURCE_HUMAN = "human"


def _same_fact(a: dict, b: dict) -> bool:
    """True only for a *safe* deterministic comparison.

    AI relation labels are not a candidate-generation gate, but deterministic
    value equality/contradiction still needs an exact relation identity. Prefer
    a mapped Wikidata property and fall back to a canonical predicate string.
    """
    pa, pb = a.get("predicate_entity"), b.get("predicate_entity")
    if pa and pb:
        return pa == pb
    # aliases name the same relation (predicates.py: date_of_birth = birth_date)
    return same_predicate(a.get("predicate"), b.get("predicate"))


def _json(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _canonical_number(claim: dict):
    """Return (number, dimension) only when a quantity is safely comparable."""
    value = _json(claim.get("value_canonical"))
    raw = value.get("base_value", value.get("value", claim.get("value_normalized")))
    if raw is None:
        return None, None
    try:
        return float(raw), value.get("dimension") or value.get("unit")
    except (TypeError, ValueError):
        return None, None


def _time_scope(claim: dict):
    qualifiers = _json(claim.get("qualifiers"))
    return qualifiers.get("as_of") or qualifiers.get("valid_at") or qualifiers.get("period")


def _date_verdict(va: str, vb: str) -> str:
    """ISO dates at mixed precision: "1900" and "1900-09-03" agree. Compare at
    the coarser of the two precisions."""
    n = min(len(va.lstrip("-")), len(vb.lstrip("-")))
    same_sign = va.startswith("-") == vb.startswith("-")
    return "supported" if same_sign and va.lstrip("-")[:n] == vb.lstrip("-")[:n] else "contradicted"


def deterministic_verdict(a: dict, b: dict) -> str | None:
    """Return a verdict, or None if this pair needs the LLM layer."""
    if a["kind"] != b["kind"] or not _same_fact(a, b):
        return None
    # same relation but maybe a different thing (predicates.same_subject): let the LLM decide
    if not same_subject(a.get("predicate"), a.get("subject"), b.get("subject")):
        return None
    va, vb = a["value_normalized"], b["value_normalized"]
    if not va or not vb:
        return None
    if a["kind"] == "date":
        return _date_verdict(va, vb)
    if a["kind"] in ("number", "quantity"):
        # Different "as of" dates are not automatically a contradiction.
        if _time_scope(a) != _time_scope(b) and (_time_scope(a) or _time_scope(b)):
            return None
        fa, da = _canonical_number(a)
        fb, db = _canonical_number(b)
        if fa is None or fb is None or (da and db and da != db):
            return None
        if abs(fa - fb) <= NUMERIC_TOLERANCE:
            return "supported"
        # Differing numbers are often time-qualified (population in 2019 vs 2023),
        # which only the LLM layer can judge from the statement text.
        return None
    if a["kind"] == "entity_relation" and a.get("object_entity") and a.get("object_entity") == b.get("object_entity"):
        return "supported"
    return None  # entity_relation / factoid -> semantic verifier


def llm_verdict(a: dict, b: dict):
    """Contextual cross-language entailment via the Verda Inference API.

    Returns (verdict, relation, confidence, rationale, available). ``available``
    is False when the LLM layer could not run (no key, or request failed), so
    the caller can mark the verdict ``deterministic-fallback`` instead of
    presenting a guess as a model output.
    """
    if not settings.verda_api_key:
        # Keep a loose vector candidate visible instead of misrepresenting it
        # as a confirmed gap when semantic verification is unavailable.
        return None, "unknown", None, "no LLM configured (set VERDA_API_KEY)", False
    messages = [
        {
            "role": "system",
            "content": (
                "You compare atomic claims from different language editions of the same "
                "Wikipedia article. Reply ONLY with JSON: "
                '{"verdict": "supported|contradicted|possible_match|possible_conflict|'
                'unverifiable|definition_mismatch", "relation": "same|related|different|unknown", '
                '"confidence": 0.0, "rationale": "one short sentence"}. '
                'A claim pair is "supported" only if '
                "the other edition states the same fact; \"contradicted\" if it states an "
                "incompatible fact about the same subject; \"definition_mismatch\" if the "
                "claims are actually about different concepts despite similar wording; "
                "\"possible_match\" or \"possible_conflict\" if the evidence plausibly points "
                "there but is incomplete. Do not turn differing time periods, units, scopes, "
                "calendars, estimates or attributed opinions into a contradiction."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Claim A ({a['lang']}): {a['statement_text']}\n"
                f"Canonical A: {json.dumps(a.get('value_canonical') or {})}\n"
                f"Qualifiers A: {json.dumps(a.get('qualifiers') or {})}\n"
                f"Context A: {a.get('context_text') or ''}\n"
                f"Citations A: {json.dumps(a.get('citation_urls') or [])}\n\n"
                f"Claim B ({b['lang']}): {b['statement_text']}\n"
                f"Canonical B: {json.dumps(b.get('value_canonical') or {})}\n"
                f"Qualifiers B: {json.dumps(b.get('qualifiers') or {})}\n"
                f"Context B: {b.get('context_text') or ''}\n"
                f"Citations B: {json.dumps(b.get('citation_urls') or [])}\n"
                "Compare the two claims."
            ),
        },
    ]
    try:
        # server/llm.py paces calls under the account's 40 requests/minute and
        # retries 429s; it raises LLMUnavailable instead of returning an error text.
        data = llm.chat_json(messages[0]["content"], messages[1]["content"], max_tokens=300)
    except llm.LLMUnavailable as exc:
        return None, "unknown", None, str(exc), False
    verdict = data.get("verdict", "unverifiable")
    if verdict not in VALID_VERDICTS:
        verdict = "unverifiable"
    relation = data.get("relation", "unknown")
    relation = relation if relation in VALID_RELATIONS else "unknown"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence"))))
    except (TypeError, ValueError):
        confidence = None
    return verdict, relation, confidence, data.get("rationale", ""), True


PAIR_SELECT = """
SELECT cl.id AS link_id, cl.verdict AS verdict, cl.relation AS relation,
       cl.candidate_confidence AS candidate_confidence, cl.verdict_source AS verdict_source,
       cl.similarity AS similarity, cl.verdict_confidence AS verdict_confidence,
       ca.id AS a_id, ca.kind AS a_kind, ca.statement_text AS a_text,
       ca.value_normalized AS a_value, ca.value_canonical AS a_canonical,
       ca.qualifiers AS a_qualifiers, ca.context_text AS a_context,
       ca.citation_urls AS a_citations, ca.object_entity AS a_object_entity,
       ca.predicate_entity AS a_predicate_entity, aa.lang AS a_lang,
       ca.predicate AS a_predicate, ca.subject AS a_subject,
       cb.id AS b_id, cb.kind AS b_kind, cb.statement_text AS b_text,
       cb.value_normalized AS b_value, cb.value_canonical AS b_canonical,
       cb.qualifiers AS b_qualifiers, cb.context_text AS b_context,
       cb.citation_urls AS b_citations, cb.object_entity AS b_object_entity,
       cb.predicate_entity AS b_predicate_entity, ab.lang AS b_lang,
       cb.predicate AS b_predicate, cb.subject AS b_subject
FROM claim_links cl
JOIN claims ca ON ca.id = cl.claim_a
JOIN articles aa ON aa.id = ca.article_id
JOIN claims cb ON cb.id = cl.claim_b
JOIN articles ab ON ab.id = cb.article_id
"""


def _side(p: dict, prefix: str) -> dict:
    return {
        "id": p[f"{prefix}_id"], "kind": p[f"{prefix}_kind"],
        "statement_text": p[f"{prefix}_text"],
        "value_normalized": p[f"{prefix}_value"], "lang": p[f"{prefix}_lang"],
        "value_canonical": _json(p.get(f"{prefix}_canonical")),
        "qualifiers": _json(p.get(f"{prefix}_qualifiers")),
        "citation_urls": _json(p.get(f"{prefix}_citations")),
        "context_text": p.get(f"{prefix}_context"),
        "object_entity": p.get(f"{prefix}_object_entity"),
        "predicate_entity": p.get(f"{prefix}_predicate_entity"),
        "predicate": p.get(f"{prefix}_predicate"), "subject": p.get(f"{prefix}_subject"),
    }


def fetch_pairs(where: str, params=()) -> list:
    """Links (with both claims) matching a WHERE clause over PAIR_SELECT aliases."""
    return query(PAIR_SELECT + " WHERE " + where + " ORDER BY cl.id", params)


def fetch_pairs_by_ids(link_ids) -> list:
    link_ids = list(link_ids)
    if not link_ids:
        return []
    return fetch_pairs(f"cl.id IN ({placeholders(link_ids)})", tuple(link_ids))


def _predicate_match_score(a: dict, b: dict) -> float:
    """How strongly the two claims' predicates agree: 1.0 for a shared Wikidata
    property, 0.8 for the same predicate or an alias of it, 0.3 when neither side has a
    predicate (unknown), 0.0 for a mismatch."""
    pea, peb = a.get("predicate_entity"), b.get("predicate_entity")
    if pea and peb:
        return 1.0 if pea == peb else 0.0
    pa, pb = (a.get("predicate") or "").lower(), (b.get("predicate") or "").lower()
    if pa and pb:
        return 0.8 if same_predicate(pa, pb) else 0.0
    return 0.3  # at least one side has no predicate: weak, not disqualifying


def compute_confidence(a: dict, b: dict, source: str, similarity, llm_confidence) -> float:
    """Combine the available signals into one 0..1 verdict confidence.

    Pure function (no DB): easy to reason about and test. Weights:
      - vector similarity 0.35 (how close the two claims are in meaning)
      - predicate agreement 0.30 (same fact, not just similar wording)
      - layer certainty 0.35 (deterministic = 1.0, LLM = its own confidence,
        fallback discounted because the semantic layer never ran)
    """
    try:
        sim = max(0.0, min(1.0, float(similarity)))
    except (TypeError, ValueError):
        sim = 0.0
    pred = _predicate_match_score(a, b)
    if source == SOURCE_DETERMINISTIC:
        certainty = 1.0
    elif source == SOURCE_HUMAN:
        certainty = 1.0
    elif source == SOURCE_LLM:
        try:
            certainty = max(0.0, min(1.0, float(llm_confidence)))
        except (TypeError, ValueError):
            certainty = 0.5  # model answered but gave no usable confidence
    else:  # deterministic-fallback: semantic layer unavailable
        certainty = 0.25
    score = 0.35 * sim + 0.30 * pred + 0.35 * certainty
    return round(max(0.0, min(1.0, score)), 4)


def grade_pair(a: dict, b: dict):
    """Grade one pair -> (verdict, source, rationale, relation, confidence).

    ``confidence`` is the LLM's self-reported certainty (or None/1.0); the
    combined verdict_confidence is computed per-link in grade_links where the
    candidate similarity is known.
    """
    det = deterministic_verdict(a, b)
    if det:
        return det, SOURCE_DETERMINISTIC, "canonical value comparison", "same", 1.0
    verdict, relation, confidence, rationale, available = llm_verdict(a, b)
    if not available:
        # LLM layer could not run (no key, rate limit not cleared in time): no
        # verdict. The caller leaves the link as it is and retries later, so an
        # error message is never stored as a verdict.
        return None
    return verdict, SOURCE_LLM, rationale, relation, confidence


def grade_links(pairs, touch_ids=()) -> list:
    """(Re)grade the given links. claim_links is only UPDATEd when the verdict
    changes, so no no-op system-versioned history rows are written, except for
    ``touch_ids``: links regraded because a claim changed get ``computed_at``
    bumped even when the verdict holds. That records "re-verified against the new
    claim version" in the link's history and stops them looking stale forever.

    A link whose ``verdict_source`` is already ``human`` (an R1 consensus, see
    consensus.py) is not overwritten by a machine regrade, unless it is in
    ``touch_ids``: one of its claims has a newer version, so the decision was made
    on text that no longer exists and the vote reopens. Returns
    [{id, before, after, changed}].
    """
    results = []
    for p in pairs:
        if p.get("verdict_source") == SOURCE_HUMAN and p["link_id"] not in touch_ids:
            # Honour the human decision; report it as unchanged.
            results.append(
                {"id": p["link_id"], "before": p["verdict"],
                 "after": p["verdict"], "changed": False}
            )
            continue
        a, b = _side(p, "a"), _side(p, "b")
        graded = grade_pair(a, b)
        if graded is None:
            # deferred: 'unmatched' links stay hidden; graded links keep their verdict
            results.append({"id": p["link_id"], "before": p["verdict"], "after": p["verdict"],
                            "changed": False, "deferred": True})
            continue
        verdict, source, rationale, relation, confidence = graded
        vconf = compute_confidence(a, b, source, p.get("similarity"), confidence)
        changed = (verdict, relation, confidence) != (
            p["verdict"], p.get("relation"), p.get("candidate_confidence"))
        if changed or p["link_id"] in touch_ids:
            # receipts: both statements, so every verdict can be checked against sources
            execute(
                """UPDATE claim_links
                   SET verdict = %s, relation = %s, candidate_confidence = %s,
                       verdict_confidence = %s, verdict_source = %s, rationale = %s,
                       evidence = CONCAT(%s, ' || ', %s),
                       computed_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (verdict, relation, confidence, vconf, source, rationale,
                 a["statement_text"], b["statement_text"],
                 p["link_id"]),
            )
        results.append(
            {"id": p["link_id"], "before": p["verdict"], "after": verdict, "changed": changed}
        )
    return results


def page_links_where(page_id: int, extra: str = ""):
    """(where, params) over PAIR_SELECT aliases: links between a claim on page X
    and a claim on one of its counterpart pages (comparison set)."""
    others = comparison_set(page_id)[1:]
    if not others:
        return "1 = 0", ()
    ph = placeholders(others)
    where = f"((aa.id = %s AND ab.id IN ({ph})) OR (ab.id = %s AND aa.id IN ({ph})))"
    if extra:
        where += " AND " + extra
    return where, (page_id, *others, page_id, *others)


def grade_unmatched(page_id: int) -> list:
    """Gate, then grade, every 'unmatched' link between this page and its
    counterparts (gate.py: one batched same-fact LLM call per counterpart instead of
    one call per candidate). Returns grade_links()-style results for links that got
    a verdict; rejected candidates become 'different_fact' and are not listed."""
    import gate  # local import: gate.py imports this module
    g = gate.gate_page(page_id)
    return g["graded"] + grade_links(fetch_pairs_by_ids(g["to_grade"]))


def run_verdicts(page_id: int) -> dict:
    """Grade every 'unmatched' link of this page and return verdict counts."""
    counts: dict = {}
    for r in grade_unmatched(page_id):
        if not r.get("deferred"):
            counts[r["after"]] = counts.get(r["after"], 0) + 1
    return counts


# Q1 (assumptions.md): a reviewer decision maps to a machine verdict. Default
# API behaviour is still audit-log + re-check; this override is opt-in.
HUMAN_DECISION_VERDICT = {
    # agree keeps whatever the current verdict is, just re-stamped as human
    "disagree": {
        # disagree on an agreement/possible-match means the reviewer sees a conflict
        "supported": "contradicted",
        "possible_match": "contradicted",
        "agree": "contradicted",
        # disagree on a conflict means the reviewer thinks the claims actually agree
        "contradicted": "supported",
        "possible_conflict": "supported",
    },
    # a "fix" says the conflict is real and the page text needs to change
    # (owner decision 2026-11-14, docs/review-consensus.md O2)
    "fix": "contradicted",
}


def apply_human_verdict(link_id: int, decision: str) -> dict | None:
    """Override a link's verdict from a human review (Q1). Writes an SVT row and
    stamps ``verdict_source='human'`` with full confidence, so later machine
    regrades leave it alone. Returns {id, before, after, changed} or None if the
    link does not exist.
    """
    row = query_one(
        "SELECT id, verdict, verdict_source FROM claim_links WHERE id = %s", (link_id,))
    if not row:
        return None
    before = row["verdict"]
    if decision == "agree":
        after = before  # reviewer confirms the current machine verdict
    elif decision == "disagree":
        # Only flip verdicts we have a clear opposite for; leave others as-is
        # rather than inventing a verdict (e.g. 'unmatched' has nothing to
        # disagree with).
        after = HUMAN_DECISION_VERDICT["disagree"].get(before)
        if after is None:
            return {"id": link_id, "before": before, "after": before, "changed": False}
    elif decision == "fix":
        after = HUMAN_DECISION_VERDICT["fix"]
    else:
        return {"id": link_id, "before": before, "after": before, "changed": False}
    # Skip a true no-op: an already-human link with the same verdict needs no
    # new SVT row (the override is already in force).
    if after == before and row["verdict_source"] == SOURCE_HUMAN:
        return {"id": link_id, "before": before, "after": after, "changed": False}
    execute(
        """UPDATE claim_links
           SET verdict = %s, verdict_source = %s, verdict_confidence = %s,
               rationale = %s, computed_at = CURRENT_TIMESTAMP
           WHERE id = %s""",
        (after, SOURCE_HUMAN, 1.0, f"human review: {decision}", link_id),
    )
    return {"id": link_id, "before": before, "after": after,
            "changed": before != after}
