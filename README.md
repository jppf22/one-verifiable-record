# One Verifiable Record

**Cross-language Wikipedia fact-consistency, built on MariaDB Vector + System-Versioned Tables.**
Junction 2026 · MariaDB challenge #307 ("Wikipedia Through Time")

> Wikipedia's language editions are parallel branches of the same truth.
> **One Verifiable Record is the merge tool.**

Pick an article. We decompose every imported source chunk in its fi / sv / en editions into
**atomic claims**, align them across languages with multilingual embeddings inside MariaDB, and
render a **git-diff-style merge view**:
what the editions agree on, where they contradict each other, and what one edition simply does not
know yet. Every verdict ships with **receipts**: the source passage, the revision id, and a deep
link back into Wikipedia.

## Key features

| Feature | Implementation |
| --- | --- |
| Cross-language claim alignment | Every extracted claim is searched in each other available language; dynamic predicate recall plus MariaDB vector search (`VEC_DISTANCE_COSINE`, HNSW index) over multilingual claim embeddings |
| Verdicts with receipts | `supported` / `contradicted` / `unverifiable` + evidence passage, revision id, wiki link |
| Claim provenance — "biography of a sentence" | MariaDB System-Versioned Tables (`claims`, `claim_links`, `reviews`, `suggestions`) |
| Grounded Q&A (RAG track) | The same vector index over article chunks; answers cite per-language sources |
| Human-in-the-loop review | Review console to confirm/fix verdicts; every decision is itself versioned |
| Patch generation | Per-language proposed wikitext edits / talk-page drafts, one-click export (never automated editing) |
| Wikipedia companion extension | Manifest V3 consistency badge + deep links on live Wikipedia articles |

## Architecture

```mermaid
flowchart LR
  subgraph cold["Cold path (this machine, run once)"]
    DS[Wikipedia datasets fi/sv/en] --> IMP["db/load import"] --> EMB["jobs/embed.py (multilingual-e5-large)"]
  end
  subgraph hot["Hot path (local docker-compose)"]
    MARIA[("MariaDB 11.8: VECTOR + HNSW + SYSTEM VERSIONING")] <--> API["FastAPI: align / verdicts / diff / patches / RAG"]
    API --> WEB["Web merge view"]
    EXT["Wikipedia extension"] --> API
    API --> LLM["Any OpenAI-compatible LLM (local or hosted): entailment + synthesis"]
  end
  EMB --> MARIA
```

* **Hot path** (local docker-compose): MariaDB + API + web, all on loopback by default.
* **Cold path** (local, CPU or GPU): the embedding job, against the local `embedder` service or in-process.
* **LLM at query time**: any OpenAI-compatible `/v1` endpoint — a locally deployed model
  (Ollama via the `local-llm` compose profile, vLLM, LM Studio, llama.cpp) or a hosted API
  key (OpenAI, OpenRouter, ...). Configure with `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`.

See `docs/deploy-local.md` for the full local deployment runbook.

## Quick start

Fast path via the root Makefile (`make help` lists everything; full runbook in
`docs/deploy-local.md`):

```bash
make up-llm                 # .env + core stack + local Ollama  (or plain `make up`, LLM-free)
# set LLM_BASE_URL=http://ollama:11434/v1 and LLM_MODEL=qwen3:8b in .env, then:
docker compose exec ollama ollama pull qwen3:8b
make data-small import      # download + import the small dataset
make pipeline               # embed chunks -> extract claims (LLM) -> embed claims
make align PAGE=<id>        # alignment + verdicts for one page (find ids via /api/pages?q=...)
# open http://localhost:8080
```

Step by step with raw commands:

```bash
cp .env.example .env          # MariaDB credentials + AI endpoints (all optional, see below)
docker compose up --build -d  # MariaDB + API + web + local embedder

# Optional local LLM (any OpenAI-compatible endpoint also works via LLM_BASE_URL):
docker compose --profile local-llm up -d ollama
docker compose exec ollama ollama pull qwen3:8b
# .env: LLM_BASE_URL=http://ollama:11434/v1  LLM_MODEL=qwen3:8b

./db/load/download_datasets.sh small
pip install -r db/load/requirements.txt
python db/load/import_dump.py --data-dir db/load/data --only-shared

pip install -r jobs/requirements.txt
python jobs/embed.py --tables chunks   # via the local embedder service (CPU is fine for small)
python jobs/extract_claims.py --pilot 20   # LLM extraction; without an LLM use jobs/extract_rules.py
python jobs/embed.py --tables claims
PAGE_ID=$(curl -s 'localhost:8000/api/pages?q=Finland' | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["page"]["id"])')
curl -X POST localhost:8000/api/pages/$PAGE_ID/align
```

