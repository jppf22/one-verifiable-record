"""One Verifiable Record — API server (page-centric, docs/page-contract.md).

The unit of comparison is a PAGE (one language edition, one revision), compared
with its counterpart pages (page_links). Endpoints (see /docs):
  GET  /api/health
  GET  /api/pages?q=&lang=                 title search; pages with >=1 counterpart
  GET  /api/pages/conflicted?limit=50      landing list, most conflicted first
                                           (SQL aggregation, claim-level counts)
  GET  /api/pages/by-title?lang&title      resolve one page (+ counterparts)
  GET  /api/pages/{id}                     page + counterparts
  POST /api/pages/{id}/align               vector alignment + verdict layers
  GET  /api/pages/{id}/compare             comparison payload (rows relative to
                                           this page: conflict / missing_here /
                                           only_here / possible match / agree)
  POST /api/pages/{id}/recheck             re-check; body (optional)
                                           {"focus_link_ids": [...]}
  POST /api/claims/clean-invalid-links      cleanup mismatched predicate links
  POST /api/pages/{id}/clean-links          cleanup invalid links for page
  GET  /api/pages/{id}/suggestions         generate (deduplicated) + list drafts
  POST /api/suggestions/{id}/export        mark a draft exported, then re-check
  GET  /api/claims/{id}/history            biography of a claim + its links and
                                           their reviews (FOR SYSTEM_TIME ALL)
  POST /api/reviews                        human review (+ R1 consensus), re-check claim_a's page
  GET  /api/links/{id}/consensus           R1 tally of one link (SVT-pinned votes)
  GET  /api/reviews?link_id=               reviews of one link, newest first
  POST /api/links/{id}/resolution          write the correct version (English) of a
                                           reviewer-confirmed conflict; drafts it
                                           for every language of the comparison set
  GET  /api/links/{id}/resolution          latest resolution of a link + its drafts
  GET  /api/qa?page=&q=&lang=              grounded Q&A over the comparison set
  GET  /api/badge?lang&title               extension badge
  GET  /api/extension/resolve              page-id-first extension resolver
  GET  /api/extension/annotations           snapshot-safe inline annotation data

The old QID endpoints (/articles, /merge, /align, /recheck, /suggestions/{qid})
are removed. wikidata_qid is an import-time detail only.
"""
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import alignment
import consensus
import diff
import pages
import patches
import rag
import recheck
import resolutions
import verdicts
from db import insert, placeholders, query, query_one

app = FastAPI(title="One Verifiable Record", version="0.2.0")

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


def _page_or_404(page_id: int) -> dict:
    page = pages.get_page(page_id)
    if not page:
        raise HTTPException(404, "page not tracked")
    return page


def _by_title_or_404(lang: str, title: str):
    """(page, counterparts); 404 unless tracked and with >=1 counterpart."""
    page = pages.find_by_title(lang, title)
    if not page:
        raise HTTPException(404, "page not tracked")
    cps = pages.counterparts(page["id"])
    if not cps:
        raise HTTPException(404, "page has no counterpart pages")
    return page, cps


@app.get("/api/pages")
def search_pages(q: str = Query(min_length=2), lang: Optional[str] = None,
                 limit: int = Query(25, ge=1, le=100)):
    """Title search over pages that have at least one counterpart page."""
    lang_sql = " AND a.lang = %s" if lang else ""
    rows = query(
        f"""SELECT {pages.PAGE_COLS} FROM articles a
            WHERE a.title LIKE %s{lang_sql}
              AND EXISTS (SELECT 1 FROM page_links pl
                          WHERE pl.page_a = a.id AND pl.page_b <> a.id)
            ORDER BY (a.title = %s) DESC, (a.title LIKE %s) DESC, a.title, a.lang
            LIMIT %s""",
        tuple([f"%{q}%"] + ([lang] if lang else []) + [q, f"{q}%", limit]),
    )
    ids = [r["id"] for r in rows]
    cps = pages.counterparts_many(ids)
    conflicts = pages.conflict_counts(ids, cps=cps)
    return [
        {"page": pages.page_obj(r), "counterparts": cps[r["id"]],
         "conflicts": conflicts[r["id"]]}
        for r in rows
    ]


@app.get("/api/pages/conflicted")
def conflicted_pages(limit: int = Query(50, ge=1, le=200)):
    """Landing list: pages with counterparts, most conflicted first. Uses bulk
    comparison row construction so counts match GET /pages/{id}/compare."""
    rows = pages.ranked_pages(min(200, max(limit, limit + 25)))
    ids = [r["id"] for r in rows]
    cps = pages.counterparts_many(ids)
    stats = pages.page_stats(ids, cps=cps)
    items = [
        {"page": pages.page_obj(r), "counterparts": cps[r["id"]], **stats[r["id"]]}
        for r in rows
    ]
    items.sort(
        key=lambda x: (
            -x["conflicts"],
            -x.get("possible_conflicts", 0),
            -(x["agree"] + x["missing_here"] + x["only_here"]),
            x["page"]["title"],
            x["page"]["lang"],
        )
    )
    return items[:limit]


