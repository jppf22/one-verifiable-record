"""Grounded Q&A: vector retrieval over chunks + LLM synthesis with receipts.

The same MariaDB vector index that powers the merge view answers reader
questions, with per-language citations (article, revision, wiki URL).
Falls back to lexical search when the embedder service is unreachable.
"""
import httpx

from config import settings
from db import query


def _vec_literal(vec) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def embed_question(text: str) -> str | None:
    """Embed via the local embedder service; None means 'use the lexical fallback'."""
    if not settings.embed_base_url:
        return None
    try:
        r = httpx.post(
            f"{settings.embed_base_url}/embeddings",
            json={"model": settings.embed_model, "input": [text], "input_type": "query"},
            timeout=30,
        )
        r.raise_for_status()
        return _vec_literal(r.json()["data"][0]["embedding"])
    except Exception:
        return None


def retrieve(qid: str, question: str, lang: str | None = None, k: int | None = None):
    k = k or settings.top_k
    lang_filter = " AND a.lang = %s" if lang else ""
    lang_param = [lang] if lang else []
    vec = embed_question(question)
    if vec is not None:
        # Restricted to one entity's editions (a few dozen chunks), so ranking them
        # by exact cosine distance is cheap. Corpus-wide search would use the HNSW
        # index via the subquery shape from the MariaDB RAG starter.
        hits = query(
            f"""SELECT ch.id, ch.text, a.lang, a.title, a.revision_id,
                       VEC_DISTANCE_COSINE(ce.embedding, VEC_FromText(%s)) AS dist
                FROM chunks ch
                JOIN chunk_embeddings ce ON ce.chunk_id = ch.id
                JOIN articles a ON a.id = ch.article_id
                WHERE a.wikidata_qid = %s{lang_filter}
                ORDER BY dist ASC
                LIMIT %s""",
            tuple([vec, qid] + lang_param + [k]),
        )
        if hits:
            return hits
    # lexical fallback: embedder unreachable, or this entity's chunks not embedded yet
    words = [w for w in question.split() if len(w) > 3][:6] or [question]
    likes = " OR ".join(["ch.text LIKE %s"] * len(words))
    return query(
        f"""SELECT ch.id, ch.text, a.lang, a.title, a.revision_id
            FROM chunks ch
            JOIN articles a ON a.id = ch.article_id
            WHERE a.wikidata_qid = %s{lang_filter} AND ({likes})
            LIMIT %s""",
        tuple([qid] + lang_param + [f"%{w}%" for w in words] + [k]),
    )


def synthesize(question: str, hits) -> str | None:
    if not settings.verda_api_key or not hits:
        return None
    context = "\n\n".join(
        f"[{h['lang']}] {h['title']} (rev {h['revision_id']}):\n{h['text']}" for h in hits
    )
    try:
        r = httpx.post(
            f"{settings.verda_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.verda_api_key}"},
            json={
                "model": settings.llm_model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Answer only from the provided excerpts. Cite each fact "
                            "as [lang] inline. If the excerpts disagree across "
                            "languages, say so explicitly and quote both sides."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Excerpts:\n{context}\n\nQuestion: {question}",
                    },
                ],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None


def answer(qid: str, question: str, lang: str | None = None) -> dict:
    hits = retrieve(qid, question, lang)
    citations = [
        {
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
        "qid": qid,
        "question": question,
        "answer": synthesize(question, hits)
        or "No synthesized answer: the LLM is unavailable (check VERDA_API_KEY and the model subscription). The retrieved passages are cited below.",
        "citations": citations,
    }
