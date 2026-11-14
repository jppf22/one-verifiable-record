"""Local embedding service: OpenAI-style /v1/embeddings over multilingual-e5-large.

Verda's inference gateway has no embeddings endpoint, so we serve the model
ourselves. The API server (query-time Q&A) and jobs/embed.py both call this.
Point EMBED_BASE_URL at a GPU-hosted copy for bulk runs.

E5 needs instruction prefixes. Pass "input_type": "query" (default) or
"passage". Inputs that already start with "query: " / "passage: " are kept as-is.
"""
import os
from typing import List, Literal, Optional, Union

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large")
model = SentenceTransformer(MODEL_NAME)
app = FastAPI(title="OVR embedder")


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
def embeddings(req: EmbedRequest):
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
