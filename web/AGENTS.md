# Web AGENTS.md - Frontend Instructions

**READ THIS BEFORE TOUCHING web/ CODE**
This file overrides the root AGENTS.md for frontend-specific instructions.

## Frontend Overview

Vanilla HTML/CSS/JS web application served by nginx. No build step, no framework.
Provides the merge view, review interface, SVT timeline, patch export, and Q&A.

### Entry Point
- `web/index.html` - Main HTML file
- Run with: `cd web && python -m http.server 8080` (dev)
- Docker: `docker compose up --build` (full stack with nginx)

### Module Structure
```
web/
├── index.html          # Main entry point, all page regions
├── Dockerfile          # nginx configuration
├── nginx.conf          # nginx server config
├── css/
│   ├── base.css        # Global styles, variables, reset
│   ├── merge.css       # Merge view styles
│   └── panels.css      # SVT timeline, suggestions, Q&A styles
└── js/
    ├── api.js          # API client (fetch wrappers for all endpoints)
    ├── bus.js          # Event bus (pub/sub pattern)
    ├── util.js         # Utilities ($, $$, el, esc, toast, getReviewer)
    ├── main.js         # App initialization, routing
    ├── router.js       # Route handling (#/, #/page/{lang}/{title})
    ├── search.js       # Search functionality
    ├── merge.js        # Merge view (core comparison display)
    ├── review.js       # Review UI (Agree/Disagree/Fix)
    ├── consensus.js    # Review consensus policy R1 (display only, docs/review-consensus.md)
    ├── keyboard.js     # Keyboard shortcuts (j/k, a/d/f, h, ?)
    ├── history.js      # SVT timeline ("biography of a claim")
    ├── suggestions.js  # Patch export (Copy, Download .wiki)
    └── qa.js           # Q&A panel
```

## Core Principles

1. **Vanilla JS**: ES6+ only, no frameworks, no transpilation
2. **Native Modules**: `<script type="module">` for all JS
3. **No Build**: Files served as-is by nginx
4. **Progressive Enhancement**: Works without JS (basic), enhanced with JS
5. **Accessibility**: Keyboard navigation, ARIA labels, semantic HTML

## Routing

### Routes
- `#/` - Landing page
  - Search box
  - "Pages with conflicts" list
- `#/page/{lang}/{title}` - Comparison view
  - `lang`: fi, sv, or en
  - `title`: URL-encoded page title (spaces as `_`)
  - Example: `#/page/fi/Urho_Kekkonen`

### Legacy Routes
- `?qid=Q123` - Redirects to `#/`
- `#/merge/Q123` - Redirects to `#/`
- Extension deep links: `/#/page/{lang}/{title}`

### Router Implementation
```javascript
// router.js
const routes = {
    '/': showLanding,
    '/page/:lang/:title': showPage
};

function parseRoute(hash) {
    // Parse #/page/fi/Urho_Kekkonen
    // Returns { route: 'page', params: { lang: 'fi', title: 'Urho Kekkonen' } }
}

function navigate(path, replace = false) {
    // Update window.location.hash
    // Emit route change events
}
```

## API Client (api.js)

### Fetch Wrapper
```javascript
// api.js
export async function api(endpoint, options = {}) {
    const base = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api';
    const url = `${base}${endpoint}`;
    const response = await fetch(url, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        }
    });
    if (!response.ok) throw new Error(`API error: ${response.status}`);
    return response.json();
}

// Specific endpoints
export async function getPagesConflicted(limit = 50) {
    return api('/pages/conflicted?limit=' + limit);
}

export async function getPageCompare(pageId) {
    return api(`/pages/${pageId}/compare`);
}

export async function postPageAlign(pageId) {
    return api(`/pages/${pageId}/align`, { method: 'POST' });
}

export async function postPageRecheck(pageId, focusLinkIds = []) {
    return api(`/pages/${pageId}/recheck`, {
        method: 'POST',
        body: JSON.stringify({ focus_link_ids: focusLinkIds })
    });
}

export async function postReview(claimLinkId, reviewer, decision, note = '') {
    return api('/reviews', {
        method: 'POST',
        body: JSON.stringify({ claim_link_id: claimLinkId, reviewer, decision, note })
    });
}
```

## Event Bus (bus.js)

