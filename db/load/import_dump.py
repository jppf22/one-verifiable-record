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

Refresh behaviour is append-only. When an imported article's text changes, its
new chunks are inserted rather than deleting the old chunks that existing
claims and embeddings reference. Queries only read chunks/claims whose
revision matches ``articles.revision_id``; old material remains available as
provenance. Run claim extraction and claim embedding again after a refresh.

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


def _split_long_paragraph(paragraph: str):
    """Yield bounded pieces without dropping text when one paragraph is huge."""
    while len(paragraph) > TARGET_CHUNK_CHARS:
        # Prefer a natural boundary, but do not let an unbroken URL/table make
        # the source chunk arbitrarily large.
        cut = paragraph.rfind(" ", 0, TARGET_CHUNK_CHARS + 1)
        if cut < TARGET_CHUNK_CHARS // 2:
            cut = TARGET_CHUNK_CHARS
        yield paragraph[:cut].strip()
        paragraph = paragraph[cut:].lstrip()
    if paragraph:
        yield paragraph


def split_chunks(text: str):
    """Split article text into paragraph-bounded chunks of ~TARGET_CHUNK_CHARS.

    A long paragraph is further split at whitespace.  Without that fallback a
    single malformed or list-heavy paragraph can become a multi-megabyte model
    input despite the nominal chunk target.
    """
    parts, cur, n = [], "", 0
    for para in SPLIT_RE.split(text or ""):
        para = para.strip()
        if not para:
            continue
        for piece in _split_long_paragraph(para):
            if cur and n + len(piece) > TARGET_CHUNK_CHARS:
                parts.append(cur.strip())
                cur, n = "", 0
            cur += piece + "\n\n"
            n += len(piece) + 2
    if cur.strip():
        parts.append(cur.strip())
    return parts or [""]


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
        text = rec.get("text", "")
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # Decide whether a new source revision needs chunks *before* updating the
        # article row.  Deleting old chunks is unsafe once claims/embeddings hold
        # foreign keys to them; retaining them is also what makes their receipts
        # reproducible after a later import.
        cur.execute(
            "SELECT id, revision_id, source_hash FROM articles WHERE lang = %s AND page_id = %s",
            (lang, page_id),
        )
        existing = cur.fetchone()
        # A new revision id is meaningful even when the text happens to be
        # identical: receipts must still point to the revision currently
        # advertised by the article row. Hash comparison covers sources that
        # do not provide a revision id.
        changed = (
            not existing
            or existing[1] != rec.get("revision_id")
            or existing[2] != source_hash
        )

        cur.execute(
            """INSERT INTO articles (lang, page_id, title, wikidata_qid, revision_id, source_hash)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 title = VALUES(title),
                 wikidata_qid = VALUES(wikidata_qid),
                 revision_id = VALUES(revision_id),
                 source_hash = VALUES(source_hash),
                 fetched_at = CURRENT_TIMESTAMP""",
            (lang, page_id, title, qid, rec.get("revision_id"), source_hash),
        )
        cur.execute("SELECT id FROM articles WHERE lang = %s AND page_id = %s", (lang, page_id))
        (article_id,) = cur.fetchone()

        if changed:
            for idx, chunk in enumerate(split_chunks(text)):
                cur.execute(
                    """INSERT INTO chunks (article_id, chunk_idx, section, text, revision_id)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (article_id, idx, rec.get("section"), chunk, rec.get("revision_id")),
                )
                n_chunks += 1
        n_articles += 1

        if n_articles % 500 == 0:
            conn.commit()
            print(f"... {n_articles} articles, {n_chunks} chunks", file=sys.stderr)

    conn.commit()
    print(f"Imported {n_articles} articles, {n_chunks} chunks (preset: {args.size}).")
    print("Next step: python jobs/embed.py --tables chunks, then python jobs/extract_claims.py, then python jobs/embed.py --tables claims")


if __name__ == "__main__":
    main()
