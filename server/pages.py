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
# sit on counterpart pages (page_links). `page` = the side being counted, `here`
# = that side's claim. Only supported/contradicted links connect claims (same
# rule as diff.py): unmatched, unverifiable and definition_mismatch do not.
_ENDPOINTS = """
SELECT ca.article_id AS page, cb.article_id AS other, cl.claim_a AS here, cl.verdict
FROM claim_links cl
JOIN claims ca ON ca.id = cl.claim_a
JOIN claims cb ON cb.id = cl.claim_b
WHERE cl.verdict IN ('supported', 'contradicted'){fa}
UNION ALL
SELECT cb.article_id, ca.article_id, cl.claim_b, cl.verdict
FROM claim_links cl
JOIN claims ca ON ca.id = cl.claim_a
JOIN claims cb ON cb.id = cl.claim_b
WHERE cl.verdict IN ('supported', 'contradicted'){fb}
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
    """Pages with >=1 counterpart, most conflicted first: one aggregation query.
    conflicts = claims on the page with a contradicted link to a counterpart."""
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


def page_stats(page_ids) -> dict:
    """Approximate per-page status counts via SQL aggregation (claim level, not
    the connected-component rows of the comparison view):
      conflict     claims here with a contradicted link to a counterpart
      agree        claims here with a supported link and no contradicted one
      only_here    claims here with no graded link to any counterpart
      missing_here counterpart claims with no graded link to a claim here
    """
    page_ids = list(page_ids)
    out = {pid: {"conflicts": 0, "missing_here": 0, "only_here": 0, "agree": 0}
           for pid in page_ids}
    if not page_ids:
        return out
    frag, params = in_list(page_ids)
    for r in query(
        f"""SELECT pc.page, SUM(pc.has_c) AS conflicts,
                   SUM(pc.has_s AND NOT pc.has_c) AS agree,
                   COUNT(*) AS linked
            FROM ({_per_claim_sql(frag)}) pc
            GROUP BY pc.page""",
        params + params,
    ):
        o = out[r["page"]]
        o["conflicts"] = int(r["conflicts"] or 0)
        o["agree"] = int(r["agree"] or 0)
        o["_linked"] = int(r["linked"] or 0)
    for r in query(
        f"""SELECT article_id AS page, COUNT(*) AS n FROM claims
            WHERE article_id IN {frag} GROUP BY article_id""",
        params,
    ):
        o = out[r["page"]]
        o["only_here"] = max(0, int(r["n"]) - o.get("_linked", 0))
    # counterpart claims with no graded link to any claim on page X
    for r in query(
        f"""SELECT pl.page_a AS page, COUNT(*) AS n
            FROM page_links pl
            JOIN claims c ON c.article_id = pl.page_b
            WHERE pl.page_a IN {frag} AND pl.page_b <> pl.page_a
              AND NOT EXISTS (
                SELECT 1 FROM claim_links cl JOIN claims h ON h.id = cl.claim_b
                WHERE cl.claim_a = c.id AND cl.verdict IN ('supported', 'contradicted')
                  AND h.article_id = pl.page_a)
              AND NOT EXISTS (
                SELECT 1 FROM claim_links cl JOIN claims h ON h.id = cl.claim_a
                WHERE cl.claim_b = c.id AND cl.verdict IN ('supported', 'contradicted')
                  AND h.article_id = pl.page_a)
            GROUP BY pl.page_a""",
        params,
    ):
        out[r["page"]]["missing_here"] = int(r["n"])
    for o in out.values():
        o.pop("_linked", None)
    return out


def conflict_counts(page_ids) -> dict:
    """{page_id: conflicts} (same definition as page_stats), one query."""
    page_ids = list(page_ids)
    out = {pid: 0 for pid in page_ids}
    if not page_ids:
        return out
    frag, params = in_list(page_ids)
    for r in query(
        f"""SELECT pc.page, SUM(pc.has_c) AS conflicts
            FROM ({_per_claim_sql(frag)}) pc GROUP BY pc.page""",
        params + params,
    ):
        out[r["page"]] = int(r["conflicts"] or 0)
    return out
