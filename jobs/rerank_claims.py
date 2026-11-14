#!/usr/bin/env python3
"""Second-sweep ML + MariaDB HNSW vector salience reranker & claim pruner.

Cuts bloated LLM claim extractions down to the core, most encyclopedically
salient and cross-lingually verifiable claims per page while keeping claims
100% dynamic and ML-discovered:

  1. MariaDB HNSW cross-edition resonance (`VEC_DISTANCE_COSINE`): claims whose
     semantic meaning resonates with a counterpart edition score higher than
     obscure single-edition narrative minutiae.
  2. MariaDB lead-chunk centrality (`VEC_DISTANCE_COSINE` vs chunk_idx=0):
     claims central to the subject's summary score higher than tangential trivia.
  3. Verda `kimi-k3-1-pro` second-sweep salience & subject-centrality judge
     (non-thinking JSON mode, rate-limit paced): scores encyclopedic importance
     (0..1), flags off-subject claims (e.g. a parent's career date on the child's
     page), and flags duplicate/redundant claims on the same page.

Safety invariants:
  - Claims referenced by `reviews`, `resolutions`, `suggestions`, or a confirmed
    `contradicted` link are ALWAYS protected and never pruned.
  - Pruned claims remain auditable in MariaDB history via `FOR SYSTEM_TIME ALL`.

Usage:
  python jobs/rerank_claims.py --all-llm --max-per-page 8           # dry run
  python jobs/rerank_claims.py --all-llm --max-per-page 8 --apply   # write scores & prune
"""
import argparse
import json
import os
import random
import re
import sys
import threading
import time

import httpx
import mariadb

RERANK_SYSTEM = """You are an encyclopedic fact-salience reranker for cross-language Wikipedia verification.
Given candidate atomic claims extracted from language editions (fi, sv, en) of ONE subject, evaluate every claim:
1. "about_subject" (boolean): true ONLY if the claim's primary subject is the article subject itself (allow spelling/middle-name variants like "Daniel Olofsson Tilas" for "Daniel Tilas", or "Regeringen Sipilä" for "Sipilä cabinet"). Set FALSE if the claim is primarily about a parent, relative, predecessor, or sub-entity (e.g. "Olof Tilas was a major when ennobled in 1719" on Daniel Tilas's page -> false).
2. "duplicate_of" (integer claim id or null): if another better-phrased or more specific claim ON THE SAME LANGUAGE EDITION states the exact same underlying fact/event, set to that claim's id; otherwise null. Never mark cross-language pairs as duplicates!
3. "salience" (float 0.0 to 1.0): encyclopedic coreness & cross-language checkability:
   - 0.90-1.00: defining core facts (birth/death/inception/dissolution dates & places, primary occupation/role, head/founder, spouse, major eponym/discovery, core population/area/size).
   - 0.65-0.89: major career offices, education, academy memberships, major historical events/fires/treaties.
   - 0.00-0.55: minor narrative episodes, transient inspections, vague commentary, or secondary trivia unlikely to appear in concise counterpart editions.

Reply ONLY with JSON:
{"scores": [{"id": <int>, "salience": <float 0..1>, "about_subject": <bool>, "duplicate_of": <int or null>}]}"""

_rate_lock = threading.Lock()
_next_slot = 0.0
_blocked_until = 0.0


def _parse_sec(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    try:
        return float(s)
    except ValueError:
        pass
    tot, ok = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|s|m|h)", s):
        ok = True
        tot += float(num) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return tot if ok else None


def paced_post(http: httpx.Client, url: str, body: dict, min_interval: float = 1.65) -> dict:
    """Rate-limit-aware POST to Verda Inference (40 req/min shared account window)."""
    global _next_slot, _blocked_until
    backoff = 2.0
    for _ in range(5):
        with _rate_lock:
            now = time.monotonic()
            start = max(now, _next_slot, _blocked_until)
            _next_slot = start + min_interval
        if start > now:
            time.sleep(start - now)
        try:
            r = http.post(url, json=body)
        except httpx.HTTPError:
            with _rate_lock:
                _blocked_until = max(_blocked_until, time.monotonic() + backoff)
            backoff *= 2
            continue
        rem = r.headers.get("x-ratelimit-remaining-requests")
        if rem == "0":
            wait_r = _parse_sec(r.headers.get("x-ratelimit-reset-requests")) or 30.0
            with _rate_lock:
                _blocked_until = max(_blocked_until, time.monotonic() + wait_r)
        elif rem in ("1", "2"):
            with _rate_lock:
                _blocked_until = max(_blocked_until, time.monotonic() + 3.0)
        if r.status_code == 429:
            wait = (_parse_sec(r.headers.get("retry-after"))
                    or _parse_sec(r.headers.get("x-ratelimit-reset-requests")) or 25.0)
            with _rate_lock:
                _blocked_until = max(_blocked_until, time.monotonic() + wait + random.uniform(0.3, 1.2))
            continue
        if r.status_code >= 500:
            with _rate_lock:
                _blocked_until = max(_blocked_until, time.monotonic() + backoff)
            backoff *= 2
            continue
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"] or "{}"
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        return json.loads(raw)
    raise RuntimeError("LLM rerank failed after retries")


