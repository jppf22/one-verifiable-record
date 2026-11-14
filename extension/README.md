# One Verifiable Record — browser extension

Manifest V3 Chromium companion. It runs only on `fi`, `sv`, and `en` Wikipedia
article pages and uses MediaWiki's live `wgArticleId` page ID when available
(with canonical title as fallback) plus `wgRevisionId` / `#t-permalink` to
resolve the imported page in `/api/extension/resolve`. It remains silent on
non-article namespaces (`Special:`, `Talk:`, `Category:`, edits, diffs, history)
and on pages without an imported counterpart in another supported language.

## Features

1. **Heading badge (`#ovr-extension-root` Shadow DOM)**
   - Severity-colored status pill (`"One Verifiable Record: N claims marked (I to check, A agree)"` on current snapshots; snapshot summary + `"Open analysed revision (oldid) ↗"` when the live page has changed since import).
   - Counterpart comparison link (`"Compare with sv · en"` → `#/page/{lang}/{title}`).
   - **Hide/Show inline findings** toggle (synced globally via `chrome.storage.sync`).
   - **Next conflict (N)** button cycling focus through active inline conflict markers.
   - **Per-type filter chips** (`Date`, `Number`, `Quantity`, `Relation`, `Fact`, plus extracted custom kinds; empty selection shows all types).
   - Browser toolbar badge showing live issue count or `✓` per tab.
2. **Inline claim highlighting (CSS Custom Highlight API)**
   - Paints unique prose matches (`ovr-conflict`, `ovr-possible-conflict`, `ovr-only-here`, `ovr-possible-match`, and thin soft-teal `ovr-agree`) without mutating Wikipedia text nodes, excluding infobox/navigation chrome to avoid false ambiguity.
   - Places non-overlapping, keyboard-reachable inline markers (`#ovr-extension-markers` Shadow DOM).
3. **Findings tray**
   - Retains ambiguous/unanchored local claims and `missing_here` counterpart claims alongside anchored claims, grouped by claim kind.
   - Every tray item provides keyboard-reachable controls: **Jump to inline mark** (when anchored), **Inspect & receipts** (opens the marker popover), **Open focused comparison ↗**, and **Draft a patch ↗** (for conflicts and missing claims).
4. **Marker popover & receipts preview**
   - Shows claim wording, counterpart wording, verdict confidence/similarity preview, and an expandable **Read receipts** drawer with analysed-revision Wikipedia links (`"Open {lang} on Wikipedia (analysed revision) ↗"`).
   - Non-editing deep links: **Open focused comparison** (`#/page/{lang}/{title}?focus=r-{id}`), **Review this claim** (`&review=1`), **Draft a patch** (`&panel=suggestions`), and **Copy claim**.

## Install (development mode)

1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. **Load unpacked** → select this `extension/` directory
4. Open an imported `fi`, `sv`, or `en` Wikipedia article with counterpart pages

## Configure (extension popup)

Click the extension toolbar icon:

- **API base URL**: FastAPI server or web-app `/api` proxy (default `http://localhost:8000`; HTTPS required outside `localhost`/`127.0.0.1`, requested via optional host permission on save)
- **Web app URL**: standalone comparison view (default `http://localhost:8080`)
- **Show badge on tracked Wikipedia pages**: pause/resume the companion
- **Show inline claim highlights**: toggle inline Custom Highlight painting globally
- **Filter inline claim types**: toggle which claim kinds are highlighted inline (synced with the in-page filter chips; empty selection shows all types)

## Scope (honest limits)

The extension is a read-only companion, not an automated editor. Inline painting
is enabled only when the live revision ID matches the analysed snapshot revision;
stale pages display snapshot counts and link to the exact analysed revision. It
never edits Wikipedia or implies unmarked text was verified.
