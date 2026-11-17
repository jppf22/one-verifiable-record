## Summary

- What does this PR change and why?

## Area

- [ ] `server/` API
- [ ] `db/` schema / migrations / loaders
- [ ] `jobs/` pipeline
- [ ] `web/` app
- [ ] `extension/` browser extension
- [ ] `docs/` contracts / decisions
- [ ] packaging / CI / community files

## Contract and decision updates

- [ ] `docs/page-contract.md` / `docs/frontend-contract.md` updated (if API shapes changed)
- [ ] `docs/assumptions.md` updated (if a decision changed)
- [ ] `db/migrations/` has an idempotent migration (if schema changed)

## Safety checks

- [ ] No automatic Wikipedia edits introduced (deep links / copyable drafts only)
- [ ] Extension stays read-only on Wikipedia and fails silently offline
- [ ] No new broad permissions (`permissions` stays `["storage"]`; host permissions stay scoped)
- [ ] No secrets, tokens, or credentials in the diff
- [ ] All vector operations still go through MariaDB VECTOR/HNSW
- [ ] System-versioned tables remain system-versioned

## Verification

- Commands run and results (CI link, local smoke test, endpoint responses):