def connect():
    return mariadb.connect(
        host=os.environ.get("DATABASE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DATABASE_PORT", "3306")),
        user=os.environ.get("DATABASE_USER", "ovr"),
        password=os.environ.get("DATABASE_PASSWORD", "ovr_pass"),
        database=os.environ.get("DATABASE_NAME", "wikipedia"),
        autocommit=False,
    )


def fetch_dicts(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def comparison_sets_to_rerank(cur, args) -> list[list[int]]:
    if args.titles:
        titles = [t.strip() for t in args.titles.split(",") if t.strip()]
        marks = ",".join(["%s"] * len(titles))
        seed_rows = fetch_dicts(cur, f"SELECT id FROM articles WHERE title IN ({marks})", tuple(titles))
    elif args.all_llm:
        seed_rows = fetch_dicts(
            cur,
            "SELECT DISTINCT article_id AS id FROM claims WHERE model_version IS NULL OR model_version <> 'rules-v1'",
        )
    else:
        seed_rows = fetch_dicts(
            cur,
            "SELECT article_id AS id FROM claims GROUP BY article_id HAVING COUNT(*) > %s",
            (args.max_per_page,),
        )
    seen = set()
    sets = []
    for r in seed_rows:
        aid = r["id"]
        if aid in seen:
            continue
        cps = fetch_dicts(
            cur,
            "SELECT page_b AS id FROM page_links WHERE page_a = %s AND page_b <> page_a",
            (aid,),
        )
        group = sorted({aid} | {c["id"] for c in cps})
        seen.update(group)
        sets.append(group)
    return sets


def vector_signals(cur, claim_ids: list[int]) -> dict[int, tuple[float, float]]:
    """Compute (cross_edition_sim, lead_chunk_sim) per claim in MariaDB via VEC_DISTANCE_COSINE."""
    if not claim_ids:
        return {}
    marks = ",".join(["%s"] * len(claim_ids))
    cross_rows = fetch_dicts(
        cur,
        f"""SELECT c1.id AS claim_id,
                   MIN(VEC_DISTANCE_COSINE(e1.embedding, e2.embedding)) AS min_dist
            FROM claims c1
            JOIN claim_embeddings e1 ON e1.claim_id = c1.id
            JOIN page_links pl ON pl.page_a = c1.article_id AND pl.page_b <> pl.page_a
            JOIN claims c2 ON c2.article_id = pl.page_b
            JOIN claim_embeddings e2 ON e2.claim_id = c2.id
            WHERE c1.id IN ({marks})
            GROUP BY c1.id""",
        tuple(claim_ids),
    )
    lead_rows = fetch_dicts(
        cur,
        f"""SELECT c.id AS claim_id,
                   VEC_DISTANCE_COSINE(ce.embedding, che.embedding) AS lead_dist
            FROM claims c
            JOIN claim_embeddings ce ON ce.claim_id = c.id
            JOIN chunks ch ON ch.article_id = c.article_id AND ch.chunk_idx = 0
            JOIN chunk_embeddings che ON che.chunk_id = ch.id
            WHERE c.id IN ({marks})""",
        tuple(claim_ids),
    )
    cross_map = {
        r["claim_id"]: max(0.0, min(1.0, 1.0 - float(r["min_dist"])))
        for r in cross_rows if r["min_dist"] is not None
    }
    lead_map = {
        r["claim_id"]: max(0.0, min(1.0, 1.0 - float(r["lead_dist"])))
        for r in lead_rows if r["lead_dist"] is not None
    }
    return {cid: (cross_map.get(cid, 0.75), lead_map.get(cid, 0.75)) for cid in claim_ids}


def protected_claim_ids(cur, claim_ids: list[int]) -> set[int]:
    """Claims that MUST NEVER be deleted: reviewed links, resolutions, suggestions, or real conflicts."""
    if not claim_ids:
        return set()
    marks = ",".join(["%s"] * len(claim_ids))
    rows = fetch_dicts(
        cur,
        f"""SELECT cl.claim_a, cl.claim_b
            FROM claim_links cl
            WHERE (cl.claim_a IN ({marks}) OR cl.claim_b IN ({marks}))
              AND (
                  cl.verdict = 'contradicted'
                  OR cl.verdict_source = 'human'
                  OR EXISTS (SELECT 1 FROM reviews r WHERE r.claim_link_id = cl.id)
                  OR EXISTS (SELECT 1 FROM suggestions s WHERE s.claim_link_id = cl.id)
                  OR EXISTS (SELECT 1 FROM resolutions res WHERE res.claim_link_id = cl.id)
              )""",
        tuple(claim_ids + claim_ids),
    )
    keep = set()
    cid_set = set(claim_ids)
    for r in rows:
        if r["claim_a"] in cid_set:
            keep.add(r["claim_a"])
        if r["claim_b"] in cid_set:
            keep.add(r["claim_b"])
    return keep


def llm_score_claims(http: httpx.Client, base_url: str, model: str, title: str, claims: list[dict]) -> dict[int, dict]:
    """Score claims in chunks of up to 45 claims per non-thinking LLM call."""
    out: dict[int, dict] = {}
    batch_size = 45
    for i in range(0, len(claims), batch_size):
        batch = claims[i:i + batch_size]
        lines = [
            f"#{c['id']} [{c['lang']}] subj={c['subject']!r} pred={c['predicate']!r} "
            f"val={c['value_text']!r} | {c['statement_text']}"
            for c in batch
        ]
        user = f"Primary article subject: {title}\n\nClaims:\n" + "\n".join(lines)
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 55 * len(batch) + 300,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": RERANK_SYSTEM},
                {"role": "user", "content": user},
            ],
        }
        reply = paced_post(http, f"{base_url}/chat/completions", body)
        by_lang = {c["id"]: c["lang"] for c in batch}
        for item in reply.get("scores") or []:
            try:
                cid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if cid not in by_lang:
                continue
            try:
                sal = max(0.0, min(1.0, float(item.get("salience", 0.6))))
            except (TypeError, ValueError):
                sal = 0.6
            about = bool(item.get("about_subject", True))
            dup = item.get("duplicate_of")
            try:
                dup = int(dup) if dup is not None else None
            except (TypeError, ValueError):
                dup = None
            if dup is not None and (dup == cid or by_lang.get(dup) != by_lang[cid]):
                dup = None
            out[cid] = {"salience": sal, "about_subject": about, "duplicate_of": dup}
    return out


