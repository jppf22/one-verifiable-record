# One Verifiable Record — browser extension

Manifest V3 Chrome extension. Adds a consistency badge under the heading of any
Wikipedia article that the backend tracks: how many cross-language conflicts, gaps
(facts missing from some editions) and verified claims the fi/sv/en editions have,
with a deep link into the merge view (`#/merge/{QID}`). The pill is red when there
are conflicts, amber when there are only gaps, green otherwise.

## Install (development mode)

1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. **Load unpacked** → select this `extension/` directory
4. Open a tracked Wikipedia article (one present in the imported dataset)

## Configure

Click the extension icon:

- **API base URL**: the web app address (default `http://localhost:8080`). Its nginx
  proxy forwards `/api` internally, so a hosted demo should use
  `http://<vm-ip>:8080`, not the private FastAPI port.
- **Web merge view URL**: where the web app runs (default `http://localhost:8080`)
- **Badge enabled**: toggle the content script off/on without reloading

## Deployed-origin note

When saving a hosted API URL, Chrome asks for permission only for that host. The
server already permits Wikipedia origins for the content-script badge request.

## Scope (honest limits)

The extension is a satellite, not the product: the merge view cannot be rendered
inside Wikipedia's DOM, so the badge deep links to the standalone web app. A
native MediaWiki extension requires WMF review and community consensus, which is
out of scope for a hackathon; see the roadmap in the main README.
