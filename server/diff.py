"""Diff engine: turn claim_links into a git-diff-style merged view.

Clusters are connected components over claim_links. Classification:
  conflict — at least one contradicted link
  agree    — all languages covered and all links supported
  gap      — present in only a subset of the entity's languages

Unlinked claims are singleton clusters (gaps in the language they appear in).
"""
from db import query


def build_merge_view(qid: str) -> dict:
    articles = query(
        """SELECT id, lang, title, wikidata_qid, revision_id
           FROM articles WHERE wikidata_qid = %s ORDER BY lang""",
        (qid,),
    )
    if not articles:
        return {"qid": qid, "articles": [], "clusters": []}

    claims = query(
        """SELECT c.id, c.article_id, c.kind, c.subject, c.predicate, c.value_text,
                  c.statement_text, c.revision_id, a.lang
           FROM claims c
           JOIN articles a ON a.id = c.article_id
           WHERE a.wikidata_qid = %s""",
        (qid,),
    )
    links = query(
        """SELECT cl.claim_a, cl.claim_b, cl.verdict, cl.similarity,
                  cl.evidence, cl.rationale
           FROM claim_links cl
           JOIN claims c ON c.id = cl.claim_a
           JOIN articles a ON a.id = c.article_id
           WHERE a.wikidata_qid = %s""",
        (qid,),
    )

    langs = {a["lang"] for a in articles}

    # union-find over claims
    parent = {c["id"]: c["id"] for c in claims}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    link_by_pair = {}
    for link in links:
        link_by_pair[(link["claim_a"], link["claim_b"])] = link
        ra, rb = find(link["claim_a"]), find(link["claim_b"])
        if ra != rb:
            parent[ra] = rb

    by_id = {c["id"]: c for c in claims}
    clusters: dict = {}
    for cid in parent:
        clusters.setdefault(find(cid), []).append(cid)

    out = []
    for _root, members in clusters.items():
        member_set = set(members)
        cluster_links = [
            link
            for (la, lb), link in link_by_pair.items()
            if la in member_set or lb in member_set
        ]
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
                "status": status,
                "claims": [by_id[m] for m in sorted(members)],
                "links": cluster_links,
                "langs_present": sorted(present),
            }
        )

    order = {"conflict": 0, "gap": 1, "agree": 2}
    out.sort(key=lambda c: (order[c["status"]], -len(c["claims"])))
    return {"qid": qid, "articles": articles, "clusters": out}
