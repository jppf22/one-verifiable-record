#!/usr/bin/env python3
"""Import the MariaDB Wikipedia datasets into articles + chunks.

Sources:
  https://github.com/MariaDB/mariadb-wikipedia-datasets
  https://github.com/MariaDB/mariadb-vector-wikipedia-rag  (fi/sv/en starter)

The loader walks --data-dir for ``articles-{lang}.parquet`` files (the format
of mariadb-vector-wikipedia-rag: lang, page_id, rev_id, wikidata_id, title, url,
date_modified, text) and for ``*.jsonl`` files whose records contain at least
``lang`` / ``title`` / ``text`` plus optional ``page_id``, ``qid`` (or
``wikidata_qid``), ``revision_id``, ``section``. If the downloaded dataset
layout differs, adapt ``iter_records`` — it is deliberately the only
format-coupled function in the file.

Embeddings are NOT computed here: run ``jobs/embed.py`` afterwards.

Usage:
  python import_dump.py --data-dir db/load/data --size small
"""
import argparse
import hashlib
import json
import os
import re
import sys

import mariadb

TARGET_CHUNK_CHARS = 1200
SPLIT_RE = re.compile(r"\n\n+")


def connect():
    conn = mariadb.connect(
        host=os.environ.get("DATABASE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DATABASE_PORT", "3306")),
        user=os.environ.get("DATABASE_USER", "ovr"),
        password=os.environ.get("DATABASE_PASSWORD", "ovr_pass"),
        database=os.environ.get("DATABASE_NAME", "wikipedia"),
    )
    conn.autocommit = False
    return conn


def split_chunks(text: str):
    """Split article text into paragraph-bounded chunks of ~TARGET_CHUNK_CHARS."""
    parts, cur, n = [], "", 0
    for para in SPLIT_RE.split(text or ""):
        para = para.strip()
        if not para:
            continue
        if cur and n + len(para) > TARGET_CHUNK_CHARS:
            parts.append(cur.strip())
            cur, n = "", 0
        cur += para + "\n\n"
        n += len(para) + 2
    if cur.strip():
        parts.append(cur.strip())
    return parts or [""]


