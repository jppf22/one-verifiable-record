"""Pages and comparison sets (contract: docs/page-contract.md).

The unit of comparison is a PAGE (one `articles` row: lang + page_id + revision).
Its counterparts are the other-language pages linked in `page_links` (stored in
both directions). The **comparison set** of page X is X plus its counterparts;
every layer (alignment, verdicts, diff, re-check, suggestions, Q&A) runs on it.
`page_links` is the only source of truth; `wikidata_qid` is never used here.
"""
from urllib.parse import quote

from config import settings
from db import placeholders, query, query_one

LANG_ORDER = {"fi": 0, "sv": 1, "en": 2}

PAGE_COLS = "a.id, a.lang, a.title, a.revision_id"


def revision_url(lang, revision_id):
    """Permalink to an exact revision (or None)."""
    if not lang or revision_id is None:
        return None
    return f"https://{lang}.wikipedia.org/w/index.php?oldid={revision_id}"


def page_obj(row: dict) -> dict:
    title = row["title"]
    return {
        "id": row["id"],
        "lang": row["lang"],
        "title": title,
        "revision_id": row["revision_id"],
        "url": revision_url(row["lang"], row["revision_id"]),
        "wiki_url": f"https://{row['lang']}.wikipedia.org/wiki/"
        + quote(title.replace(" ", "_"), safe="/:(),'!*-_.~"),
    }


def lang_key(page: dict):
    return (LANG_ORDER.get(page["lang"], 99), page["lang"], page["id"])


def get_page(page_id: int):
    row = query_one(f"SELECT {PAGE_COLS} FROM articles a WHERE a.id = %s", (page_id,))
    return page_obj(row) if row else None


def find_by_title(lang: str, title: str):
    """Exact title, then with underscores read as spaces (URL form)."""
    sql = f"SELECT {PAGE_COLS} FROM articles a WHERE a.lang = %s AND a.title = %s LIMIT 1"
    row = query_one(sql, (lang, title))
    if not row and "_" in title:
        row = query_one(sql, (lang, title.replace("_", " ")))
    return page_obj(row) if row else None


def find_by_wiki_page_id(lang: str, wiki_page_id: int):
    """Resolve a live MediaWiki page identity to its imported page record.

    Browser clients should use this in preference to title matching: titles can
    redirect or be renamed, while a page id is stable for the lifetime of a
    MediaWiki page.
    """
    row = query_one(
        f"SELECT {PAGE_COLS} FROM articles a WHERE a.lang = %s AND a.page_id = %s LIMIT 1",
        (lang, wiki_page_id),
    )
    return page_obj(row) if row else None


def counterparts(page_id: int) -> list:
    """Counterpart pages of X, ordered fi, sv, en (then other languages)."""
    rows = query(
        f"""SELECT {PAGE_COLS}
            FROM page_links pl JOIN articles a ON a.id = pl.page_b
            WHERE pl.page_a = %s AND pl.page_b <> pl.page_a
              AND pl.confidence >= %s""",
        (page_id, settings.counterpart_min_confidence),
    )
    return sorted((page_obj(r) for r in rows), key=lang_key)


def counterparts_many(page_ids) -> dict:
    """{page_id: [counterpart page, ...]} for many pages in one query."""
    page_ids = list(page_ids)
    out: dict = {pid: [] for pid in page_ids}
    if not page_ids:
        return out
    rows = query(
        f"""SELECT pl.page_a AS of_page, {PAGE_COLS}
            FROM page_links pl JOIN articles a ON a.id = pl.page_b
            WHERE pl.page_a IN ({placeholders(page_ids)}) AND pl.page_b <> pl.page_a
              AND pl.confidence >= %s""",
        (*page_ids, settings.counterpart_min_confidence),
    )
    for r in rows:
        out[r["of_page"]].append(page_obj(r))
    for pid in out:
        out[pid].sort(key=lang_key)
    return out


def comparison_set(page_id: int) -> list:
    """[X, counterpart ids...]: X first. The single helper every layer uses."""
    return [page_id] + [p["id"] for p in counterparts(page_id)]


def in_list(ids) -> tuple:
    """(sql_fragment, params) for `col IN (...)` over a non-empty id list."""
    ids = list(ids)
    return f"({placeholders(ids)})", tuple(ids)


# Claim-link endpoints seen from each side, restricted to links whose two claims
# sit on counterpart pages (page_links). Mirrors diff.connects() so SQL candidate
# ranking agrees with the comparison view.
_CONNECT_WHERE = """(
    cl.verdict IN ('supported', 'contradicted')
    OR (cl.verdict_source <> 'deterministic-fallback'
        AND (cl.verdict IN ('possible_match', 'possible_conflict')
             OR (cl.verdict = 'unverifiable' AND cl.relation = 'same')))
)"""

_ENDPOINTS = f"""
SELECT ca.article_id AS page, cb.article_id AS other, cl.claim_a AS here, cl.verdict
FROM claim_links cl
JOIN claims ca ON ca.id = cl.claim_a
JOIN claims cb ON cb.id = cl.claim_b
WHERE {_CONNECT_WHERE}{{fa}}
UNION ALL
SELECT cb.article_id, ca.article_id, cl.claim_b, cl.verdict
FROM claim_links cl
JOIN claims ca ON ca.id = cl.claim_a
JOIN claims cb ON cb.id = cl.claim_b
WHERE {_CONNECT_WHERE}{{fb}}
"""


