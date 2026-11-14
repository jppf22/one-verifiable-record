#!/usr/bin/env python3
"""Embedding job (cold path): multilingual embeddings for chunks and claims.

Default model: intfloat/multilingual-e5-large (1024 dims — matches
VECTOR(1024) in db/schema.sql). If you switch to a model with a different
dimension, update the schema accordingly.

Run on a GPU instance for the large dataset; CPU is fine for small/medium.
Idempotent: only rows without a row in chunk_embeddings / claim_embeddings are processed, so the job can
be interrupted and resumed.

Usage:
  python embed.py [--model intfloat/multilingual-e5-large] [--endpoint URL]
                  [--batch-size 64] [--tables chunks,claims] [--limit 0]
"""
import argparse
import os
import sys
import time

import mariadb

# E5 instruction prefixes: chunks are retrieval passages (asymmetric, matched
# against "query: " questions); claims are matched claim-to-claim (symmetric),
# for which E5 recommends "query: " on both sides.
PREFIX = {"chunks": "passage: ", "claims": "query: "}


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


EMB_TABLES = {
    # source table -> (text column, embedding table, foreign-key column)
    "chunks": ("text", "chunk_embeddings", "chunk_id"),
    "claims": ("statement_text", "claim_embeddings", "claim_id"),
}


class HttpEncoder:
    """Same encode() shape as SentenceTransformer, backed by embedder/ (/v1/embeddings)."""

    def __init__(self, base_url: str, model: str):
        import httpx  # only needed in endpoint mode
        self.http, self.url, self.model = httpx.Client(timeout=300), base_url.rstrip("/"), model

    def encode(self, texts, **_kw):
        # prefixes are already applied by run(), so the service keeps them as-is
        r = self.http.post(f"{self.url}/embeddings", json={"model": self.model, "input": texts})
        r.raise_for_status()
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]


def run(model, table: str, batch_size: int, limit: int, model_name: str = ""):
    conn = connect()
    cur = conn.cursor()
    text_col, emb_table, fk = EMB_TABLES[table]
    total, started = 0, time.time()
    while True:
        cur.execute(
            f"SELECT t.id, t.{text_col} FROM {table} t "
            f"LEFT JOIN {emb_table} e ON e.{fk} = t.id "
            f"WHERE e.{fk} IS NULL ORDER BY t.id LIMIT %s",
            (batch_size,),
        )
        rows = cur.fetchall()
        if not rows:
            break
        texts = [PREFIX[table] + (r[1] or "") for r in rows]
        embs = model.encode(
            texts, batch_size=batch_size, normalize_embeddings=True,
            show_progress_bar=False,
        )
        for (row_id, _text), emb in zip(rows, embs):
            cur.execute(
                f"INSERT INTO {emb_table} ({fk}, embedding, model) "
                f"VALUES (%s, VEC_FromText(%s), %s) "
                f"ON DUPLICATE KEY UPDATE embedding = VALUES(embedding), model = VALUES(model)",
                (row_id, vec_literal(emb), model_name or None),
            )
        conn.commit()
        total += len(rows)
        rate = total / max(time.time() - started, 1e-6)
        print(f"{table}: embedded {total} rows ({rate:.0f} rows/s)", file=sys.stderr)
        if limit and total >= limit:
            break
    return total


def ensure_vector_index(table: str):
    """Add the HNSW vector index once every row is embedded.

    MariaDB 11.8 only allows a VECTOR INDEX on a NOT NULL column, so the
    column is flipped to NOT NULL first (embeddings start as NULL, which is
    what makes this job resumable). DISTANCE=cosine matches the
    VEC_DISTANCE_COSINE queries in server/.
    """
    conn = connect()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*), SUM(embedding IS NULL) FROM {table}")
    total, missing = cur.fetchone()
    if not total or missing:
        conn.close()
        return
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_TYPE = 'VECTOR'",
        (table,),
    )
    if cur.fetchone()[0]:
        conn.close()
        return
    # ALTER on a system-versioned table (claims) needs this session flag
    cur.execute("SET SESSION system_versioning_alter_history = 'KEEP'")
    cur.execute(f"ALTER TABLE {table} MODIFY embedding VECTOR(1024) NOT NULL")
    cur.execute(
        f"CREATE VECTOR INDEX vidx_{table} ON {table} (embedding) M=8 DISTANCE=cosine"
    )
    conn.commit()
    conn.close()
    print(f"{table}: HNSW vector index vidx_{table} created", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large"))
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--tables", default="chunks,claims", help="comma-separated subset of chunks,claims")
    ap.add_argument("--limit", type=int, default=0, help="stop after N rows per table (0 = no limit)")
    ap.add_argument("--endpoint", default=os.environ.get("EMBED_BASE_URL", ""),
                    help="use the embedder service (e.g. http://embedder:8001/v1) instead of "
                         "loading the model in-process")
    args = ap.parse_args()

    if args.endpoint:
        print(f"Using embedder service: {args.endpoint}", file=sys.stderr)
        model = HttpEncoder(args.endpoint, args.model)
    else:
        from sentence_transformers import SentenceTransformer
        print(f"Loading model: {args.model}", file=sys.stderr)
        model = SentenceTransformer(args.model)

    for table in [t.strip() for t in args.tables.split(",") if t.strip()]:
        if table not in ("chunks", "claims"):
            sys.exit(f"unknown table: {table}")
        total = run(model, table, args.batch_size, args.limit, args.model)
        print(f"done: {table} ({total} rows)")
        ensure_vector_index(table)


if __name__ == "__main__":
    main()
