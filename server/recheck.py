"""Re-check an article (Wikidata entity) after a review or a patch export.

A human review never overwrites the machine verdict. Instead the pipeline runs
again against what is currently in MariaDB and reports whether each conflict
*resurfaces* (contradicted before and still contradicted after).

Cheap by design: a graded link is only regraded when one of its two claims has
a system-versioned row newer than the link's ``computed_at``. That is detected
with ``FOR SYSTEM_TIME ALL`` / ``ROW_START`` on ``claims``, so the database's own
version history decides what needs another LLM call. Focus links are always
regraded.
"""
from datetime import datetime

import alignment
import verdicts
from db import query, query_one
from diff import build_merge_view, status_counts

# Graded links of this entity whose claims changed after the verdict was
# computed. computed_at is a second-precision TIMESTAMP while ROW_START has
# microseconds, so we compare against the next whole second; otherwise a claim
# written in the same second as the link would look "newer" forever.
STALE_LINKS_SQL = """
SELECT cl.id, MAX(h.ROW_START) AS claim_changed_at
FROM claim_links cl
JOIN claims c ON c.id = cl.claim_a
JOIN articles a ON a.id = c.article_id
JOIN claims other_c ON other_c.id = cl.claim_b
JOIN articles other_a ON other_a.id = other_c.article_id
JOIN claims FOR SYSTEM_TIME ALL AS h
     ON h.id IN (cl.claim_a, cl.claim_b)
    AND h.ROW_START >= cl.computed_at + INTERVAL 1 SECOND
WHERE a.wikidata_qid = %s AND cl.verdict <> 'unmatched'
  AND c.revision_id <=> a.revision_id
  AND other_c.revision_id <=> other_a.revision_id
GROUP BY cl.id
"""


def _link_verdicts(qid: str) -> dict:
    rows = query(
        """SELECT cl.id, cl.verdict
           FROM claim_links cl
           JOIN claims c ON c.id = cl.claim_a
           JOIN articles a ON a.id = c.article_id
           JOIN claims other_c ON other_c.id = cl.claim_b
           JOIN articles other_a ON other_a.id = other_c.article_id
           WHERE a.wikidata_qid = %s
             AND c.revision_id <=> a.revision_id
             AND other_c.revision_id <=> other_a.revision_id""",
        (qid,),
    )
    return {r["id"]: r["verdict"] for r in rows}


def _summary(qid: str) -> dict:
    return status_counts(build_merge_view(qid, with_reviews=False)["clusters"])


def _now_iso() -> str:
    row = query_one("SELECT NOW() AS t")
    t = row["t"] if row else None
    if isinstance(t, datetime):
        return t.isoformat(timespec="seconds")
    return datetime.now().isoformat(timespec="seconds")


def recheck_article(qid: str, focus_link_ids=None) -> dict:
    focus = list(dict.fromkeys(int(i) for i in (focus_link_ids or [])))

    before_summary = _summary(qid)
    before_verdicts = _link_verdicts(qid)

    # 1. vector alignment: new cross-language candidate links ('unmatched')
    new_links = alignment.align_article(qid)["candidate_links"]

    # 2. grade every 'unmatched' link (new candidates + never graded)
    results: dict = {r["id"]: r for r in verdicts.grade_unmatched(qid)}

    # 3. regrade graded links whose claims have a newer SVT row, plus focus links
    stale = [r["id"] for r in query(STALE_LINKS_SQL, (qid,))]
    to_regrade = [i for i in dict.fromkeys(stale + focus) if i not in results]
    pairs = verdicts.fetch_pairs_by_ids(to_regrade)
    for r in verdicts.grade_links(pairs, touch_ids=set(stale)):
        results[r["id"]] = r

    after_summary = _summary(qid)

    links = []
    for link_id in dict.fromkeys(focus + sorted(results)):
        r = results.get(link_id)
        if r is None:
            continue  # focus id that doesn't exist (nothing to report)
        if link_id not in focus and not r["changed"]:
            continue
        # newly aligned links didn't exist before the re-check -> before = null
        before = before_verdicts.get(link_id)
        after = r["after"]
        links.append(
            {
                "id": link_id,
                "before": before,
                "after": after,
                "changed": before != after,
                "resurfaced": before == "contradicted" and after == "contradicted",
                "regraded": True,
            }
        )

    return {
        "qid": qid,
        "checked_at": _now_iso(),
        "before": before_summary,
        "after": after_summary,
        "new_links": new_links,
        "links": links,
    }
