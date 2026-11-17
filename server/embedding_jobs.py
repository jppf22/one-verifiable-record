"""Page-scoped embedding jobs dispatched to the configured embeddings endpoint.

The endpoint is any OpenAI-compatible ``/v1/embeddings`` service: by default the
local ``embedder`` container from docker-compose (CPU, or a GPU-hosted copy via
the ``gpu`` compose profile), but EMBED_BASE_URL/EMBED_API_KEY also accept a
hosted API. Vectors are written through this server's own MariaDB connection,
so the database is never exposed to the embedding worker.
"""
import math

import httpx

from config import settings
from db import get_conn, query

BATCH_SIZE = 64
# Must match VECTOR(1024) in db/schema.sql; a different EMBED_MODEL needs a
# schema change, and the check below turns a mismatch into a clear error.
EMBEDDING_DIMENSIONS = 1024


def _vec_literal(vector) -> str:
    return "[" + ",".join(f"{float(x):.7f}" for x in vector) + "]"


def _worker_headers() -> dict:
    return {"Authorization": f"Bearer {settings.embed_api_key}"} if settings.embed_api_key else {}


def _encode(texts: list[str], input_type: str) -> list[list[float]]:
    if not settings.embed_base_url:
        raise RuntimeError("no embeddings endpoint configured (set EMBED_BASE_URL)")
    response = httpx.post(
        f"{settings.embed_base_url.rstrip('/')}/embeddings",
        headers=_worker_headers(),
        json={"model": settings.embed_model, "input": texts, "input_type": input_type},
        timeout=300,
    )
    response.raise_for_status()
    try:
        data = response.json().get("data", [])
    except ValueError as exc:
        raise RuntimeError("embeddings endpoint returned invalid JSON") from exc
    if not isinstance(data, list) or len(data) != len(texts):
        raise RuntimeError("embeddings endpoint returned an incomplete batch")
    try:
        data = sorted(data, key=lambda item: item["index"])
    except (KeyError, TypeError) as exc:
        raise RuntimeError("embeddings endpoint returned malformed embedding metadata") from exc
    if [item["index"] for item in data] != list(range(len(texts))):
        raise RuntimeError("embeddings endpoint returned invalid embedding indexes")
    vectors = [item.get("embedding") for item in data]
    if any(
        not isinstance(vector, list)
        or len(vector) != EMBEDDING_DIMENSIONS
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
        for vector in vectors
    ):
        raise RuntimeError(
            f"embeddings endpoint returned invalid vectors (expected {EMBEDDING_DIMENSIONS} dims; "
            "EMBED_MODEL must match the VECTOR column in db/schema.sql)"
        )
    return vectors


def _pending(page_id: int, table: str) -> list[dict]:
    if table == "claims":
        return query(
            """SELECT c.id, c.statement_text AS text FROM claims c
               JOIN articles a ON a.id = c.article_id
               LEFT JOIN claim_embeddings e ON e.claim_id = c.id
               WHERE c.article_id = %s AND c.revision_id <=> a.revision_id
                 AND e.claim_id IS NULL ORDER BY c.id""",
            (page_id,),
        )
    if table == "chunks":
        return query(
            """SELECT ch.id, ch.text FROM chunks ch
               JOIN articles a ON a.id = ch.article_id
               LEFT JOIN chunk_embeddings e ON e.chunk_id = ch.id
               WHERE ch.article_id = %s AND ch.revision_id <=> a.revision_id
                 AND e.chunk_id IS NULL ORDER BY ch.id""",
            (page_id,),
        )
    raise ValueError(f"unknown embedding table: {table}")


def _store(table: str, rows: list[dict], vectors: list[list[float]]) -> None:
    destination, fk = ("claim_embeddings", "claim_id") if table == "claims" else ("chunk_embeddings", "chunk_id")
    with get_conn() as conn:
        cur = conn.cursor()
        for row, vector in zip(rows, vectors):
            cur.execute(
                f"""INSERT INTO {destination} ({fk}, embedding, model)
                    VALUES (%s, VEC_FromText(%s), %s)
                    ON DUPLICATE KEY UPDATE embedding = VALUES(embedding), model = VALUES(model)""",
                (row["id"], _vec_literal(vector), settings.embed_model),
            )
        conn.commit()


def embed_page(page_id: int, tables=("claims", "chunks")) -> dict:
    """Embed only the page's current, missing rows via the embeddings endpoint."""
    result = {"page_id": page_id, "endpoint": settings.embed_base_url, "embedded": {}}
    for table in tables:
        rows = _pending(page_id, table)
        for offset in range(0, len(rows), BATCH_SIZE):
            batch = rows[offset: offset + BATCH_SIZE]
            _store(table, batch, _encode([row["text"] or "" for row in batch], "query" if table == "claims" else "passage"))
        result["embedded"][table] = len(rows)
    return result
