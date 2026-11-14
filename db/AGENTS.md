# Database AGENTS.md - Data Pipeline Instructions

**READ THIS BEFORE TOUCHING db/ CODE**
This file overrides the root AGENTS.md for database-specific instructions.

## Database Overview

MariaDB 11.8 database with:
- **VECTOR type**: For storing embeddings (1024 dimensions)
- **HNSW indexes**: For fast vector similarity search
- **System-Versioned Tables (SVT)**: For full history tracking
- **Standard tables**: For metadata and relationships

### Directory Structure
```
db/
├── schema.sql          # Main database schema
├── .dockerignore       # Docker ignore rules
├── Dockerfile          # Database container
├── load/               # Data loading scripts
│   ├── download_datasets.sh
│   ├── import_dump.py
│   └── requirements.txt
└── migrations/         # Database migrations
    ├── 001_page_links.sql
    └── 002_richer_claim_comparison.sql
```

## Schema (schema.sql)

### Key Tables

#### Articles
```sql
CREATE TABLE articles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    lang VARCHAR(10) NOT NULL,           -- fi, sv, en
    title VARCHAR(255) NOT NULL,
    wikidata_qid VARCHAR(20),
    revision_id INT NOT NULL,            -- Wikipedia revision ID
    url VARCHAR(500) NOT NULL,
    wiki_url VARCHAR(500) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

#### Page Links (Interlanguage)
```sql
CREATE TABLE page_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_a INT NOT NULL,                 -- references articles.id
    page_b INT NOT NULL,                 -- references articles.id
    source VARCHAR(50),                  -- 'wikidata', 'manual', etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_a) REFERENCES articles(id),
    FOREIGN KEY (page_b) REFERENCES articles(id),
    UNIQUE KEY (page_a, page_b)
);

-- Stored in both directions: (a,b) and (b,a)
```

#### Claims (SYSTEM VERSIONED)
```sql
CREATE TABLE claims (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_id INT NOT NULL,                -- references articles.id
    lang VARCHAR(10) NOT NULL,
    kind VARCHAR(50),                    -- 'date', 'number', 'string', 'boolean'
    subject VARCHAR(255),                -- Entity name
    subject_entity VARCHAR(100),         -- Wikidata entity ID
    predicate VARCHAR(100),              -- Property name (snake_case)
    predicate_entity VARCHAR(100),       -- Wikidata property ID
    object_entity VARCHAR(100),          -- Object entity ID
    value_text TEXT,                     -- Raw value text
    value_normalized VARCHAR(255),       -- Normalized value (for comparison)
    value_canonical JSON,                -- Typed, structured value
    qualifiers JSON,                     -- Additional context
    context_text TEXT,                   -- Surrounding text
    citation_urls JSON,                  -- List of citation URLs
    extraction_confidence FLOAT,         -- 0-1
    statement_text TEXT NOT NULL,        -- Full claim text
    revision_id INT NOT NULL,            -- Wikipedia revision ID
    url VARCHAR(500) NOT NULL,
    model_version VARCHAR(50),           -- 'rules-v1', 'llm-v1', etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- System Versioning
    ROW_START TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    ROW_END TIMESTAMP(6) DEFAULT '9999-12-31 23:59:59.999999',
    TRANSACTION_start_id BIGINT UNSIGNED,
    
    FOREIGN KEY (page_id) REFERENCES articles(id),
    INDEX idx_claims_page (page_id),
    INDEX idx_claims_predicate (predicate),
    INDEX idx_claims_lang (lang)
) ENGINE=InnoDB SYSTEM VERSIONING;

