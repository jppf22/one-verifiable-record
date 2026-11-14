"""Cross-language claim alignment via MariaDB vector search.

For every extracted claim, candidates are collected independently from *every*
other edition of the same Wikidata entity.  There is no property allow-list:
all claims with the same extractor-produced canonical predicate are included
for recall, and semantic nearest neighbours provide a fallback for claims whose
predicates were phrased differently.  The verdict layer decides whether a
candidate is actually comparable.
"""
from config import settings
from db import get_conn, query

NEAREST_SQL = """
SELECT c2.id AS claim_b, c2.statement_text, a2.lang AS other_lang,
       VEC_DISTANCE_COSINE(e2.embedding, e1.embedding) AS dist
FROM claims c1
JOIN claim_embeddings e1 ON e1.claim_id = c1.id
JOIN articles a1 ON a1.id = c1.article_id
JOIN articles a2 ON a2.wikidata_qid = a1.wikidata_qid AND a2.lang = %s
JOIN claims c2 ON c2.article_id = a2.id
JOIN claim_embeddings e2 ON e2.claim_id = c2.id
WHERE c1.id = %s AND c2.revision_id <=> a2.revision_id
ORDER BY dist ASC
LIMIT %s
"""

# The predicate is produced at extraction time and is deliberately free-form
# (for example, `architect`, `elevation_metres`, or a relation introduced by a
# future article).  This recall pass must therefore use the claim's value, not
# a static list owned by the application.
PREDICATE_SQL = """
SELECT c2.id AS claim_b, c2.statement_text, a2.lang AS other_lang,
       VEC_DISTANCE_COSINE(e2.embedding, e1.embedding) AS dist
FROM claims c1
JOIN claim_embeddings e1 ON e1.claim_id = c1.id
JOIN articles a1 ON a1.id = c1.article_id
JOIN articles a2 ON a2.wikidata_qid = a1.wikidata_qid AND a2.lang = %s
JOIN claims c2 ON c2.article_id = a2.id
JOIN claim_embeddings e2 ON e2.claim_id = c2.id
WHERE c1.id = %s
  AND c2.revision_id <=> a2.revision_id
  AND NULLIF(LOWER(TRIM(c1.predicate)), '') = NULLIF(LOWER(TRIM(c2.predicate)), '')
ORDER BY dist ASC
"""

OTHER_LANGUAGES_SQL = """
SELECT DISTINCT a2.lang
FROM claims c1
JOIN articles a1 ON a1.id = c1.article_id
JOIN articles a2 ON a2.wikidata_qid = a1.wikidata_qid AND a2.lang <> a1.lang
WHERE c1.id = %s
ORDER BY a2.lang
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
           WHERE a.wikidata_qid = %s AND c.revision_id <=> a.revision_id
           ORDER BY c.id""",
        (qid,),
    )
    linked = 0
    with get_conn() as conn:
        cur = conn.cursor()
        for row in claims:
            # A global top-k can spend all its slots in one edition, leaving a
            # third (or later) language unchecked.  Search each edition
            # independently, then add every same-predicate claim for recall.
            cur.execute(OTHER_LANGUAGES_SQL, (row["id"],))
            languages = [lang for (lang,) in cur.fetchall()]
            candidates: dict[int, float] = {}
            for lang in languages:
                cur.execute(NEAREST_SQL, (lang, row["id"], k))
                for claim_b, _text, _lang, dist in cur.fetchall():
                    if dist <= settings.match_distance:
                        candidates[claim_b] = dist
                cur.execute(PREDICATE_SQL, (lang, row["id"]))
                for claim_b, _text, _lang, dist in cur.fetchall():
                    # Canonical predicates are extractor output, not a preset
                    # list. Include all of them; the LLM rejects false pairs.
                    candidates.setdefault(claim_b, dist)
            for claim_b, dist in candidates.items():
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
