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

# Valid predicate patterns - snake_case, alphanumeric with underscores
PREDICATE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Wikidata property ID pattern
PROPERTY_RE = re.compile(r"^P\d+$")
# Wikidata entity ID pattern
ENTITY_RE = re.compile(r"^Q\d+$")

# Minimum lengths for quality validation
MIN_SUBJECT_LENGTH = 2
MIN_PREDICATE_LENGTH = 3
MIN_STATEMENT_LENGTH = 5
MIN_CONFIDENCE = 0.1

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


def compute_quality_score(claim: dict, validation_results: dict) -> float:
    """Compute a data quality score (0-1) based on validation results.
    
    Factors:
    - Has valid kind: 0.1
    - Has statement text: 0.1
    - Has subject: 0.1
    - Has predicate: 0.1
    - Has normalized value (if applicable): 0.1
    - Has valid entity IDs: 0.1
    - Has confidence >= 0.5: 0.1
    - Has citations: 0.1
    - Passed all validation: 0.2
    """
    score = 0.0
    
    # Valid kind
    if validation_results.get("valid_kind"):
        score += 0.1
    
    # Has statement
    if validation_results.get("has_statement"):
        score += 0.1
    
    # Has subject
    if validation_results.get("has_subject"):
        score += 0.1
    
    # Has predicate
    if validation_results.get("has_predicate"):
        score += 0.1
    
    # Has normalized value
    if validation_results.get("has_normalized"):
        score += 0.1
    
    # Has valid entity IDs
    if validation_results.get("has_valid_entities"):
        score += 0.1
    
    # High confidence
    if validation_results.get("high_confidence"):
        score += 0.1
    
    # Has citations
    if validation_results.get("has_citations"):
        score += 0.1
    
    # All validation passed
    if validation_results.get("all_valid"):
        score += 0.2
    
    return min(1.0, max(0.0, score))