-- History table (created automatically by SVT)
CREATE TABLE claims_history LIKE claims;
ALTER TABLE claims_history ADD PRIMARY KEY (id, ROW_START, ROW_END);
```

#### Claim Embeddings (NOT NULL for HNSW)
```sql
CREATE TABLE claim_embeddings (
    id INT PRIMARY KEY,                  -- references claims.id
    embedding VECTOR(1024) NOT NULL,    -- 1024-dim embedding
    model VARCHAR(50) NOT NULL,         -- 'multilingual-e5-large'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (id) REFERENCES claims(id),
    INDEX idx_claim_embedding ON claim_embeddings (embedding) USING hnsw (
        embedding DISTANCE=cosine
    )
);
```

#### Chunk Embeddings (NOT NULL for HNSW)
```sql
CREATE TABLE chunk_embeddings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    page_id INT NOT NULL,
    lang VARCHAR(10) NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    model VARCHAR(50) NOT NULL,
    position INT NOT NULL,              -- Position in article
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (page_id) REFERENCES articles(id),
    INDEX idx_chunk_embedding ON chunk_embeddings (embedding) USING hnsw (
        embedding DISTANCE=cosine
    )
);
```

#### Claim Links (SYSTEM VERSIONED)
```sql
CREATE TABLE claim_links (
    id INT AUTO_INCREMENT PRIMARY KEY,
    claim_a INT NOT NULL,                -- references claims.id
    claim_b INT NOT NULL,                -- references claims.id
    verdict ENUM('unmatched', 'supported', 'contradicted', 'unverifiable', 'definition_mismatch') DEFAULT 'unmatched',
    verdict_source ENUM('deterministic', 'llm', 'human') DEFAULT 'deterministic',
    similarity FLOAT,                    -- Cosine similarity (0-1)
    evidence TEXT,                       -- Source passages
    rationale TEXT,                      -- Reasoning
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- System Versioning
    ROW_START TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    ROW_END TIMESTAMP(6) DEFAULT '9999-12-31 23:59:59.999999',
    TRANSACTION_start_id BIGINT UNSIGNED,
    
    FOREIGN KEY (claim_a) REFERENCES claims(id),
    FOREIGN KEY (claim_b) REFERENCES claims(id),
    INDEX idx_claim_links_claim_a (claim_a),
    INDEX idx_claim_links_claim_b (claim_b),
    INDEX idx_claim_links_verdict (verdict),
    INDEX idx_claim_links_computed (computed_at)
) ENGINE=InnoDB SYSTEM VERSIONING;

-- History table
CREATE TABLE claim_links_history LIKE claim_links;
ALTER TABLE claim_links_history ADD PRIMARY KEY (id, ROW_START, ROW_END);

-- Canonical order: always store as (min_id, max_id)
-- Enforced in application code, not in schema
```

#### Reviews (SYSTEM VERSIONED)
```sql
CREATE TABLE reviews (
    id INT AUTO_INCREMENT PRIMARY KEY,
    claim_link_id INT NOT NULL,         -- references claim_links.id
    reviewer VARCHAR(255) NOT NULL,     -- Display name
    decision ENUM('agree', 'disagree', 'fix') NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- System Versioning
    ROW_START TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    ROW_END TIMESTAMP(6) DEFAULT '9999-12-31 23:59:59.999999',
    TRANSACTION_start_id BIGINT UNSIGNED,
    
    FOREIGN KEY (claim_link_id) REFERENCES claim_links(id),
    INDEX idx_reviews_link (claim_link_id),
    INDEX idx_reviews_reviewer (reviewer),
    INDEX idx_reviews_created (created_at)
) ENGINE=InnoDB SYSTEM VERSIONING;

-- History table
CREATE TABLE reviews_history LIKE reviews;
ALTER TABLE reviews_history ADD PRIMARY KEY (id, ROW_START, ROW_END);
```

#### Suggestions (Patch Drafts) (SYSTEM VERSIONED)
```sql
CREATE TABLE suggestions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    page_id INT NOT NULL,                -- references articles.id
    patch_type ENUM('correction', 'addition', 'notice') NOT NULL,
    wikitext TEXT NOT NULL,             -- Wikitext content
    status ENUM('draft', 'exported', 'applied') DEFAULT 'draft',
    lang VARCHAR(10) NOT NULL,
    title VARCHAR(255) NOT NULL,
    claim_link_id INT,                  -- references claim_links.id (nullable)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- System Versioning
    ROW_START TIMESTAMP(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    ROW_END TIMESTAMP(6) DEFAULT '9999-12-31 23:59:59.999999',
    TRANSACTION_start_id BIGINT UNSIGNED,
    
    FOREIGN KEY (page_id) REFERENCES articles(id),
    FOREIGN KEY (claim_link_id) REFERENCES claim_links(id),
    INDEX idx_suggestions_page (page_id),
    INDEX idx_suggestions_type (patch_type),
    INDEX idx_suggestions_status (status)
) ENGINE=InnoDB SYSTEM VERSIONING;

-- History table
CREATE TABLE suggestions_history LIKE suggestions;
ALTER TABLE suggestions_history ADD PRIMARY KEY (id, ROW_START, ROW_END);

