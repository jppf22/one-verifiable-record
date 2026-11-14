# Frontend ⇄ API contract (shared by all agents)

This is the source of truth for parallel work. If you need to change it, don't edit
it silently: add a note under "Contract change requests" at the bottom and tell the
coordinator.

## Endpoints (existing endpoints stay backwards compatible)

### `GET /api/articles?q=` (one entity per result)
`[{id, lang, title, wikidata_qid, revision_id, editions:[{id, lang, title, wikidata_qid, revision_id}]}]`

The top-level fields identify the preferred display edition (English when present).
`editions` contains every matching imported language edition. A QID appears only once;
the `limit` applies to entities, not individual editions.

### `GET /api/discover?limit=8` (new)
```jsonc
{
  "high_conflicts": [{ "qid", "title", "editions", "conflicts", "claims", "topic_type" }],
  "diverse": [{ "qid", "title", "editions", "conflicts", "claims", "topic_type" }]
}
```
Both lists contain one item per QID. `high_conflicts` ranks distinct contradicted claim
links. `diverse` excludes entities with clear biographical claim predicates and rotates
through inferred topic types (Place, Organization, Event, Work, Topic).

### `GET /api/merge/{qid}` (extended, additive only)
```jsonc
{
  "qid": "Q1757",
  "articles": [{ "id", "lang", "title", "wikidata_qid", "revision_id", "fetched_at",
                 "url": "https://fi.wikipedia.org/w/index.php?oldid=123" }],
  "summary": { "conflict": 3, "gap": 7, "agree": 12,
               "links_total": 30, "links_reviewed": 4 },
  "clusters": [{
    "key": "c-1042",              // stable: "c-" + min claim id in the cluster
    "status": "conflict|gap|agree",
    "langs_present": ["en", "fi"],
    "claims": [{ "id", "article_id", "kind", "subject", "predicate", "value_text",
                 "statement_text", "revision_id", "lang",
                 "url": "https://{lang}.wikipedia.org/w/index.php?oldid={revision_id}" }],
    "links": [{ "id", "claim_a", "claim_b", "verdict", "verdict_source",
                "similarity", "evidence", "rationale", "computed_at",
                "review": null | { "id", "decision", "reviewer", "note", "created_at" } }]
  }]
}
```
`review` is the **latest** review for that link. `url` is `null` when `revision_id` is null.
`fetched_at` makes the snapshot age visible; a verdict applies to the imported revision,
not necessarily the live article.

### `POST /api/reviews` (extended response)
Body: `{claim_link_id, reviewer, decision: "agree"|"disagree"|"fix", note?}`
Response: `{ "ok": true, "review_id": 17, "recheck": <RecheckResult> }`.
The server **automatically re-checks the article** after storing the review.

### `GET /api/reviews?link_id=` (new)
`[{id, claim_link_id, reviewer, decision, note, created_at}]`, newest first.

### `POST /api/recheck/{qid}` (new)
Re-checks the whole article page. It re-runs vector alignment (new candidate links) and
re-grades the links. Body (optional): `{"focus_link_ids": [12, 13]}`.
Response `RecheckResult`:
```jsonc
{
  "qid": "Q1757",
  "checked_at": "2026-11-14T12:00:00",
  "before": { "conflict": 3, "gap": 7, "agree": 12 },
  "after":  { "conflict": 3, "gap": 6, "agree": 13 },
  "new_links": 1,
  "links": [{ "id": 12, "before": "contradicted", "after": "contradicted",
              "changed": false, "resurfaced": true, "regraded": true }]
}
```
* `links` covers at least every focus link and every link whose verdict changed.
* `resurfaced`: the link was contradicted before and is **still contradicted** after the re-check.
* `regraded`: the verdict was actually recomputed. Links whose two claims have
  **no newer system-versioned row since the link's `computed_at`** keep their verdict and
  skip the LLM (`regraded: false`). Focus links are always regraded.
* The server only writes `UPDATE claim_links` when the verdict changes, so no-op
  history rows aren't created. Exception: stale links (claim changed) always get
  `computed_at` bumped (assumption A6).

