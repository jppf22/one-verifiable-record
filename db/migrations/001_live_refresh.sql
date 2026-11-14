-- Upgrade an existing database created before append-only article refreshes.
-- MariaDB 11.8 supports ADD COLUMN/INDEX IF NOT EXISTS, making this safe to run
-- before every deployment. It does not alter or discard historic claims.
USE wikipedia;

ALTER TABLE articles
  ADD COLUMN IF NOT EXISTS source_hash CHAR(64) NULL
  COMMENT 'SHA-256 of the imported source text; detects revision-less refreshes'
  AFTER revision_id;

ALTER TABLE chunks
  ADD INDEX IF NOT EXISTS idx_article_revision (article_id, revision_id);
