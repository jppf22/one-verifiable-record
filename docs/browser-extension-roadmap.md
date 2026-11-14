# Chromium extension roadmap

## Purpose and repository boundary

This document describes how to build **One Verifiable Record Companion**, a
Chromium extension that activates on Wikipedia pages represented in this
project's imported dataset. Its job is to make existing cross-language
comparison results available at the article, without implying that it has
independently checked live Wikipedia content.

The extension may live in a **separate repository**. That is a useful boundary:
this repository owns the imported snapshots, extraction/alignment jobs, database
and API; the extension repository owns Manifest V3 packaging, Wikipedia UI
integration, browser-store release work and extension tests. Goal separation
should not change the public API contract. The small `extension/` directory in
this repository is a prototype and integration reference, not a requirement
that the production extension be kept here.

The extension is a companion, not an automated editing tool. It can take an
editor to receipts, comparison views and human-authored draft material, but it
must never make edits to Wikipedia.

## What "works for a page in the dataset" means

The database holds an imported revision of a concrete Wikipedia page:

- language edition (`fi`, `sv`, or `en` initially);
- MediaWiki page ID;
- canonical title;
- imported revision ID;
- a link to one or more counterpart pages in the other supported editions;
- extracted claims and their comparison results.

A page is eligible only when all of the following hold:

1. The browser is viewing a supported Wikipedia language edition.
2. The URL represents a normal article page, rather than a special page,
   history, search result or arbitrary namespace.
3. Its MediaWiki page ID is present in the imported dataset.
4. The imported page has at least one counterpart in `page_links`.

For ineligible pages the extension should be silent. A missing badge is less
confusing than a prominent "not analysed" warning on most of Wikipedia.

The findings describe the imported revision, not necessarily the live revision
being viewed. The UI must therefore report one of these states:

| State | Meaning | Safe presentation |
| --- | --- | --- |
| Current snapshot | Live revision ID matches the analysed revision. | Show normal comparison status. |
| Snapshot behind live page | Wikipedia has changed since import. | Show a stale-data indicator and retain links to the analysed revision. |
| Exact revision view | The user is viewing the imported `oldid`. | Treat findings as revision-specific. |
| Analysis incomplete | The page is tracked, but claims or verdicts are not ready. | Say so; do not display zero conflicts as success. |

## Product decisions before implementation

Agree on these points before starting browser work:

- **Initial coverage:** `fi`, `sv`, and `en`; article namespace only.
- **Primary interaction:** a small status indicator under the article heading.
- **Detailed interaction:** initially open the existing standalone comparison
  view; later, optionally render a Chromium Side Panel with a concise summary.
- **Trust language:** call results "comparison findings" rather than claims of
  absolute factual correctness. Always make source revisions available.
- **Freshness policy:** stale snapshots show findings only with an explicit
  freshness label; do not describe them as a live check.
- **Privacy policy:** no browsing history collection, analytics, or calls for
  pages that can be rejected locally. Send only the page identity and revision
  needed to resolve the dataset record.
- **Editing policy:** deep links and copyable talk-page drafts are permitted;
  automatic edits, pre-filled submissions and bot-like workflows are not.

## System responsibilities

```text
Wikipedia page
  └─ content script: read page identity; place/update UI
       └─ extension service worker: cache, request API, manage configuration
            └─ One Verifiable Record API: resolve imported page and summary
                 └─ MariaDB dataset: snapshots, page_links, claims, verdicts
```

The content script should not contain comparison logic. It is responsible only
for lifecycle and display on a Wikipedia tab. The API remains authoritative for
whether a page is tracked and which counterpart pages and results exist. The
service worker centralises networking, cache invalidation and permissions.

## Page identity and URL handling

### Prefer stable identity over title matching

Titles are a useful fallback but not a robust primary key: redirects,
capitalisation, renamed pages, URL escaping and language-specific conventions
make exact title lookup fragile. Resolve a live page using this priority:

