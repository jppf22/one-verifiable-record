# Project context (agent handoff)

This file carries the full project context for any agent (Vibe Code CLI,
VS Code extension, or a teammate). Read this before touching the code.

## What we're building

**One Verifiable Record** — Junction 2026 hackathon (Nov 13–15, Espoo),
MariaDB challenge #307 "Wikipedia Through Time". Cross-language (fi/sv/en)
Wikipedia claim comparison: "Wikipedia editions as branches, we built the
merge tool." Per-article, git-diff-style merged view across language
editions; atomic claims matched via multilingual embeddings in MariaDB
vector search; verdicts always with receipts (source passage, revision id,
wiki link). Human-in-the-loop review is a core feature.

## Hard constraints

- MariaDB is mandatory (challenge): vector search + system-versioned tables
  must be used creatively (judges score this, 20% each criterion).
- Must run on the provided datasets at all three sizes (small/medium/large).
- Hosting: Verda cloud (CPU VM for hot path: 4–8 vCPU, 16–32 GB RAM, NVMe;
  GPU instance for batch embedding, destroy after; Verda Inference API for
  LLM entailment at query time, Kimi K3.1 Pro).
- No automated Wikipedia editing (cross-wiki bot editing is prohibited by
  Wikipedia policy): suggestions are export-only patches.
- Datasets: github.com/MariaDB/mariadb-wikipedia-datasets and
  github.com/MariaDB/mariadb-vector-wikipedia-rag (fi/sv/en starter; use its
  language/stack conventions).

## Verdict model

supported / contradicted / unverifiable (+ definition_mismatch for same-name
different-concept false positives). Never bare true/false. Two layers:
deterministic (dates/numbers on value_normalized) then LLM entailment.

## Judging criteria (MariaDB challenge #307)

Effective/creative use of MariaDB RAG+SVT (20%), implementation/scalability
across datasets (20%), real-world usefulness (20%), design/demo (20%),
originality (20%); bonus for SVT or combined tracks. Verda runs a "Best use
of Verda" bounty (€10k credits) for any team using Verda on any challenge.

## 48h build order

1. h0–4: schema + ingestion + in-DB vector matching on small dataset
2. h4–10: claim pipeline (extract → embed → MariaDB)
3. h10–20: verdict layers (deterministic first, then LLM)
4. h20–32: reviewer console + merge view (+ extension)
5. h32–40: SVT provenance click-through
6. h40–48: large-dataset scale proof, demo freeze, rehearse

Cut-lines: behind at h20 → drop cross-language layer; behind at h32 → drop
extension, keep console.

## Current state (first draft pushed)

Schema (db/schema.sql) with VECTOR(1024) + HNSW + SYSTEM VERSIONING is
complete. API server skeleton covers alignment (single-SQL vector matching),
verdict layers, diff/merge engine, patch generation, RAG QA, badge endpoint
for the extension. Web merge view + Manifest V3 extension are skeletons.
TODO: dataset import format adaptation, claim extraction (chunks → claims),
Verda endpoints confirmation (VERDA_BASE_URL), populating value_normalized.

## Demo fallback

Identical docker-compose stack on laptop; localhost toggle in extension
config; pre-warmed demo article; screen-recording backstop.
