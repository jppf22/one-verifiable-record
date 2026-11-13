#!/usr/bin/env python3
"""Embedding job (cold path): multilingual embeddings for chunks and claims.

Default model: intfloat/multilingual-e5-large (1024 dims — matches
VECTOR(1024) in db/schema.sql). If you switch to a model with a different
dimension, update the schema accordingly.

Run on a GPU instance for the large dataset; CPU is fine for small/medium.
Idempotent: only rows whose embedding is NULL are processed, so the job can
be interrupted and resumed.

Usage:
  python embed.py [--model intfloat/multilingual-e5-large]
                  [--batch-size 64] [--tables chunks,claims] [--limit 0]
"""
import argparse
import os
import sys
import time

import mariadb
from sentence_transformers import SentenceTransformer

PASSAGE_PREFIX = "passage: "  # E5 instruction prefix for indexed text


def connect():
    return mariadb.connect(
        host=os.environ.get("DATABASE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DATABASE_PORT", "3306")),
        user=os.environ.get("DATABASE_USER", "ovr"),
        password=os.environ.get("DATABASE_PASSWORD", "ovr_pass"),
        database=os.environ.get("DATABASE_NAME", "wikipedia"),
    )


def vec_literal(vec) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def run(model: SentenceTransformer, table: str, batch_size: int, limit: int):
    conn = connect()
    cur = conn.cursor()
    text_col = "text" if table == "chunks" else "statement_text"
    total, started = 0, time.time()
    while True:
        cur.execute(
            f"SELECT id, {text_col} FROM {table} WHERE embedding IS NULL "
            f"ORDER BY id LIMIT %s",
            (batch_size,),
        )
        rows = cur.fetchall()
        if not rows:
            break
        texts = [PASSAGE_PREFIX + (r[1] or "") for r in rows]
        embs = model.encode(
            texts, batch_size=batch_size, normalize_embeddings=True,
            show_progress_bar=False,
        )
        for (row_id, _text), emb in zip(rows, embs):
            cur.execute(
                f"UPDATE {table} SET embedding = VEC_FromText(%s) WHERE id = %s",
                (vec_literal(emb), row_id),
            )
        conn.commit()
        total += len(rows)
        rate = total / max(time.time() - started, 1e-6)
        print(f"{table}: embedded {total} rows ({rate:.0f} rows/s)", file=sys.stderr)
        if limit and total >= limit:
            break
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large"))
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--tables", default="chunks,claims", help="comma-separated subset of chunks,claims")
    ap.add_argument("--limit", type=int, default=0, help="stop after N rows per table (0 = no limit)")
    args = ap.parse_args()

    print(f"Loading model: {args.model}", file=sys.stderr)
    model = SentenceTransformer(args.model)

    for table in [t.strip() for t in args.tables.split(",") if t.strip()]:
        if table not in ("chunks", "claims"):
            sys.exit(f"unknown table: {table}")
        total = run(model, table, args.batch_size, args.limit)
        print(f"done: {table} ({total} rows)")


if __name__ == "__main__":
    main()