1. language edition + MediaWiki page ID;
2. canonical page title returned by MediaWiki;
3. URL-derived title only as a fallback.

The API already stores `articles.lang`, `articles.page_id`, `articles.title`
and `articles.revision_id`; expose those fields in the extension-resolution
contract. A resolution request should also include the live revision ID when it
can be obtained, so the server or client can label freshness correctly.

### Handle all common article forms

Support canonical `/wiki/Title` pages first. Then handle:

- `w/index.php?title=Title` pages;
- `oldid` revision URLs;
- redirects, using the final canonical article identity;
- Vector and legacy Wikimedia skins;
- article navigation that replaces the heading or content without a full
  extension lifecycle restart.

Exclude special pages, searches, diffs, history pages, talk namespaces,
category pages and edit/submit screens in the first release. Later releases may
offer an editor-specific affordance on edit pages, but it needs separate UX and
policy review.

### Make rendering idempotent

Wikipedia can change its DOM after the document first becomes idle. The content
script should:

1. derive a page identity;
2. request the summary once for that identity;
3. find the current article heading;
4. insert or update exactly one extension-owned UI host;
5. re-run when navigation or the heading changes;
6. remove/rewrite the old host when the page identity changes.

Use a namespaced element and Shadow DOM (or equally strong style isolation) so
Wikipedia skins cannot accidentally style the extension, and vice versa.

## API contract to agree across repositories

Keep this API small and versioned. The current `GET /api/badge?lang=&title=`
endpoint proves the basic flow, but a production resolver should accept stable
page identity. For example, an extension-specific endpoint can conceptually
accept `lang`, `page_id`, `title` and `live_revision_id` and return:

- `tracked`: whether the page is in the eligible imported set;
- canonical imported `page` and `counterparts`;
- `analysed_revision_id` and `live_revision_id`;
- a freshness/analysis status;
- counts for conflicts, missing here, only here and agreements;
- a dataset or analysis version, for cache invalidation;
- a canonical deep link to the comparison view.

The API should use a successful, compact negative response for an untracked
page when feasible. The extension can then treat ordinary non-membership as a
normal outcome, not an error. Rate limits and a response size limit should be
documented.

Do not make the badge endpoint perform expensive alignment or LLM grading on a
user's page visit. It reads precomputed results. A separate explicit action in
the full product may request a re-check, subject to the project freshness
policy.

### Dataset/API work required

Before public release, verify that the import and server guarantee:

- `page_links` are derived and stored in both directions;
- every eligible page has one or more counterparts;
- imported page IDs and revision IDs are retained;
- API title lookup canonicalises URL form only as a fallback;
- no counterpart is inferred merely from similar titles;
- missing extraction or grading produces an incomplete state, not misleading
  zero counts;
- a re-import has a defined versioning strategy so snapshot freshness and claim
  provenance remain meaningful.

When upgrading an existing database, apply the page-link migration before the
richer-claim migration, then re-import so counterpart rows are materialised.

The existing system-versioned claims and verdict records are particularly
valuable here: their revisions should be linked from the detailed view as the
reason a finding was made.

## Extension architecture (Manifest V3)

Build and publish as a Chromium Manifest V3 extension, which covers Chrome,
Microsoft Edge, Brave, Vivaldi and most other Chromium browsers. Treat each
browser store as a separate distribution channel; compatibility is high but
store rules and enterprise policies vary.

### Components

| Component | Responsibility |
| --- | --- |
| Manifest | Declarative permissions, matching Wikipedia pages, popup/options and service worker registration. |
| Content script | Read page identity, observe page lifecycle, and render the small in-page affordance. |
| Service worker | Receive messages, call the API, cache summaries, manage configuration and expose tab/action state. |
| Popup/options | Enable/disable the companion, show API service health/configuration where appropriate, and explain freshness/privacy. |
| Side Panel (optional) | Display a richer, read-only finding summary without crowding Wikipedia. |
| Shared types/contracts | Keep the API response shape and state vocabulary consistent with the server repository. |

