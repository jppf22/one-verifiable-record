"""Cross-language claim alignment via MariaDB vector search.

Page-centric: the claims of page X are matched against the claims of each of
its counterpart pages (page_links). Matching is one SQL statement per
(claim, counterpart page): nearest claims by VEC_DISTANCE_COSINE over the
claim_embeddings vectors. No vectors leave the database; MariaDB is the
matching engine.

To prevent uncorrelated counterparts, we use multi-stage filtering:
1. Predicate guard in SQL: When both claims have predicates, they must match (V-S2)
2. Distance threshold: Cosine distance must be within limit
3. Similarity threshold: Similarity must be above minimum

This ensures that claims are only aligned if they are about the same fact.
"""
from config import settings
from db import execute, get_conn, placeholders, query
from pages import comparison_set

def _build_nearest_sql(other_page: int, claim_a_id: int, k: int) -> tuple:
    """Build the NEAREST_SQL query with filtering conditions.
    
    Returns (sql, params) tuple.
    """
    predicate_cond = """
    AND (
        (c1.predicate IS NULL AND c2.predicate IS NULL) OR
        (c1.predicate IS NOT NULL AND c2.predicate IS NOT NULL AND
         (c1.predicate = c2.predicate OR
          (c1.predicate_entity IS NOT NULL AND c2.predicate_entity IS NOT NULL
           AND c1.predicate_entity = c2.predicate_entity)))
    )"""
    
    subject_cond = ""
    if settings.strict_subject_filter:
        subject_cond = """
        AND (
            (c1.subject_entity IS NULL AND c2.subject_entity IS NULL) OR
            (c1.subject_entity IS NOT NULL AND c2.subject_entity IS NOT NULL
             AND c1.subject_entity = c2.subject_entity)
        )"""
    
    sql = f"""
    SELECT c2.id AS claim_b,
           VEC_DISTANCE_COSINE(e2.embedding, e1.embedding) AS dist
    FROM claims c1
    JOIN claim_embeddings e1 ON e1.claim_id = c1.id
    JOIN claims c2 ON c2.article_id = %s
    JOIN claim_embeddings e2 ON e2.claim_id = c2.id
    WHERE c1.id = %s
      {predicate_cond}
      {subject_cond}
    ORDER BY dist ASC
    LIMIT %s
    """
    return sql, (other_page, claim_a_id, k)


def align_page(page_id: int, k: int | None = None, max_distance: float | None = None):
    """Store candidate links between claims of this page and claims of its
    counterpart pages. Candidates start as 'unmatched'; verdicts.py grades them.

    Uses multi-stage filtering to prevent uncorrelated counterparts:
    1. Predicate guard in SQL: Both claims must have matching predicates (if present)
    2. Distance threshold: Cosine distance must be within limit
    3. Similarity threshold: Similarity must be above minimum

    Returns {"claims": n, "counterparts": p, "candidate_links": m}.
    """
    k = k or settings.nearest_k
    max_distance = settings.match_distance if max_distance is None else max_distance
    min_similarity = settings.min_similarity
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
                claim_a_id = row["id"]
                
                for other in others:
                    sql, params = _build_nearest_sql(other, claim_a_id, k)
                    cur.execute(sql, params)
                    for claim_b, dist in cur.fetchall():
                        if dist is None or dist > max_distance:
                            continue
                        
                        # Apply minimum similarity threshold
                        similarity = 1.0 - dist
                        if similarity < min_similarity:
                            continue
                        
                        cur.execute(
                            """INSERT IGNORE INTO claim_links
                               (claim_a, claim_b, similarity, candidate_confidence,
                                verdict, verdict_source)
                               VALUES (%s, %s, %s, %s, 'unmatched', 'deterministic')""",
                            # each pair once, canonical (lower id, higher id) order,
                            # so a->b and b->a don't become two links
                            (min(claim_a_id, claim_b), max(claim_a_id, claim_b),
                             similarity, similarity),
                        )
                        linked += cur.rowcount
            conn.commit()
    return {"claims": len(claims), "counterparts": len(others), "candidate_links": linked}


def clean_invalid_links(page_id: int | None = None) -> int:
    """Remove claim_links that violate predicate matching constraints.
    
    This cleans up links created before predicate filtering was implemented,
    where claims with mismatched predicates were incorrectly aligned.
    
    Args:
        page_id: If provided, only clean links for this page. Otherwise clean all.
    
    Returns:
        Number of links removed.
    """
    where = ""
    params = []
    
    if page_id is not None:
        where = """
            AND (ca.article_id = %s OR cb.article_id = %s)
        """
        params = [page_id, page_id]
    
    # Find links where both claims have predicates but they don't match
    invalid_links = query(
        f"""
        SELECT cl.id
        FROM claim_links cl
        JOIN claims ca ON ca.id = cl.claim_a
        JOIN claims cb ON cb.id = cl.claim_b
        WHERE cl.verdict = 'unmatched'
          AND ca.predicate IS NOT NULL
          AND cb.predicate IS NOT NULL
          AND ca.predicate <> cb.predicate
          AND (ca.predicate_entity IS NULL OR cb.predicate_entity IS NULL
               OR ca.predicate_entity <> cb.predicate_entity)
          {where}
        """,
        params
    )
    
    if invalid_links:
        link_ids = [l["id"] for l in invalid_links]
        execute(
            f"DELETE FROM claim_links WHERE id IN ({placeholders(link_ids)})",
            tuple(link_ids)
        )
        return len(link_ids)
    
    return 0