### `GET /api/suggestions/{qid}` (extended)
Drafts gain `claim_link_id` (nullable) and `status`. Repeated calls must **not**
duplicate identical drafts. Response: `{qid, created, drafts:[{id, patch_type, wikitext,
status, lang, title, claim_link_id}]}`.

### `POST /api/suggestions/{id}/export` (new)
Marks the draft `status='exported'` (system-versioned update). It then re-checks the
article with `focus_link_ids=[claim_link_id]` when the draft has a link.
Response: `{ok: true, suggestion_id, recheck: RecheckResult | null}`.

### `GET /api/claims/{id}/history` (extended, additive)
`{claim: [...], links: [...], reviews: [{id, claim_link_id, reviewer, decision, note, ROW_START, ROW_END}]}`.
`links` rows also include `similarity` and `evidence`.

### `GET /api/badge?lang&title` (extended, additive)
Adds `"gaps": n` (gap clusters) and `"conflicts": n` (conflict clusters) next to `counts`.

### `GET /api/qa?qid&q` (unchanged)

## Frontend module layout (no build step, native ES modules)

| File | Owner | Purpose |
| --- | --- | --- |
| `web/index.html` | coordinator → web-core | page regions (IDs below) |
| `web/js/api.js` | coordinator | fetch wrappers for every endpoint above |
| `web/js/util.js` | coordinator | `$`, `$$`, `el`, `esc`, `toast`, `getReviewer`, `wikiUrl` |
| `web/js/bus.js` | coordinator | tiny event bus: `on(evt, fn)`, `emit(evt, payload)` |
| `web/js/main.js`, `router.js`, `search.js`, `merge.js`, `review.js`, `keyboard.js` | **web-core** | app shell, routing, search, merge view, review + re-check UI, shortcuts |
| `web/css/base.css`, `web/css/merge.css` | **web-core** | |
| `web/js/history.js`, `suggestions.js`, `qa.js` | **panels** | SVT timeline, patch export, Q&A |
| `web/css/panels.css` | **panels** | |

### Bus events (cross-module interface)
| Event | Payload | Emitted by | Handled by |
| --- | --- | --- | --- |
| `article:loaded` | `{qid, data}` (merge payload) | merge.js | panels (Q&A, suggestions reset) |
| `history:open` | `{claimId}` | merge.js / keyboard.js | history.js |
| `recheck:done` | `{qid, result, reason: "review"|"export"|"manual", linkIds?}` | review.js / suggestions.js | merge.js (re-render + banner) |
| `merge:reload` | `{qid}` | anyone | merge.js |

Panels expose `init()` in each module. `main.js` imports and calls them.

### Page regions in index.html
`#app-header`, `#search-form`, `#search-input`, `#results`, `#article-picker`,
`#merge-view`, `#merge-header`, `#summary-bar`, `#filters`, `#recheck-banner`, `#clusters`,
`#suggestions`, `#qa`, `#history-dialog`, `#help-dialog`, `#toasts`.

## Contract change requests
(append here)
- (server) RecheckResult `links[].before` can be `null` for a link that the re-check's
  own alignment step just created (it had no verdict before). Treat `null` as "new link".
  Every entry in `links` has `regraded: true`. See assumptions S4.
- (web-core) `recheck:done` may carry `linkIds` (the reviewed link). If a review response has
  no `recheck`, review.js emits `merge:reload` instead. See W4.
- (server/web) Article search now groups editions server-side and adds `/api/discover` for
  conflict-ranked and non-biographical discovery cards. The nested `editions` field is additive;
  the web client accepts both this response and the prior flat response.
- (server/web, Kiro) Additive: `POST/GET /api/links/{id}/resolution` (fix a reviewer-confirmed
  conflict in every language), `links[].resolution` in `/compare`, `drafts[].resolution_id` in
  `/suggestions`. Bus event `suggestions:added {pageId, drafts}`. See page-contract.md and
  assumptions C1–C7. Parallel work: see `docs/agent-coordination.md`.
