# Jobs AGENTS.md - Pipeline Instructions

**READ THIS BEFORE TOUCHING jobs/ CODE**
This file overrides the root AGENTS.md for pipeline-specific instructions.

## Pipeline Overview

Batch processing jobs for:
1. **Claim Extraction**: Parse Wikipedia articles into atomic claims
2. **Embedding Generation**: Generate vector embeddings for claims and chunks
3. **Data Processing**: Transform, filter, and load data into MariaDB

### Directory Structure
```
jobs/
├── __pycache__/
├── embed.py            # Vector embedding pipeline
├── extract_claims.py   # LLM-based claim extraction
├── extract_rules.py    # Rules-based claim extraction (fallback)
└── requirements.txt    # Python dependencies
```

## Claim Extraction

### Two Extraction Methods

#### 1. LLM-based Extraction (extract_claims.py)
- Uses Verda Inference API (kimi-k3-1-pro)
- Handles complex sentences and context
- More accurate but slower and costly
- **Current Status**: Not available (Verda project not subscribed to models)

#### 2. Rules-based Extraction (extract_rules.py)
- Uses regex patterns and heuristics
- Fast and free
- Less accurate but works for hackathon
- **Current Status**: Active (model_version='rules-v1')

### Extraction Process

#### LLM Extraction (extract_claims.py)
```python
# For each article:
1. Get first ~6,000 characters (lead + opening sections) - D5
2. Send to LLM with extraction prompt
3. Parse LLM response into claims
4. Normalize values (dates, numbers, entities)
5. Store in database with model_version='llm-v1'
```

#### Rules Extraction (extract_rules.py)
```python
# For each article:
1. Parse article text
2. Identify date patterns (birth, death, etc.)
3. Extract claims using regex
4. Filter uncertain dates (c./noin/omkring, old-style calendars)
5. Store in database with model_version='rules-v1'

# V4: Current implementation
- Birth and death dates parsed from page leads
- Only dates that open their segment are used
- Pages whose title or lead names an event/period are skipped
- Result: 10,385 date claims on 6,206 pages
```

### Claim Structure
```python
@dataclass
class Claim:
    page_id: int
    lang: str
    kind: str                          # 'date', 'number', 'string', 'boolean'
    subject: str                       # Entity name
    subject_entity: str | None         # Wikidata entity ID
    predicate: str | None              # Property name (snake_case)
    predicate_entity: str | None       # Wikidata property ID
    object_entity: str | None          # Object entity ID
    value_text: str                    # Raw value text
    value_normalized: str | None       # Normalized value
    value_canonical: dict | None       # Typed, structured value
    qualifiers: dict                   # Additional context
    context_text: str                  # Surrounding text
    citation_urls: list[str]           # List of citation URLs
    extraction_confidence: float       # 0-1
    statement_text: str                # Full claim text
    revision_id: int                   # Wikipedia revision ID
    url: str                            # Wikipedia URL
    model_version: str                 # 'rules-v1', 'llm-v1', etc.
```

### Extraction Decisions (D6-D8)
- **D6**: Claim `statement_text` stays in the source language (the receipt)
- **D6**: `subject` and `predicate` are canonical English snake_case
- **D6**: Cross-language claims share a vocabulary via predicates
- **D7**: Deterministic layer only compares values when both claims have the same predicate
- **D7**: Dates compare at coarser precision ("1900" agrees with "1900-09-03")
- **D7**: Differing numbers go to LLM (often time-qualified)
- **D8**: E5 prefixes: chunks use `passage: `, claims use `query: ` on both sides

## Embedding Generation (embed.py)

### Embedding Models
- **Primary**: multilingual-e5-large (1024 dimensions)
- **Endpoint**: embedder/ container (OpenAI-compatible API)
- **Fallback**: Can use CPU for small datasets

### Embedding Process
```python
# For claims:
1. Load claims from database (batch size = 100)
2. Prefix with "query: " (D8)
3. Generate embeddings using multilingual-e5-large
4. Store in claim_embeddings table (NOT NULL)
5. Create HNSW index

# For chunks:
1. Load article chunks from database
2. Prefix with "passage: " (D8)
3. Generate embeddings
4. Store in chunk_embeddings table (NOT NULL)
5. Create HNSW index
```

### Embedding Configuration
```python
# From embed.py
MODEL_NAME = "multilingual-e5-large"
VECTOR_DIM = 1024
BATCH_SIZE = 100

# Distance threshold (D8 note about compression)
# e5-large similarities may be compressed
# Default match_distance = 0.12 (similarity >= 0.88)
```

