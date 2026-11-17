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
  GET  /api/extension/resolve              page-ID-first extension resolver (compact negative)
  GET  /api/extension/annotations          read-only inline findings for the extension
  GET  /api/discoveries/feed?since=        signed federation feed of reviews/resolutions
  POST /api/discoveries/sync               pull peer feeds and import verified records
  GET  /api/discoveries/status             federation + dataset version status
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
import federation
import pages
import patches
import rag
import recheck
import verdicts
from config import settings, get_simplification
from db import insert, placeholders, query, query_one

app = FastAPI(title="One Verifiable Record", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Wikipedia origins (extension content scripts) + local development
    allow_origin_regex=r"(https://[\w-]+\.wikipedia\.org|http://localhost(:\d+)?)$",
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# SIMPLIFICATION MODE MIDDLEWARE
# =============================================================================

# Endpoints that write data (POST) - can be blocked in read-only mode
WRITE_ENDPOINTS = [
    "/api/align",
    "/api/embed",
    "/api/recheck",
    "/api/reviews",
    "/api/suggestions",
    "/api/discoveries/sync",
]

# Endpoints that use LLM - can be blocked in minimal/deterministic modes
LLM_ENDPOINTS = [
    "/api/align",
    "/api/embed",
    "/api/recheck",
    "/api/qa",
]

# Endpoints for extension - can be blocked in no_extension mode
EXTENSION_ENDPOINTS = [
    "/api/badge",
    "/api/extension/resolve",
    "/api/extension/annotations",
]

# Endpoints that involve reviews - can be blocked in minimal mode
REVIEW_ENDPOINTS = [
    "/api/reviews",
    "/api/review/queue",
]

# Endpoints for patches - can be blocked in minimal mode
PATCH_ENDPOINTS = [
    "/api/suggestions",
]


@app.middleware("http")
async def simplification_middleware(request: Request, call_next):
    """Enforce simplification mode restrictions on API endpoints."""
    path = request.url.path
    simp = get_simplification()
    
    # Mode 1: READ_ONLY - Block all POST requests and write operations
    if simp["is_read_only"]:
        if request.method == "POST":
            blocked_endpoints = [p for p in WRITE_ENDPOINTS if path.startswith(p)]
            if blocked_endpoints:
                return JSONResponse(
                    {
                        "error": "Read-only mode enabled",
                        "detail": f"POST requests are disabled in read-only mode. Endpoint: {path}",
                        "simplification": simp,
                    },
                    status_code=403,
                )
    
    # Mode 2: MINIMAL - Block LLM, reviews, patches, Q&A
    if simp["is_minimal"]:
        # Block LLM endpoints
        llm_matches = [p for p in LLM_ENDPOINTS if path.startswith(p)]
        if llm_matches and request.method == "POST":
            return JSONResponse(
                {
                    "error": "Minimal mode enabled",
                    "detail": f"LLM operations disabled. Use deterministic-only or read-only mode for simpler operations.",
                    "simplification": simp,
                },
                status_code=403,
            )
        
        # Block review endpoints
        review_matches = [p for p in REVIEW_ENDPOINTS if path.startswith(p)]
        if review_matches:
            return JSONResponse(
                {
                    "error": "Minimal mode enabled",
                    "detail": "Human review features are disabled in minimal mode.",
                    "simplification": simp,
                },
                status_code=403,
            )
        
        # Block patch/suggestion endpoints
        patch_matches = [p for p in PATCH_ENDPOINTS if path.startswith(p)]
        if patch_matches:
            return JSONResponse(
                {
                    "error": "Minimal mode enabled",
                    "detail": "Patch generation and export are disabled in minimal mode.",
                    "simplification": simp,
                },
                status_code=403,
            )
    
    # Mode 3: NO_EXTENSION - Block extension-specific endpoints
    if simp["is_no_extension"]:
        ext_matches = [p for p in EXTENSION_ENDPOINTS if path.startswith(p)]
        if ext_matches:
            return JSONResponse(
                {
                    "error": "Extension mode disabled",
                    "detail": f"Browser extension endpoints are disabled in no-extension mode.",
                    "simplification": simp,
                },
                status_code=403,
            )
    
    # Mode 4: SINGLE_LANGUAGE - Filter results to single language
    # This is handled in the endpoint logic, not by blocking
    
    # Mode 5: DETERMINISTIC - Force deterministic-only verdicts
    # This is handled in verdicts.py
    
    return await call_next(request)


# =============================================================================


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


def _extension_page(lang: str, title: str, wiki_page_id: Optional[int]):
    """Stable identity first (MediaWiki page id), title only as fallback."""
    page = pages.find_by_wiki_page_id(lang, wiki_page_id) if wiki_page_id else None
    if page is None:
        page = pages.find_by_title(lang, title)
    return page


def _freshness(analysed, live) -> str:
    if live is None:
        return "unknown"
    return "current" if analysed == live else "stale"


@app.get("/api/extension/resolve")
def extension_resolve(lang: str, title: str, wiki_page_id: Optional[int] = None,
                      live_revision_id: Optional[int] = None):
    """Page-ID-first resolver for the browser extension (docs/browser-extension-roadmap.md).

    Read-only: precomputed summary counts only, never alignment/LLM grading.
    Untracked pages get a compact successful negative response.
    """
    page = _extension_page(lang, title, wiki_page_id)
    if page is None:
        return {"tracked": False, "reason": "not_imported"}
    cps = pages.counterparts(page["id"])
    if not cps:
        return {"tracked": False, "reason": "no_counterparts"}
    stats = pages.page_stats([page["id"]], cps={page["id"]: cps})[page["id"]]
    analysed = page["revision_id"]
    has_claims = query_one(
        "SELECT 1 AS ok FROM claims c WHERE c.article_id = %s AND c.revision_id <=> %s LIMIT 1",
        (page["id"], analysed),
    )
    return {
        "tracked": True,
        "page": page,
        "counterparts": cps,
        "analysed_revision_id": analysed,
        "live_revision_id": live_revision_id,
        "freshness": _freshness(analysed, live_revision_id),
        "analysis_status": "ready" if has_claims else "not_ready",
        **stats,
    }


@app.get("/api/extension/annotations")
def extension_annotations(lang: str, title: str, wiki_page_id: Optional[int] = None,
                          live_revision_id: Optional[int] = None):
    """Read-only inline-finding projection of diff.build_comparison for the extension."""
    page = _extension_page(lang, title, wiki_page_id)
    if page is None:
        return {"tracked": False, "reason": "not_imported"}
    cps = pages.counterparts(page["id"])
    view = diff.build_comparison(page["id"], with_reviews=False, page=page, cps=cps)
    if view is None:
        return {"tracked": False, "reason": "not_imported"}
    annotations, missing = [], []
    for row in view["rows"]:
        here = row["here"][0] if row["here"] else None
        counterparts = [
            {"lang": lg, "text": c["statement_text"],
             "value": c["value_normalized"] or c["value_text"],
             "revision_id": c["revision_id"]}
            for lg, claims in row["there"].items() for c in claims
        ]
        item = {
            "row_key": row["key"],
            "status": row["status"],
            "kind": row["kind"],
            "predicate": row["predicate"],
            "claim": ({
                "text": here["statement_text"],
                "value": here["value_normalized"] or here["value_text"],
                "context": here["context_text"],
                "predicate": here["predicate"],
            } if here else None),
            "counterparts": counterparts,
        }
        (missing if row["status"] == "missing_here" else annotations).append(item)
    has_claims = query_one(
        "SELECT 1 AS ok FROM claims c WHERE c.article_id = %s AND c.revision_id <=> %s LIMIT 1",
        (page["id"], page["revision_id"]),
    )
    return {
        "tracked": True,
        "page": page,
        "counterparts": cps,
        "freshness": _freshness(page["revision_id"], live_revision_id),
        "analysis_status": "ready" if (has_claims or view["rows"]) else "not_ready",
        "summary": view["summary"],
        "annotations": annotations,
        "missing_here": missing,
    }


@app.get("/api/discoveries/feed")
def discoveries_feed(since: int = 0):
    """Signed, append-only feed of local reviews + resolutions (docs/federation.md)."""
    return federation.feed(since)


@app.post("/api/discoveries/sync")
def discoveries_sync():
    """Pull peer discovery feeds and import new verified records."""
    return federation.sync()


@app.get("/api/discoveries/status")
def discoveries_status():
    return federation.status()


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


@app.get("/api/review/queue")
def review_queue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None),
    lang: Optional[str] = Query(None),
    reviewer: Optional[str] = Query(None),
):
    """Reviewer dashboard: paginated queue of claim links needing review.
    
    Filters:
    - status: verdict type (contradicted, possible_conflict, possible_match, etc.)
    - lang: filter by claim language (fi, sv, en)
    - reviewer: show only links this reviewer has already voted on
    
    Returns: {items: [...], total: N, limit: N, offset: N}
    Each item contains link details, both claims, and their pages.
    """
    from pages import review_queue as pages_review_queue
    return pages_review_queue(limit, offset, status, lang, reviewer)


