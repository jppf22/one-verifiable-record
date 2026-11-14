-- Page-centric counterpart relationships for databases created before the
-- page-comparison redesign. Apply this before 002_richer_claim_comparison.sql.
--
-- The importer materialises a directed row for every supported-language
-- counterpart pair. Keeping both directions makes the extension/page API's
-- lookup a simple indexed query from the page being viewed.
USE wikipedia;

CREATE TABLE IF NOT EXISTS page_links (
  page_a      BIGINT UNSIGNED NOT NULL,
  page_b      BIGINT UNSIGNED NOT NULL,
  source      VARCHAR(32) NOT NULL DEFAULT 'wikidata_sitelink',
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (page_a, page_b),
  KEY idx_b (page_b),
  CONSTRAINT fk_pl_a FOREIGN KEY (page_a) REFERENCES articles (id),
  CONSTRAINT fk_pl_b FOREIGN KEY (page_b) REFERENCES articles (id)
) ENGINE=InnoDB;
