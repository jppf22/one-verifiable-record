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

## Assumed (extension)
- X1. (extension) If the server's badge response has no `gaps` field (older server), the
  badge omits the gaps segment instead of showing "0 gaps". Conflicts fall back to
  `counts.contradicted` (link-level). "Verified" stays link-level `counts.supported`.
  The deep link is hidden if the response has no `qid`.

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

## Open questions for the owner
- Q1. Should a human Disagree/Fix eventually **override** the machine verdict
  (`verdict_source='human'`), or should reviews stay an audit log plus re-check?
  Today: re-check only (user decision #4, "for now").
- Q2. Should the re-check eventually fetch the **live** Wikipedia revision (MediaWiki API)
  so an applied patch can show "Resolved"? Today it uses the DB snapshot (A1), so
  conflicts resurface until a re-import.