### Usage
```javascript
// Subscribe
import { on, emit } from './js/bus.js';

on('page:loaded', ({ pageId, data }) => {
    // Handle page load
});

// Emit
emit('page:reload', { pageId: 123 });

// One-time subscription
on('recheck:done', handleRecheck, { once: true });
```

### Important Events
| Event | Payload | Emitted By | Description |
|-------|---------|------------|-------------|
| `page:loaded` | `{pageId, data}` | merge.js | Article loaded and displayed |
| `page:reload` | `{pageId}` | merge.js, review.js | Request to reload page |
| `recheck:done` | `{pageId, result, reason, linkIds}` | review.js, suggestions.js | Re-check completed |
| `history:open` | `{claimId}` | merge.js, keyboard.js | Open history dialog |
| `merge:reload` | `{qid}` | (deprecated, use page:reload) | |

## Merge View (merge.js)

### Comparison Payload Structure
```javascript
{
    page: { id, lang, title, revision_id, url, wiki_url },
    counterparts: [{ id, lang, title, revision_id, url, wiki_url }],
    summary: { conflict, possible_conflict, missing_here, only_here, possible_match, agree, links_total, links_reviewed },
    rows: [{
        key: 'r-1042',
        status: 'conflict',
        kind: 'date',
        predicate: 'birth_date',
        here: [claim],           // This page's claims
        there: {                 // Counterpart claims by lang
            sv: [claim],
            en: [claim]
        },
        links: [link]            // Claim links in this row
    }]
}
```

### Claim Structure
```javascript
{
    id: 123,
    page_id: 456,
    lang: 'fi',
    kind: 'date',
    subject: 'Urho Kekkonen',
    subject_entity: 'Q1757',
    predicate: 'birth_date',
    predicate_entity: 'P569',
    object_entity: null,
    value_text: '1900-09-03',
    value_normalized: '1900-09-03',
    value_canonical: { year: 1900, month: 9, day: 3 },
    qualifiers: {},
    context_text: '...',
    citation_urls: ['https://fi.wikipedia.org/...'],
    extraction_confidence: 0.95,
    statement_text: 'Urho Kekkonen syntyi 3. syyskuuta 1900',
    revision_id: 12345678,
    url: 'https://fi.wikipedia.org/w/index.php?oldid=12345678'
}
```

### Link Structure
```javascript
{
    id: 789,
    claim_a: 123,
    claim_b: 456,
    verdict: 'contradicted',
    verdict_source: 'deterministic',
    similarity: 0.92,
    evidence: '...',
    rationale: '...',
    computed_at: '2026-11-14T10:00:00',
    review: {
        id: 101,
        decision: 'agree',
        reviewer: 'test-user',
        note: '',
        created_at: '2026-11-14T11:00:00'
    }
}
```

### Rendering Logic

#### Row Status Colors
```css
/* merge.css */
.row.conflict { border-left: 4px solid var(--color-conflict); }
.row.possible_conflict { border-left: 4px solid var(--color-possible-conflict); }
.row.missing_here { border-left: 4px solid var(--color-missing); }
.row.only_here { border-left: 4px solid var(--color-only-here); }
.row.possible_match { border-left: 4px solid var(--color-possible-match); }
.row.agree { border-left: 4px solid var(--color-agree); }
```

#### Language Columns
```html
<div class="merge-view">
    <div class="language-column this-page">
        <header>This Page (fi)</header>
        <div class="claims">...</div>
    </div>
    <div class="language-column counterpart">
        <header>Swedish (sv)</header>
        <div class="claims">...</div>
    </div>
    <div class="language-column counterpart">
        <header>English (en)</header>
        <div class="claims">...</div>
    </div>
</div>
```

#### Claim Cells
```html
<div class="claim-cell">
    <div class="claim-predicate">birth_date:</div>
    <div class="claim-value">1900-09-03</div>
    <div class="claim-text">Urho Kekkonen syntyi 3. syyskuuta 1900</div>
    <div class="claim-receipts">
        <a href="..." target="_blank">View on Wikipedia</a>
        <span class="revision-id">Rev: 12345678</span>
    </div>
</div>
```

### Gap Cells
```html
<div class="claim-cell gap missing-here">
    <div class="gap-label">Not on this page</div>
    <div class="gap-source">Stated on sv, en</div>
</div>

<div class="claim-cell gap only-here">
    <div class="gap-label">Only on this page</div>
    <div class="gap-source">Missing on sv, en</div>
</div>
```

