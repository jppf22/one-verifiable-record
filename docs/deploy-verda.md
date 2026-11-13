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
4. `docker compose up -d --build`. The schema auto-loads on MariaDB's first boot.
5. Import datasets:
   ```bash
   ./db/load/download_datasets.sh small
   pip install -r db/load/requirements.txt
   python db/load/import_dump.py --data-dir db/load/data
   ```
6. Embeddings: run `jobs/embed.py` on the VM for the small dataset, or spin up the GPU
   instance and run it there against the VM's MariaDB (`DATABASE_HOST=<vm-ip>`), then
   destroy the GPU instance.
7. Pre-warm the demo article(s):
   ```bash
   curl -X POST localhost:8000/api/align/Q12345   # alignment + verdicts
   curl localhost:8000/api/merge/Q12345 > /dev/null # warm the merge view
   ```
8. Extension config: set the API base (popup) to the VM's public address. Add the
   production origin to `host_permissions` in `extension/manifest.json`.

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
`http://localhost:8000`. If the VM is unreachable mid-demo, switch the extension
config back to localhost and use the pre-warmed local merge view.