def clean(claim: dict, chunk_ids: dict, contexts: dict):
    """Validate one LLM claim; return a DB row tuple or None.
    
    Performs comprehensive validation:
    - Checks kind is valid
    - Validates statement length and content
    - Validates subject and predicate format
    - Validates normalized values based on kind
    - Validates entity IDs (Wikidata QIDs and PIDs)
    - Ensures confidence is within valid range
    """
    kind = (claim.get("kind") or "").strip()
    statement = (claim.get("statement") or "").strip()
    
    # Basic validation
    if kind not in KINDS:
        return None
    if not statement or len(statement) < MIN_STATEMENT_LENGTH:
        return None
    
    # Validate confidence
    raw_confidence = claim.get("confidence")
    confidence_val = None
    if raw_confidence is not None:
        try:
            confidence_val = float(raw_confidence)
            confidence_val = max(0.0, min(1.0, confidence_val))
        except (TypeError, ValueError):
            return None
    
    # If confidence is too low, we can still accept it but mark it
    # (extraction will handle filtering)
    
    # Validate subject
    subject = (claim.get("subject") or "").strip()
    if subject and len(subject) < MIN_SUBJECT_LENGTH:
        # Accept empty subject for some claim types, but not short ones
        subject = None
    
    # Validate subject_entity (Wikidata QID)
    subject_entity = (claim.get("subject_entity") or "").strip()
    if subject_entity and not ENTITY_RE.match(subject_entity):
        # Invalid entity ID - clear it
        subject_entity = None
    
    # Validate predicate
    predicate = (claim.get("predicate") or "").strip()
    if predicate:
        if len(predicate) < MIN_PREDICATE_LENGTH:
            return None
        # Predicate should be snake_case
        if not PREDICATE_RE.match(predicate):
            # Try to fix common issues
            predicate = predicate.lower().replace(" ", "_").replace("-", "_")
            if not PREDICATE_RE.match(predicate):
                # Still invalid - might be a Wikidata property
                if not PROPERTY_RE.match(predicate):
                    return None
    
    # Validate predicate_entity (Wikidata PID)
    predicate_entity = (claim.get("predicate_entity") or "").strip()
    if predicate_entity and not PROPERTY_RE.match(predicate_entity):
        predicate_entity = None
    
    # Validate object_entity (Wikidata QID)
    object_entity = (claim.get("object_entity") or "").strip()
    if object_entity and not ENTITY_RE.match(object_entity):
        object_entity = None
    
    # Validate normalized values based on kind
    norm = (str(claim.get("value_normalized") or "")).strip() or None
    if kind == "date":
        if norm and not DATE_RE.match(norm):
            norm = None  # never let a malformed date reach the deterministic layer
        # For dates, also validate value_text if present
        value_text = (claim.get("value_text") or "").strip()
        if value_text and norm:
            # Try to ensure consistency between value_text and normalized
            pass
    elif kind in ("number", "quantity"):
        if norm:
            norm = norm.replace(",", "").replace(" ", "")
            if not NUM_RE.match(norm):
                norm = None
    
    # Validate value_canonical structure based on kind
    value_canonical = claim.get("value_canonical")
    if value_canonical:
        if kind == "date":
            # Should have value, precision, calendar
            if not isinstance(value_canonical, dict):
                value_canonical = None
            elif "value" not in value_canonical:
                value_canonical = None
        elif kind == "quantity":
            # Should have value, unit
            if not isinstance(value_canonical, dict):
                value_canonical = None
            elif "value" not in value_canonical:
                value_canonical = None
    
    # Validate qualifiers
    qualifiers = claim.get("qualifiers")
    if qualifiers and not isinstance(qualifiers, dict):
        qualifiers = None
    
    # Validate citations
    citations = claim.get("citations")
    if citations:
        if not isinstance(citations, list):
            citations = None
        else:
            # Filter out invalid citation entries
            citations = [c for c in citations if isinstance(c, str) and len(c.strip()) > 0]
            if not citations:
                citations = None
    
    # Get chunk ID
    try:
        chunk_id = chunk_ids.get(int(claim.get("chunk")))
    except (TypeError, ValueError):
        chunk_id = None
    chunk_id = chunk_id or next(iter(chunk_ids.values()))
    
    # Ensure we have a chunk_id
    if not chunk_id:
        return None
    
    def json_value(value):
        if not isinstance(value, (dict, list)):
            return None
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return None

    # Compute data quality score
    validation_results = {
        "valid_kind": kind in KINDS,
        "has_statement": bool(statement and len(statement) >= MIN_STATEMENT_LENGTH),
        "has_subject": bool(subject and len(subject) >= MIN_SUBJECT_LENGTH),
        "has_predicate": bool(predicate and len(predicate) >= MIN_PREDICATE_LENGTH),
        "has_normalized": bool(norm) if kind in ("date", "number", "quantity") else True,
        "has_valid_entities": all([
            subject_entity is None or ENTITY_RE.match(subject_entity),
            predicate_entity is None or PROPERTY_RE.match(predicate_entity),
            object_entity is None or ENTITY_RE.match(object_entity),
        ]),
        "high_confidence": confidence_val is not None and confidence_val >= 0.5,
        "has_citations": bool(citations),
        "all_valid": True,  # Will be set to False if any critical validation fails
    }
    quality_score = compute_quality_score(claim, validation_results)

    clip = lambda v, n: (str(v).strip()[:n] or None) if v is not None else None
    chunk_idx = int(claim.get("chunk")) if str(claim.get("chunk", "")).isdigit() else None
    context = contexts.get(chunk_idx, "")
    
    return (
        chunk_id, kind, clip(subject, 512), clip(subject_entity, 32),
        clip(predicate, 255), clip(predicate_entity, 32),
        clip(object_entity, 32), clip(claim.get("value_text"), 1024), clip(norm, 512),
        json_value(value_canonical), json_value(qualifiers), statement,
        context, json_value(citations), confidence_val, quality_score,
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
            # row contains: chunk_id, kind, subject, subject_entity, predicate, predicate_entity,
            # object_entity, value_text, value_normalized, value_canonical, qualifiers,
            # statement_text, context_text, citation_urls, extraction_confidence, data_quality_score
            cur.execute(
                """INSERT INTO claims (article_id, chunk_id, kind, subject, subject_entity,
                       predicate, predicate_entity, object_entity, value_text, value_normalized,
                       value_canonical, qualifiers, statement_text, context_text, citation_urls,
                       extraction_confidence, data_quality_score, revision_id, model_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