### Summary Bar
```html
<div class="summary-bar">
    <div class="consistency-meter">
        <div class="segment conflict" style="width: 20%" title="2 conflicts"></div>
        <div class="segment missing-here" style="width: 15%" title="3 missing here"></div>
        <div class="segment only-here" style="width: 10%" title="2 only here"></div>
        <div class="segment possible-conflict" style="width: 5%" title="1 possible conflict"></div>
        <div class="segment possible-match" style="width: 10%" title="2 possible matches"></div>
        <div class="segment agree" style="width: 40%" title="8 agree"></div>
    </div>
    <div class="counts">
        <span class="conflict">2 conflicts</span>
        <span class="missing-here">3 missing</span>
        <span class="only-here">2 only here</span>
        <span class="agree">8 agree</span>
    </div>
    <div class="progress">
        7 / 23 conflicts reviewed
    </div>
</div>
```

## Review UI (review.js)

### Review Actions
```html
<div class="review-actions">
    <button class="review-agree" title="Agree with machine verdict">✓</button>
    <button class="review-disagree" title="Disagree with machine verdict">✗</button>
    <button class="review-fix" title="Fix/override">✎</button>
</div>

<div class="review-note">
    <textarea placeholder="Add a note..." maxlength="500"></textarea>
    <button class="submit-review">Submit</button>
</div>
```

### Review Flow
1. User clicks review action on a link
2. Open review dialog with:
   - Link details (claims, verdict, evidence)
   - Review options (Agree/Disagree/Fix)
   - Note field
3. On submit:
   - POST to `/api/reviews`
   - Wait for response with `recheck` result
   - Emit `recheck:done` event
   - Show human-reviewed stamp

### Human Reviewed Stamp
```html
<div class="verdict-badge human-reviewed">
    <span class="icon">✓</span>
    <span class="reviewer">test-user</span>
    <span class="decision">agreed</span>
    <span class="time">2h ago</span>
</div>
```

## Keyboard Shortcuts (keyboard.js)

### Navigation
- `j` / `k`: Move to next/previous cluster
- `g` + `j` / `g` + `k`: Move to next/previous conflict
- `g` + `a`: Move to next agree
- `g` + `m`: Move to next gap

### Review
- `a`: Agree with machine verdict (on focused cluster)
- `d`: Disagree with machine verdict
- `f`: Flag for fix/override
- `h`: Open history for focused claim

### Other
- `?`: Open help dialog
- `Esc`: Close dialogs, clear filters
- `/`: Focus search input
- `r`: Re-check current page

## History Timeline (history.js)

### "Biography of a Claim"
Displays the SVT history of a claim with:
- Version timeline
- What changed in each version
- Who/what caused the change (system, LLM, human)
- Links to related verdicts

### Timeline Structure
```javascript
{
    claim: {...},
    versions: [{
        id: 123,
        row_start: '2026-11-14T10:00:00',
        row_end: '2026-11-14T11:00:00',
        changes: {
            value_text: { old: '1900-09-03', new: '1900-09-02' },
            statement_text: { old: '...', new: '...' }
        },
        source: 'system',  // system, llm, human, import
        related_links: [linkId1, linkId2],
        related_reviews: [reviewId1]
    }],
    current: {...}
}
```

### Rendering
```html
<div class="history-dialog">
    <header>Biography of Claim #123</header>
    
    <div class="timeline">
        <div class="timeline-item current">
            <div class="timestamp">Now</div>
            <div class="version">Current Version</div>
            <div class="details">
                <div class="field">value_text: <span class="value">1900-09-02</span></div>
                <div class="field">statement_text: <span class="value">...</span></div>
            </div>
        </div>
        
        <div class="timeline-item">
            <div class="timestamp">2026-11-14 11:00</div>
            <div class="version">Version 2</div>
            <div class="source">Human Review by test-user</div>
            <div class="changes">
                <div class="change">value_text: <span class="old">1900-09-03</span> → <span class="new">1900-09-02</span></div>
            </div>
        </div>
        
        <div class="timeline-item">
            <div class="timestamp">2026-11-14 10:00</div>
            <div class="version">Version 1</div>
            <div class="source">Import from Wikipedia</div>
            <div class="changes">Initial version</div>
        </div>
    </div>
    
    <div class="sql-panel">
        <details>
            <summary>SQL Query</summary>
            <pre>SELECT * FROM claims FOR SYSTEM_TIME ALL WHERE id = 123...</pre>
        </details>
    </div>
</div>
```

