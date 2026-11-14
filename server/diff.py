"""Diff engine: turn claim_links into a git-diff-style merged view.

Clusters are connected components over claim_links. Classification:
  conflict — at least one contradicted link
  agree    — all languages covered and all links supported
  gap      — present in only a subset of the entity's languages

Unlinked claims are singleton clusters (gaps in the language they appear in).
"""
from db import query

STATUS_ORDER = {"conflict": 0, "gap": 1, "agree": 2}


def wiki_url(lang, revision_id):
    """Permalink to the exact revision a claim was extracted from (or None)."""
    if not lang or revision_id is None:
        return None
    return f"https://{lang}.wikipedia.org/w/index.php?oldid={revision_id}"


def _fetch_articles(qid: str):
    return query(
        """SELECT id, lang, title, wikidata_qid, revision_id, fetched_at
           FROM articles WHERE wikidata_qid = %s ORDER BY lang""",
        (qid,),
    )


def _fetch_claims(qid: str):
    return query(
        """SELECT c.id, c.article_id, c.kind, c.subject, c.predicate, c.value_text,
                  c.statement_text, c.revision_id, a.lang
           FROM claims c
           JOIN articles a ON a.id = c.article_id
           WHERE a.wikidata_qid = %s AND c.revision_id <=> a.revision_id""",
        (qid,),
    )


def _fetch_links(qid: str):
    return query(
        """SELECT cl.id, cl.claim_a, cl.claim_b, cl.verdict, cl.verdict_source,
                  cl.similarity, cl.evidence, cl.rationale, cl.computed_at
           FROM claim_links cl
           JOIN claims c ON c.id = cl.claim_a
           JOIN articles a ON a.id = c.article_id
           JOIN claims other_c ON other_c.id = cl.claim_b
           JOIN articles other_a ON other_a.id = other_c.article_id
           WHERE a.wikidata_qid = %s
             AND c.revision_id <=> a.revision_id
             AND other_c.revision_id <=> other_a.revision_id
           ORDER BY cl.id""",
        (qid,),
    )


def _fetch_latest_reviews(qid: str) -> dict:
    """Latest review per link of this entity, in one query (no N+1)."""
    rows = query(
        """SELECT r.id, r.claim_link_id, r.decision, r.reviewer, r.note, r.created_at
           FROM reviews r
           JOIN (SELECT r2.claim_link_id, MAX(r2.id) AS max_id
                 FROM reviews r2
                 JOIN claim_links cl ON cl.id = r2.claim_link_id
                 JOIN claims c ON c.id = cl.claim_a
                 JOIN articles a ON a.id = c.article_id
                 JOIN claims other_c ON other_c.id = cl.claim_b
                 JOIN articles other_a ON other_a.id = other_c.article_id
                 WHERE a.wikidata_qid = %s
                   AND c.revision_id <=> a.revision_id
                   AND other_c.revision_id <=> other_a.revision_id
                 GROUP BY r2.claim_link_id) latest ON latest.max_id = r.id""",
        (qid,),
    )
    return {r.pop("claim_link_id"): r for r in rows}


def cluster(articles, claims, links) -> list:
    """Pure clustering + classification (no DB access)."""
    langs = {a["lang"] for a in articles}
    by_id = {c["id"]: c for c in claims}

    # union-find over claims
    parent = {c["id"]: c["id"] for c in claims}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    links = [l for l in links if l["claim_a"] in parent and l["claim_b"] in parent]
    for link in links:
        ra, rb = find(link["claim_a"]), find(link["claim_b"])
        if ra != rb:
            parent[ra] = rb

    members_by_root: dict = {}
    for cid in parent:
        members_by_root.setdefault(find(cid), []).append(cid)
    links_by_root: dict = {}
    for link in links:
        links_by_root.setdefault(find(link["claim_a"]), []).append(link)

    out = []
    for root, members in members_by_root.items():
        members.sort()
        cluster_links = links_by_root.get(root, [])
        present = {by_id[m]["lang"] for m in members}
        verdicts = {link["verdict"] for link in cluster_links}
        if "contradicted" in verdicts:
            status = "conflict"
        elif cluster_links and verdicts <= {"supported"} and present == langs:
            status = "agree"
        else:
            status = "gap"
        out.append(
            {
                "key": f"c-{members[0]}",
                "status": status,
                "langs_present": sorted(present),
                "claims": [by_id[m] for m in members],
                "links": cluster_links,
            }
        )

    out.sort(key=lambda c: (STATUS_ORDER[c["status"]], -len(c["claims"]), c["key"]))
    return out


def status_counts(clusters) -> dict:
    counts = {"conflict": 0, "gap": 0, "agree": 0}
    for c in clusters:
        counts[c["status"]] += 1
    return counts


def summarize(clusters) -> dict:
    graded = [l for c in clusters for l in c["links"] if l["verdict"] != "unmatched"]
    return {
        **status_counts(clusters),
        "links_total": len(graded),
        "links_reviewed": sum(1 for l in graded if l.get("review")),
    }


def build_merge_view(qid: str, with_reviews: bool = True) -> dict:
    articles = _fetch_articles(qid)
    if not articles:
        return {"qid": qid, "articles": [], "summary": summarize([]), "clusters": []}

    for a in articles:
        a["url"] = wiki_url(a["lang"], a["revision_id"])
    claims = _fetch_claims(qid)
    for c in claims:
        c["url"] = wiki_url(c["lang"], c["revision_id"])
    links = _fetch_links(qid)
    reviews = _fetch_latest_reviews(qid) if with_reviews and links else {}
    for link in links:
        link["review"] = reviews.get(link["id"])

    clusters = cluster(articles, claims, links)
    return {
        "qid": qid,
        "articles": articles,
        "summary": summarize(clusters),
        "clusters": clusters,
    }
