"""One Verifiable Record — API server.

Endpoints (see /docs for the interactive reference):
  GET  /api/health
  GET  /api/articles?q=...                 search tracked articles
  GET  /api/articles/by-title?lang&title   resolve one edition
  POST /api/align/{qid}                    run alignment + verdict layers
  GET  /api/merge/{qid}                    git-diff-style merged view (+ summary,
                                           latest review per link, wiki permalinks)
  POST /api/recheck/{qid}                  re-check the article; body (optional)
                                           {"focus_link_ids": [...]}; reports which
                                           conflicts resurface
  GET  /api/badge?lang&title               extension summary badge (+ conflicts, gaps)
  GET  /api/claims/{id}/history            biography of a claim + its links and
                                           their reviews (FOR SYSTEM_TIME ALL)
  POST /api/reviews                        human-in-the-loop review, then re-check
  GET  /api/reviews?link_id=               reviews of one link, newest first
  GET  /api/suggestions/{qid}              generate (deduplicated) + list patch drafts
  POST /api/suggestions/{id}/export        mark a draft exported, then re-check
  GET  /api/qa?qid&q                       grounded Q&A with citations

The full request/response shapes live in docs/frontend-contract.md.
"""
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import alignment
import diff
import patches
import rag
import recheck
import verdicts
from db import insert, placeholders, query, query_one

app = FastAPI(title="One Verifiable Record", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Wikipedia origins (extension content scripts) + local development
    allow_origin_regex=r"(https://[\w-]+\.wikipedia\.org|http://localhost(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/articles")
def search_articles(q: str = Query(min_length=2), limit: int = 25):
    """Search tracked articles that are aligned across languages (have a QID)."""
    rows = query(
        """SELECT a.id, a.lang, a.title, a.wikidata_qid, a.revision_id
           FROM articles a
           WHERE a.title LIKE %s AND a.wikidata_qid IS NOT NULL
           ORDER BY a.title
           LIMIT %s""",
        (f"%{q}%", limit),
    )
    # collapse to one row per Wikidata entity (first title wins)
    seen, out = set(), []
    for r in rows:
        if r["wikidata_qid"] in seen:
            continue
        seen.add(r["wikidata_qid"])
        out.append(r)
    return out


@app.get("/api/articles/by-title")
def by_title(lang: str, title: str):
    row = query_one(
        "SELECT id, lang, title, wikidata_qid, revision_id FROM articles "
        "WHERE lang = %s AND title = %s",
        (lang, title),
    )
    if not row:
        raise HTTPException(404, "article not tracked")
    return row


@app.post("/api/align/{qid}")
def align(qid: str):
    """Align claims across this entity's editions, then grade the links."""
    counts = alignment.align_article(qid)
    verdict_counts = verdicts.run_verdicts(qid)
    return {"qid": qid, **counts, "verdicts": verdict_counts}


@app.get("/api/merge/{qid}")
def merge(qid: str):
    return diff.build_merge_view(qid)


class RecheckRequest(BaseModel):
    focus_link_ids: list[int] = []


@app.post("/api/recheck/{qid}")
def recheck_article(qid: str, body: Optional[RecheckRequest] = None):
    """Re-run alignment + grading for this entity and report what resurfaced."""
    if not query_one("SELECT 1 AS ok FROM articles WHERE wikidata_qid = %s LIMIT 1", (qid,)):
        raise HTTPException(404, "article not tracked")
    return recheck.recheck_article(qid, body.focus_link_ids if body else [])


@app.get("/api/badge")
def badge(lang: str, title: str):
    """Summary for the browser extension: verdict counts + merge-view target."""
    row = query_one(
        "SELECT id, wikidata_qid FROM articles WHERE lang = %s AND title = %s",
        (lang, title),
    )
    if not row or not row["wikidata_qid"]:
        raise HTTPException(404, "article not tracked")
    counts = query(
        """SELECT cl.verdict, COUNT(*) AS n
           FROM claim_links cl
           JOIN claims c ON c.id = cl.claim_a
           JOIN articles a ON a.id = c.article_id
           WHERE a.wikidata_qid = %s
           GROUP BY cl.verdict""",
        (row["wikidata_qid"],),
    )
    clusters = diff.build_merge_view(row["wikidata_qid"], with_reviews=False)["clusters"]
    status = diff.status_counts(clusters)
    return {
        "qid": row["wikidata_qid"],
        "counts": {r["verdict"]: r["n"] for r in counts},
        "conflicts": status["conflict"],
        "gaps": status["gap"],
    }


