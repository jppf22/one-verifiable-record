#!/usr/bin/env python3
"""Embedding job (cold path): multilingual embeddings for chunks and claims.

Default model: intfloat/multilingual-e5-large (1024 dims — matches
VECTOR(1024) in db/schema.sql). If you switch to a model with a different
dimension, update the schema accordingly.

Run on a GPU instance for the large dataset; CPU is fine for small/medium.
Idempotent: only rows without a row in chunk_embeddings / claim_embeddings are processed, so the job can
be interrupted and resumed.

Features:
- Automatic GPU detection and batch size optimization
- Resumable processing with checkpoint tracking
- Progress reporting with ETA
- Configurable per-table batch sizes
- Embedding quality validation

Usage:
  python embed.py [--model intfloat/multilingual-e5-large] [--endpoint URL]
                  [--batch-size 64] [--tables chunks,claims] [--limit 0]
                  [--gpu-batch-size 128] [--cpu-batch-size 32]
                  [--checkpoint-interval 1000] [--validate]
"""
import argparse
import json
import os
import sys
import time

import mariadb

# E5 instruction prefixes: chunks are retrieval passages (asymmetric, matched
# against "query: " questions); claims are matched claim-to-claim (symmetric),
# for which E5 recommends "query: " on both sides.
PREFIX = {"chunks": "passage: ", "claims": "query: "}

# Default batch sizes for different hardware
DEFAULT_GPU_BATCH_SIZE = 128
DEFAULT_CPU_BATCH_SIZE = 32
DEFAULT_ENDPOINT_BATCH_SIZE = 64

# Minimum embedding norm threshold (embeddings should be normalized to unit length)
MIN_EMBEDDING_NORM = 0.95
MAX_EMBEDDING_NORM = 1.05


def connect():
    return mariadb.connect(
        host=os.environ.get("DATABASE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DATABASE_PORT", "3306")),
        user=os.environ.get("DATABASE_USER", "ovr"),
        password=os.environ.get("DATABASE_PASSWORD", "ovr_pass"),
        database=os.environ.get("DATABASE_NAME", "wikipedia"),
    )


def detect_gpu():
    """Detect if running on GPU and return appropriate batch size."""
    try:
        import torch
        if torch.cuda.is_available():
            return True, torch.cuda.get_device_name(0)
    except ImportError:
        pass
    return False, None


def get_optimal_batch_size(gpu_available: bool, args_batch_size: int | None = None,
                           args_gpu_batch: int | None = None,
                           args_cpu_batch: int | None = None) -> int:
    """Determine optimal batch size based on available hardware and args."""
    if args_batch_size:
        return args_batch_size
    if gpu_available:
        return args_gpu_batch or DEFAULT_GPU_BATCH_SIZE
    return args_cpu_batch or DEFAULT_CPU_BATCH_SIZE


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


def compute_embedding_quality(emb) -> float:
    """Compute a quality score for an embedding (0-1).
    
    Factors:
    - Proper normalization (0.5 weight)
    - No NaN/Inf values (0.3 weight)
    - Non-zero vector (0.2 weight)
    """
    if emb is None:
        return 0.0
    
    try:
        score = 0.0
        
        # Check for NaN or Inf
        has_nan_or_inf = False
        for x in emb:
            if x != x or abs(x) == float('inf'):  # NaN or Inf check
                has_nan_or_inf = True
                break
        
        if not has_nan_or_inf:
            score += 0.3
        
        # Check normalization
        norm = sum(x * x for x in emb) ** 0.5
        if MIN_EMBEDDING_NORM <= norm <= MAX_EMBEDDING_NORM:
            score += 0.5
        elif norm > 0:  # Not normalized but not zero
            score += 0.3
        
        # Check non-zero
        if norm > 0:
            score += 0.2
        
        return min(1.0, max(0.0, score))
    except Exception:
        return 0.0


def validate_embedding(emb, text: str) -> tuple[bool, str, float]:
    """Validate an embedding for quality issues.
    
    Returns (is_valid, message, quality_score)
    """
    if emb is None:
        return False, "Null embedding", 0.0
    
    quality = compute_embedding_quality(emb)
    
    try:
        norm = sum(x * x for x in emb) ** 0.5
        if norm < MIN_EMBEDDING_NORM or norm > MAX_EMBEDDING_NORM:
            return False, f"Embedding norm {norm:.4f} outside range [{MIN_EMBEDDING_NORM}, {MAX_EMBEDDING_NORM}]", quality
        
        # Check for NaN or Inf values
        for x in emb:
            if x != x:  # NaN check
                return False, "Embedding contains NaN values", quality
            if abs(x) == float('inf'):
                return False, "Embedding contains infinite values", quality
        
        return True, "OK", quality
    except Exception as e:
        return False, f"Validation error: {e}", quality


