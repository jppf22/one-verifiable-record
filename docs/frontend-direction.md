# Frontend direction (draft for review)

## What the project is

One Verifiable Record treats the fi / sv / en Wikipedia editions of one article as
**branches of the same truth**, and the web app is the **merge tool** between them.
It breaks each edition into atomic claims, matches them across languages with MariaDB
vector search, and shows a git-diff-style view of three cases:

- **Conflict**: the editions contradict each other
- **Gap**: one edition has a fact the others don't
- **Agree**: every edition says the same thing

Every verdict has to come with **receipts**: the source passage, the revision id and a
link back to Wikipedia. The system never edits Wikipedia. It produces patch drafts that
humans export, and **human review is a core feature**. MariaDB System-Versioned Tables
record the full history of each claim and verdict (the "biography of a sentence").

The judges score five things at 20% each: creative MariaDB use (vector + SVT),
scalability, real-world usefulness, **design/demo** and originality. The frontend
carries most of the design/demo score. It also has to make the MariaDB features
*visible*, because judges can't give points for something they never see.

## Where the frontend falls short today

1. **The reviewer console doesn't exist.** `POST /api/reviews` is implemented, but
   nothing in the UI calls it, and `/api/merge` doesn't return `claim_links.id`, so the
   UI couldn't call it anyway. The README presents this as a core feature.
2. **Verdicts have no receipts.** `evidence`, `similarity` and the Wikipedia deep links
   are returned or derivable, but they aren't shown. This is the project's main promise.
3. **The merge view doesn't look like a diff.** It's a list of cards. No summary,
   no filter, no navigation, and no fixed fi | sv | en columns where a gap shows up as an
   empty cell.
4. **System versioning is buried** in a plain table inside a modal. The SVT bonus track
   deserves a real timeline.
5. **No feedback states.** Fetch errors fail silently, there are no loading skeletons,
   no deep links (URL state) and no keyboard flow.
6. **Patch drafts can't be exported** (no copy or download), though the copy promises
   "one-click export".

## Proposed improvements (in priority order)

### P1: Make the core story demoable
- **Summary bar** for the article: counts of conflicts, gaps and agreements, plus a
  stacked "consistency meter". Each segment acts as a filter.
- **Fixed language columns** (fi | sv | en) in every cluster. A missing language shows as
  a dashed "missing in sv" cell, so a gap looks like a gap in a diff.
- **Receipts drawer** on each verdict: evidence passage, similarity score, verdict
  source (deterministic / llm / human), revision id and a "View on Wikipedia" link per
  language (`https://{lang}.wikipedia.org/w/index.php?oldid={rev}`).
- **Inline review actions** on each verdict: ✓ Agree / ✗ Disagree / ✎ Fix + note.
  These post to `/api/reviews` and immediately show a "human-reviewed" stamp.
  Server change: add `cl.id` to the merge query.

### P2: Interactivity and flow
- **Filter chips + text filter** across clusters (status, claim kind, language).
- **Keyboard navigation** in reviewer mode: `j`/`k` to move between clusters,
  `a`/`d`/`f` to review, `h` to open history, `?` for help.
- **Live search** (debounced, as you type) and **hash routing** (`#/merge/Q123`), so
  the demo can deep-link straight to a pre-warmed article and the extension can link in.
- **Review progress** ("7 / 23 conflicts reviewed").

### P3: Make MariaDB visible
- **"Biography of a claim" timeline**: a vertical timeline built from the
  `FOR SYSTEM_TIME ALL` rows that interleaves claim edits with verdict changes
  (deterministic → llm → human). It shows the version diff between steps and labels
  each step with its source.
- Small **"powered by" hints**, e.g. a tooltip on similarity that reads
  "cosine distance via `VEC_DISTANCE_COSINE`, HNSW index".

### P4: Export and Q&A polish
- Patch drafts get **Copy** and **Download .wiki** buttons, plus a link to open the
  target article's edit page (opened by a human, never applied automatically).
- Q&A gets loading/error states, suggested questions, and citations shown as
  per-language chips that open the passage.

## Constraints I'll keep to
- Stay vanilla JS, HTML and CSS served by nginx: no build step, so nothing new can break
  at the hackathon.
- Keep the existing dark visual language and color-code verdicts consistently
  (red conflict, amber gap, green agree).
- Server changes stay minimal and additive: expose `claim_links.id` and
  `articles.revision_id` per language in the merge payload, nothing more.
