# One Verifiable Record - Root AGENTS.md

** READ THIS FIRST ** - This is the root instruction file for all agents working on One Verifiable Record.
All other AGENTS.md files inherit from and may override these instructions.

## Project Identity

**One Verifiable Record** is a Junction 2026 hackathon project (MariaDB Challenge #307: "Wikipedia Through Time").
We treat Wikipedia's fi/sv/en language editions as parallel branches of the same truth and built a merge tool.

**Core Promise**: Cross-language Wikipedia fact-consistency with receipts.
- Atomic claims extracted from each language edition
- Matched across languages via multilingual embeddings (MariaDB vector search)
- Verdicts: supported / contradicted / unverifiable / definition_mismatch
- Every verdict includes: source passage, revision id, Wikipedia deep link
- Human-in-the-loop review is a first-class feature
- Patch generation (exportable .wiki files, never auto-applied)

## Architecture (Must Understand)

```
┌─────────────────────────────────────────────────────────────────┐
│                      HOT PATH (Verda CPU VM)                      │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  MariaDB 11.8                                               │  │
│  │    - VECTOR(1024) columns + HNSW index                      │  │
│  │    - SYSTEM VERSIONED TABLES (claims, claim_links, reviews)  │  │
│  │    - Single-SQL vector matching (VEC_DISTANCE_COSINE)       │  │
│  └─────────────────┬───────────────────────────────────────────┘  │
│                    │                                              │
│  ┌─────────────────▼─────────────────┐                         │
│  │         FastAPI Server            │                         │
│  │  server/main.py                  │                         │
│  │  - /api/pages/* (page-centric v2)│                         │
│  │  - /api/align, /api/verdicts    │                         │
│  │  - /api/diff, /api/patches      │                         │
│  │  - /api/rag (Q&A)                │                         │
│  └─────────────────┬─────────────────┘                         │
│                    │                                              │
│  ┌─────────────────▼─────────────────┐                         │
│  │      Web App (nginx)             │                         │
│  │  web/index.html                  │                         │
│  │  - Vanilla HTML/CSS/JS           │                         │
│  │  - No build step                 │                         │
│  │  - Routes: #/page/{lang}/{title} │                         │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │      Browser Extension (Manifest V3)                        │  │
│  │  extension/                                     │  │
│  │  - Badge with conflict/gap counts                            │  │
│  │  - Inline claim highlighting (Custom Highlight API)          │  │
│  │  - Deep links to comparison view                             │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    COLD PATH (GPU Instance)                        │
│  - jobs/embed.py (multilingual-e5-large)                        │
│  - jobs/extract_claims.py (LLM extraction)                     │
│  - jobs/extract_rules.py (rules-based fallback)                 │
│  - embedder/ (OpenAI-style API for embeddings)                  │
└─────────────────────────────────────────────────────────────────┘
```

## Critical Constraints (Never Violate)

### MariaDB Challenge Requirements (20% each criterion)
1. **Vector Use**: MUST use MariaDB VECTOR type + HNSW index for ALL vector operations
   - No external vector stores (no Pinecone, no Weaviate, no Qdrant)
   - Vector distance: `VEC_DISTANCE_COSINE`
   - Embeddings stored in NOT NULL columns for HNSW compatibility

2. **System Versioning**: MUST use SVT on core tables
   - `claims`, `claim_links`, `reviews`, `suggestions` are SYSTEM VERSIONED
   - History query: `FOR SYSTEM_TIME ALL` or `AS OF`
   - Every change creates a new version row

3. **Scalability**: MUST work with small/medium/large datasets
   - Same code path for all sizes
   - Batch processing for embeddings

4. **Real-world Usefulness**: Verdicts must have receipts
   - Source passage (statement_text)
   - Revision ID (revision_id)
   - Wikipedia deep link (url)

### Wikipedia Policy
- **NO automated editing** - Cross-wiki bot editing is prohibited
- **Export only** - Generate patch drafts for human review
- **Never submit** - Users must manually apply patches

### Deployment
- **Target**: Verda cloud
  - Hot path: CPU VM (4-8 vCPU, 16-32 GB RAM, NVMe)
  - Cold path: GPU instance (destroy after embedding run)
- **LLM**: Verda Inference API (`https://inference.verda.com/v1`)
  - Model: `kimi-k3-1-pro`
  - Note: `/v1/embeddings` not available, use `embedder/` container

## Data Model (Page-Centric v2)

The project switched from entity/QID-based to **page-based** comparison on 2026-11-14.

### Core Tables
- `articles`: One row per Wikipedia page (lang, title, revision_id, url)
- `page_links`: Interlanguage links (page_a <-> page_b, both directions)
- `claims`: Atomic facts extracted from pages (page_id, lang, kind, subject, predicate, value_text, statement_text, revision_id)
  - SYSTEM VERSIONED
- `claim_embeddings`: Vector embeddings for claims (NOT NULL, HNSW indexed)
- `claim_links`: Links between claims across pages (claim_a, claim_b, verdict, similarity, evidence, computed_at)
  - SYSTEM VERSIONED
- `reviews`: Human reviews of claim links (claim_link_id, reviewer, decision, note)
  - SYSTEM VERSIONED
- `suggestions`: Patch drafts (page_id, patch_type, wikitext, status)
  - SYSTEM VERSIONED

### Verdict Model
- `supported`: Claims agree across languages
- `contradicted`: Claims contradict each other
- `unverifiable`: Cannot determine truth value
- `definition_mismatch`: Same name, different concept (false positive)
- `unmatched`: Not yet aligned

**Two-layer grading**:
1. Deterministic: Dates/numbers on value_normalized (exact match)
2. LLM: Cross-language entailment (Verda Inference API)

### Status Model (Page-Centric)
From the perspective of "this page" being viewed:
- `conflict`: This page's claim contradicts a counterpart
- `possible_conflict`: Likely incompatible, needs review
- `missing_here`: Counterpart has it, this page doesn't
- `only_here`: This page has it, no counterpart does
- `possible_match`: Likely equivalent, needs review
- `agree`: All counterparts agree with this page

## API Contract (See docs/page-contract.md for details)

### Current Endpoints (Page-Centric v2)
- `GET /api/pages?q=&lang=` - Search pages
- `GET /api/pages/conflicted` - Pages with conflicts (landing page)
- `GET /api/pages/by-title?lang=&title=` - Get page by title
- `GET /api/pages/{id}` - Get page with counterparts
- `POST /api/pages/{id}/align` - Run alignment for comparison set
- `GET /api/pages/{id}/compare` - Get comparison payload
- `POST /api/pages/{id}/recheck` - Re-check page (focus_link_ids optional)
- `GET /api/pages/{id}/suggestions` - Get patch drafts
- `POST /api/suggestions/{id}/export` - Mark draft as exported
- `POST /api/reviews` - Submit human review
- `GET /api/reviews?link_id=` - Get reviews for link
- `GET /api/claims/{id}/history` - Get claim history (SVT)
- `GET /api/qa?page=&q=&lang=` - Q&A
- `GET /api/badge?lang=&title=` - Extension badge
- `GET /api/extension/resolve` - Extension resolver

### Legacy Endpoints (Deprecated)
- QID-based endpoints (`/articles`, `/merge/{qid}`, etc.) - REMOVED
- Do NOT use in new code

## Frontend Architecture

### Structure
```
web/
├── index.html          # Main entry point
├── css/
│   ├── base.css        # Global styles
│   └── merge.css       # Merge view styles
│   └── panels.css      # Panel styles
└── js/
    ├── api.js          # API client (fetch wrappers)
    ├── bus.js          # Event bus (on/emit)
    ├── util.js         # Utilities ($, $$, el, esc, toast)
    ├── main.js         # App shell, routing
    ├── router.js       # Route handling
    ├── search.js       # Search functionality
    ├── merge.js        # Merge view
    ├── review.js       # Review UI
    ├── keyboard.js     # Keyboard shortcuts
    ├── history.js      # SVT timeline
    ├── suggestions.js  # Patch export
    └── qa.js           # Q&A panel
```

### Routes
- `#/` - Landing page (search + conflicted pages list)
- `#/page/{lang}/{title}` - Comparison view (spaces as `_`)

### Bus Events
- `page:loaded {pageId, data}` - Article loaded
- `page:reload {pageId}` - Reload page
- `recheck:done {pageId, result, reason, linkIds}` - Re-check completed
- `history:open {claimId}` - Open history dialog

## Development Workflow

### Before Touching Code
1. Read this file (AGENTS.md at repo root)
2. Read the subdirectory AGENTS.md if one exists
3. Read `docs/assumptions.md` for current decisions
4. Read `docs/page-contract.md` for API details
5. Read `docs/project-context.md` for background

### Making Changes
1. **Read first**: Never edit a file you haven't read in this session
2. **Minimal diff**: Change only what's necessary
3. **Match style**: Follow existing indentation, naming, error handling
4. **Test**: Verify changes work with the full docker-compose stack
5. **Document**: Update assumptions.md if you make a decision

### Git Discipline
- Commit messages: Include "Generated by Mistral Vibe" and "Co-Authored-By: Mistral Vibe <vibe@mistral.ai>"
- Branch: Work on `frontend-interactivity`, PR to `main`
- Push: Only after testing with docker-compose

## Testing Strategy

### Unit Tests
- Not yet implemented (hackathon priority: working demo)
- Add if time permits

### Integration Tests
1. Start stack: `docker compose up --build`
2. Import data: `python db/load/import_dump.py`
3. Run embedding: `python jobs/embed.py`
4. Test API: `curl http://localhost:8000/api/pages/conflicted`
5. Test web: Open `http://localhost:8080`

### Manual Testing
- Search for articles with conflicts
- Open comparison view
- Review a verdict (Agree/Disagree/Fix)
- Export a patch draft
- Check SVT timeline for a claim

## File Ownership & Responsibilities

| Directory | Owner | Responsibilities |
|-----------|-------|-----------------|
| `server/` | Backend | FastAPI endpoints, business logic, database queries |
| `web/` | Frontend | UI, user interactions, API client |
| `extension/` | Extension | Browser extension, badge, inline highlighting |
| `db/` | Data | Schema, migrations, loaders |
| `jobs/` | Pipeline | Embedding, claim extraction |
| `embedder/` | Service | Embedding API (OpenAI-compatible) |
| `docs/` | All | Documentation, contracts, decisions |

## Common Patterns

### API Responses
All endpoints return JSON. Errors use HTTP status codes:
- 404: Not found
- 400: Bad request
- 500: Server error

### Database Queries
- Use parameterized queries (prevent SQL injection)
- For vector search: `VEC_DISTANCE_COSINE(vector_col, ?) <= threshold`
- For SVT: `FOR SYSTEM_TIME ALL` or `AS OF timestamp`
- HNSW index: Create with `CREATE INDEX idx_name ON table_name USING hnsw (vector_col DISTANCE=cosine)`

### Frontend
- Vanilla JS (ES6+), no framework
- Native ES modules (`<script type="module">`)
- CSS: Modern CSS with flexbox/grid
- No build step

## Important Decisions (See docs/assumptions.md)

Key decisions already made:
- D1: Embeddings in separate tables (chunk_embeddings, claim_embeddings) - NOT NULL, HNSW
- D2: Claims extracted with LLM (Verda, Kimi K3.1 Pro) when available
- D3: Rules-based extraction (extract_rules.py) as fallback
- V1-V6: Page-centric model (not entity/QID based)
- A1-A6: Alignment and re-check behavior
- S1-S6: Server implementation details
- W1-W11: Web frontend behavior
- P1-P6: Panel behavior

## Open Questions (Check docs/assumptions.md)

- Q1: Should human reviews override machine verdicts? (Currently: no, re-check only)
- Q2: Should re-check fetch live Wikipedia? (Currently: no, uses DB snapshot)
- Q3: How to handle re-import with changed revisions? (Currently: not implemented)

## Getting Help

1. Check `docs/assumptions.md` for decisions
2. Check `docs/page-contract.md` for API details
3. Check existing code for patterns
4. Ask the user if truly stuck

## Emergency Contacts

User: jppf22
Project: Junction 2026 Hackathon
Location: Espoo, Finland
Timezone: EET (UTC+2)

## Hackathon Timeline

- **h0-4**: Schema + ingestion + vector matching on small dataset
- **h4-10**: Claim pipeline (extract -> embed -> MariaDB)
- **h10-20**: Verdict layers (deterministic first, then LLM)
- **h20-32**: Reviewer console + merge view + extension
- **h32-40**: SVT provenance click-through
- **h40-48**: Large dataset scale proof, demo freeze, rehearse

Current state: Frontend interactivity (merge view, review, SVT timeline, patch export)

## Success Criteria

1. **MariaDB features visible**: Vector search and SVT must be demonstrable
2. **Cross-language comparison works**: Can compare fi/sv/en articles
3. **Verdicts with receipts**: Every verdict shows source, revision, link
4. **Human review**: Can agree/disagree/fix with notes
5. **Patch export**: Can generate and download .wiki patch files
6. **SVT timeline**: Can view history of a claim
7. **Extension badge**: Shows conflict/gap counts on Wikipedia pages
8. **Demo ready**: Can demo to judges with pre-warmed articles

## Final Notes

- This is a 48-hour hackathon project - perfection is not the goal
- Working demo > polish
- Core story (merge view with receipts) > nice-to-haves
- MariaDB features must be visible to judges
- Document all decisions in `docs/assumptions.md`
