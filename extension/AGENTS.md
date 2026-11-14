# Extension AGENTS.md - Browser Extension Instructions

**READ THIS BEFORE TOUCHING `extension/` CODE**
This file inherits from and refines the root `AGENTS.md` for `extension/`.

## Extension Overview

Manifest V3 Chromium browser extension (`fi`, `sv`, and `en` Wikipedia companion).
Runs strictly on article views (`wgNamespaceNumber === 0`, `wgAction === "view"`) and provides:
- Severity-colored heading badge (`#ovr-extension-root` Shadow DOM) + browser toolbar badge
- Inline claim highlighting via the CSS Custom Highlight API (`CSS.highlights`)
- Non-overlapping, keyboard-reachable inline markers (`#ovr-extension-markers` Shadow DOM)
- Findings tray retaining ambiguous/unanchored local claims and `missing_here` counterpart claims
- Rich marker popover with verdict/confidence receipts preview, receipts drawer, and page-centric deep links
- Popup & in-page configuration UI synced via `chrome.storage.sync`

## Directory Structure

```text
extension/
├── manifest.json      # Manifest V3 (minimal permissions: ["storage"])
├── background.js      # Service worker (API broker, cache, X1 /api/badge fallback, toolbar badge)
├── content.js         # Content script (MediaWiki RLCONF detector, CSS.highlights, Shadow DOM UI)
├── popup.html         # Extension popup configuration UI
├── popup.js           # Popup logic (URLs, optional host permissions, inline toggle, kind filters)
├── AGENTS.md          # Agent developer documentation
└── README.md          # User-facing documentation
```

## Constraints (Never Violate)

1. **Manifest V3 only**: Service worker in `background.js`, no inline scripts in `popup.html`, no `eval()`.
2. **Minimal permissions**: Only `"storage"` in `permissions`, `fi/sv/en.wikipedia.org` + `localhost`/`127.0.0.1` in `host_permissions`, and `"https://*/*"` in `optional_host_permissions` (requested only on explicit user save in `popup.js`).
3. **Article-only scope on `fi`, `sv`, `en` Wikipedia**: Reject non-mainspace pages (`Special:`, `Talk:`, `Category:`, localized equivalents), edits, history views, and diffs before making any API call.
4. **Snapshot safety**: Inline highlights (`CSS.highlights`) and inline markers only render when `freshness === "current"` (live revision ID equals `analysed_revision_id`) and `analysis_status === "ready"`. Stale pages show precomputed summary counts and an `"Open analysed revision"` permalink without painting live text.
5. **No Wikipedia editing**: Deep links (`#/page/{lang}/{title}?focus=r-...`, `&review=1`, `&panel=suggestions`) and `"Copy claim"` never modify or submit anything on Wikipedia.

## Architecture & Key Flows

### 1. Page Identity & Snapshot Detection (`content.js` -> `candidate()`)
Content scripts run in Chromium's isolated world (`window.RLCONF` is not on the isolated `window`), and desktop MediaWiki skins do not emit Parsoid `<meta property="mw:pageId">` tags. `candidate()` extracts identity safely from DOM:
1. Inline `<script>` `RLCONF` JSON fields: `wgNamespaceNumber` (`0` required), `wgAction` (`"view"` required), `wgIsArticle`, `wgArticleId`, `wgRevisionId`, `wgCurRevisionId`, and canonical `wgTitle` (which works across redirects and `/w/index.php?oldid=...` permalinks without `?title=`).
2. Fallbacks: `?oldid=` / `?curid=` URL params, `#t-permalink a[href*="oldid="]`, `<link rel="canonical">`, `body.ns-0` / `body.action-view`, `#firstHeading`, and Parsoid `<meta>` tags.

### 2. Service Worker & API Contract (`background.js`)
- **Primary lookup**: `GET /api/extension/resolve?lang=&wiki_page_id=&title=&live_revision_id=`
  - Untracked pages or pages with zero counterparts return `{ visible: false }` and render nothing (`V-W10`).
- **Legacy fallback (`X1`)**: If `/api/extension/resolve` returns `404`, falls back to `GET /api/badge?lang=&title=` (handling both v2 page payloads and v1 legacy payloads without `gaps`).
- **Inline data**: `GET /api/extension/annotations?lang=&wiki_page_id=&title=&live_revision_id=`
  - Read-only projection of `diff.build_comparison`; never triggers alignment or LLM calls on Wikipedia visits.
- **Toolbar badge**: `chrome.action.setBadgeText` / `setBadgeBackgroundColor` / `setTitle` scoped per `sender.tab.id` (`#b32424` red for conflicts, `#a65b00` amber for gaps/missing, `#14866d` `✓` when all agree).

### 3. Anchoring, Custom Highlight API & Findings Tray (`content.js`)
- `indexText()` indexes visible article prose while omitting `.infobox`, `.sidebar`, `.shortdescription`, `.hatnote`, `.toc`, references, thumbnails, and navigation so repeated sidebar dates/values do not cause false ambiguity.
- `anchorClaim(index, claim)` classifies each local claim as `"anchored"`, `"ambiguous"` (`>= 2` prose matches), or `"unanchored"` (`0` verbatim matches), using `claim.text`, `claim.context` window disambiguation, and non-trivial `claim.value` (`length >= 4`).
- `CSS.highlights` registers `ovr-conflict`, `ovr-possible-conflict`, `ovr-only-here`, `ovr-possible-match`, and thin soft-teal `ovr-agree` (`X2`).
- `reposition()` places superscript-style circular marker buttons and staggers colliding coordinates so adjacent claims on the same line never overlap.
- The **Findings tray** groups all local claims and `missing_here` rows by `kind` and provides keyboard-reachable **Jump to inline mark**, **Inspect & receipts**, **Open focused comparison ↗**, and **Draft a patch ↗** controls (`X3`, `X4`).

### 4. Synced Settings (`chrome.storage.sync`)
- `apiBase` (default `http://localhost:8000`)
- `webBase` (default `http://localhost:8080`)
- `enabled` (default `true`)
- `inlineFindings` (default `true`)
- `selectedKinds` (default `[]` — empty array means show all claim types per `X5`; synced two-way between `popup.html` and the in-page `Show types:` chips)