@app.get("/api/pages/by-title")
def page_by_title(lang: str, title: str):
    page, cps = _by_title_or_404(lang, title)
    return {"page": page, "counterparts": cps}


@app.get("/api/pages/{page_id}")
def page_detail(page_id: int):
    page = _page_or_404(page_id)
    return {"page": page, "counterparts": pages.counterparts(page_id)}


@app.post("/api/pages/{page_id}/align")
def align_page(page_id: int):
    """Align this page's claims with its counterpart pages, then grade the links."""
    _page_or_404(page_id)
    # Clean up any invalid links for this page before re-aligning
    cleaned = alignment.clean_invalid_links(page_id)
    counts = alignment.align_page(page_id)
    verdict_counts = verdicts.run_verdicts(page_id)
    return {"page_id": page_id, **counts, "verdicts": verdict_counts, "cleaned_invalid_links": cleaned}


@app.get("/api/pages/{page_id}/compare")
def compare_page(page_id: int):
    view = diff.build_comparison(page_id)
    if view is None:
        raise HTTPException(404, "page not tracked")
    return view


class RecheckRequest(BaseModel):
    focus_link_ids: list[int] = []


@app.post("/api/pages/{page_id}/recheck")
def recheck_page(page_id: int, body: Optional[RecheckRequest] = None):
    """Re-run alignment + grading for this page and report what resurfaced."""
    _page_or_404(page_id)
    return recheck.recheck_page(page_id, body.focus_link_ids if body else [])


@app.post("/api/claims/clean-invalid-links")
def clean_invalid_links():
    """Remove all claim_links with mismatched predicates (one-time cleanup)."""
    cleaned = alignment.clean_invalid_links()
    return {"cleaned": cleaned, "message": f"Removed {cleaned} invalid claim links"}


@app.post("/api/pages/{page_id}/clean-links")
def clean_page_links(page_id: int):
    """Remove invalid claim_links for a specific page."""
    _page_or_404(page_id)
    cleaned = alignment.clean_invalid_links(page_id)
    return {"page_id": page_id, "cleaned": cleaned, "message": f"Removed {cleaned} invalid claim links for page {page_id}"}


@app.get("/api/pages/{page_id}/suggestions")
def page_suggestions(page_id: int):
    """Generate patch drafts for this page and its counterparts, list them."""
    result = patches.generate_suggestions(page_id)
    if result is None:
        raise HTTPException(404, "page not tracked")
    return result


@app.get("/api/badge")
def badge(lang: str, title: str):
    """Summary for the browser extension: this page's status counts + verdicts."""
    page, cps = _by_title_or_404(lang, title)
    view = diff.build_comparison(page["id"], with_reviews=False, page=page, cps=cps)
    counts: dict = {}
    for row in view["rows"]:
        for link in row["links"]:
            counts[link["verdict"]] = counts.get(link["verdict"], 0) + 1
    s = view["summary"]
    return {
        "page": page,
        "counterparts": cps,
        "conflicts": s["conflict"],
        "possible_conflicts": s["possible_conflict"],
        "missing_here": s["missing_here"],
        "only_here": s["only_here"],
        "possible_matches": s["possible_match"],
        "agree": s["agree"],
        "counts": counts,
    }


@app.get("/api/extension/resolve")
def extension_resolve(
    lang: str,
    wiki_page_id: Optional[int] = Query(None, ge=1),
    title: Optional[str] = Query(None, min_length=1, max_length=512),
    live_revision_id: Optional[int] = Query(None, ge=1),
):
    """Resolve a live Wikipedia page for the Chromium companion.

    This endpoint deliberately returns ``tracked: false`` for ordinary
    non-membership rather than making the extension treat it as an error. A
    page id is authoritative; title lookup is only a legacy/browser fallback.
    Findings are precomputed from the imported snapshot and are accompanied by
    a freshness state when the browser supplies a live revision id.
    """
    if lang not in pages.LANG_ORDER:
        return {"tracked": False, "reason": "unsupported_language"}

    page = pages.find_by_wiki_page_id(lang, wiki_page_id) if wiki_page_id else None
    # A page id is authoritative, but title is still useful for older skins
    # that report a transient/missing id while navigating.
    if page is None and title:
        page = pages.find_by_title(lang, title)
    if page is None:
        return {"tracked": False, "reason": "not_imported"}

    cps = pages.counterparts(page["id"])
    if not cps:
        return {"tracked": False, "reason": "no_counterparts"}

    view = diff.build_comparison(page["id"], with_reviews=False, page=page, cps=cps)
    s = view["summary"]
    has_claims = bool(query_one("SELECT 1 AS ok FROM claims WHERE article_id = %s LIMIT 1",
                                (page["id"],)))
    if live_revision_id is None:
        freshness = "unknown"
    elif page["revision_id"] == live_revision_id:
        freshness = "current"
    else:
        freshness = "stale"

    return {
        "tracked": True,
        "page": page,
        "counterparts": cps,
        "analysed_revision_id": page["revision_id"],
        "live_revision_id": live_revision_id,
        "freshness": freshness,
        "analysis_status": "ready" if has_claims else "not_ready",
        "dataset_key": f"{page['id']}:{page['revision_id'] or 'unknown'}",
        "conflicts": s["conflict"],
        "possible_conflicts": s.get("possible_conflict", 0),
        "missing_here": s["missing_here"],
        "only_here": s["only_here"],
        "possible_matches": s.get("possible_match", 0),
        "agree": s["agree"],
    }


