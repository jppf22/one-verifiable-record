# Server AGENTS.md - Backend Instructions

**READ THIS BEFORE TOUCHING server/ CODE**
This file overrides the root AGENTS.md for backend-specific instructions.

## Server Overview

FastAPI application providing the core comparison, alignment, verdict, and Q&A functionality.
Serves as the backend for the web app and browser extension.

### Entry Point
- `server/main.py` - FastAPI app initialization
- Run with: `uvicorn main:app --reload --port 8000`
- Docker: `docker compose up --build` (includes MariaDB, API, web, embedder)

### Module Structure
```
server/
├── main.py          # App init, routes, middleware
├── config.py        # Configuration (database URLs, API keys)
├── db.py            # Database connection, session management
├── alignment.py     # Claim alignment (vector matching)
├── verdicts.py      # Verdict grading (deterministic + LLM)
├── diff.py          # Diff engine, comparison payload assembly
├── patches.py       # Patch generation
├── rag.py           # Q&A retrieval
├── pages.py         # Page-centric operations (v2)
├── recheck.py       # Re-check functionality
└── requirements.txt # Python dependencies
```

## Database Connection

### Connection String
Read from environment variables (see `.env.example`):
```python
DATABASE_URL=mysql+pymysql://user:password@localhost:3306/one_verifiable_record
```

### Session Management
- Use `db.py` for session creation
- Always use context managers for sessions
- Commit transactions explicitly
- Rollback on errors

### Example Query
```python
from server.db import get_db

async def get_claims(page_id: int):
    async with get_db() as db:
        result = await db.execute(
            "SELECT * FROM claims WHERE page_id = %s",
            (page_id,)
        )
        return result.fetchall()
```

## Vector Search Patterns

### Basic Vector Search
```python
# Find nearest neighbors for a claim embedding
SELECT 
    id, 
    page_id,
    lang,
    statement_text,
    VEC_DISTANCE_COSINE(embedding, :query_embedding) as distance
FROM claim_embeddings
WHERE VEC_DISTANCE_COSINE(embedding, :query_embedding) <= 0.15
ORDER BY distance ASC
LIMIT 10
```

### HNSW Index Usage
Indexes are pre-created in `db/schema.sql`:
```sql
CREATE INDEX idx_claim_embedding ON claim_embeddings USING hnsw (
    embedding DISTANCE=cosine
);
```

### Important: NOT NULL Columns
HNSW indexes require NOT NULL columns. Embeddings MUST be stored in NOT NULL columns.

## System Versioned Tables (SVT)

### Querying History
```python
# Get all versions of a claim
SELECT * FROM claims 
FOR SYSTEM_TIME ALL 
WHERE id = :claim_id
ORDER BY ROW_START DESC

# Get version as of a specific time
SELECT * FROM claims 
FOR SYSTEM_TIME AS OF :timestamp 
WHERE id = :claim_id
```

### Detecting Stale Links
```python
# Check if a claim has changed since link was computed
SELECT 
    c.id,
    c.ROW_START as claim_version_time,
    cl.computed_at as link_computed_time
FROM claims FOR SYSTEM_TIME ALL c
JOIN claim_links cl ON cl.claim_a = c.id
WHERE cl.id = :link_id
AND c.ROW_START > cl.computed_at + INTERVAL 1 SECOND
```

## Alignment (alignment.py)

### Page-Centric Alignment
- Align claims from a page with its counterparts
- Uses vector similarity + predicate guard
- Default: match_distance = 0.12 (e5 similarity >= 0.88)
- Only align claims with same predicate when both have one

### Key Functions
- `align_article(page_id: int)` - Align all claims for a page's comparison set
- `find_candidates(claim_id: int, counterpart_page_ids: list[int])` - Find nearest claims
- `create_links(claim_a: int, claim_b: int, similarity: float)` - Create claim link

### Candidate Generation
- For each claim on page X, find nearest_k (3) claims on each counterpart
- Store in canonical order (min_id, max_id) to avoid duplicates
- Filter by predicate match when available

## Verdicts (verdicts.py)

### Two-Layer Grading
1. **Deterministic Layer** (fast, no LLM)
   - Same predicate + same value_normalized = supported
   - Same predicate + different value_normalized = contradicted (for dates/numbers)
   - Date comparison: coarser precision ("1900" agrees with "1900-09-03")
   - Differing numbers → pass to LLM layer

2. **LLM Layer** (Verda Inference API)
   - Cross-language entailment
   - Used when deterministic layer cannot decide
   - Model: kimi-k3-1-pro
   - Endpoint: https://inference.verda.com/v1/chat/completions

### Verdict Sources
- `deterministic`: From deterministic layer
- `llm`: From LLM entailment
- `human`: Written only when a review quorum is reached (policy R1, `consensus.py`, docs/review-consensus.md)

