"""Cross-language claim alignment via MariaDB vector search.

Page-centric: the claims of page X are matched against the claims of each of
its counterpart pages (page_links). Matching is one SQL statement per
(claim, counterpart page): nearest claims by VEC_DISTANCE_COSINE over the
claim_embeddings vectors. No vectors leave the database; MariaDB is the
matching engine.

To prevent uncorrelated counterparts, we use multi-stage filtering:
1. Predicate guard in SQL: When both claims have predicates, they must name the same
   relation (V-S2). Aliases count as the same (predicates.py: date_of_birth = birth_date)
2. Distance threshold: Cosine distance must be within limit
3. Similarity threshold: Similarity must be above minimum

This ensures that claims are only aligned if they are about the same fact.
"""
from config import settings
from db import execute, get_conn, placeholders, query
from pages import comparison_set
from predicates import ALIAS, aliases, canonical, same


def _incompatible_known_predicates(pa, pb) -> bool:
    """True when both predicates map to known, distinct canonical slots
    (e.g. birth_date vs death_date), which must never be paired even at high
    vector similarity. Dynamic/open predicates pass through to gate.py."""
    if not pa or not pb:
        return False
    p1, p2 = str(pa).strip().lower(), str(pb).strip().lower()
    if p1 in ALIAS and p2 in ALIAS:
        return canonical(p1) != canonical(p2)
    return False


def _build_nearest_sql(other_page: int, claim_a_id: int, k: int, predicate=None) -> tuple:
    """Build the NEAREST_SQL query with predicate filtering conditions.

    ``predicate`` is claim A's predicate; every alias of it matches (predicates.py).
    Returns (sql, params) tuple.
    """
    names = aliases(predicate)
    same_relation = f"c2.predicate IN ({placeholders(names)})" if names else "c1.predicate = c2.predicate"
    predicate_cond = f"""
    AND (
        (c1.predicate IS NULL AND c2.predicate IS NULL) OR
        (c1.predicate IS NOT NULL AND c2.predicate IS NOT NULL AND
         ({same_relation} OR
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
    SELECT c2.id AS claim_b, c2.predicate AS pred_b,
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
    return sql, (other_page, claim_a_id, *names, k)


def _build_semantic_sql(other_page: int, claim_a_id: int, k: int) -> tuple:
    """Tight MariaDB HNSW vector lookup for dynamic/open predicates whose
    snake_case labels differ across languages (adjudicated in one batch by gate.py)."""
    sql = """
    SELECT c2.id AS claim_b, c2.predicate AS pred_b,
           VEC_DISTANCE_COSINE(e2.embedding, e1.embedding) AS dist
    FROM claims c1
    JOIN claim_embeddings e1 ON e1.claim_id = c1.id
    JOIN claims c2 ON c2.article_id = %s
    JOIN claim_embeddings e2 ON e2.claim_id = c2.id
    WHERE c1.id = %s
    ORDER BY dist ASC
    LIMIT %s
    """
    return sql, (other_page, claim_a_id, k)


def align_page(page_id: int, k: int | None = None, max_distance: float | None = None):
    """Store candidate links between claims of this page and claims of its
    counterpart pages. Candidates start as 'unmatched'; verdicts.py grades them.

    Uses two-tier MariaDB vector filtering:
    1. Same/alias predicate candidates within ``max_distance`` (top k)
    2. Tight semantic vector candidates within ``semantic_match_distance`` (top semantic_nearest_k)
       for dynamic ML predicates, excluding known incompatible slots (e.g. birth vs death)

    Returns {"claims": n, "counterparts": p, "candidate_links": m}.
    """
    k = k or settings.nearest_k
    max_distance = settings.match_distance if max_distance is None else max_distance
    sem_k = settings.semantic_nearest_k
    sem_dist = min(max_distance, settings.semantic_match_distance)
    min_similarity = settings.min_similarity
    others = comparison_set(page_id)[1:]
    
    claims = query(
        """SELECT c.id, c.predicate FROM claims c
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
                pred_a = row["predicate"]
                
                for other in others:
                    candidates: dict[int, float] = {}
                    sql, params = _build_nearest_sql(other, claim_a_id, k, pred_a)
                    cur.execute(sql, params)
                    for claim_b, _pred_b, dist in cur.fetchall():
                        if dist is not None and dist <= max_distance:
                            candidates[claim_b] = dist

                    if sem_k > 0 and sem_dist > 0:
                        s_sql, s_params = _build_semantic_sql(other, claim_a_id, sem_k)
                        cur.execute(s_sql, s_params)
                        for claim_b, pred_b, dist in cur.fetchall():
                            if dist is None or dist > sem_dist or claim_b in candidates:
                                continue
                            if _incompatible_known_predicates(pred_a, pred_b):
                                continue
                            candidates[claim_b] = dist

                    for claim_b, dist in candidates.items():
                        similarity = 1.0 - dist
                        if similarity < min_similarity:
                            continue
                        cur.execute(
                            """INSERT IGNORE INTO claim_links
                               (claim_a, claim_b, similarity, candidate_confidence,
                                verdict, verdict_source)
                               VALUES (%s, %s, %s, %s, 'unmatched', 'deterministic')""",
                            (min(claim_a_id, claim_b), max(claim_a_id, claim_b),
                             similarity, similarity),
                        )
                        linked += cur.rowcount
            conn.commit()
    return {"claims": len(claims), "counterparts": len(others), "candidate_links": linked}


def clean_invalid_links(page_id: int | None = None) -> int:
    """Remove unreviewed 'unmatched' claim_links that pair known incompatible
    canonical predicates (FK-safe: skips links referenced by reviews/resolutions/suggestions)."""
    where = ""
    params = []
    
    if page_id is not None:
        where = """
            AND (ca.article_id = %s OR cb.article_id = %s)
        """
        params = [page_id, page_id]
    
    invalid_links = query(
        f"""
        SELECT cl.id, ca.predicate AS pa, cb.predicate AS pb
        FROM claim_links cl
        JOIN claims ca ON ca.id = cl.claim_a
        JOIN claims cb ON cb.id = cl.claim_b
        WHERE cl.verdict = 'unmatched'
          AND ca.predicate IS NOT NULL
          AND cb.predicate IS NOT NULL
          AND ca.predicate <> cb.predicate
          AND (ca.predicate_entity IS NULL OR cb.predicate_entity IS NULL
               OR ca.predicate_entity <> cb.predicate_entity)
          AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.claim_link_id = cl.id)
          AND NOT EXISTS (SELECT 1 FROM suggestions s WHERE s.claim_link_id = cl.id)
          {where}
        """,
        params
    )
    
    invalid_links = [
        l for l in invalid_links
        if not same(l["pa"], l["pb"]) and _incompatible_known_predicates(l["pa"], l["pb"])
    ]
    if invalid_links:
        link_ids = [l["id"] for l in invalid_links]
        execute(
            f"DELETE FROM claim_links WHERE id IN ({placeholders(link_ids)})",
            tuple(link_ids)
        )
        return len(link_ids)
    
    return 0