## Suggestions & Patch Export (suggestions.js)

### Draft Types
1. **Correction**: Fix a contradicted claim
2. **Addition**: Add a missing claim
3. **Notice**: Talk page notice

### Draft Structure
```javascript
{
    id: 456,
    page_id: 123,
    patch_type: 'correction',
    wikitext: '== Birth date ==\nUrho Kekkonen was born on [[September 2, 1900]].\n<ref>...</ref>',
    status: 'draft',  // draft, exported, applied
    lang: 'fi',
    title: 'Urho Kekkonen',
    claim_link_id: 789,  // nullable
    created_at: '2026-11-14T10:00:00'
}
```

### UI Components
```html
<div class="suggestions-panel">
    <header>Patch Drafts</header>
    
    <div class="drafts-list">
        <div class="draft">
            <div class="draft-header">
                <span class="type correction">Correction</span>
                <span class="page">fi: Urho Kekkonen</span>
                <span class="status draft">Draft</span>
            </div>
            <div class="draft-preview">
                <pre>== Birth date ==
Urho Kekkonen...</pre>
            </div>
            <div class="draft-actions">
                <button class="copy" title="Copy to clipboard">Copy</button>
                <button class="download" title="Download .wiki file">Download</button>
                <button class="export" title="Mark as exported">Mark Exported</button>
                <a href="..." class="edit-page" target="_blank">Open Edit Page</a>
            </div>
        </div>
    </div>
</div>
```

### Export Flow
1. User clicks "Mark Exported"
2. POST to `/api/suggestions/{id}/export`
3. Server marks status = 'exported' (SVT update)
4. Server re-checks the page (focus on claim_link_id if present)
5. On success:
   - Update draft status to 'exported'
   - Show confirmation toast
   - If recheck result available, emit `recheck:done`

### Download Flow
```javascript
function downloadDraft(draft) {
    const filename = `${draft.lang}-${draft.title}-${draft.patch_type}-${draft.id}.wiki`;
    const blob = new Blob([draft.wikitext], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}
```

### Open Edit Page
```javascript
function openEditPage(draft) {
    let pageName = draft.title.replace(/_/g, ' ');
    if (draft.patch_type === 'notice') {
        pageName = `Talk:${pageName}`;
        window.open(`https://${draft.lang}.wikipedia.org/w/index.php?title=${encodeURIComponent(pageName)}&action=edit&section=new`, '_blank');
    } else {
        window.open(`https://${draft.lang}.wikipedia.org/w/index.php?title=${encodeURIComponent(pageName)}&action=edit`, '_blank');
    }
}
```

## Q&A Panel (qa.js)

### Query Flow
1. User enters question
2. POST to `/api/qa?page={pageId}&q={question}&lang={lang}`
3. Display loading state
4. On response:
   - Display answer
   - Display citations with revision links
   - Cache in sessionStorage

### UI
```html
<div class="qa-panel">
    <div class="qa-input">
        <input type="text" placeholder="Ask about this page..." />
        <button class="ask">Ask</button>
    </div>
    
    <div class="qa-results">
        <div class="qa-result">
            <div class="question">When was Urho Kekkonen born?</div>
            <div class="answer">Urho Kekkonen was born on September 3, 1900.</div>
            <div class="citations">
                <div class="citation">
                    <a href="...">fi.wikipedia.org (Rev: 12345678)</a>
                    <span class="confidence">High confidence</span>
                </div>
            </div>
        </div>
    </div>
    
    <div class="suggested-questions">
        <button>What is his birth date?</button>
        <button>When did he serve as president?</button>
        <button>What are his major achievements?</button>
    </div>
</div>
```

### Session Storage
```javascript
// Key format: ovr.qa.{pageId}
const cacheKey = `ovr.qa.${pageId}`;
const history = JSON.parse(sessionStorage.getItem(cacheKey) || '[]');
// Store last 6 questions per page
sessionStorage.setItem(cacheKey, JSON.stringify(history.slice(-6)));
```

## Search (search.js)

### Search Box
- Debounced input (300ms)
- Searches pages by title
- Groups results by Wikidata QID (one row per entity)
- Shows counterpart chips
- Shows conflict count

### UI
```html
<div class="search-box">
    <input type="text" id="search-input" placeholder="Search Wikipedia pages..." />
    <div class="search-results" id="results">
        <div class="result">
            <div class="title">Urho Kekkonen</div>
            <div class="languages">
                <span class="lang-chip fi">fi</span>
                <span class="lang-chip sv">sv</span>
                <span class="lang-chip en">en</span>
            </div>
            <div class="conflict-count">2 conflicts</div>
        </div>
    </div>