# =============================================================================
# SIMPLIFICATION CONFIGURATION ENDPOINTS
# =============================================================================

class SimplificationToggle(BaseModel):
    """Request to toggle simplification mode."""
    mode: str  # one of: none, minimal, read_only, single_language, deterministic, no_extension
    single_language: Optional[str] = None  # only for single_language mode


@app.get("/api/simplification")
def get_simplification_config():
    """Get current simplification mode and available options."""
    simp = get_simplification()
    return {
        "current_mode": simp["mode"],
        "is_active": simp["mode"] != "none",
        "modes": {
            "none": {
                "description": "Full functionality - all features enabled",
                "enabled": simp["mode"] == "none",
            },
            "minimal": {
                "description": "Core comparison only - no LLM, no reviews, no patches, no Q&A",
                "enabled": simp["is_minimal"],
                "blocks": ["LLM operations", "Human reviews", "Patch generation", "Q&A"],
            },
            "read_only": {
                "description": "Disable all write operations - only GET requests allowed",
                "enabled": simp["is_read_only"],
                "blocks": ["POST /api/align", "POST /api/recheck", "POST /api/reviews", "POST /api/suggestions"],
            },
            "single_language": {
                "description": f"Focus on one language only ({simp['single_language']}) - no cross-language comparison",
                "enabled": simp["is_single_language"],
                "current_language": simp.get("single_language", "en"),
                "blocks": ["Cross-language alignment", "Multi-language comparison"],
            },
            "deterministic": {
                "description": "Use only deterministic verdicts - skip LLM layer entirely",
                "enabled": simp["is_deterministic"],
                "blocks": ["LLM verdict grading"],
                "uses": ["Deterministic layer only (dates, numbers, exact matches)"],
            },
            "no_extension": {
                "description": "Remove all browser extension functionality",
                "enabled": simp["is_no_extension"],
                "blocks": ["Extension badge endpoint", "Extension resolver"],
            },
        },
        "environment": {
            "SIMPLIFICATION_MODE": settings.simplification_mode.value,
            "SINGLE_LANGUAGE": settings.single_language,
        },
    }


