# Deploying One Verifiable Record locally

Everything runs on one machine with docker-compose. Nothing depends on any
external hosting: MariaDB, the API, the web app, the embedding service and
(optionally) the LLM all run locally, and every AI endpoint is a generic
OpenAI-compatible `/v1` URL — a local model or a hosted API key.

## Shape

| Piece | Where it runs | Notes |
| --- | --- | --- |
| Hot path | local docker-compose (`mariadb`, `server`, `web`) | MariaDB + API + web; all ports bound to loopback by default |
| Cold path | this machine | embedding job (`jobs/embed.py`), on CPU or a local GPU |
| Embeddings | `embedder` compose service (CPU) or `embedder-gpu` (`--profile gpu`) | OpenAI-compatible `/v1/embeddings`; or any hosted/local endpoint via `EMBED_BASE_URL` |
| Query-time LLM | any OpenAI-compatible `/v1/chat/completions` | local model (Ollama profile below, vLLM, LM Studio, llama.cpp) or hosted API key |

## Steps

Shortcut: the root `Makefile` wraps these steps (`make help`) — e.g.
`make up-llm`, `make data-small`, `make import`, `make pipeline`,
`make align PAGE=<id>`, `make check`. The raw commands are below.

1. Requirements: Docker with the compose plugin. For the optional local GPU
   embedder, the NVIDIA container toolkit.
2. `cp .env.example .env` and adjust:
   - MariaDB credentials (any values; they stay on loopback).
   - AI inference — pick one:
     - **Local model via the bundled Ollama profile:**
       ```bash
       docker compose --profile local-llm up -d ollama
       docker compose exec ollama ollama pull qwen3:8b     # any chat model
       ```
       then in `.env`: `LLM_BASE_URL=http://ollama:11434/v1`, `LLM_MODEL=qwen3:8b`.
     - **Any other local server** (vLLM, LM Studio, llama.cpp on the host):
       `LLM_BASE_URL=http://host.docker.internal:8000/v1` (port of your server),
       `LLM_MODEL=<model id>`.
     - **Hosted API key**: `LLM_BASE_URL=https://api.openai.com/v1` (or OpenRouter,
       Together, Groq, Mistral, DeepSeek, ...), `LLM_API_KEY=...`, `LLM_MODEL=...`.
       `OPENAI_BASE_URL`/`OPENAI_API_BASE`/`OPENAI_API_KEY` work as aliases.
     - **No LLM at all**: leave `LLM_BASE_URL`/`LLM_MODEL` empty. Verdicts fall back
       to the deterministic layer; `jobs/extract_rules.py` extracts claims without
       an LLM.
   - Optional: `LLM_EXTRA_BODY` (JSON merged into every chat request, e.g.
     `{"thinking": {"type": "disabled"}}`), `LLM_REASONING_EFFORT` (only sent
     when set).
   - Embeddings default to the local `embedder` service
     (`EMBED_BASE_URL=http://embedder:8001/v1`). For a **local GPU** worker:
     ```bash
     EMBED_API_KEY=<token> docker compose --profile gpu up -d embedder-gpu
     ```
     and set `EMBED_BASE_URL=http://embedder-gpu:8002/v1` in `.env`. Any other
     OpenAI-compatible embeddings endpoint works the same way. Note: `EMBED_MODEL`
     dimensions must match `VECTOR(1024)` in `db/schema.sql`.
3. `docker compose up -d --build`. The schema and migrations auto-load on
   MariaDB's first boot. For an existing database, apply migrations first:
   ```bash
   docker compose exec -T mariadb sh -lc 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" wikipedia' < db/migrations/001_live_refresh.sql
   docker compose exec -T mariadb sh -lc 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" wikipedia' < db/migrations/002_open_claim_taxonomy.sql
   ```
4. Import datasets:
   ```bash
   ./db/load/download_datasets.sh small
   pip install -r db/load/requirements.txt
   python db/load/import_dump.py --data-dir db/load/data --only-shared
   ```
5. Build the comparison corpus (cold path, fully local):
   ```bash
   python jobs/embed.py --tables chunks          # via the embedder service, or in-process
   python jobs/extract_claims.py --pilot 20      # LLM extraction (needs LLM_BASE_URL/LLM_MODEL)
   python jobs/embed.py --tables claims
   ```
   For the large dataset, run `jobs/embed.py` on a machine with a GPU (it
   auto-detects CUDA), or point `EMBED_BASE_URL` at the `embedder-gpu` service.
6. Align a page (find its id through the API):
   ```bash
   curl -s 'localhost:8000/api/pages?q=Finland' | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["page"]["id"])'
   curl -X POST localhost:8000/api/pages/<page_id>/align
   ```
7. On-demand embedding for one page (writes vectors through the API's own
   MariaDB connection, so the DB is never exposed to the embedding worker):
   ```bash
   curl -X POST 'localhost:8000/api/pages/<page_id>/embed?include_chunks=true'
   ```
8. Open http://localhost:8080 (web app; nginx also proxies `/api`) or
   http://localhost:8000/docs (API reference). `GET /api/health` shows which
   LLM/embeddings endpoints are configured.

### Refreshing a page

Re-importing a changed snapshot preserves prior chunks and claims as provenance.
After a dataset refresh, run claim extraction, claim embedding, and
`POST /api/pages/<page_id>/align` for the changed pages. The merge view then uses
the current imported revisions while history retains the prior material.

## Scale proof (challenge requirement: run at small / medium / large)

For each dataset size, record and keep:
- import time (`import_dump.py`)
- embed time and throughput (`jobs/embed.py` prints rows/s)
- alignment latency per page (`POST /api/pages/{id}/align`)
- query latency (`GET /api/pages/{id}/compare`, `GET /api/qa`)

## Publishing (optional)

Everything binds to loopback by default. To publish the web app on a server,
set `OVR_PUBLIC_BIND=0.0.0.0` in `.env` and restart `web`; open firewall port
8080 only. FastAPI (8000) and MariaDB (3306) stay loopback-only. The API applies
a small per-IP POST limit to protect expensive re-checks. Point the browser
extension's API/web URLs at `http://<host>:8080`.