### Grading Function
```python
async def grade_link(link_id: int) -> Verdict:
    # 1. Check if claims have changed (stale)
    # 2. Try deterministic comparison
    # 3. Fall back to LLM if needed
    # 4. Update claim_links.computed_at
    # 5. Return verdict
```

## Pages (pages.py)

### Comparison Set
For a page X, the comparison set = X + all counterpart pages from page_links.

### Key Function
```python
async def comparison_set(page_id: int) -> list[Page]:
    """Get page X and all its counterparts."""
    # Returns: [Page X, Page Y (fi), Page Z (sv), ...]
    # Ordered: this page first, then fi, sv, en counterparts
```

### Page Object
```python
@dataclass
class Page:
    id: int
    lang: str
    title: str
    revision_id: int
    url: str
    wiki_url: str
```

## Recheck (recheck.py)

### Recheck Flow
1. Identify links to regrade (focus links + stale links)
2. Re-run alignment for comparison set (optional, can create new links)
3. Re-grade identified links
4. Return RecheckResult

### RecheckResult Structure
```python
@dataclass
class RecheckResult:
    page_id: int
    checked_at: datetime
    before: dict  # conflict, missing_here, only_here, agree counts
    after: dict   # same as before
    new_links: int
    links: list[RecheckLinkResult]

@dataclass
class RecheckLinkResult:
    id: int
    before: Verdict | None  # None if link just created
    after: Verdict
    changed: bool
    resurfaced: bool  # was contradicted before and still contradicted
    regraded: bool
```

### Stale Link Detection
A link is stale if either of its claims has a newer SVT row since the link's computed_at.

## Diff (diff.py)

### Comparison Payload Assembly
```python
async def build_comparison(page_id: int) -> ComparisonPayload:
    """Build the full comparison payload for a page."""
    # 1. Get comparison set
    # 2. Get all claims in set
    # 3. Get all links between claims
    # 4. Build rows from connected components
    # 5. Calculate summary counts
    # 6. Return payload
```

### Row Construction
Rows are built from connected components of claim_links within the comparison set.

Each row contains:
- `key`: Unique identifier (e.g., "r-1042")
- `status`: From page's perspective (conflict, missing_here, only_here, agree, etc.)
- `kind`: Most common claim kind in row
- `predicate`: Most common claim predicate in row
- `here`: Claims from this page in the row
- `there`: Claims from counterparts, grouped by lang
- `links`: All claim_links in this row

### Status Determination
From the perspective of "this page":
1. If any claim here has a contradicted link → `conflict`
2. If no claim here but counterpart has claim → `missing_here`
3. If claim here but no counterpart claim → `only_here`
4. If all links are supported → `agree`
5. If uncertain → `possible_conflict` or `possible_match`

## Patches (patches.py)

### Draft Generation
Generate patch drafts for contradicted links and gaps:
- Correction drafts for conflicts
- Addition drafts for missing facts
- Notice drafts for gaps

### Draft Types
- `correction`: Fix a contradicted claim
- `addition`: Add a missing claim
- `notice`: Talk page notice about discrepancy

### Deduplication
Exact match on (page_id, patch_type, wikitext).

## RAG (rag.py)

### Q&A Flow
1. Embed the query
2. Search for relevant chunks in MariaDB
3. Generate answer using LLM (Verda Inference)
4. Include citations with revision IDs and URLs

### Chunk Retrieval
```python
SELECT 
    id,
    page_id,
    lang,
    chunk_text,
    VEC_DISTANCE_COSINE(embedding, :query_embedding) as distance
FROM chunk_embeddings
WHERE VEC_DISTANCE_COSINE(embedding, :query_embedding) <= 0.15
ORDER BY distance ASC
LIMIT 5
```

## API Endpoints

### Page Endpoints
- `GET /api/pages` - List pages with optional filters
- `GET /api/pages/conflicted` - Pages with conflicts (landing)
- `GET /api/pages/by-title` - Get page by lang + title
- `GET /api/pages/{id}` - Get page with counterparts
- `POST /api/pages/{id}/align` - Run alignment
- `GET /api/pages/{id}/compare` - Get comparison payload
- `POST /api/pages/{id}/recheck` - Recheck page

### Verdict Endpoints
- `POST /api/reviews` - Submit human review
- `GET /api/reviews` - Get reviews

### Patch Endpoints
- `GET /api/pages/{id}/suggestions` - Get drafts
- `POST /api/suggestions/{id}/export` - Export draft

### Q&A Endpoints
- `GET /api/qa` - Question answering

### Extension Endpoints
- `GET /api/badge` - Extension badge data
- `GET /api/extension/resolve` - Extension resolver

## Configuration (config.py)

### Environment Variables
```python
DATABASE_URL=mysql+pymysql://user:pass@host:port/db
VERDA_API_KEY=your-verda-api-key
VERDA_BASE_URL=https://inference.verda.com/v1
WEB_BASE_URL=http://localhost:8080
```

