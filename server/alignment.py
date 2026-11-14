"""Cross-language claim alignment via MariaDB vector search.

Page-centric: the claims of page X are matched against the claims of each of
its counterpart pages (page_links). Matching is one SQL statement per
(claim, counterpart page): nearest claims by VEC_DISTANCE_COSINE over the
claim_embeddings vectors. No vectors leave the database; MariaDB is the
matching engine. Relation labels are deliberately not a hard filter: an AI
extractor may map equivalent relations differently across languages, and the
semantic verdict layer decides whether they are same, related or different.
"""
from config import settings
from db import get_conn, query
from pages import comparison_set

NEAREST_SQL = """
SELECT c2.id AS claim_b,
       VEC_DISTANCE_COSINE(e2.embedding, e1.embedding) AS dist
FROM claims c1
JOIN claim_embeddings e1 ON e1.claim_id = c1.id
JOIN claims c2 ON c2.article_id = %s
JOIN claim_embeddings e2 ON e2.claim_id = c2.id
WHERE c1.id = %s
ORDER BY dist ASC
LIMIT %s
"""


def align_page(page_id: int, k: int | None = None, max_distance: float | None = None):
    """Store candidate links between claims of this page and claims of its
    counterpart pages. Candidates start as 'unmatched'; verdicts.py grades them.

    Returns {"claims": n, "counterparts": p, "candidate_links": m}.
    """
    k = k or settings.nearest_k
    max_distance = settings.match_distance if max_distance is None else max_distance
    others = comparison_set(page_id)[1:]
    claims = query(
        """SELECT c.id FROM claims c
           JOIN claim_embeddings e ON e.claim_id = c.id
           WHERE c.article_id = %s ORDER BY c.id""",
        (page_id,),
    )
    linked = 0
    if others and claims:
        with get_conn() as conn:
            cur = conn.cursor()
            for row in claims:
                for other in others:
                    cur.execute(NEAREST_SQL, (other, row["id"], k))
                    for claim_b, dist in cur.fetchall():
                        if dist is None or dist > max_distance:
                            continue
                        cur.execute(
                            """INSERT IGNORE INTO claim_links
                               (claim_a, claim_b, similarity, candidate_confidence,
                                verdict, verdict_source)
                               VALUES (%s, %s, %s, %s, 'unmatched', 'deterministic')""",
                            # each pair once, canonical (lower id, higher id) order,
                            # so a->b and b->a don't become two links
                            (min(row["id"], claim_b), max(row["id"], claim_b),
                             1.0 - dist, 1.0 - dist),
                        )
                        linked += cur.rowcount
            conn.commit()
    return {"claims": len(claims), "counterparts": len(others), "candidate_links": linked}