def rerank_set(conn, http: httpx.Client | None, args, page_ids: list[int]) -> tuple[int, int]:
    cur = conn.cursor()
    marks = ",".join(["%s"] * len(page_ids))
    pages = fetch_dicts(
        cur,
        f"SELECT id, lang, title FROM articles WHERE id IN ({marks}) ORDER BY FIELD(lang,'en','sv','fi'), id",
        tuple(page_ids),
    )
    if not pages:
        return 0, 0
    title = pages[0]["title"]
    claims = fetch_dicts(
        cur,
        f"""SELECT c.id, c.article_id, a.lang, c.kind, c.subject, c.predicate,
                   c.value_text, c.value_normalized, c.statement_text,
                   c.extraction_confidence, c.data_quality_score
            FROM claims c JOIN articles a ON a.id = c.article_id
            WHERE c.article_id IN ({marks})
            ORDER BY c.id""",
        tuple(page_ids),
    )
    if not claims:
        return 0, 0

    cids = [c["id"] for c in claims]
    vec_sig = vector_signals(cur, cids)
    protected = protected_claim_ids(cur, cids)

    llm_scores = {}
    if http is not None and args.api_key:
        try:
            llm_scores = llm_score_claims(http, args.base_url, args.model, title, claims)
        except Exception as exc:
            print(f"  [warn] {title}: LLM rerank fallback to vector+quality signals ({exc})", file=sys.stderr)

    # Combine LLM salience + MariaDB HNSW cross-edition resonance + lead centrality + confidence
    scored_by_page: dict[int, list[dict]] = {}
    for c in claims:
        cid = c["id"]
        cross_sim, lead_sim = vec_sig.get(cid, (0.75, 0.75))
        # Normalize compressed e5 cosine similarity (0.72..0.96 -> 0..1)
        cross_norm = max(0.0, min(1.0, (cross_sim - 0.72) / 0.24))
        lead_norm = max(0.0, min(1.0, (lead_sim - 0.72) / 0.24))
        conf = float(c["extraction_confidence"] if c["extraction_confidence"] is not None else 0.8)
        typed_bonus = 0.06 if c["kind"] in ("date", "number", "quantity") and c["value_normalized"] else 0.0

        info = llm_scores.get(cid)
        if info:
            llm_sal = info["salience"]
            about = info["about_subject"]
            dup = info["duplicate_of"]
        else:
            llm_sal = 0.75 if c["kind"] in ("date", "number", "quantity") else 0.60
            about = True
            dup = None

        score = 0.50 * llm_sal + 0.30 * cross_norm + 0.10 * lead_norm + 0.10 * conf + typed_bonus
        if not about:
            score *= 0.15
        if dup is not None:
            score *= 0.20
        c["salience_score"] = round(max(0.0, min(1.0, score)), 4)
        c["about_subject"] = about
        c["duplicate_of"] = dup
        c["protected"] = cid in protected
        scored_by_page.setdefault(c["article_id"], []).append(c)

    keep_ids: set[int] = set()
    prune_ids: list[int] = []
    for pid, pclaims in scored_by_page.items():
        ranked = sorted(
            pclaims,
            key=lambda x: (x["protected"], x["about_subject"], x["duplicate_of"] is None, x["salience_score"], -x["id"]),
            reverse=True,
        )
        kept_here = 0
        for item in ranked:
            if item["protected"]:
                keep_ids.add(item["id"])
                kept_here += 1
            elif (
                kept_here < args.max_per_page
                and item["about_subject"]
                and item["duplicate_of"] is None
                and item["salience_score"] >= args.min_salience
            ):
                keep_ids.add(item["id"])
                kept_here += 1
            else:
                prune_ids.append(item["id"])

    lang_counts_before = {p["lang"]: len(scored_by_page.get(p["id"], ())) for p in pages}
    lang_counts_after = {
        p["lang"]: sum(1 for c in scored_by_page.get(p["id"], ()) if c["id"] in keep_ids)
        for p in pages
    }
    print(f"{title}: before={lang_counts_before} -> after={lang_counts_after} (pruned {len(prune_ids)})", flush=True)

    if not args.apply:
        conn.rollback()
        return len(keep_ids), len(prune_ids)

    conn.rollback()  # fresh transaction before writes
    for c in claims:
        if c["id"] in keep_ids:
            cur.execute(
                "UPDATE claims SET salience_score = %s WHERE id = %s AND NOT (salience_score <=> %s)",
                (c["salience_score"], c["id"], c["salience_score"]),
            )
    if prune_ids:
        pmarks = ",".join(["%s"] * len(prune_ids))
        cur.execute(
            f"DELETE FROM claim_links WHERE (claim_a IN ({pmarks}) OR claim_b IN ({pmarks})) "
            f"AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.claim_link_id = claim_links.id) "
            f"AND NOT EXISTS (SELECT 1 FROM suggestions s WHERE s.claim_link_id = claim_links.id) "
            f"AND NOT EXISTS (SELECT 1 FROM resolutions res WHERE res.claim_link_id = claim_links.id)",
            tuple(prune_ids + prune_ids),
        )
        # Double-check none of prune_ids still has a remaining FK link
        still_linked = fetch_dicts(
            cur,
            f"SELECT claim_a, claim_b FROM claim_links WHERE claim_a IN ({pmarks}) OR claim_b IN ({pmarks})",
            tuple(prune_ids + prune_ids),
        )
        blocked = {r["claim_a"] for r in still_linked} | {r["claim_b"] for r in still_linked}
        deletable = [cid for cid in prune_ids if cid not in blocked]
        if deletable:
            dmarks = ",".join(["%s"] * len(deletable))
            cur.execute(f"DELETE FROM claim_embeddings WHERE claim_id IN ({dmarks})", tuple(deletable))
            cur.execute(f"DELETE FROM claims WHERE id IN ({dmarks})", tuple(deletable))
    conn.commit()
    return len(keep_ids), len(prune_ids)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--titles", help="comma-separated article titles")
    ap.add_argument("--all-llm", action="store_true", help="rerank all non-rules-v1 comparison sets")
    ap.add_argument("--max-per-page", type=int, default=8, help="target max core claims per page (default: 8)")
    ap.add_argument("--min-salience", type=float, default=0.35, help="minimum salience score for unprotected claims")
    ap.add_argument("--no-llm", action="store_true", help="use MariaDB vector + quality signals only")
    ap.add_argument("--apply", action="store_true", help="persist salience_score and prune excess claims")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "kimi-k3-1-pro"))
    ap.add_argument("--base-url", default=os.environ.get("VERDA_BASE_URL", "https://inference.verda.com/v1"))
    args = ap.parse_args()
    args.api_key = "" if args.no_llm else os.environ.get("VERDA_API_KEY", "")

    conn = connect()
    try:
        cur = conn.cursor()
        sets = comparison_sets_to_rerank(cur, args)
        conn.rollback()
        print(f"reranking {len(sets)} comparison sets (max_per_page={args.max_per_page}, apply={args.apply})", flush=True)
        http = (
            httpx.Client(timeout=90.0, headers={"Authorization": f"Bearer {args.api_key}"})
            if args.api_key else None
        )
        kept_total, pruned_total = 0, 0
        try:
            for group in sets:
                k, p = rerank_set(conn, http, args, group)
                kept_total += k
                pruned_total += p
        finally:
            if http:
                http.close()
        print(f"done: kept={kept_total}, pruned={pruned_total} (apply={args.apply})", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