Claim predicates are extractor-generated canonical relations, not an application
allow-list. For existing data made with an older extractor, re-run extraction and
embedding before interpreting coverage. For example, for selected topics:

```bash
python jobs/extract_claims.py --qids Q12345 --reextract
python jobs/embed.py --tables claims
curl -X POST localhost:8000/api/pages/$PAGE_ID/align
```

`--reextract` is deliberately scoped to the selected QIDs and records the
prior system-versioned claim/link rows as history.

### On-demand embeddings for one page

`EMBED_BASE_URL` points at any OpenAI-compatible `/v1/embeddings` endpoint — the
local `embedder` service by default, a local GPU worker
(`docker compose --profile gpu up -d embedder-gpu`, then
`EMBED_BASE_URL=http://embedder-gpu:8002/v1`), or a hosted API. To embed only the
missing current vectors for one page:

```bash
curl -X POST 'http://localhost:8000/api/pages/$PAGE_ID/embed?include_chunks=true'
```

The API sends text batches to the embeddings endpoint but writes vectors through
its own MariaDB connection, so MariaDB is never exposed to the embedding worker.
The route is intentionally page-scoped: a browser cannot start a corpus-wide job.

Open http://localhost:8080 (merge view) or http://localhost:8000/docs (API reference).
`GET /api/health` reports which LLM/embeddings endpoints are configured.

### Refreshing a dataset snapshot

Imports are append-only: a changed article adds its new revision's chunks and retains
the old revision for claim receipts. The merge view and RAG read only the current
article revision. Re-run extraction, claim embedding, and alignment for refreshed
QIDs; this is what allows a corrected Wikipedia revision to resolve a finding.

For an existing database created before this behaviour, apply the one-time migration
before importing:

```bash
docker compose exec -T mariadb sh -lc 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" wikipedia' < db/migrations/001_live_refresh.sql
docker compose exec -T mariadb sh -lc 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" wikipedia' < db/migrations/002_open_claim_taxonomy.sql
```

## Repository layout

```
db/          schema.sql (VECTOR, HNSW, SYSTEM VERSIONING) + dataset loaders
jobs/        GPU embedding pipeline (multilingual-e5-large)
server/      FastAPI: claim alignment, verdict layers, diff engine, patches, RAG
web/         Merge-view web app (static, served by nginx)
extension/   Manifest V3 Wikipedia companion
docs/        local deployment guide
```

## How this uses MariaDB (challenge criteria)

* **Vector, creative use (20%)**: claims and chunks are embedded once; all cross-language matching
  and RAG retrieval are single SQL statements (`VEC_DISTANCE_COSINE` over an HNSW index). No external
  vector store, no special service: the database *is* the retrieval engine.
* **System Versioning (SVT track bonus)**: `claims`, `claim_links`, `reviews`, `suggestions` are
  system-versioned. Any verdict change keeps its full history; the "biography of a sentence"
  click-through (`FOR SYSTEM_TIME ALL`) shows what was claimed, when, and why it changed.
* **Scalability (20%)**: the identical stack runs on the small / medium / large datasets; HNSW +
  a batch embedding job keep the cold path linear. Record timings per size for the demo.
* **Real-world usefulness (20%)**: readers get a trust signal; editors get actionable, cited
  per-language patch drafts instead of vague warnings.
* **Originality (20%)**: language editions as branches — and a merge tool between them.

## Honest limits (and roadmap)

* We do **not** auto-edit Wikipedia: cross-wiki bot editing is prohibited by Wikipedia policy.
  We generate exportable patch drafts for human editors.
* A MediaWiki extension is not deployable in 48h (WMF review + community consensus). Our companion
  is a browser extension; roadmap: Toolforge hosting + user scripts.

## License

MIT
