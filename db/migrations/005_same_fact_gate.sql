-- Same-fact gate (server/gate.py). Additive and idempotent.
-- A vector-search candidate pair that the gate decides is NOT about the same fact
-- (birth date vs death date, first vs second marriage) gets verdict 'different_fact'.
-- The row is kept rather than deleted so alignment's INSERT IGNORE on uq_pair does not
-- propose the same pair again on every re-check, and the decision stays in the
-- link's system-versioned history. diff.py never shows these links as verdicts.
USE wikipedia;

SET @@system_versioning_alter_history = KEEP;

ALTER TABLE claim_links MODIFY verdict
  ENUM('supported','contradicted','possible_match','possible_conflict',
       'unverifiable','definition_mismatch','unmatched','different_fact') NOT NULL;
