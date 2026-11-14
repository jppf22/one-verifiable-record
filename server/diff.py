"""Diff engine: the page-centric comparison payload (docs/page-contract.md).

Rows are connected components over claim_links within the comparison set of
page X (X plus its counterpart pages). Only links that say "same fact"
(supported / contradicted) and plausible semantic links
(possible_match / possible_conflict) connect claims. This keeps discovery
candidates visible without presenting them as confirmed truth. Unmatched,
unverifiable and definition_mismatch links remain attached to their local row
as receipts but do not connect claims.

Row status, relative to this page:
  conflict      the row has a claim here and a contradicted link (normally
                here <-> there; a contradiction between two counterparts in a
                row this page takes part in also counts)
  possible_conflict plausible incompatible claims need evidence/review
  possible_match    plausible equivalent claims need evidence/review
  missing_here  counterpart page(s) state it, this page has no claim in the row
  only_here     this page states it, no counterpart claim in the row
  agree         claims here and there, all connecting links supported
"""
from db import query
from pages import counterparts, get_page, in_list, revision_url

STATUSES = ("conflict", "possible_conflict", "missing_here", "only_here", "possible_match", "agree")
STATUS_ORDER = {s: i for i, s in enumerate(STATUSES)}
STRONG_CONNECTING = {"supported", "contradicted"}
WEAK_CONNECTING = {"possible_match", "possible_conflict"}
CONNECTING = STRONG_CONNECTING | WEAK_CONNECTING

# kept for callers that build revision permalinks
wiki_url = revision_url


def _fetch_claims(ids):
    frag, params = in_list(ids)
    return query(
        f"""SELECT c.id, c.article_id AS page_id, a.lang, c.kind, c.subject, c.subject_entity,
                   c.predicate, c.predicate_entity, c.object_entity, c.value_text, c.value_normalized,
                   c.value_canonical, c.qualifiers, c.context_text, c.citation_urls,
                   c.extraction_confidence, c.statement_text, c.revision_id
            FROM claims c
            JOIN articles a ON a.id = c.article_id
            WHERE c.article_id IN {frag}
            ORDER BY c.id""",
        params,
    )


def _fetch_links(ids):
    """Links whose two claims sit on two different pages of the comparison set."""
    frag, params = in_list(ids)
    return query(
        f"""SELECT cl.id, cl.claim_a, cl.claim_b, cl.verdict, cl.relation,
                   cl.candidate_confidence, cl.verdict_confidence, cl.verdict_source,
                   cl.similarity, cl.evidence, cl.rationale, cl.computed_at
            FROM claim_links cl
            JOIN claims ca ON ca.id = cl.claim_a
            JOIN claims cb ON cb.id = cl.claim_b
            WHERE ca.article_id IN {frag} AND cb.article_id IN {frag}
              AND ca.article_id <> cb.article_id
            ORDER BY cl.id""",
        params + params,
    )


def _fetch_latest_reviews(link_ids) -> dict:
    """Latest review per link, in one query (no N+1)."""
    frag, params = in_list(link_ids)
    rows = query(
        f"""SELECT r.id, r.claim_link_id, r.decision, r.reviewer, r.note, r.created_at
            FROM reviews r
            JOIN (SELECT claim_link_id, MAX(id) AS max_id FROM reviews
                  WHERE claim_link_id IN {frag}
                  GROUP BY claim_link_id) latest ON latest.max_id = r.id""",
        params,
    )
    return {r.pop("claim_link_id"): r for r in rows}


def _row_label(claims, field):
    """Most common non-null value of a field among the row's claims (or None)."""
    counts: dict = {}
    for c in claims:
        v = c.get(field)
        if v:
            counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda v: (counts[v], -len(v))) if counts else None


def build_rows(page_id: int, claims, links) -> list:
    """Pure clustering + classification relative to page_id (no DB access)."""
    by_id = {c["id"]: c for c in claims}
    parent = {cid: cid for cid in by_id}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    links = [l for l in links if l["claim_a"] in by_id and l["claim_b"] in by_id]
    for link in links:
        if link["verdict"] in CONNECTING:
            ra, rb = find(link["claim_a"]), find(link["claim_b"])
            if ra != rb:
                parent[ra] = rb

    members_by_root: dict = {}
    for cid in by_id:
        members_by_root.setdefault(find(cid), []).append(cid)

    def is_here(cid):
        return by_id[cid]["page_id"] == page_id

    links_by_root: dict = {}
    for link in links:
        if link["verdict"] in CONNECTING:
            root = find(link["claim_a"])
        else:
            # non-connecting link: show it on the row of its claim on this page
            anchor = link["claim_a"] if is_here(link["claim_a"]) else link["claim_b"]
            root = find(anchor)
        links_by_root.setdefault(root, []).append(link)

    rows = []
    for root, members in members_by_root.items():
        members.sort()
        row_links = links_by_root.get(root, [])
        here = [by_id[m] for m in members if is_here(m)]
        there: dict = {}
        for m in members:
            if not is_here(m):
                there.setdefault(by_id[m]["lang"], []).append(by_id[m])
        connecting = [l for l in row_links if l["verdict"] in CONNECTING]
        if not here:
            status = "missing_here"  # even if counterparts disagree among themselves
        elif any(l["verdict"] == "contradicted" for l in connecting):
            status = "conflict"
        elif any(l["verdict"] == "possible_conflict" for l in connecting):
            status = "possible_conflict"
        elif not there:
            status = "only_here"
        elif any(l["verdict"] == "possible_match" for l in connecting):
            status = "possible_match"
        else:
            status = "agree"
        all_claims = [by_id[m] for m in members]
        rows.append(
            {
                "key": f"r-{members[0]}",
                "status": status,
                "kind": _row_label(all_claims, "kind"),
                "predicate": _row_label(all_claims, "predicate"),
                "here": here,
                "there": there,
                "links": row_links,
            }
        )

    rows.sort(key=lambda r: (STATUS_ORDER[r["status"]],
                             -(len(r["here"]) + sum(map(len, r["there"].values()))),
                             int(r["key"][2:])))
    return rows


def status_counts(rows) -> dict:
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        counts[r["status"]] += 1
    return counts


def summarize(rows) -> dict:
    graded = [l for r in rows for l in r["links"] if l["verdict"] != "unmatched"]
    return {
        **status_counts(rows),
        "links_total": len(graded),
        "links_reviewed": sum(1 for l in graded if l.get("review")),
    }


def build_comparison(page_id: int, with_reviews: bool = True, page=None, cps=None):
    """GET /pages/{id}/compare. Returns None if the page does not exist."""
    page = page or get_page(page_id)
    if not page:
        return None
    cps = counterparts(page_id) if cps is None else cps
    ids = [page_id] + [p["id"] for p in cps]
    if not cps:
        return {"page": page, "counterparts": [], "summary": summarize([]), "rows": []}

    claims = _fetch_claims(ids)
    for c in claims:
        c["url"] = revision_url(c["lang"], c["revision_id"])
    links = _fetch_links(ids)
    reviews = _fetch_latest_reviews([l["id"] for l in links]) if with_reviews and links else {}
    for link in links:
        link["review"] = reviews.get(link["id"])

    rows = build_rows(page_id, claims, links)
    return {"page": page, "counterparts": cps, "summary": summarize(rows), "rows": rows}


def page_summary(page_id: int) -> dict:
    """Status counts of the comparison view (used by re-check)."""
    view = build_comparison(page_id, with_reviews=False)
    return status_counts(view["rows"]) if view else status_counts([])
