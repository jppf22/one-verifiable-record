# Backend Verdict Engine — implementation plan (Agent 1)

Scope: `server/verdicts.py`, `server/alignment.py`, `server/recheck.py`, plus a
DB migration and `server/main.py` wiring. No frontend/extension changes. All
changes additive and backwards compatible with the existing API.

## Current state (verified against the running stack, 2026-11-14)
- `claim_links.verdict_source ENUM('deterministic','llm','human')` — `human`
  already in the enum, never written.
- `idx_verdict` on `claim_links(verdict)` already exists.
- `idx_kind` on `claims(kind)` exists; **no** index on `claims(predicate)` or
  `claims(predicate_entity)`.
- No numeric confidence persisted for verdicts: `candidate_confidence` is the
  vector-similarity candidate score reused as an LLM confidence slot.
- Live verdict distribution: 6099 supported, 235 contradicted, 491
  possible_match, 465 unverifiable, 2 definition_mismatch.

## Tasks

### 1. LLM fallback marking (`verdict_source='deterministic-fallback'`)
Problem today: when `VERDA_API_KEY` is unset, `llm_verdict` silently returns a
`possible_match`/`unknown` labelled `verdict_source='llm'`, which misrepresents
a guess as a model output. Fix:
- Add enum value `deterministic-fallback` to `claim_links.verdict_source`
  (migration 003).
- `grade_pair` detects LLM-unavailable / LLM-error and tags the resulting
  verdict `deterministic-fallback` with a clear rationale.
- Behaviour unchanged when the key is present.

### 2. SQL indexes for faster filtering
Migration 003 adds, idempotently (`IF NOT EXISTS`):
- `idx_claims_predicate` on `claims(predicate)`
- `idx_claims_predicate_entity` on `claims(predicate_entity)`
- (`idx_verdict` on `claim_links(verdict)` already present — no-op.)
These speed up the alignment predicate guard and verdict filtering.

### 3. Verdict confidence scoring
Add `verdict_confidence FLOAT NULL` to `claim_links` (migration 003). Combine:
- vector similarity (`similarity`),
- predicate agreement (exact entity match > predicate string match > none),
- layer certainty (deterministic = 1.0; LLM = model `confidence`; fallback
  discounted).
`compute_confidence()` is pure and unit-testable. Written on every graded
UPDATE. Exposed additively in `diff._fetch_links` and the extension link
projection. Null for existing rows (backwards compatible).

### 4. Q1 — human override path (`verdict_source='human'`)
Keep the default re-check-only behaviour, but add an **opt-in** override so a
reviewer decision can set the machine verdict:
- `decision='agree'` keeps the current verdict but stamps it `human`
  (confidence 1.0).
- `decision='disagree'` on a `supported`/`possible_*` link flips to
  `contradicted`; on a `contradicted` link flips to `supported`. Recorded as
  `human`.
- `decision='fix'` sets `definition_mismatch` (reviewer says the pair is a
  false positive).
- Guard: a link already `verdict_source='human'` is not overwritten by machine
  regrades in `grade_links` / recheck, so human decisions stick.
- The override is applied via `verdicts.apply_human_verdict(link_id, decision)`,
  called from `POST /api/reviews` only when `override=true` in the body
  (default false → existing audit-log + re-check behaviour, API unchanged for
  current callers).
Every write is a normal UPDATE → new SVT row, so provenance is preserved.

## Invariants kept
- SVT on all `claim_links` updates (plain UPDATE, no `DELETE HISTORY`).
- Canonical `(min_id, max_id)` link order.
- No no-op UPDATEs except the documented `touch_ids` path.
- Existing endpoints/response shapes unchanged; new fields are additive and the
  `override` request field is optional.

## Validation
- `compute_confidence` unit checks (pure function).
- Apply migration to the running MariaDB; confirm enum + columns + indexes.
- Re-grade a page; confirm `verdict_confidence` populated and fallback tagging
  when the key is unset.
- Submit a review with `override=true`; confirm verdict flips, source `human`,
  a new SVT row exists, and a later recheck does not revert it.
- Timing: `EXPLAIN` + timed verdict-filter query < 100 ms.