### Running Embeddings
```bash
# Install dependencies
pip install -r jobs/requirements.txt

# Run for all claims
python jobs/embed.py --all-claims

# Run for all chunks
python jobs/embed.py --all-chunks

# Run with custom endpoint
python jobs/embed.py --endpoint http://localhost:8001

# Run with GPU (if available)
python jobs/embed.py --device cuda
```

### Embedder Service (embedder/)
- OpenAI-compatible API
- Runs multilingual-e5-large
- Used for query-time embeddings (Q&A, claim-to-claim matching)
- Docker container: `embedder/`

## Data Flow

### Full Pipeline
```
Wikipedia Datasets
    │
    ▼
Download (db/load/download_datasets.sh)
    │
    ▼
Import (db/load/import_dump.py)
    │
    ▼
     articles table
    page_links table
    │
    ▼
Claim Extraction (jobs/extract_claims.py OR jobs/extract_rules.py)
    │
    ▼
     claims table (SYSTEM VERSIONED)
    │
    ▼
Embedding Generation (jobs/embed.py)
    │
    ▼
  claim_embeddings table (NOT NULL, HNSW indexed)
  chunk_embeddings table (NOT NULL, HNSW indexed)
    │
    ▼
Alignment (server/alignment.py)
    │
    ▼
  claim_links table (SYSTEM VERSIONED)
    │
    ▼
Verdict Grading (server/verdicts.py)
    │
    ▼
  claim_links.verdict updated
    │
    ▼
Ready for Web App & API
```

### Re-extraction
```python
# For existing database, to apply new extraction rules:
# 1. Apply migration if needed
# 2. Re-extract claims
python jobs/extract_claims.py --all-trilingual --reextract

# --reextract: Deliberately replaces active derived rows
# Their system-versioned history remains available for audit

# 3. Re-embed claims
python jobs/embed.py --all-claims --force

# 4. Re-align pages
# (Trigger via API: POST /pages/{id}/align for each page)
```

## Important Decisions

### D9: Dataset Filtering
- Only load articles whose QID exists in 2+ languages (`--only-shared`)
- Result: 17,431 articles, 54,144 chunks

### D10: Re-import Issue
- Known issue: re-import deletes and reinserts `chunks`
- This fails once claims or embeddings reference them (FKs)
- Needs upsert re-import (not built yet, see Q3)
- For now: first import only, no re-import

### V5: Extraction History
- First extraction pass (looser rules) was wiped
- Including `DELETE HISTORY FROM claims`
- So claim timelines don't show the discarded pass

### V4: Page-Centric Extraction
- 10,385 date claims on 6,206 pages
- 252 counterpart pairs with disagreeing dates on 221 pages
- Pre-alignment SQL check found real cross-edition discrepancies
- Some Julian/Gregorian differences on pre-1918 dates (undetectable by rules)

## Running the Pipeline

### First-Time Setup
```bash
# 1. Download datasets
./db/load/download_datasets.sh small

# 2. Import into MariaDB
pip install -r db/load/requirements.txt
python db/load/import_dump.py --data-dir db/load/data --only-shared

# 3. Extract claims (rules-based for now)
pip install -r jobs/requirements.txt
python jobs/extract_rules.py

# 4. Generate embeddings (CPU for small dataset)
python jobs/embed.py --all-claims --all-chunks

# 5. Start API and web
cd server && uvicorn main:app --port 8000 &
cd web && python -m http.server 8080 &
```

### For Medium/Large Datasets
```bash
# Download medium/large dataset
./db/load/download_datasets.sh medium

# Import (may take longer)
python db/load/import_dump.py --data-dir db/load/data --only-shared

# Extract claims with LLM (when available)
python jobs/extract_claims.py --all-trilingual

# Generate embeddings with GPU
python jobs/embed.py --all-claims --device cuda

# Or use embedder service
python jobs/embed.py --endpoint http://localhost:8001
```

## File Descriptions

### embed.py
- Main embedding generation script
- Supports batch processing
- Can use CPU or GPU
- Stores embeddings in database
- Creates HNSW indexes

**Key Functions**:
- `generate_embeddings(texts: list[str]) -> list[list[float]]`
- `embed_claims(batch_size: int = 100)`
- `embed_chunks(batch_size: int = 100)`
- `main()` - CLI entry point

**CLI Arguments**:
- `--all-claims`: Embed all claims
- `--all-chunks`: Embed all chunks
- `--batch-size`: Batch size (default: 100)
- `--endpoint`: Embedder API endpoint
- `--device`: 'cpu' or 'cuda'
- `--force`: Re-embed even if already exists