The service worker, rather than a page-injected script, should broker API
requests. This centralises retries, caching and host permission handling, and
avoids spreading service configuration across tabs.

### Permissions

Ask for the smallest set possible:

- `storage` for user settings and cache metadata;
- a Wikipedia host pattern restricted to supported editions;
- the hosted API origin, preferably through optional host permissions when a
  user-configurable endpoint is needed;
- `sidePanel` only if that feature ships.

Do not request broad access to all sites, tabs, clipboard write, downloads, or
identity unless a reviewed feature genuinely requires it. Production endpoints
must use HTTPS. Localhost permissions are appropriate for developer builds but
should not be the default public-release story.

## User experience

### The in-page indicator

Place a compact, accessible indicator immediately below the article heading.
It should include a short status, the counterpart languages and an obvious
detail action. Suggested visual states are:

- **red:** one or more contradicted claims;
- **amber:** facts found in counterpart pages but missing in this edition;
- **green:** no detected conflicts or gaps in the analysed claim set;
- **gray/blue:** tracked but analysis incomplete or its snapshot is stale.

The precise wording should not overstate what the pipeline knows. For example,
"2 comparison conflicts in analysed revision" is preferable to "2 false
facts." Counts should be visually secondary to the action that reveals
receipts.

### Detail view

The first release can deep-link into the existing page comparison route. The
link must identify the server-resolved canonical page, not reconstruct it from
the raw URL title. That avoids broken links after redirects or normalisation.

A later Side Panel should contain, at most:

- freshness label and analysed revision link;
- the counterpart page list, each with language and source revision;
- top conflicts/gaps with one-sentence source receipts;
- a link to the full comparison UI;
- a link to project privacy/methodology documentation.

Avoid recreating a dense comparison console inside Wikipedia. The standalone
application remains the appropriate place for filtering, claim history,
reviews, questions and draft generation.

### Accessibility and localisation

Use semantic buttons/links, keyboard navigation, sufficient contrast, focus
states and text labels in addition to color. Keep the indicator short enough
for narrow layouts. Initially localise the extension interface in English, then
add Finnish and Swedish strings; never translate source claim text in a way that
could be mistaken for a quotation.

## Caching, performance and resilience

- Cache summaries by supported language, page ID, live revision ID and dataset
  version. Do not reuse a cached summary after any of those changes.
- Use short-lived in-memory/session caching for navigation and a bounded
  persisted cache only if necessary.
- Coalesce simultaneous lookups from multiple UI events for the same page.
- Give network calls a timeout and fail silently on ordinary Wikipedia pages if
  the API is unavailable.
- Cache a compact negative result briefly to avoid repeating lookups while a
  user visits unsupported pages, but do not retain browsing history indefinitely.
- Never block page rendering, alter article text, or retry in a tight loop.

An unavailable API should produce no badge or a non-alarming unavailable state
only when a badge host was already created. It must not impair Wikipedia.

## Security, privacy and trust

- Render API content through DOM-safe text APIs; never insert server text as
  HTML into Wikipedia.
- Validate any deep-link origin before opening it; configuration must not turn
  received API data into arbitrary navigation.
- Restrict API CORS and host permissions to the real extension origins and
  Wikipedia as needed. Review the extension ID/origin implications before
  production deployment.
- Document what request data is sent, its retention period and how users disable
  the extension.
- Protect the API against origin abuse, request bursts and oversized titles.
- Keep review identity, if later supported, separate from browser identity and
  require explicit user action before sending a review.
- Include methodology and provenance links so readers can inspect the source
  passages and exact revisions.

## Development process

### 1. Establish the contract

Write a short versioned API specification jointly with the server owners. Add
example positive, untracked, stale and incomplete responses. Decide ownership
of API version changes, deployment URLs, CORS, rate limits and deprecation.

### 2. Prove identity resolution

