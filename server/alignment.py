"""Cross-language claim alignment via MariaDB vector search.

All matching is a single SQL statement: nearest cross-language claims by
VEC_DISTANCE_COSINE over the HNSW-indexed claim embeddings, restricted to the
same Wikidata entity (the same article across editions). No vectors leave the
database; MariaDB is the matching engine.
"""
from config import settings
from db import get_conn, query

NEAREST_SQL = """
SELECT c2.id AS claim_b, c2.statement_text, a2.lang AS other_lang,
       VEC_DISTANCE_COSINE(c2.embedding, c1.embedding) AS dist
FROM claims c1
JOIN articles a1 ON a1.id = c1.article_id
JOIN articles a2 ON a2.wikidata_qid = a1.wikidata_qid AND a2.lang <> a1.lang
JOIN claims c2 ON c2.article_id = a2.id AND c2.embedding IS NOT NULL
WHERE c1.id = %s AND c1.embedding IS NOT NULL
ORDER BY dist ASC
LIMIT %s
"""


def align_article(qid: str, k: int | None = None):
    """Store candidate cross-language links for every claim of this Wikidata
    entity. Candidate links start as 'unmatched'; verdicts.py then grades them.

    Returns {"claims": n, "candidate_links": m}.
    """
    k = k or settings.nearest_k
    claims = query(
        """SELECT c.id FROM claims c
           JOIN articles a ON a.id = c.article_id
           WHERE a.wikidata_qid = %s
           ORDER BY c.id""",
        (qid,),
    )
    linked = 0
    with get_conn() as conn:
        cur = conn.cursor()
        for row in claims:
            cur.execute(NEAREST_SQL, (row["id"], k))
            for claim_b, _text, _lang, dist in cur.fetchall():
                if dist > settings.match_distance:
                    continue
                cur.execute(
                    """INSERT IGNORE INTO claim_links
                       (claim_a, claim_b, similarity, verdict, verdict_source)
                       VALUES (%s, %s, %s, 'unmatched', 'deterministic')""",
                    # store each pair once, in canonical (lower id, higher id)
                    # order, so a->b and b->a don't become two links
                    (min(row["id"], claim_b), max(row["id"], claim_b), 1.0 - dist),
                )
                linked += cur.rowcount
        conn.commit()
    return {"claims": len(claims), "candidate_links": linked}