### extract_claims.py
- LLM-based claim extraction
- Uses Verda Inference API
- More accurate but requires LLM access

**Key Functions**:
- `extract_claims_from_text(text: str, lang: str) -> list[Claim]`
- `process_article(article: Article) -> list[Claim]`
- `extract_all_articles()`
- `main()` - CLI entry point

**CLI Arguments**:
- `--all-trilingual`: Extract from all tri-lingual articles
- `--reextract`: Replace existing claims
- `--limit`: Limit number of articles
- `--model`: Model to use (default: kimi-k3-1-pro)

### extract_rules.py
- Rules-based claim extraction
- Fast, no LLM required
- Currently active for hackathon

**Key Functions**:
- `extract_date_claims(text: str, lang: str) -> list[Claim]`
- `process_article(article: Article) -> list[Claim]`
- `extract_all_articles()`

**Current Implementation**:
- Extracts birth and death dates from page leads
- Handles fi, sv, en languages
- Skips uncertain dates (c., noin, omkring, etc.)
- Skips old-style calendar notes
- Skips pages about events/periods

## Before You Start

1. Read root AGENTS.md
2. Read docs/assumptions.md (especially D1-D10, V1-V6)
3. Read db/AGENTS.md for database schema
4. Check if LLM is available or use rules-based extraction

## Common Tasks

### Adding a New Claim Type
1. Add extraction logic to extract_claims.py or extract_rules.py
2. Update Claim dataclass if needed
3. Test extraction on sample articles
4. Verify alignment works with new claim type

### Modifying Embedding Configuration
1. Update MODEL_NAME, VECTOR_DIM in embed.py
2. Update schema.sql if dimension changes
3. Re-embed all data
4. Test vector search

### Adding a New Extraction Model
1. Create new extraction script (e.g., extract_llm_v2.py)
2. Set model_version appropriately
3. Test on sample articles
4. Compare with existing extractions

## Gotchas

1. **LLM Not Available**: Falls back to rules-based extraction
   - Check Verda project subscription
   - Use rules-based as fallback

2. **Re-import Issues**: Cannot re-import if FKs exist
   - Need upsert re-import (not yet implemented)
   - For now, only run import once

3. **Memory Issues**: Large datasets may exhaust memory
   - Use smaller batch sizes
   - Process in chunks
   - Use GPU if available

4. **Embedding Dimension**: Must match schema (1024 for multilingual-e5-large)
   - Changing dimension requires schema migration
   - All embeddings must be regenerated

5. **NULL Embeddings**: HNSW requires NOT NULL
   - Must populate all embeddings before creating index
   - Or create column as NOT NULL from start

## Performance Tips

1. **Batch Processing**: Use larger batches for GPU, smaller for CPU
   - GPU: batch_size = 1000
   - CPU: batch_size = 100

2. **Parallel Processing**: Use multiple processes for CPU
   - But be careful with database connections

3. **Memory Management**: Clear CUDA cache between batches
   - `torch.cuda.empty_cache()`

4. **Database Optimization**: Use bulk inserts
   - Reduce number of database queries
   - Use transactions for batch operations

## Testing

### Test Extraction
```bash
# Extract claims from a single article
python -c "
from jobs.extract_rules import extract_date_claims
text = 'Urho Kekkonen syntyi 3. syyskuuta 1900.'
claims = extract_date_claims(text, 'fi')
print(claims)
"
```

### Test Embedding
```bash
# Generate embedding for a single text
python -c "
from jobs.embed import generate_embeddings
embeddings = generate_embeddings(['test text'])
print(embeddings[0][:10])  # First 10 dimensions
"
```

### Common Issues
- **ImportError**: Missing dependencies, run `pip install -r jobs/requirements.txt`
- **ConnectionError**: Database not running, start with `docker compose up -d mariadb`
- **CUDA Error**: GPU not available, use `--device cpu`
- **API Error**: Embedder service not running, start with `docker compose up -d embedder`
- **Memory Error**: Batch size too large, reduce batch size

## Final Notes

The pipeline is the backbone of the project. It must:
1. Extract accurate claims from Wikipedia
2. Generate high-quality embeddings
3. Load data efficiently into MariaDB
4. Scale to large datasets

Current focus:
- Rules-based extraction (works without LLM)
- CPU embedding for small/medium datasets
- GPU embedding for large datasets (when available)

Future improvements:
- LLM-based extraction (when Verda subscription is available)
- Better claim parsing (more types, better accuracy)
- Incremental updates (re-import support)
- Parallel processing for faster throughput