Create a test set covering a tracked page in each language, a redirect, a
renamed-looking title, an untracked article, an `oldid` URL and a non-article
URL. Confirm page ID resolution maps each tracked page to the same database row
the dataset importer created.

### 3. Ship the smallest usable vertical slice

Implement only the page detector, service-worker lookup, silent ineligible
path, heading indicator and deep link. Exercise it against a deployed API—not
only localhost—and ensure the full comparison route opens to the resolved page.

### 4. Add freshness and incomplete states

Pass live revision identity, show the analysed revision, and ensure stale data
cannot look like a current verification. Add useful explanatory copy before
adding richer interactions.

### 5. Improve detailed inspection

Decide whether the standalone view alone is sufficient. If a Side Panel is
added, keep it read-only and make it use the same API state model as the badge.

### 6. Harden and prepare distribution

Perform permission review, privacy review, accessibility testing, extension
security review, packaging, store assets, support documentation and release
notes. Validate on current Chrome and at least Edge and Brave.

## Test plan

### Automated checks

- unit tests for URL parsing, namespace filtering, identity state transitions,
  title fallback, message validation and cache keys;
- API contract tests against recorded positive/negative/stale/incomplete
responses;
- browser integration tests that load an unpacked extension and verify a single
badge is inserted, updated after navigation and removed when ineligible;
- visual/accessibility checks for supported Wikimedia skins and narrow windows;
- security tests for hostile title/receipt strings and invalid configured URLs.

### Manual acceptance matrix

Test each supported language with a tracked current page, a tracked old revision,
an untracked page, a page lacking counterpart links, a redirect, a Special page,
an article with a changed live revision and API-offline operation. Test at least
Chrome, Edge and Brave. Confirm no extension UI appears outside eligible pages.

## Deployment and release

1. Deploy the API behind a stable HTTPS domain and configure the extension API
   permission and server CORS policy for that domain.
2. Publish an API version and observe it in staging with a fixed dataset version.
3. Build a signed production package with public-release defaults, not local
   development endpoints.
4. Distribute internally as an unpacked or enterprise-managed extension first.
5. Submit to the Chrome Web Store with clear screenshots, privacy disclosure,
   support contact and an explanation that results are dataset-snapshot based.
6. Monitor request/error rates and freshness drift after release.
7. Roll out API or extension versions gradually; retain compatibility during an
   upgrade window and provide a kill switch for a defective badge release.

## Operational ownership

The extension is only as trustworthy as the data pipeline. Set explicit owners
and service objectives for:

| Area | Suggested owner |
| --- | --- |
| Import, extraction, alignment and snapshot version | Data/API project |
| API availability, CORS, rate limiting and schema compatibility | API project |
| Manifest, UI, store submissions and browser compatibility | Extension project |
| Freshness messaging and methodology | Shared product/editorial ownership |
| Security/privacy incident response | Shared, with a named on-call contact |

Track dataset age and the share of badge views whose live revision differs from
the imported revision. That metric is more meaningful than installation count:
it shows when the companion may be becoming stale.

## Milestones and definition of done

### Milestone A — reliable badge

- Page-ID-backed resolution works for fi/sv/en article pages.
- Only pages with imported counterparts render a single indicator.
- The indicator deep-links to the correct comparison page.
- Offline/untracked pages do not degrade Wikipedia.

### Milestone B — trustworthy status

- Live and analysed revision IDs are compared.
- Stale and incomplete states are explicit and accessible.
- Findings link to source receipts and exact revisions.
- Cache, timeout, privacy and permission policies are implemented and tested.

### Milestone C — distributable companion

- Hosted HTTPS API and production permission configuration are in place.
- Cross-browser, accessibility and security test matrices pass.
- Store listing, privacy notice, support path and release/rollback procedure are
  ready.

The extension is complete for an initial release when it can silently recognise
eligible imported pages, accurately communicate what revision was analysed,
open evidence-backed comparison results, and fail harmlessly everywhere else.
