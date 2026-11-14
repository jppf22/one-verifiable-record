-- ============================================================================
-- One Verifiable Record — schema
-- Requires MariaDB 11.8+ (VECTOR type, HNSW vector index, VEC_* functions).
-- All "decision" tables (claims, claim_links, reviews, suggestions) are
-- SYSTEM-VERSIONED: every change keeps its history in the database itself.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS wikipedia CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE wikipedia;

-- ----------------------------------------------------------------------------
-- articles: one row per PAGE (language + page_id + revision). wikidata_qid is only
-- used at import time to derive page_links between counterpart pages.
-- ----------------------------------------------------------------------------
CREATE TABLE articles (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  lang          CHAR(2) NOT NULL COMMENT 'fi | sv | en',
  page_id       BIGINT UNSIGNED NOT NULL,
  title         VARCHAR(512) NOT NULL,
  wikidata_qid  VARCHAR(16) NULL COMMENT 'e.g. Q9080; NULL if not linked to Wikidata',
  revision_id   BIGINT UNSIGNED NULL,
  fetched_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_lang_page (lang, page_id),
  KEY idx_qid (wikidata_qid),
  KEY idx_title (title(191))
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- page_links: interlanguage links between concrete pages. The unit of comparison
-- is a PAGE (one lang + page_id + revision), compared with its counterpart
-- pages in other languages. Stored in both directions (a->b and b->a) so
-- "counterparts of page X" is a single indexed lookup. Derived from the
-- dataset's Wikidata sitelinks at import time.
-- ----------------------------------------------------------------------------
CREATE TABLE page_links (
  page_a      BIGINT UNSIGNED NOT NULL,
  page_b      BIGINT UNSIGNED NOT NULL,
  source      VARCHAR(32) NOT NULL DEFAULT 'wikidata_sitelink'
              COMMENT 'wikidata_sitelink | mediawiki_langlink | entity_resolution | semantic_candidate | manual',
  confidence  FLOAT NOT NULL DEFAULT 1.0,
  evidence    JSON NULL COMMENT 'source-specific identifiers and refresh metadata',
  verified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (page_a, page_b),
  KEY idx_b (page_b),
  CONSTRAINT fk_pl_a FOREIGN KEY (page_a) REFERENCES articles (id),
  CONSTRAINT fk_pl_b FOREIGN KEY (page_b) REFERENCES articles (id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- chunks: passage splits of article text. Substrate for RAG retrieval.
-- Embeddings live in chunk_embeddings (see below), written by jobs/embed.py.
-- ----------------------------------------------------------------------------
CREATE TABLE chunks (
  id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  article_id   BIGINT UNSIGNED NOT NULL,
  chunk_idx    INT NOT NULL,
  section      VARCHAR(255) NULL,
  text         MEDIUMTEXT NOT NULL,
  revision_id  BIGINT UNSIGNED NULL,
  KEY idx_article (article_id),
  UNIQUE KEY uq_article_chunk (article_id, chunk_idx),
  CONSTRAINT fk_chunks_article FOREIGN KEY (article_id) REFERENCES articles (id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- chunk_embeddings: one multilingual-e5-large vector (1024 dims) per chunk.
-- Kept in a separate table because MariaDB vector indexes need NOT NULL columns,
-- while rows are imported before the embedding job runs. A missing row means
-- "not embedded yet". HNSW index: all RAG retrieval is a single SQL statement.
-- ----------------------------------------------------------------------------
CREATE TABLE chunk_embeddings (
  chunk_id     BIGINT UNSIGNED PRIMARY KEY,
  embedding    VECTOR(1024) NOT NULL,
  model        VARCHAR(128) NULL,
  embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  embedding_quality FLOAT NULL COMMENT '0-1 quality score (normalization, validation)',
  VECTOR INDEX vidx_chunks (embedding) M=8 DISTANCE=cosine,
  CONSTRAINT fk_chunk_emb FOREIGN KEY (chunk_id) REFERENCES chunks (id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- claims: atomic statements extracted from chunks. The diff unit of the merge
-- view. SYSTEM VERSIONING gives the "biography of a sentence": every value a
-- claim ever had, with row timestamps, queryable via FOR SYSTEM_TIME.
-- ----------------------------------------------------------------------------
CREATE TABLE claims (
  id               BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  article_id       BIGINT UNSIGNED NOT NULL,
  chunk_id         BIGINT UNSIGNED NOT NULL,
  -- A descriptive extraction label, not a controlled vocabulary.  The relation
  -- being checked is `predicate`; new kinds and predicates must not require a
  -- schema or code change.
  kind             VARCHAR(64) NOT NULL,
  subject          VARCHAR(512) NULL,
  subject_entity   VARCHAR(32) NULL COMMENT 'Wikidata QID when entity linking resolved the subject',
  predicate        VARCHAR(255) NULL,
  predicate_entity VARCHAR(32) NULL COMMENT 'Wikidata property ID when relation mapping resolved it',
  object_entity    VARCHAR(32) NULL COMMENT 'Wikidata QID for entity-valued objects',
  value_text       VARCHAR(1024) NULL,
  value_normalized VARCHAR(512) NULL COMMENT 'canonical form: ISO date / plain number, for deterministic checks',
  value_canonical  JSON NULL COMMENT 'typed canonical value: unit, calendar, range and precision',
  qualifiers       JSON NULL COMMENT 'scope, as-of time, attribution and other fact qualifiers',
  statement_text   TEXT NOT NULL,
  context_text     MEDIUMTEXT NULL COMMENT 'source paragraph/section surrounding the atomic claim',
  citation_urls    JSON NULL COMMENT 'citation URLs or identifiers found with the claim',
  extraction_confidence FLOAT NULL,
  data_quality_score FLOAT NULL COMMENT '0-1 quality score based on validation (structure, completeness, consistency)',
  salience_score   FLOAT NULL COMMENT '0-1 ML second-sweep salience and cross-edition relevance score',
  revision_id      BIGINT UNSIGNED NULL,
  model_version    VARCHAR(64) NULL COMMENT 'extractor model that produced this claim',
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_article (article_id),
  KEY idx_chunk (chunk_id),
  KEY idx_kind (kind),
  KEY idx_predicate (predicate),
  KEY idx_predicate_entity (predicate_entity),
  CONSTRAINT fk_claims_article FOREIGN KEY (article_id) REFERENCES articles (id),
  CONSTRAINT fk_claims_chunk FOREIGN KEY (chunk_id) REFERENCES chunks (id)
) ENGINE=InnoDB
WITH SYSTEM VERSIONING;

-- ----------------------------------------------------------------------------
-- claim_embeddings: claim-level vectors for cross-language matching. Deliberately
-- NOT system-versioned and NOT on `claims`: writing an embedding must not add a
-- new claim version. The claim's history ("biography of a sentence") and the
-- re-check's staleness test only see real content changes.
-- ----------------------------------------------------------------------------
CREATE TABLE claim_embeddings (
  claim_id     BIGINT UNSIGNED PRIMARY KEY,
  embedding    VECTOR(1024) NOT NULL,
  model        VARCHAR(128) NULL,
  embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  embedding_quality FLOAT NULL COMMENT '0-1 quality score (normalization, validation)',
  VECTOR INDEX vidx_claims (embedding) M=8 DISTANCE=cosine,
  CONSTRAINT fk_claim_emb FOREIGN KEY (claim_id) REFERENCES claims (id)
) ENGINE=InnoDB;

-- ----------------------------------------------------------------------------
-- claim_links: cross-language alignment between two claims + its verdict.
-- Rows are never deleted: an UPDATE writes a new version (SYSTEM VERSIONING),
-- so the full evolution of every verdict is preserved.
-- ----------------------------------------------------------------------------
CREATE TABLE claim_links (
  id             BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  claim_a        BIGINT UNSIGNED NOT NULL,
  claim_b        BIGINT UNSIGNED NOT NULL,
  similarity     FLOAT NOT NULL COMMENT '1 - cosine distance from MariaDB vector search',
  relation       ENUM('same','related','different','unknown') NOT NULL DEFAULT 'unknown',
  candidate_confidence FLOAT NULL COMMENT 'semantic matcher confidence; similarity alone is not a verdict',
  verdict_confidence FLOAT NULL COMMENT 'combined confidence: similarity + predicate match + layer certainty',
  verdict        ENUM('supported','contradicted','possible_match','possible_conflict',
                      'unverifiable','definition_mismatch','unmatched','different_fact') NOT NULL,
  verdict_source ENUM('deterministic','llm','human','deterministic-fallback') NOT NULL,
  evidence       TEXT NULL COMMENT 'receipts: source passages',
  rationale      TEXT NULL,
  computed_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_pair (claim_a, claim_b),
  KEY idx_verdict (verdict),
  CONSTRAINT fk_link_a FOREIGN KEY (claim_a) REFERENCES claims (id),
  CONSTRAINT fk_link_b FOREIGN KEY (claim_b) REFERENCES claims (id)
) ENGINE=InnoDB
WITH SYSTEM VERSIONING;

-- ----------------------------------------------------------------------------
-- reviews: human-in-the-loop decisions. Guards against definition-mismatch
-- false positives; each review is itself versioned.
-- ----------------------------------------------------------------------------
CREATE TABLE reviews (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  claim_link_id BIGINT UNSIGNED NOT NULL,
  reviewer      VARCHAR(128) NOT NULL,
  decision      ENUM('agree','disagree','fix') NOT NULL,
  note          TEXT NULL,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_link (claim_link_id),
  CONSTRAINT fk_reviews_link FOREIGN KEY (claim_link_id) REFERENCES claim_links (id)
) ENGINE=InnoDB
WITH SYSTEM VERSIONING;

-- ----------------------------------------------------------------------------
-- suggestions: proposed patches per target language. Exported for human
-- editors; we never apply edits automatically (cross-wiki bot editing is
-- prohibited by Wikipedia policy).
-- ----------------------------------------------------------------------------
CREATE TABLE suggestions (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  article_id    BIGINT UNSIGNED NOT NULL COMMENT 'target article the patch is proposed for',
  claim_link_id BIGINT UNSIGNED NULL,
  resolution_id BIGINT UNSIGNED NULL COMMENT 'resolution this draft was generated from',
  patch_type    ENUM('wikitext','talk_page','citation') NOT NULL,
  wikitext      MEDIUMTEXT NOT NULL,
  status        ENUM('draft','exported','applied') NOT NULL DEFAULT 'draft',
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_article (article_id),
  KEY idx_sugg_resolution (resolution_id),
  CONSTRAINT fk_sugg_article FOREIGN KEY (article_id) REFERENCES articles (id)
) ENGINE=InnoDB
WITH SYSTEM VERSIONING;

-- ----------------------------------------------------------------------------
-- resolutions: the correct version of a reviewer-confirmed conflict (verdict
-- 'contradicted', source 'human'), written in English. The server turns it into
-- one draft per page of the comparison set (suggestions.resolution_id). Versioned,
-- so every rewrite of the correct text is kept. Migration 004.
-- ----------------------------------------------------------------------------
CREATE TABLE resolutions (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  claim_link_id BIGINT UNSIGNED NOT NULL,
  author        VARCHAR(128) NOT NULL,
  correct_text  TEXT NOT NULL COMMENT 'the correct version, written in English',
  source_url    VARCHAR(2048) NULL COMMENT 'reliable non-Wikipedia source (WP:CIRCULAR)',
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_res_link (claim_link_id),
  CONSTRAINT fk_res_link FOREIGN KEY (claim_link_id) REFERENCES claim_links (id)
) ENGINE=InnoDB
WITH SYSTEM VERSIONING;

-- ============================================================================
-- Provenance query examples (the SVT demo):
--   Biography of a claim across all its versions:
--     SELECT id, statement_text, ROW_START, ROW_END
--     FROM claims FOR SYSTEM_TIME ALL WHERE id = 42 ORDER BY ROW_START;
--   How a link verdict evolved:
--     SELECT * FROM claim_links FOR SYSTEM_TIME ALL WHERE id = 7 ORDER BY ROW_START;
--   Point-in-time audit ("what did we believe at demo freeze?"):
--     SELECT * FROM claim_links FOR SYSTEM_TIME AS OF '2026-11-15 12:00:00';
--   Difference between two moments:
--     SELECT * FROM claim_links FOR SYSTEM_TIME BETWEEN '2026-11-14 00:00:00' AND NOW();
-- ============================================================================