def _extension_page(lang: str, wiki_page_id: Optional[int], title: Optional[str]):
    """Resolve an extension request without treating ordinary misses as errors."""
    if lang not in pages.LANG_ORDER:
        return None, "unsupported_language"
    page = pages.find_by_wiki_page_id(lang, wiki_page_id) if wiki_page_id else None
    if page is None and title:
        page = pages.find_by_title(lang, title)
    if page is None:
        return None, "not_imported"
    if not pages.counterparts(page["id"]):
        return None, "no_counterparts"
    return page, None


def _annotation_claim(claim: dict) -> dict:
    """The small, stable claim representation consumed by the extension."""
    return {
        "id": claim["id"],
        "lang": claim["lang"],
        "text": claim.get("statement_text") or "",
        "context": claim.get("context_text") or "",
        "value": claim.get("value_text") or claim.get("value_normalized") or "",
        "kind": claim.get("kind") or "factoid",
        "predicate": claim.get("predicate") or "claim",
        "revision_id": claim.get("revision_id"),
        "revision_url": claim.get("url"),
    }


def _annotation_link(link: dict, claim_ids: set[int]) -> dict:
    """Receipt information only; no scoring/recomputation happens on visits."""
    return {
        "id": link["id"],
        "verdict": link.get("verdict"),
        "evidence": link.get("evidence") or "",
        "rationale": link.get("rationale") or "",
        "confidence": link.get("candidate_confidence"),
        "verdict_confidence": link.get("verdict_confidence"),
        "similarity": link.get("similarity"),
        "local": link.get("claim_a") in claim_ids or link.get("claim_b") in claim_ids,
    }


def _annotation_payload(view: dict) -> tuple[list, list]:
    """Project comparison rows to local marks and overlay-only missing rows."""
    annotations, missing_here = [], []
    # Every row that has at least one local claim is annotatable so the overlay
    # can mark all claims by type, including verified ones. "missing_here" rows
    # have no local claim and stay in the overlay-only list below.
    annotatable = {"conflict", "possible_conflict", "only_here", "possible_match", "agree"}
    for row in view["rows"]:
        status = row["status"]
        counterparts = [_annotation_claim(c) for claims in row["there"].values() for c in claims]
        if status == "missing_here":
            missing_here.append({
                "row_key": row["key"], "status": status,
                "kind": row.get("kind") or "factoid",
                "predicate": row.get("predicate") or "claim",
                "counterparts": counterparts,
                "links": [_annotation_link(link, set()) for link in row["links"]],
            })
            continue
        if status not in annotatable:
            continue
        for local in row["here"]:
            local_id = local["id"]
            links = [link for link in row["links"]
                     if link.get("claim_a") == local_id or link.get("claim_b") == local_id]
            annotations.append({
                "row_key": row["key"],
                "status": status,
                "kind": local.get("kind") or row.get("kind") or "factoid",
                "claim": _annotation_claim(local),
                "counterparts": counterparts,
                "links": [_annotation_link(link, {local_id}) for link in links],
                # The web app's hash route is deliberately represented as data
                # so a separately deployed extension need not know its host.
                "focus": {"lang": view["page"]["lang"], "title": view["page"]["title"],
                          "row_key": row["key"]},
            })
    return annotations, missing_here


