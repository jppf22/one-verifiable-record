-- ML second-sweep salience score on claims (jobs/rerank_claims.py, jobs/extract_claims.py).
-- Additive and idempotent; preserves MariaDB SYSTEM VERSIONING history.
USE wikipedia;

SET @@system_versioning_alter_history = KEEP;

ALTER TABLE claims
  ADD COLUMN IF NOT EXISTS salience_score FLOAT NULL
  COMMENT '0-1 ML second-sweep salience and cross-edition relevance score'
  AFTER data_quality_score;
