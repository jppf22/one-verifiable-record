-- Federation + whole-Wikipedia support (docs/federation.md).
-- Additive and idempotent; preserves MariaDB SYSTEM VERSIONING history.
USE wikipedia;

SET @@system_versioning_alter_history = KEEP;

-- Language edition codes go beyond two letters (ceb, war, ...).
ALTER TABLE articles
  MODIFY COLUMN lang VARCHAR(10) NOT NULL
  COMMENT 'language edition code, e.g. fi | sv | en | ceb';

CREATE TABLE IF NOT EXISTS dataset_versions (
  id               BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  dataset_hash     CHAR(64) NOT NULL COMMENT 'sha256 over the imported (lang, page_id, revision_id) set',
  extractor_model  VARCHAR(128) NULL,
  embedding_model  VARCHAR(128) NULL,
  pipeline_version VARCHAR(64) NULL,
  created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_dataset (dataset_hash)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS instances (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  name          VARCHAR(64) NOT NULL,
  base_url      VARCHAR(512) NOT NULL DEFAULT '',
  pubkey        VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'hex ed25519 public key of the feed signer',
  trust_weight  FLOAT NOT NULL DEFAULT 1.0,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_instance_name (name)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS discovery_imports (
  origin_instance  VARCHAR(64) NOT NULL,
  record_type      ENUM('review','resolution') NOT NULL,
  origin_record_id BIGINT UNSIGNED NOT NULL,
  local_table      VARCHAR(32) NOT NULL,
  local_id         BIGINT UNSIGNED NOT NULL,
  imported_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (origin_instance, record_type, origin_record_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS discovery_sync_state (
  instance_name   VARCHAR(64) NOT NULL PRIMARY KEY,
  last_record_id  BIGINT UNSIGNED NOT NULL DEFAULT 0,
  last_sync_at    TIMESTAMP NULL,
  status          VARCHAR(32) NOT NULL DEFAULT 'pending'
) ENGINE=InnoDB;
