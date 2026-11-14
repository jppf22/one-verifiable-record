"""One Verifiable Record — API server.

Endpoints (see /docs for the interactive reference):
  GET  /api/health
  GET  /api/articles?q=...                 search tracked articles
  GET  /api/articles/by-title?lang&title   resolve one edition
  POST /api/align/{qid}                    run alignment + verdict layers
  POST /api/embed/{qid}                    GPU-embed missing current claims (and optional chunks)
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
from collections import defaultdict, deque
from contextlib import contextmanager
import re
from threading import Lock
from time import monotonic
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import alignment
import diff
import embedding_jobs
import patches
import rag
import recheck
import verdicts
from config import settings
from db import insert, placeholders, query, query_one

app = FastAPI(title="One Verifiable Record", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Wikipedia origins (extension content scripts) + local development
    allow_origin_regex=r"(https://[\w-]+\.wikipedia\.org|http://localhost(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
)


# Alignment and re-checks can make multiple inference calls. This modest,
# in-process limiter is deliberately a safety net for a public demo, not a
# substitute for account-level authentication in a multi-user deployment.
_post_windows: dict[str, deque[float]] = defaultdict(deque)
_post_lock = Lock()
_entity_locks: dict[str, Lock] = {}
_entity_locks_guard = Lock()
QID_RE = re.compile(r"^Q[1-9][0-9]{0,14}$")


def _require_qid(qid: str) -> str:
    if not QID_RE.fullmatch(qid):
        raise HTTPException(422, "qid must be a Wikidata identifier such as Q42")
    return qid


@contextmanager
def _entity_job(qid: str):
    """Reject overlapping costly jobs for the same entity in this API process.

    Without this guard, double-clicks and retries can run the same GPU/LLM work
    concurrently. Database uniqueness prevents duplicate links, but cannot
    recover the duplicate inference cost or inconsistent timing.
    """
    with _entity_locks_guard:
        lock = _entity_locks.setdefault(qid, Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "an alignment, embedding, or re-check is already running for this article")
    try:
        yield
    finally:
        lock.release()
        with _entity_locks_guard:
            if _entity_locks.get(qid) is lock and not lock.locked():
                _entity_locks.pop(qid, None)


@app.middleware("http")
async def limit_costly_writes(request: Request, call_next):
    costly = request.method == "POST" or (
        request.method == "GET" and request.url.path.startswith("/api/suggestions/")
    )
    if not costly or not request.url.path.startswith("/api/"):
        return await call_next(request)
    # The only public path is nginx, which sets this header. Direct API access
    # is loopback-only in compose, so it is safe to use the forwarded address.
    client = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    now = monotonic()
    with _post_lock:
        window = _post_windows[client]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= settings.post_rate_limit_per_minute:
            return JSONResponse(
                {"detail": "Too many write requests; try again in a minute."}, status_code=429,
            )
        window.append(now)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/articles")
def search_articles(q: str = Query(min_length=2, max_length=160), limit: int = 25):
    """Search tracked entities, returning exactly one result per Wikidata QID.

    Editions are nested beneath their shared entity rather than consuming separate
    result slots.  This matters for a three-language corpus: limiting rows before
    grouping made a search for a common title look like it contained duplicate pages.
    """
    limit = max(1, min(limit, 100))
    rows = query(
        """SELECT a.id, a.lang, a.title, a.wikidata_qid, a.revision_id
           FROM articles a
           JOIN (
             SELECT wikidata_qid, MIN(title) AS match_title
             FROM articles
             WHERE title LIKE %s AND wikidata_qid IS NOT NULL
             GROUP BY wikidata_qid
             ORDER BY MIN(title), wikidata_qid
             LIMIT %s
           ) matches ON matches.wikidata_qid = a.wikidata_qid
           ORDER BY matches.match_title, matches.wikidata_qid,
                    CASE a.lang WHEN 'en' THEN 0 WHEN 'fi' THEN 1 WHEN 'sv' THEN 2 ELSE 3 END,
                    a.title""",
        (f"%{q}%", limit),
    )
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["wikidata_qid"]].append(r)

    out = []
    for qid, editions in grouped.items():
        # The English title is usually the most recognisable display title.  The
        # complete edition list keeps searches in Finnish and Swedish discoverable.
        primary = next((e for e in editions if e["lang"] == "en"), editions[0]).copy()
        primary["editions"] = editions
        out.append(primary)
    return out


PERSON_PREDICATES = {
    "birth_date", "birth_place", "death_date", "death_place", "spouse",
    "occupation", "office_held", "parent", "child",
}
PLACE_PREDICATES = {"population", "area", "area_km2", "elevation", "located_in", "coordinates"}
ORGANIZATION_PREDICATES = {"founded", "headquarters", "parent_organization", "subsidiary"}
EVENT_PREDICATES = {"start_date", "end_date", "outcome", "participants", "location"}
WORK_PREDICATES = {"author", "director", "release_date", "publisher", "genre"}


def _topic_type(predicates: set[str]) -> str:
    """A deliberately conservative label for browse cards, not a Wikidata assertion."""
    if predicates & PLACE_PREDICATES:
        return "Place"
    if predicates & ORGANIZATION_PREDICATES:
        return "Organization"
    if predicates & EVENT_PREDICATES:
        return "Event"
    if predicates & WORK_PREDICATES:
        return "Work"
    return "Topic"


@app.get("/api/discover")
def discover_articles(limit: int = Query(8, ge=1, le=20)):
    """Return one-card-per-page discovery lists from the imported corpus.

    ``high_conflicts`` is ranked by distinct contradicted claim links; ``diverse``
    deliberately excludes pages with clear biography predicates, so the landing
    page is not just a roster of people.
    """
    rows = query(
        """SELECT a.wikidata_qid,
                  MAX(CASE WHEN a.lang = 'en' THEN a.title END) AS english_title,
                  MIN(a.title) AS fallback_title,
                  COUNT(DISTINCT a.lang) AS edition_count,
                  COUNT(DISTINCT c.id) AS claim_count,
                  COUNT(DISTINCT CASE WHEN cl.verdict = 'contradicted' THEN cl.id END) AS conflict_count,
                  GROUP_CONCAT(DISTINCT c.predicate SEPARATOR '|') AS predicates
           FROM articles a
           LEFT JOIN claims c ON c.article_id = a.id AND c.revision_id <=> a.revision_id
           LEFT JOIN claim_links cl ON cl.claim_a = c.id
           WHERE a.wikidata_qid IS NOT NULL
           GROUP BY a.wikidata_qid
           HAVING COUNT(DISTINCT a.lang) >= 2
           ORDER BY conflict_count DESC, claim_count DESC, english_title, fallback_title
           LIMIT 250"""
    )
    qids = [r["wikidata_qid"] for r in rows]
    editions_by_qid = defaultdict(list)
    if qids:
        edition_rows = query(
            f"""SELECT id, lang, title, wikidata_qid, revision_id FROM articles
                WHERE wikidata_qid IN ({placeholders(qids)})
                ORDER BY wikidata_qid,
                         CASE lang WHEN 'en' THEN 0 WHEN 'fi' THEN 1 WHEN 'sv' THEN 2 ELSE 3 END,
                         title""",
            tuple(qids),
        )
        for edition in edition_rows:
            editions_by_qid[edition["wikidata_qid"]].append(edition)

    cards = []
    for row in rows:
        predicates = {p for p in (row.get("predicates") or "").split("|") if p}
        editions = editions_by_qid[row["wikidata_qid"]]
        cards.append({
            "qid": row["wikidata_qid"],
            "title": row["english_title"] or row["fallback_title"],
            "editions": editions,
            "conflicts": int(row["conflict_count"] or 0),
            "claims": int(row["claim_count"] or 0),
            "is_person": bool(predicates & PERSON_PREDICATES),
            "topic_type": _topic_type(predicates),
        })

    high_conflicts = [card for card in cards if card["conflicts"] > 0][:limit]
    # Keep the broadest labels represented before adding a second item of a type.
    diverse, seen_types = [], set()
    candidates = [card for card in cards if not card["is_person"]]
    for card in candidates:
        if card["topic_type"] not in seen_types:
            diverse.append(card)
            seen_types.add(card["topic_type"])
            if len(diverse) == limit:
                break
    if len(diverse) < limit:
        used = {card["qid"] for card in diverse}
        diverse.extend(card for card in candidates if card["qid"] not in used)
    return {"high_conflicts": high_conflicts, "diverse": diverse[:limit]}


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
    qid = _require_qid(qid)
    with _entity_job(qid):
        counts = alignment.align_article(qid)
        verdict_counts = verdicts.run_verdicts(qid)
    return {"qid": qid, **counts, "verdicts": verdict_counts}


@app.post("/api/embed/{qid}")
def embed_entity(qid: str, include_chunks: bool = False):
    """Embed missing current claims for one entity through the Verda GPU worker.

    Pass ``include_chunks=true`` after an import to also populate RAG passages.
    This is intentionally QID-scoped: a browser cannot accidentally start a
    corpus-wide GPU job.
    """
    qid = _require_qid(qid)
    if not query_one("SELECT 1 AS ok FROM articles WHERE wikidata_qid = %s LIMIT 1", (qid,)):
        raise HTTPException(404, "article not tracked")
    try:
        with _entity_job(qid):
            return embedding_jobs.embed_qid(qid, ("claims", "chunks") if include_chunks else ("claims",))
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(503, f"GPU embedding worker unavailable: {exc}")


@app.get("/api/merge/{qid}")
def merge(qid: str):
    return diff.build_merge_view(_require_qid(qid))


class RecheckRequest(BaseModel):
    focus_link_ids: list[int] = Field(default_factory=list, max_length=100)


@app.post("/api/recheck/{qid}")
def recheck_article(qid: str, body: Optional[RecheckRequest] = None):
    """Re-run alignment + grading for this entity and report what resurfaced."""
    qid = _require_qid(qid)
    if not query_one("SELECT 1 AS ok FROM articles WHERE wikidata_qid = %s LIMIT 1", (qid,)):
        raise HTTPException(404, "article not tracked")
    with _entity_job(qid):
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
           JOIN claims other_c ON other_c.id = cl.claim_b
           JOIN articles other_a ON other_a.id = other_c.article_id
           WHERE a.wikidata_qid = %s
             AND c.revision_id <=> a.revision_id
             AND other_c.revision_id <=> other_a.revision_id
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
    reviewer: str = Field(min_length=1, max_length=128)
    decision: str  # agree | disagree | fix
    note: Optional[str] = Field(default=None, max_length=10_000)


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
    return patches.generate_suggestions(_require_qid(qid))


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
def qa(qid: str, q: str = Query(min_length=2, max_length=1000), lang: Optional[str] = None):
    question = q.strip()
    if len(question) < 2:
        raise HTTPException(422, "q must contain at least two non-whitespace characters")
    return rag.answer(_require_qid(qid), question, lang)
