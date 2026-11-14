#!/usr/bin/env python3
"""Claim extraction (cold path): article chunks -> atomic claims, via Verda Inference.

Each language edition is extracted separately, in its own language, so the
statement text stays a faithful receipt. To make claims comparable across
languages, the LLM also emits a canonical English ``subject`` / ``predicate``
(snake_case, e.g. ``birth_date``) and a normalized value:

  date            ISO 8601 at the precision stated: YYYY, YYYY-MM or YYYY-MM-DD
  number/quantity plain decimal, no separators ("674500"); unit goes in value_text

Every source chunk is sent to the extractor.  Chunks are batched only to stay
inside the model context window; no section or relation is selected by a
hard-coded property list.

Idempotent by default: articles that already have claims are skipped, so the
job can be interrupted and resumed. ``--replace-current`` deliberately
re-extracts the selected current revisions after removing their derived links
and embeddings; system-versioned claim/link history remains in MariaDB.

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

DATE_RE = re.compile(r"^-?\d{1,4}(-\d{2}(-\d{2})?)?$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")

SYSTEM_PROMPT = """You extract atomic, verifiable factual claims from one Wikipedia article excerpt.
The excerpt is split into chunks marked [#N]. Reply ONLY with JSON:
{"claims": [{"chunk": N, "kind": "...",
  "subject": "...", "predicate": "...", "value_text": "...", "value_normalized": "...",
  "statement": "..."}]}

Rules:
- One fact per claim. Extract every concrete, source-supported fact that another
  language edition could confirm or contradict. This includes dates, years,
  numbers, places, office holders, memberships, founders, outcomes, material,
  classification, composition, and relations not named here.
- The page may be about a person, place, organization, event, creative work, species,
  or concept. Extract the facts that fit that subject; do not assume it is a biography.
- "statement": a short self-contained sentence in the SAME LANGUAGE as the excerpt, close to
  the source wording. Resolve pronouns ("he" -> the person's name).
- "subject": the entity the claim is about, in English (e.g. "Urho Kekkonen").
- "kind": a short, optional descriptive label. It is not a fixed vocabulary; use
  "date", "number", or "quantity" when those normalizations apply, otherwise any
  useful lower_snake_case label.
- "predicate": canonical English snake_case relation. It is an open vocabulary:
  introduce the relation that accurately describes the fact and use the same
  predicate for the same relation every time. Examples are illustrative only.
- "value_text": the value as written in the source.
- "value_normalized": for kind=date use ISO at the stated precision (YYYY, YYYY-MM or
  YYYY-MM-DD; BCE years negative); for number/quantity a plain decimal without thousands
  separators or units; otherwise a short canonical English value (e.g. "Lapua").
- "chunk": the [#N] the claim comes from.
- %(claim_limit)s Skip opinions, vague statements and navigation text."""


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
        JOIN (SELECT ch.article_id, SUM(CHAR_LENGTH(ch.text)) AS len
              FROM chunks ch JOIN articles current_a ON current_a.id = ch.article_id
              WHERE ch.revision_id <=> current_a.revision_id
              GROUP BY ch.article_id) t ON t.article_id = a.id
        WHERE a.wikidata_qid IS NOT NULL
        GROUP BY a.wikidata_qid
        HAVING COUNT(DISTINCT a.lang) = 3
        ORDER BY min_len DESC
    """
    qids = [r["qid"] for r in rows(cur, sql)]
    return qids[: args.pilot] if args.pilot else qids


def article_chunks(cur, article_id: int):
    """Return every current source chunk, without a lead-section cutoff."""
    chunks = rows(
        cur,
        """SELECT ch.id, ch.chunk_idx, ch.text FROM chunks ch
           JOIN articles a ON a.id = ch.article_id
           WHERE ch.article_id = %s AND ch.revision_id <=> a.revision_id
           ORDER BY ch.chunk_idx""",
        (article_id,),
    )
    return [(ch, ch["text"] or "") for ch in chunks]


def _split_for_request(chunk, text: str, batch_chars: int):
    """Yield source-preserving slices for an oversized imported chunk.

    Current imports produce bounded chunks, but this also protects old imports
    and arbitrary JSONL input. Reusing the chunk index keeps every extracted
    claim attached to its original source receipt.
    """
    text = text or ""
    while len(text) > batch_chars:
        cut = text.rfind(" ", 0, batch_chars + 1)
        if cut < batch_chars // 2:
            cut = batch_chars
        yield chunk, text[:cut]
        text = text[cut:].lstrip()
    if text:
        yield chunk, text


def call_llm(http: httpx.Client, args, title: str, lang: str, picked) -> list:
    excerpt = "\n\n".join(f"[#{ch['chunk_idx']}]\n{text}" for ch, text in picked)
    body = {
        "model": args.model,
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.reasoning_effort,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT % {
                "claim_limit": (
                    f"At most {args.max_claims} claims for this batch."
                    if args.max_claims else "Extract all qualifying claims in this batch."
                ),
            }},
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
        # Some reasoning-capable OpenAI-compatible gateways occasionally put a
        # short preamble before an otherwise valid JSON object.  Treat the
        # first JSON object as the structured answer rather than discarding a
        # whole article because of that presentation detail.
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        start = content.find("{")
        if start < 0:
            raise ValueError("LLM returned no JSON object")
        data, _ = json.JSONDecoder().raw_decode(content[start:])
        claims = data.get("claims", []) if isinstance(data, dict) else []
        if not isinstance(claims, list):
            raise ValueError("LLM JSON field 'claims' is not a list")
        return claims
    r.raise_for_status()
    return []


def clean(claim: dict, chunk_ids: dict):
    """Validate one LLM claim; return a DB row tuple or None."""
    kind = (claim.get("kind") or "fact").strip().lower()
    statement = (claim.get("statement") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind) or not statement:
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
    clip = lambda v, n: (str(v).strip()[:n] or None) if v is not None else None
    return (
        chunk_id, kind, clip(claim.get("subject"), 512), clip(claim.get("predicate"), 255),
        clip(claim.get("value_text"), 1024), clip(norm, 512), statement,
    )


def remove_current_claims(cur, article_id: int, revision_id):
    """Remove only derived state for one selected current article revision.

    `claims` and `claim_links` are system-versioned, so this records their end
    time instead of erasing audit history. Claim embeddings are deliberately
    unversioned cache rows and must be regenerated for the replacement claims.
    """
    cur.execute(
        "SELECT id FROM claims WHERE article_id = %s AND revision_id <=> %s",
        (article_id, revision_id),
    )
    ids = [row[0] for row in cur.fetchall()]
    if not ids:
        return
    marks = ",".join(["%s"] * len(ids))
    values = tuple(ids)
    # Reviews belong to a specific old claim link. Retire them before retiring
    # that link; all three tables are system-versioned, so their audit history
    # is retained but no review is accidentally attached to a replacement link.
    cur.execute(
        f"SELECT id FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks})",
        values + values,
    )
    link_ids = [row[0] for row in cur.fetchall()]
    if link_ids:
        link_marks = ",".join(["%s"] * len(link_ids))
        cur.execute(f"DELETE FROM reviews WHERE claim_link_id IN ({link_marks})", tuple(link_ids))
    cur.execute(f"DELETE FROM claim_embeddings WHERE claim_id IN ({marks})", values)
    cur.execute(
        f"DELETE FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks})",
        values + values,
    )
    cur.execute(f"DELETE FROM claims WHERE id IN ({marks})", values)


def extract_article(args, art: dict) -> tuple:
    """Returns (article, n_claims, error)."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT COUNT(*) FROM claims WHERE article_id = %s
               AND revision_id <=> %s""",
            (art["id"], art["revision_id"]),
        )
        if cur.fetchone()[0] and not args.replace_current:
            return art, 0, "skipped (current revision already has claims)"
        if args.replace_current:
            remove_current_claims(cur, art["id"], art["revision_id"])
        chunks = article_chunks(cur, art["id"])
        if not chunks:
            return art, 0, "no chunks"
        # Every source character is included once. `batch_chars` limits an
        # individual request, not the portion of the article that is audited.
        batches, batch, used = [], [], 0
        for chunk, text in chunks:
            for item in _split_for_request(chunk, text, args.batch_chars):
                text_len = len(item[1])
                if batch and used + text_len > args.batch_chars:
                    batches.append(batch)
                    batch, used = [], 0
                batch.append(item)
                used += text_len
        if batch:
            batches.append(batch)
        good = []
        with httpx.Client(
            timeout=args.timeout,
            headers={"Authorization": f"Bearer {args.api_key}"},
        ) as http:
            for picked in batches:
                claims = call_llm(http, args, art["title"], art["lang"], picked)
                chunk_ids = {ch["chunk_idx"]: ch["id"] for ch, _ in picked}
                good.extend(row for row in (clean(c, chunk_ids) for c in claims) if row)
        if args.dry_run:
            for row in good:
                print(f"  [{art['lang']}] {row[1]:<15} {row[3]!s:<18} {row[5]!s:<12} {row[6]}")
            return art, len(good), None
        for row in good:
            cur.execute(
                """INSERT INTO claims (article_id, chunk_id, kind, subject, predicate,
                       value_text, value_normalized, statement_text, revision_id, model_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
    ap.add_argument("--batch-chars", type=int, default=6000,
                    help="maximum characters per model request; every chunk is processed")
    ap.add_argument("--max-claims", type=int, default=0,
                    help="maximum claims per request (0 = no extractor-side cap)")
    ap.add_argument("--replace-current", action="store_true",
                    help="re-extract selected current revisions; removes their derived links/embeddings")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--dry-run", action="store_true", help="print claims, don't write")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "kimi-k3-1-pro"))
    ap.add_argument("--reasoning-effort", default=os.environ.get("LLM_REASONING_EFFORT", "low"),
                    help="provider reasoning effort; low preserves room for structured output")
    ap.add_argument("--base-url", default=os.environ.get("VERDA_BASE_URL", "https://inference.verda.com/v1"))
    args = ap.parse_args()
    if args.batch_chars <= 0:
        ap.error("--batch-chars must be greater than zero")
    if args.max_claims < 0:
        ap.error("--max-claims cannot be negative")
    if args.replace_current and args.concurrency > 1:
        # Replacing different editions of one QID deletes their shared derived
        # claim_links. Serialise that destructive phase so MariaDB does not
        # abort concurrent transactions with "Record has changed".
        print("--replace-current uses concurrency 1 to preserve shared claim links", file=sys.stderr)
        args.concurrency = 1
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