-- Deduplication: unique (page_id, patch_type, wikitext)
CREATE UNIQUE INDEX idx_suggestions_dedupe ON suggestions (page_id, patch_type, wikitext);
```

## Data Loading

### Dataset Source
- **Primary**: github.com/MariaDB/mariadb-wikipedia-datasets
- **Secondary**: github.com/MariaDB/mariadb-vector-wikipedia-rag
- **Languages**: fi, sv, en
- **Sizes**: small, medium, large

### Load Process
1. **Download**: Run `download_datasets.sh` with size parameter
   ```bash
   ./db/load/download_datasets.sh small
   ```

2. **Import**: Run Python import script
   ```bash
   pip install -r db/load/requirements.txt
   python db/load/import_dump.py --data-dir db/load/data
   ```

3. **Options**:
   - `--only-shared`: Only import articles with 2+ languages (default)
   - `--size small|medium|large`: Dataset size

### Import Script (import_dump.py)
- Parses Wikipedia dump files
- Extracts articles, revisions, metadata
- Creates articles table entries
- Creates page_links from Wikidata interlanguage links
- Handles spaces in titles (stored with underscores in DB)

## Migrations

### Applied Migrations
1. **001_page_links.sql**: Creates page_links table
2. **002_richer_claim_comparison.sql**: Enhances claim comparison

### Migration Process
```bash
# Apply all migrations
for file in db/migrations/*.sql; do
    mysql -u user -p one_verifiable_record < "$file"
done
```

### Writing Migrations
1. Create new file: `db/migrations/003_description.sql`
2. Include both forward and backward SQL
3. Test on staging database
4. Document in assumptions.md if significant changes

## Important Implementation Notes

### System Versioning (SVT)

#### How It Works
- Each row has `ROW_START` and `ROW_END` timestamps
- `ROW_START`: When the row was created/updated
- `ROW_END`: When the row was deleted/updated (default: far future)
- Updates create new rows, old rows get `ROW_END` set

#### Querying History
```sql
-- Get all versions of a claim
SELECT * FROM claims FOR SYSTEM_TIME ALL 
WHERE id = 123
ORDER BY ROW_START DESC;

-- Get version as of specific time
SELECT * FROM claims FOR SYSTEM_TIME AS OF '2026-11-14 10:00:00'
WHERE id = 123;

-- Get version between two times
SELECT * FROM claims 
FOR SYSTEM_TIME BETWEEN '2026-11-14 10:00:00' AND '2026-11-14 11:00:00'
WHERE id = 123;

-- Check if claim has changed since a timestamp
SELECT ROW_START > '2026-11-14 10:00:00' as has_changed
FROM claims FOR SYSTEM_TIME ALL 
WHERE id = 123
AND ROW_START > '2026-11-14 10:00:00'
ORDER BY ROW_START DESC
LIMIT 1;
```

#### Stale Detection (S1, A6 in assumptions.md)
```sql
-- Find links where claim_a has changed since link was computed
SELECT cl.id, cl.computed_at, c.ROW_START as claim_version
FROM claim_links cl
JOIN claims FOR SYSTEM_TIME ALL c ON c.id = cl.claim_a
WHERE cl.id = :link_id
AND c.ROW_START > cl.computed_at + INTERVAL 1 SECOND

-- The +1 second buffer is needed because:
-- - ROW_START has microseconds
-- - computed_at has seconds
-- Without it, a claim written in the same second would look "newer"
```

### Vector Search

#### HNSW Index Requirements
1. Column must be `NOT NULL`
2. Vector type must be declared (e.g., `VECTOR(1024)`)
3. Distance metric must be specified

#### Creating Index
```sql
CREATE INDEX idx_name ON table_name USING hnsw (
    vector_column DISTANCE=cosine
);
```

#### Vector Distance Functions
```sql
-- Cosine distance (0 = identical, 2 = opposite)
VEC_DISTANCE_COSINE(vector_a, vector_b)

-- Note: e5-large embeddings use cosine similarity
-- Similarity = 1 - (cosine_distance / 2)
-- So similarity >= 0.88 means distance <= 0.24
-- But we use 0.12 as match_distance (similarity >= 0.88)
```

#### Vector Search Query
```sql
SELECT 
    id,
    page_id,
    lang,
    statement_text,
    VEC_DISTANCE_COSINE(embedding, :query_embedding) as distance
FROM claim_embeddings ce
JOIN claims c ON c.id = ce.id
WHERE VEC_DISTANCE_COSINE(embedding, :query_embedding) <= 0.15
ORDER BY distance ASC
LIMIT 10
```

### Canonical Order (S3 in assumptions.md)
```python
# Always store links as (min_id, max_id) to avoid duplicates
def canonical_order(claim_a, claim_b):
    if claim_a < claim_b:
        return (claim_a, claim_b)
    else:
        return (claim_b, claim_a)

# In SQL: use LEAST() and GREATEST()
INSERT INTO claim_links (claim_a, claim_b, ...)
VALUES (LEAST(:a, :b), GREATEST(:a, :b), ...)
```

### NOT NULL Columns for Embeddings
```sql
-- This will fail:
ALTER TABLE claim_embeddings ADD COLUMN embedding VECTOR(1024);
-- Because it allows NULL by default

-- This works:
ALTER TABLE claim_embeddings ADD COLUMN embedding VECTOR(1024) NOT NULL;
-- But you must populate all existing rows first

-- Better: create new table with NOT NULL, migrate data, then replace
```

### Page Links in Both Directions
```python
# When creating page links from Wikidata:
def create_page_links(articles):
    # For each article with interlanguage links
    for article in articles:
        for target_lang, target_title in article.interlanguage_links.items():
            if target_lang in ['fi', 'sv', 'en']:
                target_article = find_article(target_lang, target_title)
                if target_article:
                    # Create link in both directions
                    create_link(article.id, target_article.id, source='wikidata')
                    create_link(target_article.id, article.id, source='wikidata')
```

## Useful Queries

### Get Comparison Set for a Page
```sql
-- Get page X and all its counterparts
SELECT 
    p.id,
    p.lang,
    p.title,
    p.revision_id,
    p.url,
    p.wiki_url
FROM articles p
WHERE p.id = :page_id OR p.id IN (
    SELECT pl.page_b FROM page_links pl WHERE pl.page_a = :page_id
)
ORDER BY 
    CASE p.id WHEN :page_id THEN 0 ELSE 1 END,
    FIELD(p.lang, 'fi', 'sv', 'en')
```

### Get Claims for Comparison Set
```sql
SELECT 
    c.id,
    c.page_id,
    c.lang,
    c.kind,
    c.subject,
    c.predicate,
    c.value_text,
    c.value_normalized,
    c.value_canonical,
    c.statement_text,
    c.revision_id,
    c.url
FROM claims c
WHERE c.page_id IN (:page_ids)
AND c.ROW_END > CURRENT_TIMESTAMP
```

### Get Links for Comparison Set
```sql
SELECT 
    cl.id,
    cl.claim_a,
    cl.claim_b,
    cl.verdict,
    cl.verdict_source,
    cl.similarity,
    cl.evidence,
    cl.rationale,
    cl.computed_at
FROM claim_links cl
WHERE cl.claim_a IN (:claim_ids) OR cl.claim_b IN (:claim_ids)
AND cl.ROW_END > CURRENT_TIMESTAMP
```

### Find Stale Links
```sql
-- Links where either claim has a newer version
SELECT DISTINCT cl.id
FROM claim_links cl
JOIN claims FOR SYSTEM_TIME ALL ca ON ca.id = cl.claim_a
JOIN claims FOR SYSTEM_TIME ALL cb ON cb.id = cl.claim_b
WHERE cl.ROW_END > CURRENT_TIMESTAMP
AND (ca.ROW_START > cl.computed_at + INTERVAL 1 SECOND 
     OR cb.ROW_START > cl.computed_at + INTERVAL 1 SECOND)
```

### Get Pages with Conflicts
```sql
SELECT 
    p.id,
    p.lang,
    p.title,
    COUNT(DISTINCT cl.id) as conflict_count
FROM articles p
JOIN claims c ON c.page_id = p.id AND c.ROW_END > CURRENT_TIMESTAMP
JOIN claim_links cl ON (cl.claim_a = c.id OR cl.claim_b = c.id) 
    AND cl.verdict = 'contradicted'
    AND cl.ROW_END > CURRENT_TIMESTAMP
GROUP BY p.id, p.lang, p.title
HAVING conflict_count > 0
ORDER BY conflict_count DESC
LIMIT 50
```

### Get Claim History with Links
```sql
SELECT 
    c.id,
    c.ROW_START,
    c.ROW_END,
    c.value_text as value,
    c.statement_text,
    cl.verdict,
    cl.computed_at as link_time
FROM claims FOR SYSTEM_TIME ALL c
LEFT JOIN claim_links cl ON cl.claim_a = c.id OR cl.claim_b = c.id
WHERE c.id = :claim_id
AND c.ROW_END > CURRENT_TIMESTAMP
ORDER BY c.ROW_START DESC
```

## Before You Start

1. Read root AGENTS.md
2. Read docs/assumptions.md (especially D1-D10, S1-S6, V1-V6)
3. Understand the page-centric model
4. Review schema.sql to understand table structures

## Common Tasks

### Adding a New Table
1. Add CREATE TABLE statement to schema.sql
2. Add history table if SYSTEM VERSIONING
3. Add indexes (especially for HNSW on vector columns)
4. Update import scripts if needed
5. Test with docker-compose

### Modifying Schema
1. Create migration file in db/migrations/
2. Test migration on staging database
3. Update any dependent code
4. Document changes in assumptions.md

### Adding a New Index
```sql
-- For regular columns
CREATE INDEX idx_name ON table_name (column1, column2);

-- For vector columns (HNSW)
CREATE INDEX idx_name ON table_name USING hnsw (
    vector_column DISTANCE=cosine
);
```

### Querying System Versioned Tables
Remember: All queries on SVT tables need to filter by ROW_END:
```sql
SELECT * FROM claims 
WHERE ROW_END > CURRENT_TIMESTAMP
```
Or use the AS OF syntax:
```sql
SELECT * FROM claims FOR SYSTEM_TIME AS OF CURRENT_TIMESTAMP
WHERE ...
```

## Gotchas

1. **SVT and Joins**: When joining SVT tables, you must handle the temporal aspect
   - Either join on the current version only
   - Or use complex temporal joins

2. **Vector Index Size**: HNSW indexes can be large for many vectors
   - Consider M=16, ef_construction=200 for balance
   - Higher M = more accurate, larger index
   - Higher ef = faster search, more memory

3. **NOT NULL for Vectors**: HNSW requires NOT NULL columns
   - Must populate all rows before creating index
   - Or create column as NOT NULL from the start

4. **Temporal Precision**: ROW_START has microseconds, computed_at has seconds
   - Need +1 second buffer for stale detection

5. **Canonical Order**: Always store claim_links as (min_id, max_id)
   - Prevents duplicates
   - Must check existing link before inserting

6. **Page Links Direction**: Stored in both directions
   - Makes queries easier
   - But need to avoid infinite loops in traversal

## Performance Tips

1. **Vector Search**: Use HNSW with appropriate parameters
   - For 10K vectors: M=16, ef_construction=200
   - For 100K vectors: M=32, ef_construction=300

2. **SVT Queries**: Filter by ROW_END early
   ```sql
   SELECT * FROM claims 
   WHERE ROW_END > CURRENT_TIMESTAMP
   AND ...
   ```

3. **Index Usage**: Create indexes on frequently queried columns
   - page_id, lang, predicate, verdict
   - For system-versioned tables: (id, ROW_START, ROW_END)

4. **Batch Operations**: Use bulk inserts for large operations
   - Especially for embeddings and claims

## Testing

### Local Testing
1. Start MariaDB: `docker compose up -d mariadb`
2. Connect: `mysql -u root -p -h localhost -P 3306`
3. Create database: `CREATE DATABASE one_verifiable_record;`
4. Import schema: `mysql -u root -p one_verifiable_record < db/schema.sql`
5. Run migrations: Apply all files in db/migrations/
6. Import data: `python db/load/import_dump.py`

### Common Issues
- **Connection refused**: Check docker-compose.yml for port mapping
- **Access denied**: Check database credentials in .env
- **Table not found**: Run migrations
- **Vector index error**: Check column is NOT NULL
- **SVT not working**: Verify ENGINE=InnoDB and SYSTEM VERSIONING

## Final Notes

The database is the heart of this project. It must:
1. Store all Wikipedia data (articles, claims, embeddings)
2. Enable fast vector search (HNSW)
3. Track all changes (SVT)
4. Scale to large datasets (100K+ articles)

Focus on:
- Correct schema design
- Proper indexing (especially HNSW)
- Efficient queries
- Data integrity (NOT NULL, foreign keys)