</div>
```

### Direct Navigation
- Typing a bare QID (e.g., `Q1757`) and pressing Enter: redirects to `#/` (QID routes removed)
- Typing a page title and pressing Enter: searches for that page

## Filters

### Status Filters
```html
<div class="filters">
    <button class="filter active" data-status="">All</button>
    <button class="filter" data-status="conflict">Conflicts</button>
    <button class="filter" data-status="possible_conflict">Possible Conflicts</button>
    <button class="filter" data-status="missing_here">Missing Here</button>
    <button class="filter" data-status="only_here">Only Here</button>
    <button class="filter" data-status="possible_match">Possible Matches</button>
    <button class="filter" data-status="agree">Agreed</button>
</div>
```

### Text Filter
```html
<input type="text" class="text-filter" placeholder="Filter claims..." />
```
- Filters by: predicate, value_text, statement_text
- Case-insensitive
- Debounced (200ms)

### URL State
- Only `status` filter goes into URL: `#/page/fi/Urho_Kekkonen?status=conflict`
- Other filters are per-session (not in URL)
- Use `history.replaceState` to avoid adding history entries

## Recheck Banner

### Display Conditions
- Show after any recheck completes
- Auto-dismiss after 5 seconds (or user click)
- Can be manually dismissed

### Content
```html
<div class="recheck-banner">
    <span class="icon">✓</span>
    <span class="message">Re-check complete: 2 conflicts resurfaced, 1 resolved</span>
    <button class="dismiss">×</button>
</div>
```

### Outcome Per Link
- "Conflict resurfaced" - was contradicted before and still contradicted
- "Resolved on re-check" - was contradicted, now something else
- "New link found" - link was created by this recheck
- "Verdict changed" - verdict changed
- "Unchanged" - verdict stayed the same

### A1 Note
Show note about database revisions when something resurfaced:
```
"still present in the source revisions; apply the patch on Wikipedia, then re-import"
```

## Landing Page

### "Pages with Conflicts" List
- Fetches from `/api/pages/conflicted?limit=50`
- Sorted by: conflicts desc, then links_total desc, then title
- Reloads every time landing is shown
- Shows all pages, including those with 0 conflicts (grey edge)

### UI
```html
<div class="landing">
    <div class="search-container">
        <!-- Search box -->
    </div>
    
    <div class="conflicted-pages">
        <header>Pages with Conflicts</header>
        <div class="page-list">
            <div class="page-card">
                <div class="title">Urho Kekkonen</div>
                <div class="languages">
                    <span class="lang">fi</span>
                    <span class="lang">sv</span>
                    <span class="lang">en</span>
                </div>
                <div class="stats">
                    <span class="conflict">2 conflicts</span>
                    <span class="total">15 links</span>
                </div>
            </div>
        </div>
    </div>
</div>
```

## CSS Architecture

### Variables (base.css)
```css
:root {
    /* Colors */
    --color-conflict: #ff4444;
    --color-possible-conflict: #ff6666;
    --color-missing: #ffaa44;
    --color-only-here: #4488ff;
    --color-possible-match: #88aaff;
    --color-agree: #44cc44;
    
    /* Backgrounds */
    --bg-primary: #1a1a1a;
    --bg-secondary: #2a2a2a;
    --bg-tertiary: #3a3a3a;
    
    /* Text */
    --text-primary: #ffffff;
    --text-secondary: #aaaaaa;
    --text-muted: #666666;
    
    /* Borders */
    --border-color: #444444;
    
    /* Spacing */
    --spacing-xs: 0.25rem;
    --spacing-sm: 0.5rem;
    --spacing-md: 1rem;
    --spacing-lg: 1.5rem;
    --spacing-xl: 2rem;
    
    /* Font */
    --font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --font-mono: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
```

### Utility Classes
```css
.hidden { display: none !important; }
.visually-hidden { /* Accessibility */ }
.flex { display: flex; }
.grid { display: grid; }
.gap-xs { gap: var(--spacing-xs); }
.gap-sm { gap: var(--spacing-sm); }
/* ... */
```

## Utilities (util.js)

