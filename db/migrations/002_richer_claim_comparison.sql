-- Richer claim representation and confidence-aware candidate links.
-- Apply once to an existing database created from an earlier schema revision.
ALTER TABLE claims
  ADD COLUMN subject_entity VARCHAR(32) NULL AFTER subject,
  ADD COLUMN predicate_entity VARCHAR(32) NULL AFTER predicate,
  ADD COLUMN object_entity VARCHAR(32) NULL AFTER predicate_entity,
  ADD COLUMN value_canonical JSON NULL AFTER value_normalized,
  ADD COLUMN qualifiers JSON NULL AFTER value_canonical,
  ADD COLUMN context_text MEDIUMTEXT NULL AFTER statement_text,
  ADD COLUMN citation_urls JSON NULL AFTER context_text,
  ADD COLUMN extraction_confidence FLOAT NULL AFTER citation_urls;

ALTER TABLE claim_links
  ADD COLUMN relation ENUM('same','related','different','unknown') NOT NULL DEFAULT 'unknown' AFTER similarity,
  ADD COLUMN candidate_confidence FLOAT NULL AFTER relation;

ALTER TABLE claim_links MODIFY verdict
  ENUM('supported','contradicted','possible_match','possible_conflict',
       'unverifiable','definition_mismatch','unmatched') NOT NULL;

ALTER TABLE page_links
  MODIFY source VARCHAR(32) NOT NULL DEFAULT 'wikidata_sitelink',
  ADD COLUMN confidence FLOAT NOT NULL DEFAULT 1.0 AFTER source,
  ADD COLUMN evidence JSON NULL AFTER confidence,
  ADD COLUMN verified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER evidence;
