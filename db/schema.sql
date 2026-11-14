-- ============================================================================
-- One Verifiable Record — schema
-- Requires MariaDB 11.8+ (VECTOR type, HNSW vector index, VEC_* functions).
-- All "decision" tables (claims, claim_links, reviews, suggestions) are
-- SYSTEM-VERSIONED: every change keeps its history in the database itself.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS wikipedia CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE wikipedia;

-- ----------------------------------------------------------------------------
-- articles: one row per (language, page). wikidata_qid is the join key that
-- aligns the same article across the fi / sv / en editions.
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
  kind             ENUM('date','number','quantity','entity_relation','factoid') NOT NULL,
  subject          VARCHAR(512) NULL,
  predicate        VARCHAR(255) NULL,
  value_text       VARCHAR(1024) NULL,
  value_normalized VARCHAR(512) NULL COMMENT 'canonical form: ISO date / plain number, for deterministic checks',
  statement_text   TEXT NOT NULL,
  revision_id      BIGINT UNSIGNED NULL,
  model_version    VARCHAR(64) NULL COMMENT 'extractor model that produced this claim',
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_article (article_id),
  KEY idx_chunk (chunk_id),
  KEY idx_kind (kind),
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
  verdict        ENUM('supported','contradicted','unverifiable','definition_mismatch','unmatched') NOT NULL,
  verdict_source ENUM('deterministic','llm','human') NOT NULL,
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
  patch_type    ENUM('wikitext','talk_page','citation') NOT NULL,
  wikitext      MEDIUMTEXT NOT NULL,
  status        ENUM('draft','exported','applied') NOT NULL DEFAULT 'draft',
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_article (article_id),
  CONSTRAINT fk_sugg_article FOREIGN KEY (article_id) REFERENCES articles (id)
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