### DOM Helpers
```javascript
// Select single element
export function $(selector, parent = document) {
    return parent.querySelector(selector);
}

// Select all elements
export function $$(selector, parent = document) {
    return Array.from(parent.querySelectorAll(selector));
}

// Create element
export function el(tag, props = {}, ...children) {
    const element = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
        if (key === 'className') element.className = value;
        else if (key === 'dataset') Object.assign(element.dataset, value);
        else if (key === 'text') element.textContent = value;
        else element.setAttribute(key, value);
    });
    children.forEach(child => {
        if (typeof child === 'string') {
            element.appendChild(document.createTextNode(child));
        } else if (child instanceof Node) {
            element.appendChild(child);
        }
    });
    return element;
}
```

### Escape HTML
export function esc(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

### Toast Notifications
```javascript
export function toast(message, type = 'info', duration = 3000) {
    const container = $('#toasts');
    const toastEl = el('div', { className: `toast ${type}` }, message);
    container.appendChild(toastEl);
    setTimeout(() => toastEl.remove(), duration);
}
```

### Get Reviewer
```javascript
export function getReviewer() {
    let reviewer = localStorage.getItem('ovr.reviewer');
    if (!reviewer) {
        reviewer = prompt('Enter your display name for reviews:');
        if (reviewer) localStorage.setItem('ovr.reviewer', reviewer);
    }
    return reviewer;
}
```

### Wikipedia URL
```javascript
export function wikiUrl(lang, revisionId, title) {
    if (revisionId) {
        return `https://${lang}.wikipedia.org/w/index.php?oldid=${revisionId}`;
    }
    return `https://${lang}.wikipedia.org/wiki/${encodeURIComponent(title.replace(/_/g, ' '))}`;
}
```

## Before You Start

1. Read root AGENTS.md
2. Read docs/assumptions.md (especially web assumptions W1-W11, V-W1-V-W11)
3. Read docs/frontend-contract.md (API contract, bus events)
4. Read docs/page-contract.md (page-centric model)
5. Understand the module structure above

## Common Tasks

### Adding a New Feature
1. Create new JS file in `web/js/`
2. Import in `web/index.html`
3. Initialize in `web/js/main.js`
4. Add CSS in appropriate file
5. Test in browser

### Modifying the Merge View
1. Update `merge.js` for data handling
2. Update `merge.css` for styles
3. Check `index.html` for required elements
4. Test with various page types (conflict, gap, agree)

### Adding Keyboard Shortcut
1. Add to `keyboard.js`
2. Document in help dialog
3. Test doesn't conflict with existing shortcuts

## Gotchas

1. **No Build Step**: Changes are live immediately - refresh browser
2. **ES Modules**: Use `type="module"` and proper paths
3. **CORS**: API and web must be on same origin or CORS configured
4. **Session Storage**: Use `ovr.` prefix for all keys
5. **URL Encoding**: Titles use `_` for spaces, decode for display
6. **LocalStorage**: Reviewer name stored in `ovr.reviewer`

## Browser Compatibility

Target: Modern browsers (Chrome, Firefox, Safari, Edge)
- ES6+ support required
- No IE11 support
- Use native ES modules

## Testing

### Manual Testing Checklist
- [ ] Search works and finds pages
- [ ] Can open comparison view for a page
- [ ] Merge view shows claims in language columns
- [ ] Gaps show as dashed cells
- [ ] Can filter by status
- [ ] Can review a verdict (Agree/Disagree/Fix)
- [ ] Recheck works after review
- [ ] Can open history timeline
- [ ] Can generate and export patches
- [ ] Q&A works
- [ ] Keyboard shortcuts work
- [ ] Extension badge works

### Common Issues
- **404 on pages**: Page not imported or no counterparts
- **No data**: Database empty, run import and embedding jobs
- **CORS errors**: API and web on different origins
- **JS errors**: Check browser console
- **Styling issues**: Check browser dev tools

## Performance Considerations

- Debounce search input (300ms)
- Cache API responses when appropriate
- Lazy load panels (history, suggestions, Q&A)
- Virtualize long lists if needed (not yet implemented)

## Final Notes

This is a hackathon project - focus on:
1. Core story (merge view with receipts)
2. Working demo
3. MariaDB features visible
4. Don't over-engineer

The frontend must demonstrate:
- Cross-language comparison
- Verdicts with receipts
- Human review
- SVT timeline
- Patch export

Everything else is nice-to-have.
