# One Verifiable Record — browser extension

Manifest V3 Chromium companion. It runs only on fi/sv/en Wikipedia pages and uses
the live MediaWiki page ID when available (title is only a fallback) to resolve a
page in the imported dataset. It remains invisible unless that page has an imported
counterpart in another supported language.

The heading badge is the summary and findings control. On an exact match between
the live and imported revision it automatically paints unique, exact claim
matches with the browser Custom Highlight API and adds keyboard-reachable
markers. The findings tray retains ambiguous/unanchored claims and
`missing_here` rows; the marker popover links to
`#/page/{lang}/{title}?focus=r-{claim-id}` in the comparison app.

Inline painting is deliberately unavailable for stale, revision-unknown or
not-ready pages. The badge explains that restriction while still offering the
full comparison. “Hide inline findings” persists as a global extension setting.

## Install (development mode)

1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. **Load unpacked** → select this `extension/` directory
4. Open an imported Wikipedia page that has counterpart pages in another language

## Configure

Click the extension icon:

- **API base URL**: where the FastAPI server runs (default `http://localhost:8000`;
  use HTTPS for any non-localhost deployment). Saving a new HTTPS host asks Chrome
  for permission to contact that host.
- **Web app URL (comparison view)**: where the web app runs (default `http://localhost:8080`)
- **Badge enabled**: toggle the companion off/on without reloading
- **Hide/show inline findings**: use the button in an eligible page's heading
  badge; the choice is stored globally in extension sync storage.

## Deployed-origin note

For a hosted API, configure its HTTPS origin in the popup; Chrome requests a
narrow optional host permission at that point. The extension ships with only
fi/sv/en Wikipedia and local-development host permissions. See
[`../docs/browser-extension-roadmap.md`](../docs/browser-extension-roadmap.md)
for the full cross-repository/API plan.

## Scope (honest limits)

The extension is a satellite, not the product. It never edits Wikipedia or
implies that unmarked article text was checked; detailed receipts remain in the
standalone comparison view. A native MediaWiki extension requires WMF review
and community consensus, which is out of scope.
