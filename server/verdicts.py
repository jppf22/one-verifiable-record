"""Two verdict layers, cheapest first:

1. deterministic — dates and numbers compared on canonical ``value_normalized``
2. LLM — cross-language entailment via the Verda Inference API

Every UPDATE to claim_links writes a new row version (SYSTEM VERSIONING), so
the full evolution of every verdict stays queryable in SQL.
"""
import json

import httpx

from config import settings
from db import execute, query

NUMERIC_TOLERANCE = 1e-9


def deterministic_verdict(a: dict, b: dict) -> str | None:
    """Return a verdict, or None if this pair needs the LLM layer."""
    if a["kind"] != b["kind"]:
        return None
    if a["kind"] == "date":
        if not a["value_normalized"] or not b["value_normalized"]:
            return None
        return "supported" if a["value_normalized"] == b["value_normalized"] else "contradicted"
    if a["kind"] in ("number", "quantity"):
        try:
            va, vb = float(a["value_normalized"]), float(b["value_normalized"])
        except (TypeError, ValueError):
            return None
        return "supported" if abs(va - vb) <= NUMERIC_TOLERANCE else "contradicted"
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


def run_verdicts(qid: str) -> dict:
    """Grade every 'unmatched' link of this entity and return verdict counts."""
    pairs = query(
        """SELECT cl.id AS link_id,
                  ca.id AS a_id, ca.kind AS a_kind, ca.statement_text AS a_text,
                  ca.value_normalized AS a_value, aa.lang AS a_lang,
                  cb.id AS b_id, cb.kind AS b_kind, cb.statement_text AS b_text,
                  cb.value_normalized AS b_value, ab.lang AS b_lang
           FROM claim_links cl
           JOIN claims ca ON ca.id = cl.claim_a
           JOIN articles aa ON aa.id = ca.article_id
           JOIN claims cb ON cb.id = cl.claim_b
           JOIN articles ab ON ab.id = cb.article_id
           WHERE aa.wikidata_qid = %s AND cl.verdict = 'unmatched'""",
        (qid,),
    )
    counts: dict = {}
    for p in pairs:
        a = {
            "id": p["a_id"], "kind": p["a_kind"], "statement_text": p["a_text"],
            "value_normalized": p["a_value"], "lang": p["a_lang"],
        }
        b = {
            "id": p["b_id"], "kind": p["b_kind"], "statement_text": p["b_text"],
            "value_normalized": p["b_value"], "lang": p["b_lang"],
        }
        det = deterministic_verdict(a, b)
        if det:
            verdict, source, rationale = det, "deterministic", "canonical value comparison"
        else:
            verdict, rationale = llm_verdict(a, b)
            source = "llm"
        # receipts: both statements, so every verdict can be checked against sources
        execute(
            """UPDATE claim_links
               SET verdict = %s, verdict_source = %s, rationale = %s,
                   evidence = CONCAT(%s, ' || ', %s)
               WHERE id = %s""",
            (verdict, source, rationale, a["statement_text"], b["statement_text"], p["link_id"]),
        )
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts
