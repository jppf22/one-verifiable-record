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
REQUIRE_EMBED_API_TOKEN = os.environ.get("REQUIRE_EMBED_API_TOKEN", "").lower() in {"1", "true", "yes"}
MAX_INPUTS = int(os.environ.get("EMBED_MAX_INPUTS", "64"))
MAX_INPUT_CHARS = int(os.environ.get("EMBED_MAX_INPUT_CHARS", "24000"))
if MAX_INPUTS < 1 or MAX_INPUT_CHARS < 1:
    raise RuntimeError("EMBED_MAX_INPUTS and EMBED_MAX_INPUT_CHARS must be positive")
if REQUIRE_EMBED_API_TOKEN and not EMBED_API_TOKEN:
    raise RuntimeError("EMBED_API_TOKEN is required for this embedding worker")
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
    if not texts:
        raise HTTPException(422, "input must contain at least one text")
    if len(texts) > MAX_INPUTS:
        raise HTTPException(422, f"input contains too many texts (maximum {MAX_INPUTS})")
    if any(len(text or "") > MAX_INPUT_CHARS for text in texts):
        raise HTTPException(422, f"an input text exceeds {MAX_INPUT_CHARS} characters")
    vecs = model.encode(
        [_prefixed(t or "", req.input_type) for t in texts],
        batch_size=32, normalize_embeddings=True, show_progress_bar=False,
    )
    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [{"object": "embedding", "index": i, "embedding": v.tolist()} for i, v in enumerate(vecs)],
    }