def _per_claim_sql(page_filter: str) -> str:
    fa = f" AND ca.article_id IN {page_filter}" if page_filter else ""
    fb = f" AND cb.article_id IN {page_filter}" if page_filter else ""
    return f"""
SELECT e.page, e.here,
       MAX(e.verdict = 'contradicted') AS has_c,
       MAX(e.verdict = 'supported') AS has_s,
       COUNT(*) AS n_links
FROM ({_ENDPOINTS.format(fa=fa, fb=fb)}) e
JOIN page_links pl ON pl.page_a = e.page AND pl.page_b = e.other
GROUP BY e.page, e.here
"""


def ranked_pages(limit: int) -> list:
    """Pages with >=1 counterpart, most conflicted and richly linked first."""
    rows = query(
        f"""SELECT {PAGE_COLS},
                   COALESCE(s.conflicts, 0) AS conflicts,
                   COALESCE(s.graded, 0) AS graded
            FROM articles a
            JOIN (SELECT DISTINCT page_a FROM page_links) p ON p.page_a = a.id
            LEFT JOIN (SELECT pc.page, SUM(pc.has_c) AS conflicts,
                              SUM(pc.n_links) AS graded
                       FROM ({_per_claim_sql('')}) pc
                       GROUP BY pc.page) s ON s.page = a.id
            ORDER BY conflicts DESC, graded DESC, a.title, a.lang
            LIMIT %s""",
        (limit,),
    )
    return rows


def page_stats(page_ids, cps: dict | None = None) -> dict:
    """Exact per-page comparison row counts, computed with two bulk SQL queries
    and diff.build_rows() so the landing list, search results, badge and
    comparison view always report identical numbers."""
    from diff import _fetch_claims, _fetch_links, build_rows, status_counts

    page_ids = list(page_ids)
    out = {
        pid: {
            "conflicts": 0,
            "possible_conflicts": 0,
            "missing_here": 0,
            "only_here": 0,
            "possible_matches": 0,
            "agree": 0,
        }
        for pid in page_ids
    }
    if not page_ids:
        return out

    cps = counterparts_many(page_ids) if cps is None else cps
    all_ids = sorted({pid for pid in page_ids} | {p["id"] for pid in page_ids for p in cps.get(pid, ())})
    if not all_ids:
        return out

    all_claims = _fetch_claims(all_ids)
    all_links = _fetch_links(all_ids)

    claims_by_page: dict[int, list] = {}
    claim_page: dict[int, int] = {}
    for c in all_claims:
        claims_by_page.setdefault(c["page_id"], []).append(c)
        claim_page[c["id"]] = c["page_id"]

    links_by_page: dict[int, list] = {}
    for l in all_links:
        pa = claim_page.get(l["claim_a"])
        pb = claim_page.get(l["claim_b"])
        if pa is not None and pb is not None:
            links_by_page.setdefault(pa, []).append((pb, l))

    for pid in page_ids:
        cp_list = cps.get(pid, ())
        if not cp_list:
            continue
        set_ids = [pid] + [p["id"] for p in cp_list]
        set_lookup = set(set_ids)
        set_claims = [c for sid in set_ids for c in claims_by_page.get(sid, ())]
        set_links = [
            l for sid in set_ids
            for other_pid, l in links_by_page.get(sid, ())
            if other_pid in set_lookup
        ]
        sc = status_counts(build_rows(pid, set_claims, set_links))
        out[pid] = {
            "conflicts": sc["conflict"],
            "possible_conflicts": sc["possible_conflict"],
            "missing_here": sc["missing_here"],
            "only_here": sc["only_here"],
            "possible_matches": sc["possible_match"],
            "agree": sc["agree"],
        }
    return out


def conflict_counts(page_ids, cps: dict | None = None) -> dict:
    """{page_id: exact conflict row count} matching the comparison view."""
    return {pid: s["conflicts"] for pid, s in page_stats(page_ids, cps=cps).items()}