@app.get("/api/claims/{claim_id}/history")
def claim_history(claim_id: int):
    """Biography of a sentence: full system-versioned history of a claim and
    every link that involves it."""
    claim_rows = query(
        "SELECT id, kind, statement_text, value_normalized, model_version, "
        "ROW_START, ROW_END FROM claims FOR SYSTEM_TIME ALL WHERE id = %s "
        "ORDER BY ROW_START",
        (claim_id,),
    )
    link_rows = query(
        "SELECT id, claim_a, claim_b, verdict, verdict_source, similarity, evidence, "
        "rationale, ROW_START, ROW_END FROM claim_links FOR SYSTEM_TIME ALL "
        "WHERE claim_a = %s OR claim_b = %s ORDER BY ROW_START",
        (claim_id, claim_id),
    )
    link_ids = sorted({r["id"] for r in link_rows})
    review_rows = []
    if link_ids:
        review_rows = query(
            "SELECT id, claim_link_id, reviewer, decision, note, ROW_START, ROW_END "
            "FROM reviews FOR SYSTEM_TIME ALL "
            f"WHERE claim_link_id IN ({placeholders(link_ids)}) ORDER BY ROW_START",
            tuple(link_ids),
        )
    return {"claim": claim_rows, "links": link_rows, "reviews": review_rows}


class Review(BaseModel):
    claim_link_id: int
    reviewer: str
    decision: str  # agree | disagree | fix
    note: Optional[str] = None


@app.post("/api/reviews")
def create_review(r: Review):
    if r.decision not in ("agree", "disagree", "fix"):
        raise HTTPException(422, "decision must be agree | disagree | fix")
    link = query_one(
        """SELECT cl.id, a.wikidata_qid
           FROM claim_links cl
           JOIN claims c ON c.id = cl.claim_a
           JOIN articles a ON a.id = c.article_id
           WHERE cl.id = %s""",
        (r.claim_link_id,),
    )
    if not link:
        raise HTTPException(404, "claim link not found")
    review_id = insert(
        "INSERT INTO reviews (claim_link_id, reviewer, decision, note) "
        "VALUES (%s, %s, %s, %s)",
        (r.claim_link_id, r.reviewer, r.decision, r.note),
    )
    # The review does not overwrite the machine verdict: re-check the article
    # and report whether the conflict resurfaces (assumptions.md, decision 4).
    result = None
    if link["wikidata_qid"]:
        result = recheck.recheck_article(link["wikidata_qid"], [r.claim_link_id])
    return {"ok": True, "review_id": review_id, "recheck": result}


@app.get("/api/reviews")
def list_reviews(link_id: int):
    return query(
        "SELECT id, claim_link_id, reviewer, decision, note, created_at "
        "FROM reviews WHERE claim_link_id = %s ORDER BY created_at DESC, id DESC",
        (link_id,),
    )


@app.get("/api/suggestions/{qid}")
def suggestions(qid: str):
    """Generate patch drafts for this entity and return them for export."""
    return patches.generate_suggestions(qid)


@app.post("/api/suggestions/{suggestion_id}/export")
def export_suggestion(suggestion_id: int):
    """Mark a draft exported (SVT row), then re-check with focus on its link."""
    s = patches.get_suggestion(suggestion_id)
    if not s:
        raise HTTPException(404, "suggestion not found")
    patches.mark_exported(suggestion_id)
    result = None
    if s["claim_link_id"] is not None and s["wikidata_qid"]:
        result = recheck.recheck_article(s["wikidata_qid"], [s["claim_link_id"]])
    return {"ok": True, "suggestion_id": suggestion_id, "recheck": result}


@app.get("/api/qa")
def qa(qid: str, q: str = Query(min_length=2), lang: Optional[str] = None):
    return rag.answer(qid, q, lang)
