-- Resolutions: the correct version of a reviewer-confirmed conflict, written in
-- English, plus a per-language draft for every page of the comparison set.
-- Additive and idempotent: safe to run on an existing database before a deploy.
--   1. resolutions table (SYSTEM VERSIONED: every rewrite of the correct text is kept)
--   2. suggestions.resolution_id: which resolution a draft was generated from
--   3. data repair: links graded while the LLM returned errors were stored with
--      verdict_source 'llm'. Relabel them 'deterministic-fallback' (what the
--      current code writes), so they are not presented as model verdicts and
--      don't glue unrelated claims into one comparison row (diff.py).
USE wikipedia;

-- 1. A resolution is only accepted for a link whose verdict is 'contradicted'
--    with verdict_source 'human' (policy R1 reached, server/consensus.py).
CREATE TABLE IF NOT EXISTS resolutions (
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

-- suggestions is SYSTEM VERSIONED; altering it requires keeping history rows.
SET @@system_versioning_alter_history = KEEP;

-- 2. Drafts generated from a resolution point back at it.
ALTER TABLE suggestions
  ADD COLUMN IF NOT EXISTS resolution_id BIGINT UNSIGNED NULL
  COMMENT 'resolution this draft was generated from' AFTER claim_link_id,
  ADD INDEX IF NOT EXISTS idx_sugg_resolution (resolution_id);

-- 3. One UPDATE per link (a new SVT version; the old row stays in history).
UPDATE claim_links
   SET verdict_source = 'deterministic-fallback'
 WHERE verdict_source = 'llm'
   AND (rationale LIKE 'LLM error:%' OR rationale LIKE 'no LLM configured%');

-- Backward (manual):
--   SET @@system_versioning_alter_history = KEEP;
--   ALTER TABLE suggestions DROP INDEX idx_sugg_resolution, DROP COLUMN resolution_id;
--   DROP TABLE resolutions;
--   Step 3 is a data repair: the previous labels stay readable with
--   SELECT ... FROM claim_links FOR SYSTEM_TIME ALL.
