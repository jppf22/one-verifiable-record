# Applying fixes and making review decisions permanent

Status: **R1 adopted and enforced** (owner, 2026-11-14: O1 and O2 accepted). The server
computes and applies it (`server/consensus.py`); the web app renders the server's tally.
O3 and O4 are still open.

## 1. How fixes get applied to Wikipedia

### Constraints (verified sources)
- **Bot vs. assisted editing.** A tool is a bot when no human decides each edit. Assisted
  tools that "leave the actual decision up to the user" need no bot approval, but must not
  edit at bot speed (WP:ASSISTED, WP:MEATBOT, https://en.wikipedia.org/wiki/Wikipedia:Bot_policy).
  For tools acting for other users, OAuth on the user's own account is "recommended and
  preferable". fi requires a bot flag and a trial run for bots
  (https://fi.wikipedia.org/wiki/Wikipedia:Botit). sv uses Wikipedia:Robotansökan.
- **Another edition is not a source.** "Do not use articles from Wikipedia (whether English
  Wikipedia or Wikipedias in other languages) as sources" (WP:CIRCULAR,
  https://en.wikipedia.org/wiki/Wikipedia:Verifiability). Wikidata also rejects Wikipedia
  as a reference (https://www.wikidata.org/wiki/Help:Sources).
- **Attribution.** Translated prose copied from another edition needs an attribution
  edit summary ("Content in this edit is translated from [[:fi:X]]; see its history for
  attribution"). Copying a reference or rewording a fact does not
  (https://en.wikipedia.org/wiki/Wikipedia:Copying_within_Wikipedia).
- **Prefilling.** MediaWiki `preload` only fills a page or section that doesn't exist yet.
  An existing article section can't be prefilled. `summary=` works for normal edits, and
  `section=new&preloadtitle=` works for new talk sections. en has
  `Template:Preload_wikitext` (body `$1`), so `preload=Template:Preload_wikitext&preloadparams[]=…`
  prefills a talk post. No equivalent template was found on fi or sv
  (https://www.mediawiki.org/wiki/Manual:Creating_pages_with_preloaded_text).

### What the tool does (implemented in web/js/suggestions.js)
- Article patches: **Copy** + **Open edit page**. The edit link now prefills the edit summary.
  The human pastes the text, previews it and saves it under their own account.
- Talk-page notices: open a new section. On en the heading and body are prefilled through
  `Template:Preload_wikitext` (bodies up to 1,800 characters). On fi/sv only the heading
  is prefilled.
- Nothing is ever submitted by the tool.

### Known problem (server, not fixed here)
`server/patches.py::_citation` writes `<ref>{{cite web|url=https://{lang}.wikipedia.org/w/index.php?oldid=…}}</ref>`.
That cites one Wikipedia from another, which WP:CIRCULAR forbids. A patch built this way
would be reverted. Proposed fix: cite the counterpart claim's own external reference
(`claims.citation_urls`, skipping `*.wikipedia.org`). If there is none, produce only a
talk-page notice with an "in the {lang} edition" comparison link, which is not a `<ref>`.

### Production path (not built)
- Use OAuth 2.0 (Special:OAuthConsumerRegistration) with the "edit existing pages" grant
  on fi/sv/en.
- Make one edit per explicit click, after a diff preview. Pass the base revision so that
  edit conflicts fail instead of overwriting.
- Edits are tagged automatically with `OAuth CID`. Follow API etiquette (`maxlag`, backoff).
- For structured facts (dates), fixing Wikidata with a real reference often fixes fi/sv
  infoboxes. On en, infoboxes with a local value don't read from Wikidata.

## 2. When a review becomes a permanent decision

### Comparable systems
| System | Final when | Reopens |
|---|---|---|
| en Pending changes | 1 reviewer who holds the reviewer right | each new edit |
| MapRoulette review | 1 reviewer: approve / reject | reject goes back to the mapper |
| Stack Exchange suggested edits | 2 votes | yes |
| Stack Overflow close/reopen | 3 votes | reopen queue, 3 votes |
| Zooniverse | default 15 classifications, or early on consensus | can be unretired |
| Community Notes | at least 5 ratings, helpfulness agreed across viewpoints | recomputed, locks after 2 weeks |
| Wikidata ranks | talk-page consensus; wrong values *deprecated*, not deleted | by discussion |

Single-reviewer finality only appears where the reviewer holds a granted right. Our
reviewers are self-declared names with no authentication, so one click must not be final.

### Policy R1 (`server/consensus.py`, rendered by `web/js/consensus.js`)
- **One vote per reviewer per link.** A reviewer's latest review replaces their earlier
  ones. Names are compared case-insensitively.
- **What a vote means.** *Agree* says the machine verdict is right. *Disagree* says it is
  wrong. *Fix* says the page text needs to change, so it confirms a `contradicted` or
  `possible_conflict` verdict and opposes any other verdict.
- **Binding thresholds.** A verdict is **confirmed** with ≥2 confirming votes and a lead of
  ≥2. It is **overturned** with ≥3 opposing votes and a lead of ≥2. Overturning a machine
  verdict needs the higher bar. Confirming `possible_conflict` writes `contradicted`, and
  confirming `possible_match` writes `supported` (assumptions C2).
- **Button wording.** The UI words the votes per verdict: "Real conflict" / "False alarm" on
  conflicts, "Same fact" / "They conflict" on matches, "Not comparable" / "Real conflict"
  (fix) on unverifiable and definition_mismatch (assumptions C1).
- **Disputed state.** When both sides have votes and neither reaches a threshold, the link
  is **disputed**.
- **Votes are pinned to the verdict version.** A vote counts only while the verdict that was
  current when it was cast is still current. The client reads `claim_links FOR SYSTEM_TIME ALL`
  and `reviews.ROW_START` from `/api/claims/{id}/history`. When a regrade changes the verdict,
  older votes are listed as *stale* and the vote reopens. This makes SVT do real work in
  the review flow.
- **UI.** The UI shows progress such as "1 of 2 confirmations", "1 of 3 votes to overturn",
  "Disputed…", and the list of votes with stale ones struck through.

### Server enforcement (implemented)
1. **No schema change.** A vote's subject is derived with SVT: the latest machine version
   of the link (`claim_links FOR SYSTEM_TIME ALL`, source not `human`) that started before
   the review's `ROW_START`. A vote is stale if that verdict differs from the current machine
   verdict (`verdict_changed`), if either claim's current row started after the vote
   (`claim_changed`), or if the same reviewer voted again later (`superseded`).
2. **API.** `GET /api/links/{id}/consensus` returns the tally. `/compare` adds
   `links[].consensus` for reviewed links (3 queries per page, whatever the link count).
   `POST /api/reviews` returns `consensus`.
3. **`override: true` is gated by R1.** The web app sends it on every review.
   - *Confirmed* writes the machine verdict again with `verdict_source='human'`.
   - *Overturned* writes the flipped verdict: supported/possible_match → contradicted,
     contradicted/possible_conflict → supported. For unverifiable/definition_mismatch it
     writes contradicted only if every opposing vote is Fix; otherwise nothing is written,
     and the UI says the reviewers didn't say what the verdict should be.
   - A human-decided link that loses its quorum (for example a new Disagree makes it
     disputed) gets its latest machine version restored.
   - Only real changes are written, so each SVT row is a meaningful step.
4. **Re-check.** Human-decided links are not regraded unless one of their claims changed.
   A claim change regrades the link and reopens the vote. Reviewed machine links whose
   claims haven't changed are held (`regraded: false`) instead of re-asking the LLM, so a
   different LLM answer can't make fresh votes stale.
5. **Built: fix it in every language.** A reviewer-confirmed conflict (`contradicted`,
   source `human`) unlocks a form: write the correct version once in English, with a source.
   `POST /api/links/{id}/resolution` drafts it for every language of the comparison set
   (assumptions C6). **Not built:** disputed links are still not excluded from the older
   automatic patch drafts.

## Open points for the owner
- O1. **Accepted:** 2 to confirm, 3 to overturn. Demo votes are seeded by
  `jobs/seed_demo_votes.py` (see assumptions M8).
- O2. **Accepted:** Fix means "the conflict is real, the page needs editing".
  `HUMAN_DECISION_VERDICT["fix"]` is now `contradicted` (was `definition_mismatch`).
- O3. Should the circular-citation problem in `patches.py` be fixed now? This is a
  server-owner change.
- O4. A "trusted reviewer" role needs identity (Wikimedia OAuth, identify-only). It is out
  of scope without authentication.
