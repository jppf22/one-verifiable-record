#!/usr/bin/env python3
"""Import the MariaDB Wikipedia datasets into articles + chunks.

Sources:
  https://github.com/MariaDB/mariadb-wikipedia-datasets
  https://github.com/MariaDB/mariadb-vector-wikipedia-rag  (fi/sv/en starter)

The loader walks --data-dir for ``*.jsonl`` files whose records contain at
least ``lang`` / ``title`` / ``text`` plus optional ``page_id``, ``qid`` (or
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


def iter_records(data_dir: str):
    for root, _dirs, files in os.walk(data_dir):
        for fname in sorted(files):
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
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    n_articles = n_chunks = 0

    for rec in iter_records(args.data_dir):
        lang = rec.get("lang")
        title = rec.get("title")
        if not lang or not title:
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

        # re-import is idempotent: replace the article's chunks
        cur.execute("DELETE FROM chunks WHERE article_id = %s", (article_id,))
        for idx, chunk in enumerate(split_chunks(rec.get("text", ""))):
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
    print("Next step: python jobs/embed.py")


if __name__ == "__main__":
    main()
