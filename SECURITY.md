# Security Policy

## Reporting a vulnerability

Please report security issues privately by opening a GitHub Security Advisory
("Report a vulnerability" on the repo's Security tab) or by emailing the
maintainer listed in the repository profile. Do not open a public issue.

Include: affected component (`server/`, `extension/`, `web/`, `jobs/`), a
reproduction, and the impact you see. We aim to acknowledge reports within
72 hours and to publish a fix or mitigation within 30 days for confirmed
issues.

## Scope

In scope:

- The FastAPI server (`server/`): SQL handling, CORS, rate limiting, the
  federation feed signature verification (`server/federation.py`), and the
  `/api/extension/*` read-only endpoints.
- The browser extension (`extension/`): permissions, content-script DOM
  handling (all API text rendered via text APIs, never innerHTML with server
  data), deep-link validation, service-worker caching.
- The web app (`web/`): rendering of server data.

Out of scope: Wikipedia itself, the Verda Inference API, and the MariaDB
dataset downloads.

## Notes for reporters

- The extension sends only `lang`, `wiki_page_id`, `title`, and
  `live_revision_id` to the configured API; it collects no browsing history.
- Federation feeds are ed25519-signed per record; a canonical instance is a
  convenience aggregator, not an authority — imported findings are verifiable
  locally by recomputation and receipts.