@app.get("/api/extension/annotations")
def extension_annotations(
    lang: str,
    wiki_page_id: Optional[int] = Query(None, ge=1),
    title: Optional[str] = Query(None, min_length=1, max_length=512),
    live_revision_id: Optional[int] = Query(None, ge=1),
):
    """Return already-computed inline findings for one exact live revision.

    This is intentionally a read-only projection of ``diff.build_comparison``.
    It never aligns claims or invokes a model while somebody reads Wikipedia.
    """
    page, reason = _extension_page(lang, wiki_page_id, title)
    if page is None:
        return {"tracked": False, "reason": reason, "annotations": [], "missing_here": []}

    has_claims = bool(query_one("SELECT 1 AS ok FROM claims WHERE article_id = %s LIMIT 1",
                                (page["id"],)))
    freshness = ("unknown" if live_revision_id is None else
                 "current" if page["revision_id"] == live_revision_id else "stale")
    ready = has_claims
    base = {
        "tracked": True,
        "page": page,
        "analysed_revision_id": page["revision_id"],
        "live_revision_id": live_revision_id,
        "freshness": freshness,
        "analysis_status": "ready" if ready else "not_ready",
        "annotations": [],
        "missing_here": [],
    }
    # Exact snapshot equality is a safety boundary: never make marks on a
    # changed page or one whose live revision cannot be established.
    if freshness != "current" or not ready:
        return base

    view = diff.build_comparison(page["id"], with_reviews=False, page=page,
                                 cps=pages.counterparts(page["id"]))
    annotations, missing_here = _annotation_payload(view)
    return {**base, "annotations": annotations, "missing_here": missing_here}


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
    override: bool = False  # Q1/Q4: let the R1 consensus decide the verdict


@app.post("/api/reviews")
def create_review(r: Review):
    if r.decision not in ("agree", "disagree", "fix"):
        raise HTTPException(422, "decision must be agree | disagree | fix")
    link = query_one(
        """SELECT cl.id, c.article_id AS page_id
           FROM claim_links cl
           JOIN claims c ON c.id = cl.claim_a
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
    # Q1/Q4 (assumptions.md): a single review never sets the verdict. With
    # override=true the R1 consensus (consensus.py) is evaluated: once enough
    # distinct reviewers agree, the decided verdict is written with
    # verdict_source='human'; if later votes break the consensus, the machine
    # verdict comes back. Then the page of claim_a is re-checked as before.
    override_result = consensus.enforce(r.claim_link_id) if r.override else None
    result = recheck.recheck_page(link["page_id"], [r.claim_link_id])
    return {"ok": True, "review_id": review_id, "override": override_result,
            "consensus": consensus.tally(r.claim_link_id), "recheck": result}


@app.get("/api/links/{link_id}/consensus")
def link_consensus(link_id: int):
    """R1 tally for one link: votes pinned to the verdict version they were cast on."""
    if not query_one("SELECT id FROM claim_links WHERE id = %s", (link_id,)):
        raise HTTPException(404, "claim link not found")
    return consensus.tally(link_id)


class Resolution(BaseModel):
    author: str
    correct_text: str  # the correct version, in English
    source_url: Optional[str] = None  # reliable non-Wikipedia source


@app.post("/api/links/{link_id}/resolution")
def create_resolution(link_id: int, r: Resolution):
    """Only for a conflict confirmed by reviewers (contradicted, source human).
    Stores the English text and drafts it, translated, for every page of the
    comparison set. Export-only: nothing is sent to Wikipedia."""
    try:
        return resolutions.create(link_id, r.author, r.correct_text, r.source_url)
    except resolutions.ResolutionError as e:
        raise HTTPException(e.status, e.message)


@app.get("/api/links/{link_id}/resolution")
def get_resolution(link_id: int):
    if not query_one("SELECT id FROM claim_links WHERE id = %s", (link_id,)):
        raise HTTPException(404, "claim link not found")
    return resolutions.latest(link_id) or {"resolution": None, "drafts": []}


@app.get("/api/reviews")
def list_reviews(link_id: int):
    return query(
        "SELECT id, claim_link_id, reviewer, decision, note, created_at "
        "FROM reviews WHERE claim_link_id = %s ORDER BY created_at DESC, id DESC",
        (link_id,),
    )


@app.post("/api/suggestions/{suggestion_id}/export")
def export_suggestion(suggestion_id: int):
    """Mark a draft exported (SVT row), then re-check with focus on its link."""
    s = patches.get_suggestion(suggestion_id)
    if not s:
        raise HTTPException(404, "suggestion not found")
    patches.mark_exported(suggestion_id)
    focus = [s["claim_link_id"]] if s["claim_link_id"] is not None else []
    result = recheck.recheck_page(s["page_id"], focus)
    return {"ok": True, "suggestion_id": suggestion_id, "recheck": result}


@app.get("/api/qa")
def qa(page: int, q: str = Query(min_length=2), lang: Optional[str] = None):
    """Grounded Q&A over the comparison set of `page` (a page id)."""
    _page_or_404(page)
    return rag.answer(page, q, lang)


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
    return pages.review_queue(limit, offset, status, lang, reviewer)
