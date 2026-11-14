"""Local embedding service: OpenAI-style /v1/embeddings over multilingual-e5-large.

Verda's inference gateway has no embeddings endpoint, so we serve the model
ourselves. The API server (query-time Q&A) and jobs/embed.py both call this.
Point EMBED_BASE_URL at a GPU-hosted copy for bulk runs.

E5 needs instruction prefixes. Pass "input_type": "query" (default) or
"passage". Inputs that already start with "query: " / "passage: " are kept as-is.
"""
import os
import secrets
from typing import List, Literal, Optional, Union

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large")
EMBED_API_TOKEN = os.environ.get("EMBED_API_TOKEN", "")
model = SentenceTransformer(MODEL_NAME)
app = FastAPI(title="OVR embedder")


def _authorize(authorization: str | None):
    """Require a bearer token when this service is hosted off the app VM."""
    if not EMBED_API_TOKEN:
        return
    supplied = (authorization or "").removeprefix("Bearer ")
    if not secrets.compare_digest(supplied, EMBED_API_TOKEN):
        raise HTTPException(401, "invalid embedding worker token")


class EmbedRequest(BaseModel):
    input: Union[str, List[str]]
    model: Optional[str] = None
    input_type: Literal["query", "passage"] = "query"


def _prefixed(text: str, kind: str) -> str:
    return text if text.startswith(("query: ", "passage: ")) else f"{kind}: {text}"


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "dims": model.get_sentence_embedding_dimension()}


@app.post("/v1/embeddings")
def embeddings(req: EmbedRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    texts = [req.input] if isinstance(req.input, str) else req.input
    vecs = model.encode(
        [_prefixed(t or "", req.input_type) for t in texts],
        batch_size=32, normalize_embeddings=True, show_progress_bar=False,
    )
    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [{"object": "embedding", "index": i, "embedding": v.tolist()} for i, v in enumerate(vecs)],
    }
