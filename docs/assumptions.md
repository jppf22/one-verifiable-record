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

## Open questions for the owner
(append here, then tell the coordinator)
