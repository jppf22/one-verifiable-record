-- Verdict-engine upgrade (Agent 1: backend verdict engine).
-- Additive and idempotent: safe to run on an existing database before a deploy.
-- MariaDB 11.8 supports ADD COLUMN/INDEX IF NOT EXISTS.
--   1. 'deterministic-fallback' verdict source (LLM-unavailable path, task 1)
--   2. filtering indexes on claims(predicate), claims(predicate_entity) (task 2)
--   3. verdict_confidence score column (task 3)
-- The 'human' verdict source is already in the enum; the Q1 override (task 4)
-- needs no schema change.
USE wikipedia;

-- claim_links is SYSTEM VERSIONED; altering it requires keeping history rows.
SET @@system_versioning_alter_history = KEEP;

-- 1. Mark verdicts produced by the deterministic layer because the LLM was
--    unavailable, so a guess is never presented as a model output.
ALTER TABLE claim_links MODIFY verdict_source
  ENUM('deterministic','llm','human','deterministic-fallback') NOT NULL;

-- 3. Combined confidence score (vector similarity + predicate match + layer
--    certainty). NULL for rows graded before this migration (backwards compat).
ALTER TABLE claim_links
  ADD COLUMN IF NOT EXISTS verdict_confidence FLOAT NULL
  COMMENT 'combined confidence: similarity + predicate match + layer certainty'
  AFTER candidate_confidence;

-- 2. Filtering / predicate-guard indexes. idx_verdict on claim_links(verdict)
--    already exists in schema.sql; these cover the claims side.
ALTER TABLE claims
  ADD INDEX IF NOT EXISTS idx_claims_predicate (predicate),
  ADD INDEX IF NOT EXISTS idx_claims_predicate_entity (predicate_entity);
