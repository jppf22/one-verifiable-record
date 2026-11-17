# Federation: shared discoveries across self-hosted instances

Status: **implemented (initial slice)** — `server/federation.py`, migration
`db/migrations/008_federation.sql`, endpoints `/api/discoveries/*`. Follow-ups
listed at the bottom.

## Problem

Human review is the scarce resource in this project, not compute: machine
verdicts are deterministic given (article revision, extractor model, embedding
model, pipeline version) and can be recomputed by anyone. Without federation,
every self-hosted instance re-reviews the same conflicts from zero votes.

## Design

**Content addressing, not row ids.** A discovery record (review or resolution)
is addressed by `claim_link_hash`: sha256 over the canonical JSON of both
claims' sides `(lang, page_id, revision_id, statement_text)`, sorted. Two
instances that imported the same Wikipedia revisions therefore resolve the same
record to the same local `claim_links` row. The consensus staleness rule
(votes stop counting when a claim's `ROW_START` is newer) carries over
unchanged, so a Wikipedia edit invalidates ported votes automatically.

**Signed append-only feeds.** `GET /api/discoveries/feed?since=<id>` returns
this instance's reviews + resolutions as JSON records, each ed25519-signed with
the instance key (`INSTANCE_PRIVATE_KEY`; ephemeral when unset). Peers are
listed in the `instances` table (name, base_url, pubkey, trust_weight) plus
`CANONICAL_INSTANCE_URL` as the default remote.

**Pull-based sync.** `POST /api/discoveries/sync` (or
`jobs/sync_discoveries.py` on cron) pulls each remote feed from its
`discovery_sync_state` cursor, verifies each record's signature against the
registered pubkey, resolves the local `claim_links` row by content identity,
and inserts into `reviews` / `resolutions` with `reviewer = "<instance>:<name>"`.
`discovery_imports` makes the import idempotent. `consensus.enforce` runs per
imported link, so binding verdicts apply locally.

**Weighted consensus.** `consensus.py` weighs each vote by the origin
instance's `trust_weight` (local reviews weigh 1.0). R1 thresholds (confirm
>= 2 with lead >= 2, overturn >= 3 with lead >= 2) now operate on weighted
tallies. Cross-instance disagreement renders as `disputed` with full provenance.

**Dataset versions.** `dataset_versions` records the imported snapshot hash +
models + pipeline version, so an operator can tell which peers share a
comparable dataset (`register_dataset_version`, surfaced in
`/api/discoveries/status`).

**Privacy.** Feed records contain only claim identity, decision, note, and
signature — never browsing data. Publishing is opt-in (`FEDERATION_ENABLED`).

## Trust model

The canonical instance is a **convenience aggregator, not an authority**:
findings are verifiable locally by recomputation, every record is signed and
attributed to its origin instance, and anyone can run their own aggregator over
the same feeds. Page-view reads (`/api/extension/resolve`,
`/api/extension/annotations`) stay strictly local and precomputed; sync is a
separate explicit operation, so a hostile or down remote can never affect
badge rendering.

## Follow-ups

- Weight by reviewer reputation beyond instance trust (e.g. OAuth-anchored
  wiki identity on the canonical instance only; never linked to browser
  identity).
- Publish-only-binding mode for feeds (export only R1-binding decisions).
- Streaming sync via Wikimedia-agnostic WebSub or long-poll instead of cron.
- `dataset_versions` populated automatically by `db/load/import_dump.py`
  (sha256 over the imported (lang, page_id, revision_id) set).
