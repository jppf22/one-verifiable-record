# Agent coordination (parallel agents in one working tree)

Several agents (Kiro, Codex, Mistral Vibe) edit this repository and share one docker
stack and one database at the same time. This file is how we stay out of each other's
way. `docs/assumptions.md` stays the record of decisions; this file only says who is
working on what right now.

## Agreement
1. **Claim before you edit.** Add an entry below with the files, tables and containers you
   will change, and remove or close it when you are done.
2. **Respect open claims.** Don't edit a file another open entry lists. If you need to,
   append a "Request" line under that entry and work on something else meanwhile.
3. **Targeted edits only in shared files.** Re-read a file right before editing it and
   change only the lines you need. Never rewrite or revert a file you didn't create in your
   task, and never `git checkout`/`git stash`/`git reset` the shared tree.
4. **Shared database.** No destructive statements (DELETE, DROP, re-extraction) on rows
   another agent's work depends on: reviews, seeded demo votes, resolutions, suggestions.
   Prefer additive, idempotent migrations. Test risky changes on a disposable copy.
5. **Shared containers.** Rebuild only the container your change needs
   (`docker compose up -d --build server`), and say so in your entry.
6. **Contracts.** API additions go in `docs/page-contract.md` and under "Contract change
   requests" in `docs/frontend-contract.md`.
7. **No commits or pushes** of another agent's uncommitted files.

## Open entries

### Kiro (session sess_383469e3): CLOSED 2026-11-14 17:10 CET
Done: `server/llm.py`, `server/gate.py`, `server/regate.py` (new); targeted edits in
`server/verdicts.py`, `server/diff.py` (`build_rows`, `connects`), `server/config.py`,
`web/js/merge.js`, `web/css/merge.css`; migration 005 applied (verdict `different_fact`);
server + web rebuilt; Daniel Tilas re-gated (assumptions G1-G6).
- Note to sess_61787768: re-aligning Sipilä through `POST /api/pages/{id}/align` is now
  gated automatically (about 12 batched LLM calls for its 208 claims). If you keep the
  current Sipilä claims instead of re-extracting, run
  `docker exec -w /app ovr-server python regate.py --title "Sipilän hallitus" --apply`
  to clear its 318 "LLM error" links.

### Kiro (session sess_61787768): CLOSED 2026-11-14 20:10 CET (stopped by the owner)
- Not finished: my Sipilä cabinet re-extraction (25 claims per page). Antigravity
  (c425fdc3) has since re-extracted Sipilä at 8 claims per page; that result stands and
  Sipilä is theirs. No job of mine is running and none will be restarted.
- Not verified after Antigravity's changes: the Sipilä and Oskari Tokoi comparison views.
Original entry, for reference (started 2026-11-14 16:50 CET):
Goal: clearer review, fewer claims, "fix it in every language" (assumptions C1–C7), then:
- Predicate aliases so equivalent facts pair across languages (`birth_date` = `date_of_birth`).
  Files: `server/predicates.py` (new), `server/alignment.py`, targeted edits in
  `server/verdicts.py` (`_same_fact`, `_predicate_match_score` only).
- Re-align the two LLM-extracted topics (Daniel Tilas, Sipilä cabinet) through the API.
- Fewer claims for the Sipilä cabinet (0 reviews): page-level cap in
  `jobs/extract_claims.py` (new option, defaults unchanged), then re-extract that one
  topic only. Daniel Tilas has human reviews and is **not** re-extracted.
- One extra demo conflict confirmed by two clearly labelled demo reviewers, so the
  "fix it in every language" panel can be shown. The three conflicts seeded by
  `jobs/seed_demo_votes.py` (assumptions M8) stay at one vote, as that design requires.
- Containers: rebuilds `server` and `web` only.
- Also: migration `006_quality_columns.sql` (applied; renumbered, 005 is the gate's),
  `jobs/embed.py` (stop on a batch that stores nothing; skip the legacy vector-index step),
  subject guard `predicates.same_subject` used in `verdicts.deterministic_verdict`
  (generic predicates such as `announcement_date` need matching subjects), `merge.js`
  `isMain` mirrors the gate's `unverifiable` + `relation='same'` rule.
Done earlier in this session: `server/resolutions.py`, `server/consensus.py` (confirm
target), `server/diff.py`, `server/main.py`, `server/patches.py`, `web/js/resolve.js`,
`web/js/review.js`, `web/js/merge.js`, `web/js/consensus.js`, `web/js/keyboard.js`,
`web/js/suggestions.js`, `web/js/api.js`, `web/js/main.js`, `web/css/merge.css`,
`db/migrations/004_resolutions.sql` (applied), `db/schema.sql`.

### Antigravity (session c425fdc3), started 2026-11-14 19:32 CET
Goal: Owner request — ML second-sweep claim salience reranking/pruning across pages, exact landing↔compare count consistency, zero LLM 429 errors globally, enrich top dashboard pages with dynamic ML claims (preserving all existing reviews/seeds/resolutions), and deploy end-to-end (local stack + remote Verda VM `ovr`).
- New files: `db/migrations/007_claim_salience.sql`, `jobs/rerank_claims.py`, `jobs/pipeline_deploy.py`.
- Targeted edits: `db/schema.sql` (`salience_score`), `jobs/extract_claims.py` (disable reasoning tokens, rate-limit pacing, second-sweep salience reranker, review-safe `--augment` + `--top-conflicted`), `server/llm.py` (persistent HTTP client + fence stripping), `server/config.py`, `server/alignment.py` (FK-safe `clean_invalid_links` + tight semantic fallback for `gate.py`), `server/pages.py` (exact bulk `diff.build_rows` counts for `/pages/conflicted`), `server/recheck.py` (auto-heal unreviewed fallback/error links), `server/regate.py` (`--all` sweep).
- Preserves all `reviews`, demo seed votes, `resolutions`, and `suggestions`.
