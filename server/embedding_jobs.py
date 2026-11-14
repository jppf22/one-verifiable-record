"""QID-scoped embedding jobs dispatched to the configured embedding worker."""
import math

import httpx

from config import settings
from db import get_conn, query

BATCH_SIZE = 64
EMBEDDING_DIMENSIONS = 1024


def _vec_literal(vector) -> str:
    return "[" + ",".join(f"{float(x):.7f}" for x in vector) + "]"


def _worker_headers() -> dict:
    return {"Authorization": f"Bearer {settings.gpu_embed_token}"} if settings.gpu_embed_token else {}


def _encode(texts: list[str], input_type: str) -> list[list[float]]:
    if not settings.gpu_embed_base_url:
        raise RuntimeError("GPU embedding worker is not configured")
    response = httpx.post(
        f"{settings.gpu_embed_base_url.rstrip('/')}/embeddings",
        headers=_worker_headers(),
        json={"model": settings.embed_model, "input": texts, "input_type": input_type},
        timeout=300,
    )
    response.raise_for_status()
    try:
        data = response.json().get("data", [])
    except ValueError as exc:
        raise RuntimeError("embedding worker returned invalid JSON") from exc
    if not isinstance(data, list) or len(data) != len(texts):
        raise RuntimeError("embedding worker returned an incomplete batch")
    try:
        data = sorted(data, key=lambda item: item["index"])
    except (KeyError, TypeError) as exc:
        raise RuntimeError("embedding worker returned malformed embedding metadata") from exc
    if [item["index"] for item in data] != list(range(len(texts))):
        raise RuntimeError("embedding worker returned invalid embedding indexes")
    vectors = [item.get("embedding") for item in data]
    if any(
        not isinstance(vector, list)
        or len(vector) != EMBEDDING_DIMENSIONS
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector)
        for vector in vectors
    ):
        raise RuntimeError("embedding worker returned invalid embedding vectors")
    return vectors


def _pending(qid: str, table: str) -> list[dict]:
    if table == "claims":
        return query(
            """SELECT c.id, c.statement_text AS text FROM claims c
               JOIN articles a ON a.id = c.article_id
               LEFT JOIN claim_embeddings e ON e.claim_id = c.id
               WHERE a.wikidata_qid = %s AND c.revision_id <=> a.revision_id
                 AND e.claim_id IS NULL ORDER BY c.id""",
            (qid,),
        )
    if table == "chunks":
        return query(
            """SELECT ch.id, ch.text FROM chunks ch
               JOIN articles a ON a.id = ch.article_id
               LEFT JOIN chunk_embeddings e ON e.chunk_id = ch.id
               WHERE a.wikidata_qid = %s AND ch.revision_id <=> a.revision_id
                 AND e.chunk_id IS NULL ORDER BY ch.id""",
            (qid,),
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


def embed_qid(qid: str, tables=("claims", "chunks")) -> dict:
    """Embed only current, missing rows for one entity through the GPU worker."""
    result = {"qid": qid, "worker": settings.gpu_embed_base_url, "embedded": {}}
    for table in tables:
        rows = _pending(qid, table)
        for offset in range(0, len(rows), BATCH_SIZE):
            batch = rows[offset: offset + BATCH_SIZE]
            _store(table, batch, _encode([row["text"] or "" for row in batch], "query" if table == "claims" else "passage"))
        result["embedded"][table] = len(rows)
    return result
