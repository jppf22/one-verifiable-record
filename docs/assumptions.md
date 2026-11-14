# Assumptions & decisions log

These are decisions made while building the frontend interactivity work (see
`docs/frontend-direction.md`). **User-confirmed** entries come from the project owner.
**Assumed** entries are our own calls and are open to revision. Open questions that need
the owner go under "Open questions". Agents add entries here as they make calls.

## User-confirmed (2026-11-14)
1. **Reviewer identity**: ask for a display name on the first review and store it in
   `localStorage`. It can be changed from the header. No auth.
2. **No demo/mock mode** in the shipped app. Live API only. (Throwaway stubs for local
   testing are fine but are never committed.)
3. **Browser extension gets a small update**: correct deep link into the new routes and
   show gap counts in the badge.
4. **Review/patch outcome = re-check, not override**: after a reviewer chooses a verdict
   (agree / disagree / fix) or exports a patch, the app re-checks the article page and
   reports whether the conflict **resurfaces**. A human review does not overwrite the
   machine verdict in `claim_links` (for now).

## Assumed (coordinator)
- A1. "Re-check the page" means re-running the alignment + verdict pipeline for the
  whole article (Wikidata QID) against the data **currently in MariaDB**. It does not
  fetch live Wikipedia. Consequence: on a static dataset snapshot, a contradicted link
  normally **resurfaces** until a newer import brings changed revisions. The UI says so
  explicitly ("still present in the source revisions; apply the patch on Wikipedia,
  then re-import").
- A2. To keep re-checks fast and cheap, only links whose claims have a newer
  system-versioned row since `claim_links.computed_at` are regraded (detected with
  `FOR SYSTEM_TIME`). Links the user focused on are always regraded.
- A3. Re-check results aren't persisted in a new table (no schema migration during
  the hackathon). The verdict history already lives in `claim_links` (SVT). The last
  re-check result per article is cached in `sessionStorage` for display.
- A4. Frontend stays vanilla HTML/CSS/JS with native ES modules. No build step.
- A5. The old `/?qid=Q…` link (used by the extension) keeps working and redirects to
  `#/merge/Q…`.

## Deployment & data pipeline (2026-11-14)
User-confirmed:
- D1. Embeddings live in separate tables `chunk_embeddings` / `claim_embeddings`
  (NOT NULL, HNSW `DISTANCE=cosine`, not versioned). MariaDB vector indexes need NOT
  NULL columns, and writing an embedding must not create a new `claims` version.
- D2. Claims are extracted with the **LLM (Verda, Kimi K3.1 Pro)**: a pilot of ~20
  best-covered tri-lingual topics first, reviewed with the owner, then all 2,919
  tri-lingual topics in the background.
- D3. The owner asked for a **GPU instance** for bulk embedding (pending: needs Verda
  cloud credentials or CLI access).

Assumed (coordinator):
- D4. Verda Inference is `https://inference.verda.com/v1`, model id `kimi-k3-1-pro`. The
  old `api.verda.com` URL and `kimi-k3.1-pro` id were wrong. The gateway has **no
  `/v1/embeddings`**, so e5-large is served by our own `embedder` container
  (OpenAI-style API). Q&A query embedding and `jobs/embed.py --endpoint` use it.
- D5. Extraction sends only the first ~6,000 characters per article (lead + opening
  sections). That caps cost and covers the facts editions most often share.
- D6. Claim `statement_text` stays in the source language (the receipt). `subject` and
  `predicate` are canonical English snake_case, so cross-language claims share a
  vocabulary.
- D7. Deterministic layer is tightened:
  - It only compares values when both claims have the **same predicate**, so
    vector neighbours like "born 1900" vs "died 1986" aren't false conflicts.
  - Dates compare at the **coarser precision** ("1900" agrees with "1900-09-03").
  - **Differing numbers go to the LLM** (often time-qualified, e.g. population by
    year). Equal numbers are still `supported` deterministically.
- D8. E5 prefixes: chunks use `passage: ` (retrieval). Claims use `query: ` on both
  sides (symmetric claim-to-claim matching).
- D9. The importer loads only articles whose QID exists in 2+ languages
  (`--only-shared`): 17,431 articles, 54,144 chunks.
- D10. Known issue: re-import deletes and reinserts `chunks`, which fails once claims or
  embeddings reference them (FKs). The A1 "re-import to resolve" story needs an upsert
  re-import. Not built yet; see Q3.

## Assumed (extension)
- X1. (extension) If the server's badge response has no `gaps` field (older server), the
  badge omits the gaps segment instead of showing "0 gaps". Conflicts fall back to
  `counts.contradicted` (link-level). "Verified" stays link-level `counts.supported`.
  The deep link is hidden if the response has no `qid`.