def replace_article_material(cur, article_id: int):
    """Replace an article safely when a newer dump is imported.

    Claims and verdicts are derived from a specific revision's chunks. Keeping
    them after a chunk replacement would be worse than removing them; the
    decision tables are system-versioned, so their prior state remains auditable.
    
    For chunks: we use upsert (ON DUPLICATE KEY UPDATE) to preserve chunk IDs,
    which maintains foreign key references from claims and embeddings.
    Old chunks that are no longer present in the new import are deleted.
    """
    # Get existing chunk IDs for this article
    cur.execute("SELECT id, chunk_idx FROM chunks WHERE article_id = %s", (article_id,))
    existing_chunks = {row[1]: row[0] for row in cur.fetchall()}
    
    # Delete claims for this article (cascade will handle claim_embeddings via FK)
    # We must do this before deleting chunks to avoid FK violations
    cur.execute("SELECT id FROM claims WHERE article_id = %s", (article_id,))
    claim_ids = [row[0] for row in cur.fetchall()]
    if claim_ids:
        marks = ",".join(["%s"] * len(claim_ids))
        # Detach suggestions from claim links that will be deleted
        cur.execute(
            f"UPDATE suggestions SET claim_link_id = NULL WHERE claim_link_id IN "
            f"(SELECT id FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks}))",
            tuple(claim_ids + claim_ids),
        )
        # Delete reviews (cascades with claim_links)
        cur.execute(
            f"DELETE FROM reviews WHERE claim_link_id IN "
            f"(SELECT id FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks}))",
            tuple(claim_ids + claim_ids),
        )
        # Delete claim_links
        cur.execute(
            f"DELETE FROM claim_links WHERE claim_a IN ({marks}) OR claim_b IN ({marks})",
            tuple(claim_ids + claim_ids),
        )
        # Delete claim_embeddings
        cur.execute(f"DELETE FROM claim_embeddings WHERE claim_id IN ({marks})", tuple(claim_ids))
        # Delete claims
        cur.execute(f"DELETE FROM claims WHERE id IN ({marks})", tuple(claim_ids))
    
    # Mark existing chunks that we will keep (matched by chunk_idx)
    # We'll delete any chunks that are NOT matched after the upsert
    # For now, we delete all old chunk_embeddings for this article's chunks
    if existing_chunks:
        marks = ",".join(["%s"] * len(existing_chunks))
        cur.execute(f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({marks})", tuple(existing_chunks.values()))
    
    # Return the set of existing chunk_idx values so the caller can delete unmatched ones later
    return existing_chunks


PARQUET_RE = re.compile(r"^articles-(fi|sv|en)\.parquet$")


def _from_parquet(rec: dict) -> dict:
    """Map the RAG starter's parquet columns onto the loader's record shape."""
    return {
        "lang": rec.get("lang"),
        "title": rec.get("title"),
        "text": rec.get("text") or "",
        "page_id": rec.get("page_id"),
        "qid": rec.get("wikidata_id"),
        "revision_id": rec.get("rev_id"),
    }


def iter_records(data_dir: str, langs=None):
    seen = set()  # the same parquet file may exist in several cloned repos
    for root, _dirs, files in os.walk(data_dir):
        if "/.git" in root:
            continue
        for fname in sorted(files):
            m = PARQUET_RE.match(fname)
            if m:
                if fname in seen or (langs and m.group(1) not in langs):
                    continue
                seen.add(fname)
                import pyarrow.parquet as pq  # only needed for parquet input
                for batch in pq.ParquetFile(os.path.join(root, fname)).iter_batches(batch_size=500):
                    for rec in batch.to_pylist():
                        yield _from_parquet(rec)
                continue
            if not fname.endswith(".jsonl"):
                continue
            with open(os.path.join(root, fname), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield json.loads(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="db/load/data")
    ap.add_argument("--size", default="small", choices=["small", "medium", "large"],
                    help="dataset preset; used only for progress reporting so far")
    ap.add_argument("--langs", default="fi,sv,en", help="comma-separated languages to import")
    ap.add_argument("--only-shared", action="store_true",
                    help="only import articles whose Wikidata QID exists in 2+ selected languages "
                         "(the merge view needs at least two editions)")
    args = ap.parse_args()
    langs = set(args.langs.split(","))

    shared = None
    if args.only_shared:
        from collections import Counter
        qids = Counter(r["qid"] for r in iter_records(args.data_dir, langs) if r.get("qid"))
        shared = {q for q, n in qids.items() if n >= 2}
        print(f"{len(shared)} QIDs exist in 2+ languages", file=sys.stderr)

    conn = connect()
    cur = conn.cursor()
    n_articles = n_chunks = 0

    for rec in iter_records(args.data_dir, langs):
        lang = rec.get("lang")
        title = rec.get("title")
        if not lang or not title:
            continue
        if shared is not None and rec.get("qid") not in shared:
            continue
        page_id = int(rec.get("page_id") or 0)
        if not page_id:
            # stable pseudo page_id when the dataset lacks one
            digest = hashlib.sha1(f"{lang}:{title}".encode()).hexdigest()
            page_id = int(digest[:12], 16)
        qid = rec.get("qid") or rec.get("wikidata_qid")

        cur.execute(
            """INSERT INTO articles (lang, page_id, title, wikidata_qid, revision_id)
               VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 title = VALUES(title),
                 wikidata_qid = VALUES(wikidata_qid),
                 revision_id = VALUES(revision_id)""",
            (lang, page_id, title, qid, rec.get("revision_id")),
        )
        cur.execute("SELECT id FROM articles WHERE lang = %s AND page_id = %s", (lang, page_id))
        (article_id,) = cur.fetchone()

        # Re-import is revision-safe: invalidate all derived material before
        # changing its source chunks, while keeping system-versioned history.
        # For chunks, we use upsert to preserve chunk IDs for FK references.
        existing_chunks = replace_article_material(cur, article_id)
        
        # Track which chunk_idx values we're processing
        processed_chunk_indices = set()
        for idx, chunk in enumerate(split_chunks(rec.get("text", ""))):
            cur.execute(
                """INSERT INTO chunks (article_id, chunk_idx, section, text, revision_id)
                   VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                     section = VALUES(section),
                     text = VALUES(text),
                     revision_id = VALUES(revision_id)""",
                (article_id, idx, rec.get("section"), chunk, rec.get("revision_id")),
            )
            processed_chunk_indices.add(idx)
            n_chunks += cur.rowcount
        
        # Delete any chunks that were in the old version but not in the new import
        old_chunk_indices = set(existing_chunks.keys())
        deleted_chunk_indices = old_chunk_indices - processed_chunk_indices
        if deleted_chunk_indices:
            # Get the chunk IDs for the deleted indices
            deleted_chunk_ids = [existing_chunks[idx] for idx in deleted_chunk_indices if idx in existing_chunks]
            if deleted_chunk_ids:
                marks = ",".join(["%s"] * len(deleted_chunk_ids))
                # Delete chunk_embeddings first (FK constraint)
                cur.execute(f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({marks})", tuple(deleted_chunk_ids))
                # Then delete the chunks
                cur.execute(f"DELETE FROM chunks WHERE id IN ({marks})", tuple(deleted_chunk_ids))
        
        n_articles += 1

        if n_articles % 500 == 0:
            conn.commit()
            print(f"... {n_articles} articles, {n_chunks} chunks", file=sys.stderr)

    # Materialise current high-confidence counterparts from the imported
    # Wikidata sitelinks. The comparison API reads page_links—not a QID—so this
    # one import-time operation gives all layers a concrete, refreshable page
    # relationship. Other discovery sources can add lower-confidence rows with
    # their own provenance without changing the comparison engine.
    cur.execute(
        """INSERT INTO page_links (page_a, page_b, source, confidence, evidence)
           SELECT a.id, b.id, 'wikidata_sitelink', 1.0,
                  JSON_OBJECT('qid', a.wikidata_qid)
           FROM articles a
           JOIN articles b ON b.wikidata_qid = a.wikidata_qid AND b.id <> a.id
           WHERE a.wikidata_qid IS NOT NULL
           ON DUPLICATE KEY UPDATE source = VALUES(source), confidence = VALUES(confidence),
             evidence = VALUES(evidence), verified_at = CURRENT_TIMESTAMP"""
    )
    n_links = cur.rowcount
    conn.commit()
    print(f"Imported {n_articles} articles, {n_chunks} chunks, {n_links} counterpart links (preset: {args.size}).")
    print("Next step: python jobs/embed.py")


if __name__ == "__main__":
    main()
