# Deploying One Verifiable Record on Verda

Target: Junction 2026 demo. Building on Verda also makes the project eligible for the
**"Best use of Verda" bounty** (any team using Verda services on any challenge).

## Shape

| Piece | Verda resource | Notes |
| --- | --- | --- |
| Hot path | 1 CPU VM, 4–8 vCPU, 16–32 GB RAM, NVMe ≥ 100 GB | MariaDB + API + web via docker-compose |
| Cold path | GPU instance, ephemeral | embedding job (`jobs/embed.py`); destroy when done |
| Query-time LLM | Verda Inference API | entailment + RAG synthesis (Kimi K3.1 Pro) |

## Steps

1. Provision the CPU VM (Ubuntu 24.04), attach the NVMe volume.
2. Install Docker: `curl -fsSL https://get.docker.com | sh` (includes the compose plugin).
3. Clone the repo; `cp .env.example .env`. Fill `VERDA_API_KEY` (redeem credits via the
   Junction Platform coupon code; Verda's Junction blog post documents redemption) and
   confirm `VERDA_BASE_URL` against the current Verda docs.
4. `docker compose up -d --build`. The schema and refresh migration auto-load on
   MariaDB's first boot. For an existing database, apply the migration first:
   ```bash
   docker compose exec -T mariadb sh -lc 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" wikipedia' < db/migrations/001_live_refresh.sql
   ```
5. Import datasets:
   ```bash
   ./db/load/download_datasets.sh small
   pip install -r db/load/requirements.txt
   python db/load/import_dump.py --data-dir db/load/data --only-shared
   ```
6. Build the comparison corpus (the old sequence stopped after chunks and therefore
   could not produce a merge):
   ```bash
   python jobs/embed.py --tables chunks
   python jobs/extract_claims.py --pilot 20
   python jobs/embed.py --tables claims
   ```
   Run embeddings on the VM for the small dataset, or on a GPU instance against the
   VM's MariaDB, then destroy the GPU instance.
7. Pre-warm the demo article(s):
   ```bash
   curl -X POST localhost:8000/api/align/Q12345   # alignment + verdicts
   curl localhost:8000/api/merge/Q12345 > /dev/null # warm the merge view
   ```
8. To publish the demo, set `OVR_PUBLIC_BIND=0.0.0.0` in `.env` and restart the
   `web` service. Open firewall port 8080. Only nginx/web is published; FastAPI and
   MariaDB remain loopback-only. Configure both extension URLs as
   `http://<vm-ip>:8080`; Chrome requests permission for that host when saved.
   The API applies a small per-IP POST limit to protect expensive re-checks.

### Refreshing a page

Re-importing a changed snapshot preserves prior chunks and claims as provenance. It
does not automatically call Wikipedia: after a dataset refresh, run claim extraction,
claim embedding, and `POST /api/align/{qid}` for the changed QID. The merge view then
uses the current imported revisions while history retains the prior material.

## Scale proof (challenge requirement: run at small / medium / large)

For each dataset size, record and keep for the demo:
- import time (`import_dump.py`)
- embed time and throughput (`embed.py` prints rows/s)
- alignment latency per article (`POST /api/align/{qid}`)
- query latency (`GET /api/merge/{qid}`, `GET /api/qa`)

## Demo pre-warm (before judging)

- Pick 2–3 demo articles with known fi/sv/en divergences.
- Align + verdict + suggestions ahead of time; leave the merge views open in tabs.
- Record a screen capture of the full flow as a network-failure fallback.

## Fallback

The identical docker-compose stack runs on a laptop; the extension defaults to
`http://localhost:8080` (the nginx web app also proxies `/api`). If the VM is
unreachable mid-demo, switch both extension URLs back to localhost and use the
pre-warmed local merge view.
