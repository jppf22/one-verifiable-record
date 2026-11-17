"""Grounded Q&A: vector retrieval over chunks + LLM synthesis with receipts.

Restricted to the comparison set of one page (the page + its counterpart
pages), with per-page citations (page, revision, wiki URL).
Falls back to lexical search when the embedder service is unreachable.
"""
import httpx

import llm
from config import settings
from db import placeholders, query
from pages import comparison_set


def _vec_literal(vec) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def embed_question(text: str) -> str | None:
    """Embed via the configured embeddings endpoint (local embedder service or
    any OpenAI-compatible /v1/embeddings API); None = 'use the lexical fallback'."""
    if not settings.embed_base_url:
        return None
    try:
        headers = {"Authorization": f"Bearer {settings.embed_api_key}"} if settings.embed_api_key else {}
        r = httpx.post(
            f"{settings.embed_base_url.rstrip('/')}/embeddings",
            headers=headers,
            json={"model": settings.embed_model, "input": [text], "input_type": "query"},
            timeout=30,
        )
        r.raise_for_status()
        return _vec_literal(r.json()["data"][0]["embedding"])
    except Exception:
        return None


def retrieve(page_id: int, question: str, lang: str | None = None, k: int | None = None):
    """Passages from the comparison set of page_id (the page + its counterparts)."""
    k = k or settings.top_k
    ids = comparison_set(page_id)
    pages_filter = f"ch.article_id IN ({placeholders(ids)})"
    lang_filter = " AND a.lang = %s" if lang else ""
    lang_param = [lang] if lang else []
    vec = embed_question(question)
    if vec is not None:
        # Restricted to one comparison set (a few dozen chunks), so ranking them
        # by exact cosine distance is cheap. Corpus-wide search would use the HNSW
        # index via the subquery shape from the MariaDB RAG starter.
        hits = query(
            f"""SELECT ch.id, ch.article_id AS page_id, ch.text, a.lang, a.title, a.revision_id,
                       VEC_DISTANCE_COSINE(ce.embedding, VEC_FromText(%s)) AS dist
                FROM chunks ch
                JOIN chunk_embeddings ce ON ce.chunk_id = ch.id
                JOIN articles a ON a.id = ch.article_id
                WHERE {pages_filter}{lang_filter}
                ORDER BY dist ASC
                LIMIT %s""",
            tuple([vec] + ids + lang_param + [k]),
        )
        if hits:
            return hits
    # lexical fallback: embedder unreachable, or these pages' chunks not embedded yet
    words = [w for w in question.split() if len(w) > 3][:6] or [question]
    likes = " OR ".join(["ch.text LIKE %s"] * len(words))
    return query(
        f"""SELECT ch.id, ch.article_id AS page_id, ch.text, a.lang, a.title, a.revision_id
            FROM chunks ch
            JOIN articles a ON a.id = ch.article_id
            WHERE {pages_filter}{lang_filter} AND ({likes})
            LIMIT %s""",
        tuple(ids + lang_param + [f"%{w}%" for w in words] + [k]),
    )


def synthesize(question: str, hits) -> str | None:
    if not llm.configured() or not hits:
        return None
    context = "\n\n".join(
        f"[{h['lang']}] {h['title']} (rev {h['revision_id']}):\n{h['text']}" for h in hits
    )
    try:
        return llm.chat_text(
            "Answer only from the provided excerpts. Cite each fact "
            "as [lang] inline. If the excerpts disagree across "
            "languages, say so explicitly and quote both sides.",
            f"Excerpts:\n{context}\n\nQuestion: {question}",
            max_tokens=800,
        )
    except llm.LLMUnavailable:
        return None


def answer(page_id: int, question: str, lang: str | None = None) -> dict:
    hits = retrieve(page_id, question, lang)
    citations = [
        {
            "page_id": h["page_id"],
            "lang": h["lang"],
            "title": h["title"],
            "revision_id": h["revision_id"],
            "url": (
                f"https://{h['lang']}.wikipedia.org/w/index.php?oldid={h['revision_id']}"
            ),
            "text": (h["text"] or "")[:280],
        }
        for h in hits
    ]
    return {
        "page_id": page_id,
        "question": question,
        "answer": synthesize(question, hits)
        or "No synthesized answer: the LLM is unavailable (set LLM_BASE_URL and LLM_MODEL to any OpenAI-compatible endpoint, local or hosted). The retrieved passages are cited below.",
        "citations": citations,
    }