@app.post("/api/simplification")
def set_simplification_config(toggle: SimplificationToggle):
    """Toggle simplification mode. Requires restart to take full effect.
    
    This endpoint updates the in-memory settings. For persistent changes,
    set the SIMPLIFICATION_MODE environment variable and restart the server.
    """
    from config import SimplificationMode
    
    if toggle.mode not in [m.value for m in SimplificationMode]:
        raise HTTPException(
            422,
            f"Invalid mode: {toggle.mode}. Valid modes: {[m.value for m in SimplificationMode]}"
        )
    
    # Update settings
    settings.simplification_mode = SimplificationMode(toggle.mode)
    if toggle.single_language:
        settings.single_language = toggle.single_language
    
    return {
        "status": "ok",
        "message": f"Simplification mode set to '{toggle.mode}'. Some changes require server restart.",
        "current_config": get_simplification(),
    }


@app.get("/api/simplification/options")
def get_simplification_options():
    """Get detailed information about each simplification mode."""
    return {
        "options": [
            {
                "id": "none",
                "name": "Full Mode",
                "description": "All features enabled. Complete cross-language Wikipedia comparison with LLM support, human review, patch generation, and browser extension.",
                "pro hathath": ["All features available", "Best for development", "Complete demo experience"],
                "cons": ["Complex", "Resource-intensive", "Slower (LLM calls)"],
                "best_for": "Development, full demo, production",
            },
            {
                "id": "minimal",
                "name": "Minimal Mode",
                "description": "Core comparison only. Strips away LLM, reviews, patches, and Q&A to focus on the essential page-to-page comparison.",
                "pros": ["Fast", "Simple", "Low resource usage", "Easy to debug"],
                "cons": ["No human review", "No patch export", "No Q&A", "No LLM verdicts"],
                "blocks": ["LLM endpoints", "Review system", "Patch generation", "Q&A"],
                "best_for": "Testing core comparison logic, debugging alignment",
            },
            {
                "id": "read_only",
                "name": "Read-Only Mode", 
                "description": "Public demo mode. Allows viewing data but blocks any modifications. Perfect for presentations where you don't want accidental changes.",
                "pros": ["Safe for public demos", "Prevents accidental data modification", "Full read access"],
                "cons": ["No rechecks", "No reviews", "No alignment runs", "No exports"],
                "blocks": ["All POST endpoints"],
                "best_for": "Public demos, presentations, read-only deployments",
            },
            {
                "id": "single_language",
                "name": "Single-Language Mode",
                "description": "Focus on one language edition only. Disables cross-language comparison and shows only the selected language's data.",
                "pros": ["Simpler data model", "Faster queries", "Easier to understand", "Reduced complexity"],
                "cons": ["No cross-language matching", "Loses the main value proposition", "No comparison view"],
                "blocks": ["Cross-language alignment", "Multi-language comparison", "Interlanguage links"],
                "configurable": True,
                "config": ["single_language: fi|sv|en"],
                "best_for": "Language-specific analysis, debugging single-language extraction",
            },
            {
                "id": "deterministic",
                "name": "Deterministic Mode",
                "description": "Skip the LLM layer entirely. Uses only fast deterministic comparison for verdicts (exact matches, date/number comparisons).",
                "pros": ["Fast", "Deterministic results", "No LLM dependency", "No API costs", "Reproducible"],
                "cons": ["Less accurate for complex claims", "No cross-language entailment", "More false positives/negatives"],
                "blocks": ["LLM verdict layer"],
                "uses": ["Deterministic layer (exact matches, date comparison, number comparison)"],
                "best_for": "Offline use, environments without LLM access, testing deterministic logic",
            },
            {
                "id": "no_extension",
                "name": "No-Extension Mode",
                "description": "Remove all browser extension-specific functionality. Useful when deploying without the extension.",
                "pros": ["Reduced attack surface", "Cleaner API", "No extension dependencies"],
                "cons": ["Browser extension won't work", "Losing a key integration point"],
                "blocks": ["Extension badge endpoint", "Extension resolver endpoint"],
                "best_for": "Server-only deployments, API-only use, headless environments",
            },
        ],
    }
