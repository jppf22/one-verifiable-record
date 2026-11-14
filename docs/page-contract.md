# Page-centric contract (v2), which replaces the entity/QID API

**Owner directive (2026-11-14):** "compare over pages, not entities or topics". The unit of
comparison is a **page**: one Wikipedia page in one language (`articles` row: lang,
page_id, title, revision_id). It is compared with its **counterpart pages**, the
same-subject pages in the other language editions (`page_links`). Only pages with at
least one counterpart are offered. The UI and API must not talk about "entities",
"topics" or QIDs. The `wikidata_qid` column is an import-time detail only and never
shown.

## Data model
- `articles`: one row per page (unchanged table, now called "page" in the API).
- `page_links(page_a, page_b, source)`: interlanguage links, **stored in both directions**.
  The counterparts of page X are `SELECT page_b FROM page_links WHERE page_a = X`.
- `claims.article_id` = the page the claim was read from. `claim_links` pair claims
  across **two pages** (unchanged).
- The **comparison set** for page X is X plus its counterparts. Alignment, verdicts,
  diff, suggestions, re-check and Q&A all run on that set, from X's point of view.

## Page object (used everywhere)
```jsonc
{ "id": 2867, "lang": "fi", "title": "Urho Kekkonen", "revision_id": 23495202,
  "url": "https://fi.wikipedia.org/w/index.php?oldid=23495202",
  "wiki_url": "https://fi.wikipedia.org/wiki/Urho_Kekkonen" }
```

## Endpoints (all under /api)
| Method | Path | Returns |
| --- | --- | --- |
| GET | `/pages?q=&lang=` | `[{page, counterparts:[page], conflicts:int}]`. Title search; only pages with ≥1 counterpart; `lang` optional |
| GET | `/pages/conflicted?limit=50` | `[{page, counterparts:[page], conflicts:int, missing_here:int, only_here:int, agree:int}]`, pages with ≥1 conflict first. Landing page list |
| GET | `/pages/by-title?lang=&title=` | `{page, counterparts}` or 404 |
| GET | `/pages/{id}` | `{page, counterparts}` |
| POST | `/pages/{id}/align` | runs vector alignment + verdicts for the comparison set. Returns counts |
| GET | `/pages/{id}/compare` | **Comparison payload** (below) |
| POST | `/pages/{id}/recheck` | body `{focus_link_ids?}` → RecheckResult (as v1, `qid` replaced by `page_id`; counts use the v2 status keys) |
| GET | `/pages/{id}/suggestions` | `{page_id, created, drafts:[{id, patch_type, wikitext, status, lang, title, page_id, claim_link_id, resolution_id}]}` |
| POST | `/suggestions/{id}/export` | as v1 (`recheck` is a RecheckResult for the draft's page) |
| GET | `/claims/{id}/history` | unchanged |
| POST/GET | `/reviews` | unchanged body. POST response `recheck` is for the page of claim_a |
| POST | `/links/{id}/resolution` | body `{author, correct_text, source_url?}`. Only for a link with verdict `contradicted` and source `human` (409 otherwise). `correct_text` is English, 10–2000 chars; `source_url` must be http(s) and not on wikipedia.org / wikidata.org / wikimedia.org (422). Stores a `resolutions` row and one `suggestions` draft per page of the comparison set (fi/sv machine-translated). Returns `{resolution, drafts:[{id, page_id, lang, title, patch_type, wikitext, status, claim_link_id, resolution_id, untranslated}], skipped:[{page_id, lang, reason}]}` |
| GET | `/links/{id}/resolution` | latest `{resolution, drafts}` of a link, or `{resolution: null, drafts: []}` |
| GET | `/qa?page=&q=&lang=` | as v1, restricted to the comparison set of `page` |
| GET | `/badge?lang=&title=` | `{page, counterparts, conflicts, missing_here, only_here, agree, counts}` |
| GET | `/extension/resolve?lang=&wiki_page_id=&title=&live_revision_id=` | Extension resolver. Returns `{tracked:false}` for unsupported/unimported/ineligible pages; otherwise page/counterparts, summary counts, snapshot freshness and analysis state. `wiki_page_id` is authoritative; `title` is used only when it is absent. |

Remove the QID endpoints (`/articles`, `/merge/{qid}`, `/align/{qid}`, `/recheck/{qid}`,
`/suggestions/{qid}`) or keep them as thin internal aliases. The frontend must not use them.

## Comparison payload: `GET /pages/{id}/compare`
```jsonc
{
  "page": {page},                      // the page being examined ("this page")
  "counterparts": [{page}],            // other-language pages, ordered fi, sv, en
  "summary": { "conflict": 2, "possible_conflict": 1, "missing_here": 5,
               "only_here": 3, "possible_match": 2, "agree": 9,
               "links_total": 30, "links_reviewed": 1,
               "links_hidden": 12 },  // additive: candidate pairs ruled out (different_fact) or unreviewed non-connecting links
  "rows": [{
    "key": "r-1042",
    "status": "conflict | possible_conflict | missing_here | only_here | possible_match | agree",
    "kind": "date", "predicate": "birth_date",       // from the claims, may be null
    "here":  [claim],                                 // this page's claims in the row (may be empty)
    "there": { "sv": [claim], "en": [claim] },        // counterpart claims by lang (missing lang = absent)
    "links": [link]                                   // as v1, incl. id, verdict, verdict_source, similarity,
  }]                                                  // evidence, rationale, computed_at, review,
}                                                     // consensus (if reviewed), resolution (if any)
```
`link.resolution` (only on reviewer-confirmed conflicts that have one) =
`{id, author, correct_text, source_url, created_at, drafts}` (`drafts` = count).
`claim` = `{id, page_id, lang, kind, subject, subject_entity, predicate,
predicate_entity, object_entity, value_text, value_normalized, value_canonical,
qualifiers, context_text, citation_urls, extraction_confidence, statement_text,
revision_id, url}`. `value_canonical` carries typed unit/calendar/precision data;
`qualifiers` records scope and time instead of flattening them into a string.

Row status, **relative to this page**:
- `conflict`: a link between a claim here and a counterpart claim is `contradicted`.
- `possible_conflict`: likely incompatible claims were retained but need evidence or review.
- `missing_here`: counterpart page(s) state it, this page has no matching claim.
- `only_here`: this page states it, no counterpart has a matching claim.
- `possible_match`: likely equivalent claims were retained but need evidence or review.
- `agree`: claim here linked `supported` to every counterpart that has the fact.

Rows are built from connected components over claim_links within the comparison set.
Only `supported` / `contradicted` links, and `possible_match` / `possible_conflict` links
that a grader actually produced, connect claims. Guesses written while the LLM was
unavailable (`verdict_source = 'deterministic-fallback'`) stay attached as receipts only.
Sort order: conflict, possible conflict, missing here, only here, possible match, agree.

## Frontend routes
- `#/` landing: search box + "Pages with conflicts" list (from `/pages/conflicted`).
- `#/page/{lang}/{title}` (title URL-encoded, spaces as `_`): comparison view.
  Counterpart tabs/columns show each counterpart page's title, language and revision link.
- Legacy `?qid=` / `#/merge/Q…` links → `#/`.