### Settings Class
```python
@dataclass
class Settings:
    database_url: str
    verda_api_key: str
    verda_base_url: str = "https://inference.verda.com/v1"
    web_base_url: str = "http://localhost:8080"
    debug: bool = True
```

## Testing

### Manual Testing
1. Start docker-compose stack
2. Import data: `python db/load/import_dump.py`
3. Run embeddings: `python jobs/embed.py`
4. Test endpoints with curl:
   ```bash
   curl http://localhost:8000/api/pages/conflicted
   curl http://localhost:8000/api/pages/by-title?lang=fi&title=Urho_Kekkonen
   ```

### Common Issues
- **No data**: Run import and embedding jobs first
- **Vector index missing**: Check schema.sql for HNSW index creation
- **LLM not available**: Falls back to deterministic layer only
- **Stale links**: Recheck will identify and regrade

## Coding Standards

### Style
- Match existing style (snake_case, type hints, async/await)
- Use dataclasses for data structures
- Use Pydantic models for API responses
- Keep functions focused and short

### Error Handling
- Use HTTP status codes appropriately
- Return JSON error responses
- Log errors with context
- Don't expose internal details in production

### Performance
- Use database indexes effectively
- Vector search with HNSW is fast (milliseconds)
- LLM calls are the bottleneck (rate limit, latency)
- Cache where appropriate (but hackathon time constraints may limit this)

## Important Implementation Notes

### Page-Centric Model (v2)
- The unit of comparison is a **page**, not an entity or QID
- `wikidata_qid` is import-time only, never used in logic
- All operations work on the comparison set (page + counterparts)
- Links are between claims on **different pages**

### Deterministic Layer
- Only compares claims with the **same predicate**
- Dates: compare at coarser precision
- Numbers: if different, pass to LLM layer
- Equal numbers: always `supported`

### LLM Layer
- Currently **not available** (Verda project not subscribed)
- Falls back to deterministic layer
- When available: use `kimi-k3-1-pro` via Verda Inference API
- Embeddings: use `embedder/` container (OpenAI-compatible API)

### Recheck Behavior
- Focus links are always regraded
- Stale links (claim changed since computed_at) are always regraded
- Other links are only regraded if claims have newer SVT rows
- No-op updates are not written (no new SVT row)

### Alignment Behavior
- Creates candidate pairs in canonical order (min_id, max_id)
- Only when both claims have predicates, they must match
- match_distance default: 0.12 (e5 similarity >= 0.88)
- nearest_k: 3 candidates per claim per counterpart

## Before You Start

1. Read root AGENTS.md
2. Read docs/assumptions.md (especially server assumptions S1-S6, V-S1-V-S9)
3. Read docs/page-contract.md (API contract)
4. Check the current state of the code you're about to modify
5. Understand the data flow: Database → alignment → verdicts → diff → API

## Common Tasks

### Adding a New Endpoint
1. Add route in `main.py`
2. Add handler function in appropriate module
3. Add Pydantic model for request/response if needed
4. Test with curl

### Modifying Verdict Logic
1. Update `verdicts.py`
2. Check if recheck logic needs updating
3. Update assumptions.md if changing behavior
4. Test with known conflict/gap/agree cases

### Adding a New Claim Type
1. Update claim extraction in `jobs/extract_claims.py` or `jobs/extract_rules.py`
2. Update database schema if needed
3. Update alignment if new predicate matching needed
4. Test extraction and alignment

## Gotchas

1. **SVT and Row Start Times**: ROW_START has microseconds, computed_at has seconds. Add 1 second buffer for stale detection.
2. **Vector Index Requirements**: HNSW requires NOT NULL columns. Don't allow NULL embeddings.
3. **Canonical Order**: Always store links as (min_id, max_id) to avoid duplicates.
4. **Page Links**: Stored in both directions (page_a -> page_b and page_b -> page_a).
5. **Embedding Compression**: e5-large embeddings may be compressed, affecting distance thresholds.

## Helpful Queries

### Find Pages with Conflicts
```sql
SELECT 
    p.id,
    p.lang,
    p.title,
    COUNT(DISTINCT cl.id) as conflict_count
FROM pages p
JOIN claim_links cl ON cl.claim_a = p.id OR cl.claim_b = p.id
WHERE cl.verdict = 'contradicted'
GROUP BY p.id, p.lang, p.title
HAVING conflict_count > 0
ORDER BY conflict_count DESC
LIMIT 50
```

### Check Alignment for a Page
```sql
SELECT 
    cl.id as link_id,
    ca.id as claim_a_id,
    ca.lang as lang_a,
    ca.statement_text as text_a,
    cb.id as claim_b_id,
    cb.lang as lang_b,
    cb.statement_text as text_b,
    cl.verdict,
    cl.similarity
FROM claim_links cl
JOIN claims ca ON ca.id = cl.claim_a
JOIN claims cb ON cb.id = cl.claim_b
WHERE ca.page_id = :page_id OR cb.page_id = :page_id
```
