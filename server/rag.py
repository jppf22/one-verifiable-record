"""Grounded Q&A: vector retrieval over chunks + LLM synthesis with receipts.

The same MariaDB vector index that powers the merge view answers reader
questions, with per-language citations (article, revision, wiki URL).
Falls back to lexical search when no embeddings endpoint is configured.
"""
import httpx

from config import settings
from db import query


def _vec_literal(vec) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def embed_question(text: str) -> str | None:
    """Embed via the Verda Inference API; None means 'use the lexical fallback'."""
    if not settings.verda_api_key:
        return None
    try:
        r = httpx.post(
            f"{settings.verda_base_url}/embeddings",
            headers={"Authorization": f"Bearer {settings.verda_api_key}"},
            json={"model": settings.embed_model, "input": [f"query: {text}"]},
            timeout=30,
        )
        r.raise_for_status()
        return _vec_literal(r.json()["data"][0]["embedding"])
    except Exception:
        return None


def retrieve(qid: str, question: str, lang: str | None = None, k: int | None = None):
    k = k or settings.top_k
    vec = embed_question(question)
    if vec is None:
        # lexical fallback (no embeddings endpoint configured)
        return query(
            """SELECT ch.id, ch.text, a.lang, a.title, a.revision_id
               FROM chunks ch
               JOIN articles a ON a.id = ch.article_id
               WHERE a.wikidata_qid = %s AND ch.text LIKE %s
               LIMIT %s""",
            (qid, f"%{question}%", k),
        )
    lang_filter = " AND a.lang = %s" if lang else ""
    sql = f"""
        SELECT ch.id, ch.text, a.lang, a.title, a.revision_id,
               VEC_DISTANCE_COSINE(ch.embedding, VEC_FromText(%s)) AS dist
        FROM chunks ch
        JOIN articles a ON a.id = ch.article_id
        WHERE a.wikidata_qid = %s AND ch.embedding IS NOT NULL{lang_filter}
        ORDER BY dist ASC
        LIMIT %s
    """
    params = [vec, qid] + ([lang] if lang else []) + [k]
    return query(sql, tuple(params))


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
        or "Set VERDA_API_KEY to enable synthesized answers.",
        "citations": citations,
    }
