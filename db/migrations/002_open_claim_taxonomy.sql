-- Replace the initial demo's closed claim-kind enum with an open descriptive
-- label.  The relation being compared (`claims.predicate`) has always been
-- data-driven; this migration makes the schema reflect that contract too.
--
-- MariaDB requires history-alter permission when changing a system-versioned
-- table.  Existing historic claim rows are preserved.
USE wikipedia;

SET @@session.system_versioning_alter_history = 1;

ALTER TABLE claims
  MODIFY COLUMN kind VARCHAR(64) NOT NULL
  COMMENT 'free-form extraction label; predicate is the compared relation';