- X2. (extension) The inline overlay now marks **every** local claim that has a graded
  link, grouped by status colour: conflict / possible_conflict / only_here /
  possible_match / **agree** (soft teal, thin underline for the non-issue case).
  `/api/extension/annotations` adds `agree` to its annotatable statuses and emits a
  `kind` field (date/number/quantity/entity_relation/factoid) per annotation, so the
  overlay can label and group claims by type. `missing_here` stays overlay-only (no
  local claim to anchor). The badge pill reports "N claims marked (I to check, A agree)".
- X3. (extension) The marker popover offers three non-editing actions besides the
  receipts drawer: "Open focused comparison" (`?focus=r-…`), "Review this claim"
  (`?focus=r-…&review=1`, which focuses the row's first review control in the web app),
  and "Copy claim" (copies this page's sentence plus counterpart wording to the
  clipboard for drafting a talk-page note). None of them edit Wikipedia.
- X4. (extension) For conflict / possible_conflict / missing_here markers the popover
  also offers "Draft a patch" (`?focus=r-…&panel=suggestions`), which opens the web
  app's export-only suggestions panel scrolled into view. The revision links are
  relabelled "Open {lang} on Wikipedia (analysed revision)".
- X5. (extension) The badge bar adds, when the overlay is on: a "Next conflict (N)"
  button that cycles focus through conflict/possible_conflict markers (mirrors the
  web app's `g j`), and per-type filter chips (one per claim `kind` present) that
  toggle which kinds the overlay paints. An empty selection shows all kinds.
- X6. (extension) Live MediaWiki identity & snapshot detection (`candidate()` in
  `extension/content.js`): desktop Wikipedia skins embed `wgNamespaceNumber`,
  `wgAction`, `wgArticleId`, `wgRevisionId`, `wgCurRevisionId` and canonical
  `wgTitle` in inline `RLCONF` JSON (plus `#t-permalink`) rather than Parsoid
  `<meta property="mw:pageId">` tags. Parsing `RLCONF` safely from DOM script text
  in the MV3 isolated world enables exact snapshot matching on normal `/wiki/Title`
  visits as well as `/w/index.php?oldid=…` permalinks, while rejecting non-article
  namespaces (`wgNamespaceNumber !== 0`) and non-view screens (`diff`, `action!=view`).
- X7. (extension) Prose-only text indexing (`OMIT` in `extension/content.js`) skips
  `.infobox`, `.sidebar`, `.shortdescription`, `.hatnote`, `.toc`, thumbnails and
  reference blocks so sidebar duplicates don't turn unique lead claims into false
  ambiguities; `claim.context` disambiguates repeated values when a unique context
  window exists. Ambiguous (`>=2` prose matches), unanchored, and `missing_here`
  findings remain keyboard-reachable in the Findings tray with full receipts
  popovers and deep links (`?focus=r-…`, `&review=1`, `&panel=suggestions`).
- X8. (extension) Configuration sync & stale-page guidance: `popup.html` exposes
  both the `inlineFindings` toggle and `selectedKinds` per-type filter chips synced
  two-way via `chrome.storage.sync` with the in-page badge bar (which renders type
  chips whenever `>=1` claim kind is present). On `stale` live pages, the badge
  reports precomputed snapshot counts without painting live text and links directly
  to the analysed Wikipedia `oldid` revision. `background.js` also sets tab-scoped
  `chrome.action` badges and falls back to `/api/badge` on `404` (`X1`).

## Assumed (server)
- S1. Stale-link detection compares `claims FOR SYSTEM_TIME ALL` `ROW_START` against
  `claim_links.computed_at + 1 second`. `computed_at` is a second-precision TIMESTAMP,
  while ROW_START has microseconds. Without the extra second, a claim written in the
  same second as its link would look "newer" on every re-check. Side effect: a stale
  link whose regrade gives the **same** verdict isn't written (contract: no no-op
  UPDATEs), so `computed_at` stays old and the link is regraded again on every
  re-check until its verdict changes. That's acceptable at hackathon scale. If it turns
  out to be costly, we can touch `computed_at` without a verdict change, which costs an
  SVT row.
- S2. Grading an `unmatched` link (in `/api/align` and in re-check) now also sets
  `computed_at = CURRENT_TIMESTAMP`, because the verdict changes there too.
- S3. `alignment.align_article` now stores each new candidate pair once, in canonical
  (lower id, higher id) order, so a->b and b->a no longer become two links. Existing
  rows are untouched. To revert, remove the `min/max` in alignment.py.
- S4. RecheckResult `links[].before` is `null` for links created by the re-check's own
  alignment step, because they didn't exist before. Every link in `links` was actually
  regraded, so `regraded` is always `true` there. Unchanged non-focus links aren't
  listed.
- S5. `POST /api/suggestions/{id}/export` only moves `draft -> exported`. Already
  exported or applied drafts aren't rewritten (no no-op SVT row), but the re-check still
  runs. `links_reviewed` only counts graded links (not `unmatched`), so it's always
  <= `links_total`.
- S6. `GET /api/articles` now also returns `revision_id`, which the contract already
  listed. It's an additive change.
- A6. (coordinator, refines S1) A link regraded because one of its claims has a newer
  SVT row always gets `computed_at` bumped, even if the verdict is unchanged. The
  extra history row means "re-verified against the new claim version". Without it,
  the link would look stale on every re-check and trigger an LLM call each time.
  Focus-only regrades with an unchanged verdict still write nothing.

## Assumed (panels)
- P1. Patch drafts are **not generated automatically** on `article:loaded`. `GET /api/suggestions`
  inserts rows, so the user clicks "Generate drafts". If `article:loaded` fires again for the same
  QID (for example after a re-check), the drafts already on screen and their re-check notes stay.
- P2. "Open edit page" for `talk_page` drafts opens `Talk:{title}` with `&section=new`. Other types
  open the article's own edit page. A human opens it in a new tab and nothing is prefilled or submitted.
- P3. After "Mark exported", suggestions.js also shows a short inline note on the draft
  (resurfaced / changed / unchanged for its `claim_link_id`) as well as emitting `recheck:done`.
  Drafts with no link say that no re-check ran.
- P4. Q&A history is kept per QID in `sessionStorage` (`ovr.qa.{qid}`, last 6), so it survives
  navigating between articles in the same tab. Suggested questions are generic, not per article.
- P5. Citation links use the API `url` only if it is an `https://*.wikipedia.org/` URL. Otherwise
  they are rebuilt with `wikiUrl(lang, revision_id, title)`.
- P6. The history "SQL" panel shows the query shape with the claim/link ids substituted, plus an
  `AS OF` time-travel example at the first version's timestamp. It is illustrative and not run by the UI.

## Assumed (web-core)
- W1. On first open of an article, if `/api/merge` returns no links or any `unmatched`
  link, merge.js calls `POST /api/align/{qid}` once and refetches (the legacy app always
  aligned on open). Untracked QIDs (no `articles`) skip align and show "isn't tracked yet".
- W2. Ungraded `unmatched` links are hidden in the merge view and excluded from keyboard
  review targets (consistent with the server's `links_total`, which only counts graded links).
- W3. The search results list (`#results`) moved into the header search box: inline on the
  picker page, as a dropdown over the merge view, so you can search from anywhere. Results
  are grouped per Wikidata QID (one row per entity with its fi/sv/en titles). Typing a bare
  QID (`Q1757`) and pressing Enter opens it directly.
- W4. `review.js` adds an extra `linkIds: [id]` field to the `recheck:done` payload
  (additive) so the reviewed cluster is highlighted even if the server omits it from
  `result.links`. If the review response has no `recheck`, it emits `merge:reload` instead.
- W5. Re-check banner outcome per listed link: "Conflict resurfaced" (`resurfaced`),
  "Resolved on re-check" (contradicted -> anything else), "New link found" (`before: null`),
  "Verdict changed", otherwise "Unchanged". The A1 database-revisions note shows only when
  something resurfaced. "Dismiss" also clears the sessionStorage cache for that QID.
- W6. Only the `status` filter goes into the URL (`?status=conflict,gap`, via replaceState,
  so no extra history entries). The kind, text and unreviewed filters are per-session view state.

## Assumed (merge view polish + review consensus, Agent 2, 2026-11-14)
See `docs/review-consensus.md` for the research and sources.
- M1. Rows are rendered once per compare payload. Filters toggle `[hidden]` instead of
  re-rendering, and the text filter is debounced (150 ms) and doesn't rebuild the filter bar.
  On the 187-row / 359-link page (fi "Sipilän hallitus") the first render takes about 250 ms
  including the API call, and a status filter toggle about 2 ms.
- M2. Review note form: "+ note" (key `n`) opens a form with an Agree/Disagree/Fix choice.
  The note is required only for Fix. The form is created on demand, so there is no textarea per link.
- M3. Keyboard: `g j`/`g k` next/previous conflict, `g m` next gap, `g a` next agreeing row
  (wraps, visible rows only), `o` toggles the focused row's receipts.
- M4. Receipts show `verdict_confidence` when present (additive field from the backend).
  `deterministic-fallback` is labelled "rules (LLM unavailable)".
- M5. **Policy R1 (owner-accepted, enforced in `server/consensus.py`):** one vote per
  reviewer, 2 confirm / 3 overturn with a lead of 2, otherwise disputed. A vote counts only
  while the machine verdict it was cast on is current and neither claim changed (SVT).
  The web sends `override: true` on every review. A single review still never changes a
  verdict; a quorum writes `verdict_source='human'`, and losing the quorum restores the
  machine verdict. This replaces user decision #4 for links that reach a quorum.
- M8. **Demo seed votes** (owner-approved): `jobs/seed_demo_votes.py` posts one Agree,
  as reviewer "OVR team (demo seed)" with a note saying so, on three clear-cut conflicts:
  Daniel Tilas en↔sv death date (17 vs 27 Oct 1772), Bertel Gripenberg fi↔sv death date
  (5 vs 6 May 1947), Algar von Heiroth en↔fi death year (1988 vs 1985). Each sits at
  "1 of 2 confirmations", so one judge click confirms it live. Links are found by page and
  predicate through the API (not by id), dry run by default, idempotent. Run it once on each
  deployment: `python jobs/seed_demo_votes.py --apply --api http://<host>:8080/api`.
  Seeded on the local stack 2026-11-14 (links 11076, 268, 130).
- M7. Re-check holds reviewed links whose claims haven't changed (`regraded: false`) and
  regrades human-decided links only after a claim change (reopening the vote).
- M6. Patch edit links: article edits prefill `summary=`. en talk notices prefill heading
  and body through `Template:Preload_wikitext`. fi/sv prefill only the heading.

## Assumed (same-fact gate and LLM pacing, sess_383469e3, 2026-11-14)
Why the Daniel Tilas page showed one 45-claim "conflict" row with 629 verdicts:
alignment was widened to 12 candidates per claim at similarity >= 0.72; every candidate
got its own LLM call (~400 per page) with no pacing; Verda allows 40 requests/minute per
account, so most calls returned HTTP 429 and the error text was stored as a verdict
(838 links); and rows were transitive connected components, so those links chained
every claim into one row.
- G1. `server/llm.py`: one throttled Verda client (>= 1.6 s between call starts, reads
  `retry-after` / `x-ratelimit-*`, retries 429 and 5xx, reasoning mode off). On failure
  it raises; nothing is written. A link that could not be graded stays `unmatched`
  (hidden) and is retried on the next re-check. An error is never a verdict.
- G2. `server/gate.py`, run inside `verdicts.grade_unmatched` (align and re-check):
  same-property candidates (predicates.py aliases) with matching subjects skip the LLM;
  the rest go to ONE batched call per counterpart page (<= 20 claims, <= 4 candidates
  each) that picks the single same-fact candidate per claim and grades it. Matching is
  1:1 per language pair. On a 45-claim page: 2 LLM calls instead of ~400.
- G3. Rejected candidates get the new verdict `different_fact` (migration 005, additive
  enum). They are kept, so alignment's INSERT IGNORE does not propose them again, and
  are never shown. `summary.links_hidden` counts them (UI: "N candidate pairs ruled out").
- G4. `diff.build_rows`: one fact per row. Rows are anchored on this page's claims;
  a counterpart claim joins at most one row, at most one claim per language per row.
  Strongest link wins (confirmed verdict, then same-property, then similarity). Non-
  connecting links are listed only if reviewed. `unverifiable` connects only with
  relation `same` (gate-confirmed same fact) and makes the row `possible_match`.
- G5. Web default view: when the URL names no status and the page has conflicts, only
  conflict / possible-conflict rows are shown, with a "Show all N rows" link
  (`?status=all`). Pages without conflicts open on everything.
- G6. Data repair `server/regate.py --title ... --apply` (dry run by default): resets
  unreviewed LLM/fallback verdicts, reviewed placeholder verdicts ("LLM error",
  fallback), and same-property links with different subjects, then gates again. Applied
  to Daniel Tilas only: 625 links reset, 605 became `different_fact`, 0 error verdicts
  remain there; joao's agree on an error placeholder is now shown as a stale vote (R1).
  Sipilä cabinet is left for its planned re-extraction (agent-coordination.md).
  The remaining 318 "LLM error" links in the DB are all on the Sipilä pages.

## Open questions for the owner
- Q1. Should a human Disagree/Fix eventually **override** the machine verdict
  (`verdict_source='human'`), or should reviews stay an audit log plus re-check?
  **Resolved (opt-in): `POST /api/reviews` accepts `override: true`** (default
  `false`, so existing callers are unchanged). With it, a decision maps to a
  verdict stamped `verdict_source='human'`, confidence 1.0, and machine regrades
  no longer touch the link. See V-S10. Without it, behaviour is unchanged
  (audit log + re-check, user decision #4).
- Q2. Should the re-check eventually fetch the **live** Wikipedia revision (MediaWiki API)
  so an applied patch can show "Resolved"? Today it uses the DB snapshot (A1), so
  conflicts resurface until a re-import.
- Q3. Re-import with changed revisions (needed for a conflict to ever be "resolved",
  A1): should new revisions **update claims in place**, so SVT keeps the old version
  and the re-check sees them as stale, or be stored as **new articles/claims rows**?
  Updating in place fits the SVT story best.
- Q4. **Resolved (owner, 2026-11-14):** R1 is enforced server-side and gates `override=true`,
  and Fix means "the conflict is real" (fix → contradicted). See M5, M7 and review-consensus.md.
- Q5. `server/patches.py` cites other Wikipedia editions as `<ref>`s, which WP:CIRCULAR
  forbids. Switch to `claims.citation_urls`, or fall back to a talk-page notice only? (O3)

## Page-centric rework (2026-11-14, owner away, authorised to proceed)
User directive: "compare over pages, not entities or topics". Pages must exist in
≥2 languages, and there must be ≥5 pages with fact conflicts. See `docs/page-contract.md`.
- V1. The unit of comparison is a **page** (`articles` row). The new `page_links` table holds
  interlanguage links between pages (both directions, 26,188 rows, 17,431 pages). It is
  derived once from the dataset's Wikidata ids, which are never shown or used for
  logic afterwards.
- V2. Statuses are **relative to the page being viewed**: conflict / missing on this page /
  only on this page / agree (v1's symmetric "gap" is split into the two directions).
- V3. **LLM still unavailable** (Verda project not subscribed to any model). Claims for this
  demo come from `jobs/extract_rules.py` (`model_version='rules-v1'`): birth and death
  dates parsed from page leads in fi/sv/en. Uncertain dates (c./noin/omkring,
  old-style calendar notes, "or") are skipped, and only dates that open their segment
  are used. Pages whose title or lead names an event or period (war, treaty, era,
  kingdom…) are skipped. `jobs/extract_claims.py` (LLM) stays for when a model is enabled.
- V4. Result: 10,385 date claims on 6,206 pages. A pre-alignment SQL check found 252
  counterpart pairs whose dates disagree, on 221 pages. Spot checks look like real
  cross-edition discrepancies (e.g. Saara-Sofia Sirén born 1985 vs 1986; Tyyne
  Leivo-Larsson died 1977 vs 1979). Some will be Julian/Gregorian differences on
  pre-1918 dates the rules couldn't detect (e.g. Snorri Sturluson 22 vs 23 Sept).
- V5. First extraction pass (looser rules) was wiped before any links existed, including
  `DELETE HISTORY FROM claims`, so claim timelines don't show the discarded pass.
- V6. Alignment: e5-large similarities are compressed, so `match_distance` defaults to
  0.12, and candidate pairs must share the predicate when both have one.

## Assumed (server, page-centric v2)
- V-S1. One helper, `pages.comparison_set(page_id)` (X + `page_links.page_b`), drives
  alignment, verdict grading, re-check, diff, suggestions and Q&A. The landing aggregation
  (`/pages/conflicted`) joins `page_links` in SQL directly, so the source of truth is the same.
  `wikidata_qid` is not read anywhere in `server/`.
- V-S2. Alignment runs per (claim of X, counterpart page): the `nearest_k` (3) nearest claims
  on *each* counterpart, so every counterpart gets candidates. Only X's claims are queried, so
  a link exists once the page on either side has been aligned. Default `match_distance` is
  0.12 (e5 similarity >= 0.88). Predicate guard in SQL: when both claims have a predicate,
  they must be equal.
- V-S3. Rows: only `supported`/`contradicted` links join claims into a row. `unmatched`
  (ungraded), `unverifiable` and `definition_mismatch` links do not join claims. They are still
  listed in `links` on the row of their claim on this page, so the receipts stay visible.
  An unverifiable pair therefore shows up as `only_here` + `missing_here`.
- V-S4. Status order of checks: no claim here -> `missing_here` (even if two counterparts
  contradict each other); any contradicted link in the row -> `conflict` (this includes a
  counterpart-vs-counterpart contradiction in a row this page takes part in); no counterpart
  claim -> `only_here`; otherwise `agree`. Row `kind`/`predicate` are the most common non-null
  value among the row's claims. `summary.links_total` counts graded links in the comparison set.
- V-S5. `/pages/conflicted` and the `conflicts` in `/pages?q=` are **claim-level SQL counts**,
  not the row counts of the comparison view, so they can differ slightly from `/compare`:
  conflicts = claims here with a contradicted link to a counterpart, agree = supported and not
  contradicted, only_here = claims here with no supported/contradicted link, missing_here =
  counterpart claims with no such link to this page (the same fact on two counterparts counts
  twice). Order: conflicts desc, then number of graded links, then title. Every page of a
  linked set is listed on its own (fi/sv/en of the same subject can all appear).
  The badge uses the exact comparison view.
- V-S6. `/pages/by-title` and `/badge` return 404 for pages with no counterpart ("only pages
  with >=1 counterpart are offered"). `/pages/{id}` still returns such a page with
  `counterparts: []`. by-title also accepts the URL form (`_` for spaces).
- V-S7. Suggestions for page X: a correction for each side of every contradicted link between
  X and a counterpart. Counterpart-vs-counterpart conflicts are drafted from those pages' own
  views. Each `missing_here` row gives one notice for X, sourced from the first counterpart in
  fi/sv/en order. Each `only_here` row gives one notice for every counterpart. Every page in the
  set gets one talk-page draft. Dedupe stays exact-match on (page, type, wikitext).
  `drafts` lists the newest 200 drafts across the comparison set, each with `page_id`.
- V-S8. Export always re-checks the draft's page (focus = its link, if any), so drafts without
  a link also return a RecheckResult (v1 returned `null`). The review re-check runs on the page
  of `claim_a`. A focus link's `before` falls back to its pre-regrade verdict if the link is
  outside that page's set.
- V-S9. The old QID endpoints are removed, not aliased. Note: `extension/content.js` still reads
  `data.qid` from `/api/badge` and links to `#/merge/{qid}`. It needs `#/page/{lang}/{title}`
  (owner of extension/).

## Assumed (server, verdict engine — Agent 1, 2026-11-14)
- V-S10. **LLM fallback marking** (migration 003 adds enum value): when the
  Verda API is unavailable (no key) or the request fails, `verdicts.grade_pair`
  keeps the loose candidate verdict but tags it `verdict_source='deterministic-
  fallback'` instead of `'llm'`, so a guess is never presented as a model
  output. Behaviour with a working key is unchanged.
- V-S11. **Verdict confidence** (`claim_links.verdict_confidence FLOAT NULL`,
  migration 003): `verdicts.compute_confidence` combines vector similarity
  (0.35), predicate agreement (0.30: shared Wikidata property = 1.0, equal
  predicate string = 0.8, missing = 0.3, mismatch = 0.0), and layer certainty
  (0.35: deterministic/human = 1.0, LLM = its self-reported confidence, fallback
  = 0.25). Written on every graded UPDATE; NULL for rows graded before the
  migration. Exposed additively in `/compare` links and the extension link
  projection (`verdict_confidence`). `candidate_confidence` (the raw similarity
  slot) is unchanged.
- V-S12. **Q1 human override** (see Q1): `verdicts.apply_human_verdict` maps a
  review decision to a verdict — agree keeps the current verdict, disagree flips
  supported/possible_match↔contradicted (else unverifiable), fix →
  contradicted (changed from definition_mismatch by owner decision Q4) — stamps `verdict_source='human'`, confidence 1.0, and a
  new SVT row. `grade_links` skips any link already `verdict_source='human'`, so
  recheck/align never revert a human decision. Opt-in via the `override` request
  field on `POST /api/reviews` (default false).
  **Superseded by M5:** `override=true` now runs `consensus.enforce` (R1 quorum)
  instead of `apply_human_verdict`, which is kept but no longer called by the API.
  Human links are regraded after a claim change (M7).
- V-S13. **Indexes** (migration 003): `idx_claims_predicate`,
  `idx_claims_predicate_entity` (the alignment predicate guard and verdict
  filters). `idx_verdict` on `claim_links(verdict)` already existed. Verified:
  verdict and predicate filters use the indexes and run sub-millisecond;
  `/pages/conflicted` and `/compare` are well under 100 ms.
- V-S14. **Pre-existing bug surfaced, not fixed (out of scope):**
  `alignment.clean_invalid_links` does a hard `DELETE FROM claim_links`, which
  raises a FK error (`fk_reviews_link`) when a link being cleaned has a
  `reviews` row. `POST /api/pages/{id}/align` 500s on such pages. Owner of
  `alignment.py` should guard the delete (skip reviewed links) or detach
  reviews first.

## Assumed (web, page-centric v2)
- V-W1. Route `#/page/{lang}/{title}` is resolved with `GET /pages/by-title` to a page id, then
  `GET /pages/{id}/compare`. A 404, or a page with zero counterparts, shows "This page can't be
  compared yet" (no retry). Legacy `?qid=` and `#/merge/…` links (also when typed later) go to `#/`.
- V-W2. First-open alignment (W1) carries over: if the compare payload has no links or any
  `unmatched` link, the view calls `POST /pages/{id}/align` once and refetches.
- V-W3. Bus events renamed: `article:loaded` → `page:loaded {pageId, data}`, `merge:reload` →
  `page:reload {pageId}`, `recheck:done` carries `pageId` instead of `qid`. The re-check result
  from a review/export is for the page of claim_a / the draft; the view always reloads the page
  it is showing when the ids match (otherwise it only caches the result).
- V-W4. Diff layout: left column = this page, then one column per counterpart language in the
  payload's `counterparts` order (fi, sv, en), plus any extra language seen in `rows[].there`.
  Dashed cells: amber "Not on this page" for `missing_here`, blue "Not on the {lang} page" for
  `only_here`, muted grey for a language that simply lacks the fact in conflict/agree rows.
  `only_here` uses the accent blue (meter, chips, badge), `missing_here` amber.
- V-W5. Row headline = humanised `predicate` (row-level, else first claim's) + kind tags + a
  short note from this page's view ("contradicted by sv, en", "stated on en, not on this page").
  Claim cells show `predicate: value_text` above the statement. The text filter also matches
  values and predicates.
- V-W6. Header switcher: each counterpart card has "Make this page ⇄", a plain link to that
  page's route (new history entry), so Back returns to the previous point of view.
- V-W7. Session caches are keyed by page id: `ovr.recheck.page.{id}`, `ovr.qa.page.{id}`.
  Q&A language select reads "this page only" / "{Language} counterpart only".
- V-W8. Search shows one result per page (a subject imported in 3 languages yields 3 results,
  one per point of view) with counterpart chips and its conflict count; rows without
  counterparts are dropped client-side as well. Typing a QID no longer opens anything.
- V-W9. Landing "Pages with conflicts" reloads every time the landing is shown, so counts reflect
  reviews made in the comparison view. Rows with 0 conflicts (server returns them after the
  conflicted ones) are listed too, with a grey edge instead of red.
- V-W10. Extension: no badge when `/badge` returns no counterparts. Pill = conflicts / missing
  here / only here / agree; red if conflicts, amber if only missing-here, else green. Link text
  "Compare with sv · en" → `${webBase}/#/page/{lang}/{title}` (spaces as `_`).
- V-W11. Drafts download file names use `{lang}-{title}-{type}-{id}.wiki` instead of the QID;
  drafts for this page are headed "This page", others "{Language} counterpart".

## Assumed (review clarity, fewer rows, fix in every language, 2026-11-14)
- C1. **Vote buttons are worded per verdict.** Conflict verdicts: "Real conflict" (agree) /
  "False alarm" (disagree). Match verdicts: "Same fact" (agree) / "They conflict" (disagree).
  unverifiable / definition_mismatch: "Not comparable" (agree) / "Real conflict" (fix). The
  API decisions and policy R1 are unchanged. Fix no longer requires a note. Under the buttons
  one line says how many more votes settle the verdict and what it becomes.
- C2. **Confirming a possible verdict settles it** (`consensus.CONFIRMED_VERDICT`): a confirmed
  `possible_conflict` is written as `contradicted`, a confirmed `possible_match` as `supported`.
  Otherwise "Real conflict" on a possible conflict would never become a conflict.
- C3. **Fallback guesses don't build rows.** Links graded while the LLM failed (HTTP 429) were
  stored as `possible_match` / `unverifiable` with source `llm`. Migration 004 relabels them
  `deterministic-fallback` (845 links), and `diff.connects()` ignores fallback weak links.
  Daniel Tilas (sv) went from 1 row with 629 links to 30 rows with 3 conflicts.
- C4. **Folded side pairs.** A row shows only its "main" links (same rule as `diff.connects`).
  Unrelated pairs and fallback guesses sit under "N more pairs checked", rendered on first open.
  Review progress counts main links only.
- C5. **One-language facts are compact and hidden by default on busy pages.** missing_here /
  only_here rows render as a short claim list. They are hidden when a page has more than 12 of
  them; the filter bar has "Show facts found in only one language (N)". A status chip for a gap
  status shows them regardless.
- C6. **Fix it in every language** (`server/resolutions.py`, `web/js/resolve.js`, migration 004):
  only on a reviewer-confirmed conflict (`contradicted` + `human`). A reviewer writes the correct
  version once, in English, with an optional source (Wikimedia URLs are rejected, WP:CIRCULAR).
  The server stores it in `resolutions` (SYSTEM VERSIONED) and writes one `suggestions` draft per
  page of the comparison set (`resolution_id` set): "Replace: … With: …" when the page has a claim
  in the row, "Add: …" otherwise, skipped when the page already says it. fi/sv text is translated
  by the Verda LLM; if that fails, the English text goes in with a "translate before saving"
  comment and the UI flags the draft. References are plain `<ref>[url host] (date)</ref>` so they
  work on every edition. Nothing is submitted to Wikipedia; each draft has Copy, Open edit page
  and Mark exported.
- C7. Not changed: the alignment predicate guard still requires equal predicate strings, so
  LLM-extracted claims with different predicate names (`birth_date` vs `date_of_birth`) are no
  longer paired by a fresh alignment. Fewer claims at extraction time would need a re-run of
  `jobs/extract_claims.py`.
- C8. **Predicate aliases** (`server/predicates.py`): alignment's predicate guard, link cleanup
  and the deterministic layer compare canonical predicates, so `date_of_birth` pairs with
  `birth_date`. Only true synonyms are listed. Existing links are untouched (pairs are unique), so
  this applies to new alignments. The extraction prompt now lists the canonical names.
- C9. **Extraction fixes** (`jobs/extract_claims.py`): `--max-claims-per-page N` keeps typed
  values first (dates, numbers), then the model's confidence. Old claims are deleted only after
  the LLM calls (in a fresh transaction: MariaDB's snapshot isolation rejects the DELETE if
  another article of the topic replaced shared links meanwhile), so the locks last
  milliseconds instead of the whole run. `--reextract`
  skips articles whose links have human reviews unless `--drop-reviews` is given, and keeps the
  old claims if the model returns none. `kimi-k3-1-pro` reasons inside `max_tokens`: use
  `--max-tokens 32000 --max-chars 4000`. Migration 006 adds the missing
  `claims.data_quality_score` and `*_embeddings.embedding_quality` columns (every extraction
  and every embedding INSERT failed without them).
- C10. **Demo conflict for "fix it in every language"**: link 4 (Oskari Tokoi, en 15 April vs fi
  15 May 1873) is confirmed by "OVR team (demo seed)" and "Kiro agent (demo seed)", both labelled
  with a note. The three M8 seed conflicts keep one vote, so a judge's vote still completes them.
  No resolution was pre-written: the correct version and its source are left to the reviewer.

## Assumed (ML second-sweep salience reranking, global consistency & Verda deploy, 2026-11-14)
- R-S1. **Hybrid ML + MariaDB HNSW Second-Sweep Reranker** (`jobs/rerank_claims.py`, migration 007 `claims.salience_score`):
  Cuts bloated LLM extractions down to `--max-per-page 8` core, verifiable claims per page without
  losing dynamic ML discovery. Combines three signals: (1) Verda `kimi-k3-1-pro` second-sweep
  encyclopedic salience + primary-subject check (`about_subject`, dropping parent/relative tangents
  such as Olof Tilas's 1719 ennoblement on Daniel Tilas's page) + same-edition deduplication,
  (2) MariaDB `VEC_DISTANCE_COSINE` cross-edition resonance across `claim_embeddings`, and
  (3) MariaDB `VEC_DISTANCE_COSINE` centrality to lead `chunk_idx=0`. Claims referenced by
  `reviews`, `resolutions`, `suggestions`, or `contradicted` links are always protected; pruned
  claims remain auditable in MariaDB SVT (`FOR SYSTEM_TIME ALL`).
- R-S2. **Fast non-thinking extraction + review-safe augmentation** (`jobs/extract_claims.py`):
  Disables reasoning tokens (`"thinking": {"type": "disabled"}`) and enforces thread-safe pacing
  against Verda's 40 req/min headers (`x-ratelimit-remaining-requests` / `retry-after`), speeding
  extraction ~75x with zero 429s. `--augment --top-conflicted N` enriches `rules-v1`-only dashboard
  pages with dynamic second-sweep-reranked ML claims while preserving existing conflicted/reviewed
  claims and demo votes intact.
- R-S3. **Exact Landing <-> Compare count parity** (`server/pages.py`, `server/main.py`):
  `pages.page_stats()` now computes exact row status counts via two bulk SQL queries and
  `diff.build_rows()`, replacing the approximate SQL heuristic so `/api/pages/conflicted`,
  `/api/pages?q=`, `/api/badge`, and `/api/pages/{id}/compare` always report identical numbers.
- R-S4. **Two-tier MariaDB vector alignment, FK-safe cleanup & self-healing recheck**
  (`server/alignment.py`, `server/recheck.py`, `server/regate.py`, `server/llm.py`):
  Fixes `V-S14` (skips reviewed/suggested links in `clean_invalid_links`), pairs both canonical
  predicate aliases and tight (`VEC_DISTANCE_COSINE <= 0.14`) open-predicate semantic neighbors for
  batched `gate.py` verification, reuses a persistent HTTP keep-alive client in `llm.py`, and
  auto-heals any unreviewed fallback/error links on recheck and via `regate.py --all --apply`.