def review_queue(limit: int = 50, offset: int = 0, status: Optional[str] = None,
                 lang: Optional[str] = None, reviewer: Optional[str] = None) -> dict:
    """Get claim links needing review for the reviewer dashboard.
    
    Returns paginated list of claim links that:
    - Have verdict != 'supported' (need attention)
    - Either have no reviews or have reviews but not enough for consensus
    - Can be filtered by status, language, and reviewer
    
    Sorts by: most conflicts first, then by newest claims, then by similarity score
    """
    from db import query, query_one
    
    # Build WHERE clause
    where_parts = []
    params = []
    
    # Only links that need review (not already settled by human consensus)
    where_parts.append("cl.verdict_source != 'human' OR cl.verdict_source IS NULL")
    
    # Filter by verdict status if provided
    if status and status != "all":
        where_parts.append("cl.verdict = %s")
        params.append(status)
    else:
        # Default: show conflicts and possible conflicts (things that need attention)
        where_parts.append("cl.verdict IN ('contradicted', 'possible_conflict', 'possible_match', 'unverifiable')")
    
    # Join tables
    sql = f"""
        SELECT DISTINCT cl.id as link_id, cl.verdict, cl.similarity, cl.verdict_source,
               cl.computed_at,
               ca.id as claim_a_id, ca.article_id as page_a_id, ca.lang as lang_a,
               ca.statement_text as text_a, ca.value_text as value_a,
               ca.predicate as predicate_a, ca.revision_id as revision_a,
               cb.id as claim_b_id, cb.article_id as page_b_id, cb.lang as lang_b,
               cb.statement_text as text_b, cb.value_text as value_b,
               cb.predicate as predicate_b, cb.revision_id as revision_b,
               pa.id as page_a_article_id, pa.lang as page_a_lang, pa.title as page_a_title,
               pb.id as page_b_article_id, pb.lang as page_b_lang, pb.title as page_b_title,
               COUNT(DISTINCT r.id) as review_count,
               MAX(r.decision = 'agree') as has_agree,
               MAX(r.decision = 'disagree') as has_disagree,
               MAX(r.decision = 'fix') as has_fix
        FROM claim_links cl
        JOIN claims ca ON ca.id = cl.claim_a
        JOIN claims cb ON cb.id = cl.claim_b
        JOIN articles pa ON pa.id = ca.article_id
        JOIN articles pb ON pb.id = cb.article_id
        LEFT JOIN reviews r ON r.claim_link_id = cl.id
    """
    
    # Add WHERE conditions
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    
    # Filter by language if provided
    if lang:
        sql += " AND (ca.lang = %s OR cb.lang = %s)"
        params.extend([lang, lang])
    
    # Filter by reviewer (show links they've already reviewed)
    if reviewer:
        sql += " AND EXISTS (SELECT 1 FROM reviews r2 WHERE r2.claim_link_id = cl.id AND r2.reviewer = %s)"
        params.append(reviewer)
    
    # Group by link
    sql += """
        GROUP BY cl.id, cl.verdict, cl.similarity, cl.verdict_source, cl.computed_at,
                 ca.id, ca.article_id, ca.lang, ca.statement_text, ca.value_text, ca.predicate, ca.revision_id,
                 cb.id, cb.article_id, cb.lang, cb.statement_text, cb.value_text, cb.predicate, cb.revision_id,
                 pa.id, pa.lang, pa.title, pb.id, pb.lang, pb.title
    """
    
    # Order by: conflicts first, then by newest computed_at, then by lowest similarity (most uncertain)
    sql += " ORDER BY "
    if status == "agree" or status == "supported":
        sql += "cl.computed_at DESC, cl.similarity ASC"
    else:
        sql += "(cl.verdict = 'contradicted') DESC, cl.computed_at DESC, cl.similarity ASC"
    
    # Pagination
    sql += " LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    # Count total for pagination
    count_sql = f"""
        SELECT COUNT(DISTINCT cl.id)
        FROM claim_links cl
        JOIN claims ca ON ca.id = cl.claim_a
        JOIN claims cb ON cb.id = cl.claim_b
        JOIN articles pa ON pa.id = ca.article_id
        JOIN articles pb ON pb.id = cb.article_id
    """
    if where_parts:
        count_sql += " WHERE " + " AND ".join(where_parts)
    if lang:
        count_sql += " AND (ca.lang = %s OR cb.lang = %s)"
    if reviewer:
        count_sql += " AND EXISTS (SELECT 1 FROM reviews r2 WHERE r2.claim_link_id = cl.id AND r2.reviewer = %s)"
    
    total = query_one(count_sql, tuple(params[:-2]))
    total_count = total["COUNT(DISTINCT cl.id)"] if total else 0
    
    rows = query(sql, params)
    
    items = []
    for r in rows:
        # Determine which page is "this page" perspective
        # For dashboard, we'll use page_a as the primary
        items.append({
            "link_id": r["link_id"],
            "verdict": r["verdict"],
            "verdict_source": r["verdict_source"],
            "similarity": r["similarity"],
            "computed_at": r["computed_at"],
            "review_count": r["review_count"],
            "has_review": r["review_count"] > 0,
            "claim_a": {
                "id": r["claim_a_id"],
                "page_id": r["page_a_id"],
                "lang": r["lang_a"],
                "text": r["text_a"],
                "value": r["value_a"],
                "predicate": r["predicate_a"],
                "revision_id": r["revision_a"],
            },
            "claim_b": {
                "id": r["claim_b_id"],
                "page_id": r["page_b_id"],
                "lang": r["lang_b"],
                "text": r["text_b"],
                "value": r["value_b"],
                "predicate": r["predicate_b"],
                "revision_id": r["revision_b"],
            },
            "page_a": {
                "id": r["page_a_article_id"],
                "lang": r["page_a_lang"],
                "title": r["page_a_title"],
            },
            "page_b": {
                "id": r["page_b_article_id"],
                "lang": r["page_b_lang"],
                "title": r["page_b_title"],
            },
        })
    
    return {
        "items": items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }
