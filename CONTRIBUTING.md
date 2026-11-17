# Contributing to One Verifiable Record

Thanks for helping make cross-language Wikipedia consistency verifiable. This
project is MIT-licensed (code) with content-derived data under CC BY-SA 4.0 —
see `THIRD-PARTY-NOTICES.md`.

## Getting started

```bash
cp .env.example .env
docker compose up --build
./db/load/download_datasets.sh small
pip install -r db/load/requirements.txt
python db/load/import_dump.py --data-dir db/load/data --only-shared
```

The API reference is at http://localhost:8000/docs, the merge view at
http://localhost:8080. Load the browser extension from `extension/` via
`chrome://extensions` → "Load unpacked" for extension work.

## Where to work

| Area | Path | Guidance |
| --- | --- | --- |
| API server | `server/` | `server/AGENTS.md` |
| Database / pipeline | `db/`, `jobs/`, `embedder/` | `db/AGENTS.md`, `jobs/AGENTS.md` |
| Web app | `web/` | `web/AGENTS.md` |
| Browser extension | `extension/` | `extension/AGENTS.md` |
| Decisions & contracts | `docs/` | `docs/assumptions.md` is the decisions log |

Every directory has an `AGENTS.md`; the root `AGENTS.md` applies everywhere.
Read the one for your area before editing.

## Hard rules

- **Never auto-edit Wikipedia.** Patch drafts are exported for human editors.
- MariaDB VECTOR + HNSW for all vector operations; no external vector store.
- `claims`, `claim_links`, `reviews`, `suggestions` stay SYSTEM VERSIONED.
- The extension must stay read-only on Wikipedia, fail silently when the API
  is unreachable, and never send browsing data.
- Extension/API reads on page views must be precomputed only (no alignment or
  LLM calls in `/api/extension/*`).

## Pull requests

1. Fork, branch (`vibe/<short-slug>`), and keep changes focused.
2. Sign off your commits (`git commit -s`) — DCO.
3. Fill in `.github/PULL_REQUEST_TEMPLATE.md`.
4. CI must pass (syntax/lint checks in `.github/workflows/ci.yml`).
5. If your change touches an API contract, update `docs/page-contract.md` /
   `docs/frontend-contract.md` and `docs/assumptions.md` in the same PR.

## Reporting security issues

See `SECURITY.md` — do not open public issues for vulnerabilities.
