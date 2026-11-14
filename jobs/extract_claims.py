#!/usr/bin/env python3
"""Claim extraction (cold path): article chunks -> atomic claims, via Verda Inference.

Each language edition is extracted separately, in its own language, so the
statement text stays a faithful receipt. To make claims comparable across
languages, the LLM also emits a canonical English ``subject`` / ``predicate``
(snake_case, e.g. ``birth_date``) and a normalized value:

  date            ISO 8601 at the precision stated: YYYY, YYYY-MM or YYYY-MM-DD
  number/quantity plain decimal, no separators ("674500"); unit goes in value_text

Every chunk of every imported article is processed. Chunks are packed into
bounded model requests, so the extractor has complete article coverage without
assuming a model has an unlimited context window. The source chunk, its
surrounding context and any citations reported by the model stay with the
claim, allowing later comparison to escalate beyond a bare one-sentence diff.

Idempotent by default: articles that already have claims are skipped, so the
job can be interrupted and resumed. ``--reextract`` explicitly replaces an
article's active claims after preserving their system-versioned history; use it
when upgrading from the former lead-only extractor.

Usage:
  python extract_claims.py --pilot 20               # best-covered tri-lingual topics
  python extract_claims.py --qids Q33,Q1757
  python extract_claims.py --all-trilingual --concurrency 4
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import mariadb

KINDS = {"date", "number", "quantity", "entity_relation", "factoid"}
DATE_RE = re.compile(r"^-?\d{1,4}(-\d{2}(-\d{2})?)?$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")

SYSTEM_PROMPT = """You extract atomic, verifiable factual claims from supplied chunks of one Wikipedia article.
The excerpt is split into chunks marked [#N]. Reply ONLY with JSON:
{"claims": [{"chunk": N, "kind": "date|number|quantity|entity_relation|factoid",
  "subject": "...", "subject_entity": "Q...|null", "predicate": "...",
  "predicate_entity": "P...|null", "object_entity": "Q...|null",
  "value_text": "...", "value_normalized": "...", "value_canonical": {},
  "qualifiers": {}, "citations": ["url-or-reference-id"], "confidence": 0.0,
  "statement": "..."}]}

Rules:
- One fact per claim. Prefer facts another language edition could confirm or contradict:
  dates, years, numbers, places, office holders, memberships, founders, outcomes.
- "statement": a short self-contained sentence in the SAME LANGUAGE as the excerpt, close to
  the source wording. Resolve pronouns ("he" -> the person's name).
- "subject": the entity the claim is about, in English (e.g. "Urho Kekkonen").
  Include ``subject_entity`` as a Wikidata QID only when you are confident; never invent IDs.
- "predicate": canonical English snake_case relation (e.g. birth_date, birth_place,
  death_date, population, area_km2, founded, office_held, spouse, member_of, signed_on).
  Use the same predicate for the same kind of fact every time. Include the matching Wikidata
  property in ``predicate_entity`` only when known.
- "value_text": the value as written in the source.
- "value_normalized": for kind=date use ISO at the stated precision (YYYY, YYYY-MM or
  YYYY-MM-DD; BCE years negative); for number/quantity a plain decimal without thousands
  separators or units; otherwise a short canonical English value (e.g. "Lapua").
- "value_canonical": a compact typed object. For quantities include ``value`` (decimal string),
  ``unit`` (UCUM/SI symbol when possible), ``dimension`` and an SI ``base_value`` when conversion
  is exact. For dates include ``value``, ``precision`` and ``calendar``. Include ranges,
  uncertainty, scope and ``as_of`` in ``qualifiers`` instead of silently discarding them.
- "object_entity": use a Wikidata QID for an entity-valued object when confidently known.
- "citations": copy only citation URLs or reference identifiers visibly associated with the claim.
- "confidence": your extraction confidence from 0 to 1. Do not extract an assertion that is
  merely speculation, quotation without attribution, navigation text or an opinion.
- "chunk": the [#N] the claim comes from.
- At most %(max_claims)d claims. Skip opinions, vague statements and navigation text."""


def connect():
    return mariadb.connect(
        host=os.environ.get("DATABASE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DATABASE_PORT", "3306")),
        user=os.environ.get("DATABASE_USER", "ovr"),
        password=os.environ.get("DATABASE_PASSWORD", "ovr_pass"),
        database=os.environ.get("DATABASE_NAME", "wikipedia"),
        autocommit=False,
    )


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def pick_qids(cur, args) -> list:
    if args.qids:
        return [q.strip() for q in args.qids.split(",") if q.strip()]
    # tri-lingual topics, best-covered first: rank by the shortest of the three editions
    sql = """
        SELECT a.wikidata_qid AS qid, MIN(t.len) AS min_len
        FROM articles a
        JOIN (SELECT article_id, SUM(CHAR_LENGTH(text)) AS len FROM chunks GROUP BY article_id) t
          ON t.article_id = a.id
        WHERE a.wikidata_qid IS NOT NULL
        GROUP BY a.wikidata_qid
        HAVING COUNT(DISTINCT a.lang) = 3
        ORDER BY min_len DESC
    """
    qids = [r["qid"] for r in rows(cur, sql)]
    return qids[: args.pilot] if args.pilot else qids


def article_chunks(cur, article_id: int):
    chunks = rows(
        cur,
        "SELECT id, chunk_idx, text FROM chunks WHERE article_id = %s ORDER BY chunk_idx",
        (article_id,),
    )
    return [(ch, ch["text"] or "") for ch in chunks if ch["text"]]


def chunk_batches(picked, max_chars: int):
    """Yield every source chunk once in model-sized, paragraph-safe batches."""
    batch, used = [], 0
    for ch, body in picked:
        # An unexpectedly large chunk is still covered, in sequential slices.
        pieces = [body[i:i + max_chars] for i in range(0, len(body), max_chars)] or [""]
        for piece in pieces:
            cost = len(piece) + 32
            if batch and used + cost > max_chars:
                yield batch
                batch, used = [], 0
            batch.append((ch, piece))
            used += cost
    if batch:
        yield batch


def call_llm(http: httpx.Client, args, title: str, lang: str, picked) -> list:
    excerpt = "\n\n".join(f"[#{ch['chunk_idx']}]\n{text}" for ch, text in picked)
    body = {
        "model": args.model,
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT % {"max_claims": args.max_claims}},
            {"role": "user", "content": f"Article: {title} (language: {lang})\n\n{excerpt}"},
        ],
    }
    for attempt in range(4):
        r = http.post(f"{args.base_url}/chat/completions", json=body)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(float(r.headers.get("retry-after") or 2 ** (attempt + 1)))
            continue
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"] or ""
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        return json.loads(content).get("claims", [])
    r.raise_for_status()
    return []


def clean(claim: dict, chunk_ids: dict, contexts: dict):
    """Validate one LLM claim; return a DB row tuple or None."""
    kind = (claim.get("kind") or "").strip()
    statement = (claim.get("statement") or "").strip()
    if kind not in KINDS or not statement:
        return None
    norm = (str(claim.get("value_normalized") or "")).strip() or None
    if kind == "date" and norm and not DATE_RE.match(norm):
        norm = None  # never let a malformed date reach the deterministic layer
    if kind in ("number", "quantity") and norm:
        norm = norm.replace(",", "").replace(" ", "")
        if not NUM_RE.match(norm):
            norm = None
    try:
        chunk_id = chunk_ids.get(int(claim.get("chunk")))
    except (TypeError, ValueError):
        chunk_id = None
    chunk_id = chunk_id or next(iter(chunk_ids.values()))
    def json_value(value):
        if not isinstance(value, (dict, list)):
            return None
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return None

    def confidence(value):
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return None

    clip = lambda v, n: (str(v).strip()[:n] or None) if v is not None else None
    chunk_idx = int(claim.get("chunk")) if str(claim.get("chunk", "")).isdigit() else None
    context = contexts.get(chunk_idx, "")
    return (
        chunk_id, kind, clip(claim.get("subject"), 512), clip(claim.get("subject_entity"), 32),
        clip(claim.get("predicate"), 255), clip(claim.get("predicate_entity"), 32),
        clip(claim.get("object_entity"), 32), clip(claim.get("value_text"), 1024), clip(norm, 512),
        json_value(claim.get("value_canonical")), json_value(claim.get("qualifiers")), statement,
        context, json_value(claim.get("citations")), confidence(claim.get("confidence")),
    )


def extract_article(args, art: dict) -> tuple:
    """Returns (article, n_claims, error)."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM claims WHERE article_id = %s ORDER BY id", (art["id"],))
        existing = [r[0] for r in cur.fetchall()]
        if existing and not args.reextract:
            return art, 0, "skipped (already has claims; use --reextract for full-page extraction)"
        if existing:
            marks = ",".join(["%s"] * len(existing))
            # Preserve auditability: these are system-versioned tables, so the
            # old extraction, links and reviews remain queryable FOR SYSTEM_TIME.
            cur.execute(
                f"UPDATE suggestions SET claim_link_id = NULL WHERE claim_link_id IN "
                f"(SELECT id FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks}))",
                tuple(existing + existing),
            )
            cur.execute(
                f"DELETE FROM reviews WHERE claim_link_id IN "
                f"(SELECT id FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks}))",
                tuple(existing + existing),
            )
            cur.execute(
                f"DELETE FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks})",
                tuple(existing + existing),
            )
            cur.execute(f"DELETE FROM claim_embeddings WHERE claim_id IN ({marks})", tuple(existing))
            cur.execute(f"DELETE FROM claims WHERE id IN ({marks})", tuple(existing))
        picked = article_chunks(cur, art["id"])
        if not picked:
            return art, 0, "no chunks"
        with httpx.Client(
            timeout=args.timeout,
            headers={"Authorization": f"Bearer {args.api_key}"},
        ) as http:
            claims = []
            for batch in chunk_batches(picked, args.max_chars):
                claims.extend(call_llm(http, args, art["title"], art["lang"], batch))
        chunk_ids = {ch["chunk_idx"]: ch["id"] for ch, _ in picked}
        contexts = {ch["chunk_idx"]: text for ch, text in picked}
        good, seen = [], set()
        for raw in claims:
            row = clean(raw, chunk_ids, contexts)
            if not row:
                continue
            # Avoid repeated lead/template facts while keeping distinct time/scope-qualified facts.
            key = (row[2] or row[3], row[4] or row[5], row[6] or row[8], row[10] or row[11])
            if key not in seen:
                seen.add(key)
                good.append(row)
        if args.dry_run:
            for row in good:
                print(f"  [{art['lang']}] {row[1]:<15} {row[4]!s:<18} {row[8]!s:<12} {row[11]}")
            return art, len(good), None
        for row in good:
            cur.execute(
                """INSERT INTO claims (article_id, chunk_id, kind, subject, subject_entity,
                       predicate, predicate_entity, object_entity, value_text, value_normalized,
                       value_canonical, qualifiers, statement_text, context_text, citation_urls,
                       extraction_confidence, revision_id, model_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s)""",
                (art["id"], *row, art["revision_id"], args.model),
            )
        conn.commit()
        return art, len(good), None
    except Exception as exc:  # keep the batch going; report and move on
        conn.rollback()
        return art, 0, f"error: {exc}"
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--qids", help="comma-separated Wikidata QIDs")
    sel.add_argument("--pilot", type=int, help="the N best-covered tri-lingual topics")
    sel.add_argument("--all-trilingual", action="store_true", help="every topic in fi+sv+en")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--reextract", action="store_true",
                    help="replace existing active claims (their system-version history remains)")
    ap.add_argument("--max-chars", type=int, default=6000,
                    help="maximum characters per extraction request; all article chunks are processed")
    ap.add_argument("--max-claims", type=int, default=30)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--dry-run", action="store_true", help="print claims, don't write")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "kimi-k3-1-pro"))
    ap.add_argument("--base-url", default=os.environ.get("VERDA_BASE_URL", "https://inference.verda.com/v1"))
    args = ap.parse_args()
    args.api_key = os.environ.get("VERDA_API_KEY", "")
    if not args.api_key:
        sys.exit("VERDA_API_KEY is not set")

    conn = connect()
    cur = conn.cursor()
    qids = pick_qids(cur, args)
    if not qids:
        sys.exit("no matching QIDs")
    marks = ",".join(["%s"] * len(qids))
    arts = rows(
        cur,
        f"SELECT id, lang, title, wikidata_qid, revision_id FROM articles "
        f"WHERE wikidata_qid IN ({marks}) ORDER BY wikidata_qid, lang",
        tuple(qids),
    )
    conn.close()
    print(f"{len(qids)} topics, {len(arts)} articles, concurrency {args.concurrency}", file=sys.stderr)

    started, done, total_claims, errors = time.time(), 0, 0, 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(extract_article, args, a) for a in arts]
        for fut in as_completed(futures):
            art, n, err = fut.result()
            done += 1
            total_claims += n
            errors += bool(err and err.startswith("error"))
            status = err or f"{n} claims"
            rate = done / max(time.time() - started, 1e-6) * 60
            print(f"[{done}/{len(arts)} {rate:.1f}/min] {art['wikidata_qid']} {art['lang']} "
                  f"{art['title']}: {status}", file=sys.stderr, flush=True)
    print(f"done: {total_claims} claims from {done} articles ({errors} errors)")
    print("next: python jobs/embed.py --tables claims, then POST /api/align/{qid}")


if __name__ == "__main__":
    main()
