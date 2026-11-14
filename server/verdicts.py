"""Confidence-aware claim grading, cheapest evidence first:

1. deterministic — dates and numbers compared on canonical ``value_normalized``
2. LLM — cross-language entailment via the Verda Inference API

Every UPDATE to claim_links writes a new row version (SYSTEM VERSIONING), so
the full evolution of every verdict stays queryable in SQL.
"""
import json

import httpx

from config import settings
from db import execute, placeholders, query
from pages import comparison_set

NUMERIC_TOLERANCE = 1e-9
VALID_VERDICTS = {
    "supported", "contradicted", "possible_match", "possible_conflict",
    "unverifiable", "definition_mismatch",
}
VALID_RELATIONS = {"same", "related", "different", "unknown"}


def _same_fact(a: dict, b: dict) -> bool:
    """True only for a *safe* deterministic comparison.

    AI relation labels are not a candidate-generation gate, but deterministic
    value equality/contradiction still needs an exact relation identity. Prefer
    a mapped Wikidata property and fall back to a canonical predicate string.
    """
    pa, pb = a.get("predicate_entity"), b.get("predicate_entity")
    if pa and pb:
        return pa == pb
    pa, pb = (a.get("predicate") or "").lower(), (b.get("predicate") or "").lower()
    return bool(pa) and pa == pb


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
    """Contextual cross-language entailment via the Verda Inference API."""
    if not settings.verda_api_key:
        # Keep a loose vector candidate visible instead of misrepresenting it
        # as a confirmed gap when semantic verification is unavailable.
        return "possible_match", "unknown", None, "no LLM configured (set VERDA_API_KEY)"
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
        r = httpx.post(
            f"{settings.verda_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.verda_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        verdict = data.get("verdict", "unverifiable")
        if verdict not in VALID_VERDICTS:
            verdict = "unverifiable"
        relation = data.get("relation", "unknown")
        relation = relation if relation in VALID_RELATIONS else "unknown"
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        return verdict, relation, confidence, data.get("rationale", "")
    except Exception as exc:  # degrade gracefully, never crash the pipeline
        return "possible_match", "unknown", None, f"LLM error: {exc}"


PAIR_SELECT = """
SELECT cl.id AS link_id, cl.verdict AS verdict, cl.relation AS relation,
       cl.candidate_confidence AS candidate_confidence, cl.verdict_source AS verdict_source,
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


def grade_pair(a: dict, b: dict):
    """Grade one pair -> (verdict, source, rationale, relation, confidence)."""
    det = deterministic_verdict(a, b)
    if det:
        return det, "deterministic", "canonical value comparison", "same", 1.0
    verdict, relation, confidence, rationale = llm_verdict(a, b)
    return verdict, "llm", rationale, relation, confidence


def grade_links(pairs, touch_ids=()) -> list:
    """(Re)grade the given links. claim_links is only UPDATEd when the verdict
    changes, so no no-op system-versioned history rows are written, except for
    ``touch_ids``: links regraded because a claim changed get ``computed_at``
    bumped even when the verdict holds. That records "re-verified against the new
    claim version" in the link's history and stops them looking stale forever.

    Returns [{id, before, after, changed}].
    """
    results = []
    for p in pairs:
        a, b = _side(p, "a"), _side(p, "b")
        verdict, source, rationale, relation, confidence = grade_pair(a, b)
        changed = (verdict, relation, confidence) != (
            p["verdict"], p.get("relation"), p.get("candidate_confidence"))
        if changed or p["link_id"] in touch_ids:
            # receipts: both statements, so every verdict can be checked against sources
            execute(
                """UPDATE claim_links
                   SET verdict = %s, relation = %s, candidate_confidence = %s,
                       verdict_source = %s, rationale = %s,
                       evidence = CONCAT(%s, ' || ', %s),
                       computed_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (verdict, relation, confidence, source, rationale,
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
    """Grade every 'unmatched' link between this page and its counterparts.
    Returns grade_links() results."""
    return grade_links(fetch_pairs(*page_links_where(page_id, "cl.verdict = 'unmatched'")))


def run_verdicts(page_id: int) -> dict:
    """Grade every 'unmatched' link of this page and return verdict counts."""
    counts: dict = {}
    for r in grade_unmatched(page_id):
        counts[r["after"]] = counts.get(r["after"], 0) + 1
    return counts