def run(model, table: str, batch_size: int, limit: int, model_name: str = "",
        validate: bool = False, checkpoint_interval: int = 1000) -> tuple[int, int, int]:
    """Run embedding for a table.
    
    Returns (total_processed, valid_count, invalid_count)
    """
    conn = connect()
    cur = conn.cursor()
    text_col, emb_table, fk = EMB_TABLES[table]
    total, valid, invalid, started = 0, 0, 0, time.time()
    
    # Get total count for ETA
    cur.execute(f"SELECT COUNT(*) FROM {table} t LEFT JOIN {emb_table} e ON e.{fk} = t.id WHERE e.{fk} IS NULL")
    remaining = cur.fetchone()[0]
    
    last_checkpoint = time.time()
    
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
        
        try:
            embs = model.encode(
                texts, batch_size=batch_size, normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as e:
            print(f"{table}: encoding failed: {e}", file=sys.stderr)
            conn.rollback()
            continue
        
        batch_valid = 0
        batch_invalid = 0
        
        for (row_id, _text), emb in zip(rows, embs):
            quality_score = 1.0  # Default to perfect if not validating
            
            if validate:
                is_valid, msg, quality_score = validate_embedding(emb, _text or "")
                if not is_valid:
                    print(f"{table}: invalid embedding for id {row_id}: {msg}", file=sys.stderr)
                    batch_invalid += 1
                    invalid += 1
                    continue
                batch_valid += 1
                valid += 1
            else:
                # Always compute quality even if not strictly validating
                quality_score = compute_embedding_quality(emb)
            
            try:
                cur.execute(
                    f"INSERT INTO {emb_table} ({fk}, embedding, model, embedding_quality) "
                    f"VALUES (%s, VEC_FromText(%s), %s, %s) "
                    f"ON DUPLICATE KEY UPDATE embedding = VALUES(embedding), "
                    f"model = VALUES(model), embedding_quality = VALUES(embedding_quality)",
                    (row_id, vec_literal(emb), model_name or None, quality_score),
                )
            except Exception as e:
                print(f"{table}: failed to insert embedding for id {row_id}: {e}", file=sys.stderr)
                conn.rollback()
                batch_invalid += 1
                invalid += 1
                continue
        
        try:
            conn.commit()
            total += batch_valid + batch_invalid
            remaining -= batch_valid + batch_invalid
        except Exception as e:
            print(f"{table}: commit failed: {e}", file=sys.stderr)
            conn.rollback()
        
        elapsed = time.time() - started
        rate = total / max(elapsed, 1e-6)
        
        # Progress reporting
        if elapsed > 0 and rate > 0:
            eta = remaining / rate if remaining > 0 else 0
            eta_str = f"{eta:.0f}s" if eta < 3600 else f"{eta/3600:.1f}h"
            print(f"{table}: {total}/{total + remaining} embedded ({rate:.1f}/s, ETA: {eta_str}) "
                  f"[valid: {valid}, invalid: {invalid}]", file=sys.stderr)
        else:
            print(f"{table}: embedded {total} rows ({rate:.0f} rows/s) "
                  f"[valid: {valid}, invalid: {invalid}]", file=sys.stderr)
        
        # Checkpoint commit
        if checkpoint_interval and total >= checkpoint_interval:
            conn.commit()
            last_checkpoint = time.time()
        
        if limit and total >= limit:
            break
    
    return total, valid, invalid


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
    ap.add_argument("--batch-size", type=int, default=None,
                    help="batch size (default: auto based on hardware)")
    ap.add_argument("--gpu-batch-size", type=int, default=DEFAULT_GPU_BATCH_SIZE,
                    help=f"batch size when GPU detected (default: {DEFAULT_GPU_BATCH_SIZE})")
    ap.add_argument("--cpu-batch-size", type=int, default=DEFAULT_CPU_BATCH_SIZE,
                    help=f"batch size when on CPU (default: {DEFAULT_CPU_BATCH_SIZE})")
    ap.add_argument("--endpoint", default=os.environ.get("EMBED_BASE_URL", ""),
                    help="use the embedder service (e.g. http://embedder:8001/v1) instead of "
                         "loading the model in-process")
    ap.add_argument("--endpoint-batch-size", type=int, default=DEFAULT_ENDPOINT_BATCH_SIZE,
                    help=f"batch size for endpoint mode (default: {DEFAULT_ENDPOINT_BATCH_SIZE})")
    ap.add_argument("--tables", default="chunks,claims", help="comma-separated subset of chunks,claims")
    ap.add_argument("--limit", type=int, default=0, help="stop after N rows per table (0 = no limit)")
    ap.add_argument("--validate", action="store_true",
                    help="validate embeddings for NaN/Inf and proper normalization")
    ap.add_argument("--checkpoint-interval", type=int, default=1000,
                    help="commit checkpoint every N rows (0 to disable)")
    args = ap.parse_args()

    # Detect GPU and determine batch size
    gpu_available, gpu_name = detect_gpu()
    
    if args.endpoint:
        # Use endpoint-specific batch size
        batch_size = args.endpoint_batch_size if args.batch_size is None else args.batch_size
        print(f"Using embedder service: {args.endpoint} (batch_size={batch_size})", file=sys.stderr)
        model = HttpEncoder(args.endpoint, args.model)
    else:
        # Determine batch size based on hardware
        if args.batch_size:
            batch_size = args.batch_size
        elif gpu_available:
            batch_size = args.gpu_batch_size
            print(f"Detected GPU: {gpu_name}, using batch_size={batch_size}", file=sys.stderr)
        else:
            batch_size = args.cpu_batch_size
            print(f"Running on CPU, using batch_size={batch_size}", file=sys.stderr)
        
        from sentence_transformers import SentenceTransformer
        print(f"Loading model: {args.model}", file=sys.stderr)
        model = SentenceTransformer(args.model)

    print(f"Batch size: {batch_size}", file=sys.stderr)
    if args.validate:
        print("Embedding validation enabled", file=sys.stderr)
    
    for table in [t.strip() for t in args.tables.split(",") if t.strip()]:
        if table not in ("chunks", "claims"):
            sys.exit(f"unknown table: {table}")
        
        start_time = time.time()
        total, valid, invalid = run(
            model, table, batch_size, args.limit, args.model,
            validate=args.validate, checkpoint_interval=args.checkpoint_interval
        )
        elapsed = time.time() - start_time
        
        print(f"done: {table} ({total} rows, {elapsed:.1f}s, "
              f"{total/elapsed:.1f} rows/s, "
              f"valid: {valid}, invalid: {invalid})", file=sys.stderr)
        
        ensure_vector_index(table)


if __name__ == "__main__":
    main()
