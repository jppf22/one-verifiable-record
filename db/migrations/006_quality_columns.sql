-- Quality columns written by the Agent 4 pipeline (docs/agent-4-data-pipeline-summary.md)
-- and declared in db/schema.sql, but missing from databases created before that change:
--   claims.data_quality_score             (jobs/extract_claims.py, jobs/extract_rules.py)
--   claim_embeddings.embedding_quality    (jobs/embed.py)
--   chunk_embeddings.embedding_quality    (jobs/embed.py)
-- Without them every extraction INSERT and every embedding INSERT fails with
-- "Unknown column". Additive and idempotent.
USE wikipedia;

-- claims is SYSTEM VERSIONED; altering it requires keeping history rows.
SET @@system_versioning_alter_history = KEEP;

ALTER TABLE claims
  ADD COLUMN IF NOT EXISTS data_quality_score FLOAT NULL
  COMMENT '0-1 quality score based on validation (structure, completeness, consistency)'
  AFTER extraction_confidence;

ALTER TABLE claim_embeddings
  ADD COLUMN IF NOT EXISTS embedding_quality FLOAT NULL
  COMMENT '0-1 quality score (normalization, validation)';

ALTER TABLE chunk_embeddings
  ADD COLUMN IF NOT EXISTS embedding_quality FLOAT NULL
  COMMENT '0-1 quality score (normalization, validation)';

-- Backward (manual):
--   SET @@system_versioning_alter_history = KEEP;
--   ALTER TABLE claims DROP COLUMN data_quality_score;
--   ALTER TABLE claim_embeddings DROP COLUMN embedding_quality;
--   ALTER TABLE chunk_embeddings DROP COLUMN embedding_quality;
