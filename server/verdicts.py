"""Two verdict layers, cheapest first:

1. deterministic — dates and numbers compared on canonical ``value_normalized``
2. LLM — cross-language entailment via the Verda Inference API

Every UPDATE to claim_links writes a new row version (SYSTEM VERSIONING), so
the full evolution of every verdict stays queryable in SQL.
"""
import json
from concurrent.futures import ThreadPoolExecutor

import httpx

from config import settings
from db import execute, placeholders, query

NUMERIC_TOLERANCE = 1e-9


def _same_fact(a: dict, b: dict) -> bool:
    """Only compare values when both claims state the same relation. Vector
    neighbours can be different facts ("born 1900" vs "died 1986"), so a
    deterministic contradiction needs matching canonical predicates."""
    pa, pb = (a.get("predicate") or "").lower(), (b.get("predicate") or "").lower()
    return bool(pa) and pa == pb


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
        try:
            fa, fb = float(va), float(vb)
        except (TypeError, ValueError):
            return None
        if abs(fa - fb) <= NUMERIC_TOLERANCE:
            return "supported"
        # Differing numbers are often time-qualified (population in 2019 vs 2023),
        # which only the LLM layer can judge from the statement text.
        return None
    return None  # entity_relation / factoid -> LLM


def llm_verdict(a: dict, b: dict):
    """Cross-language entailment via the Verda Inference API.

    The prompt asks for one of: supported / contradicted / unverifiable /
    definition_mismatch (the last one covers false positives where two
    languages use the same name for different concepts).
    """
    if not settings.verda_api_key:
        return "unverifiable", "no LLM configured (set VERDA_API_KEY)"
    messages = [
        {
            "role": "system",
            "content": (
                "You compare atomic claims from different language editions of the same "
                "Wikipedia article. Reply ONLY with JSON: "
                '{"verdict": "supported|contradicted|unverifiable|definition_mismatch", '
                '"rationale": "one short sentence"}. A claim pair is "supported" only if '
                "the other edition states the same fact; \"contradicted\" if it states an "
                "incompatible fact about the same subject; \"definition_mismatch\" if the "
                "claims are actually about different concepts despite similar wording; "
                "otherwise \"unverifiable\"."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Claim A ({a['lang']}): {a['statement_text']}\n"
                f"Claim B ({b['lang']}): {b['statement_text']}\n"
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
                "reasoning_effort": settings.llm_reasoning_effort,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        verdict = data.get("verdict", "unverifiable")
        if verdict not in ("supported", "contradicted", "unverifiable", "definition_mismatch"):
            verdict = "unverifiable"
        return verdict, data.get("rationale", "")
    except Exception as exc:  # degrade gracefully, never crash the pipeline
        return "unverifiable", f"LLM error: {exc}"


PAIR_SELECT = """
SELECT cl.id AS link_id, cl.verdict AS verdict, cl.verdict_source AS verdict_source,
       ca.id AS a_id, ca.kind AS a_kind, ca.statement_text AS a_text,
       ca.value_normalized AS a_value, aa.lang AS a_lang,
       ca.predicate AS a_predicate, ca.subject AS a_subject,
       cb.id AS b_id, cb.kind AS b_kind, cb.statement_text AS b_text,
       cb.value_normalized AS b_value, ab.lang AS b_lang,
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
    """Grade one claim pair, cheapest layer first -> (verdict, source, rationale)."""
    det = deterministic_verdict(a, b)
    if det:
        return det, "deterministic", "canonical value comparison"
    verdict, rationale = llm_verdict(a, b)
    return verdict, "llm", rationale


def grade_links(pairs, touch_ids=()) -> list:
    """(Re)grade the given links. claim_links is only UPDATEd when the verdict
    changes, so no no-op system-versioned history rows are written, except for
    ``touch_ids``: links regraded because a claim changed get ``computed_at``
    bumped even when the verdict holds. That records "re-verified against the new
    claim version" in the link's history and stops them looking stale forever.

    Returns [{id, before, after, changed}].
    """
    touch_ids = set(touch_ids)

    def grade_and_store(p):
        a, b = _side(p, "a"), _side(p, "b")
        verdict, source, rationale = grade_pair(a, b)
        changed = verdict != p["verdict"]
        if changed or p["link_id"] in touch_ids:
            # receipts: both statements, so every verdict can be checked against sources
            execute(
                """UPDATE claim_links
                   SET verdict = %s, verdict_source = %s, rationale = %s,
                       evidence = CONCAT(%s, ' || ', %s),
                       computed_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (verdict, source, rationale, a["statement_text"], b["statement_text"],
                 p["link_id"]),
            )
        return {"id": p["link_id"], "before": p["verdict"], "after": verdict, "changed": changed}

    pairs = list(pairs)
    if not pairs:
        return []
    # LLM calls are network-bound and each verdict write gets its own short
    # database connection through execute(), so parallel grading is safe while
    # preserving the input order expected by callers.
    workers = min(settings.verdict_concurrency, len(pairs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(grade_and_store, pairs))


def grade_unmatched(qid: str) -> list:
    """Grade every 'unmatched' link of this entity. Returns grade_links() results."""
    return grade_links(fetch_pairs(
        """aa.wikidata_qid = %s AND cl.verdict = 'unmatched'
           AND ca.revision_id <=> (SELECT revision_id FROM articles WHERE id = ca.article_id)
           AND cb.revision_id <=> (SELECT revision_id FROM articles WHERE id = cb.article_id)""",
        (qid,),
    ))


def run_verdicts(qid: str) -> dict:
    """Grade every 'unmatched' link of this entity and return verdict counts."""
    counts: dict = {}
    for r in grade_unmatched(qid):
        counts[r["after"]] = counts.get(r["after"], 0) + 1
    return counts
