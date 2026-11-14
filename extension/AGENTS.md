# Extension AGENTS.md - Browser Extension Instructions

**READ THIS BEFORE TOUCHING extension/ CODE**
This file overrides the root AGENTS.md for extension-specific instructions.

## Extension Overview

Manifest V3 Chromium browser extension. Companion to the web app.
Runs on fi/sv/en Wikipedia pages and provides:
- Badge with conflict/gap counts
- Inline claim highlighting (Custom Highlight API)
- Deep links to comparison view
- Findings tray for ambiguous/unanchored claims

### Manifest
```json
{
    "manifest_version": 3,
    "name": "One Verifiable Record",
    "version": "1.0.0",
    "description": "Wikipedia cross-language consistency checker",
    "permissions": ["storage", "scripting", "activeTab"],
    "host_permissions": [
        "https://*.wikipedia.org/*",
        "http://localhost:*/*"
    ],
    "action": {
        "default_popup": "popup.html",
        "default_icon": "icons/icon48.png"
    },
    "background": {
        "service_worker": "background.js"
    },
    "content_scripts": [{
        "matches": ["https://*.wikipedia.org/*", "http://localhost:*/*"],
        "js": ["content.js"],
        "run_at": "document_idle"
    }]
}
```

### Directory Structure
```
extension/
├── manifest.json      # Manifest V3
├── popup.html         # Popup configuration UI
├── popup.js           # Popup logic
├── popup.css          # Popup styles
├── background.js      # Service worker (badge updates)
├── content.js         # Content script (inline highlighting)
├── content.css        # Content script styles
├── icons/             # Extension icons
│   ├── icon16.png
│   ├── icon48.png
│   └── icon128.png
└── README.md          # User-facing documentation
```

## Core Functionality

### 1. Badge
- Shows conflict/gap counts for the current page
- Updates dynamically based on page content
- Links to comparison view

### 2. Inline Claim Highlighting
- Uses Custom Highlight API to highlight exact claim matches
- Shows markers for ambiguous/unanchored claims
- Markers are keyboard-reachable

### 3. Findings Tray
- Retains ambiguous/unanchored claims
- Marker popover links to comparison view
- Can be hidden/shown

### 4. Deep Links
- Links to `#/page/{lang}/{title}` in web app
- Handles spaces as `_` in titles

## API Endpoints Used

### Badge Endpoint
```
GET /api/badge?lang={lang}&title={title}
```

**Response**:
```json
{
    "page": { "id": 123, "lang": "fi", "title": "Urho_Kekkonen" },
    "counterparts": [{ "lang": "sv", "title": "Urho_Kekkonen" }, { "lang": "en", "title": "Urho Kekkonen" }],
    "conflicts": 2,
    "missing_here": 3,
    "only_here": 1,
    "agree": 8,
    "counts": {
        "conflict": 2,
        "gap": 4,
        "agree": 8
    }
}
```

### Resolve Endpoint
```
GET /api/extension/resolve?lang={lang}&wiki_page_id={wikiPageId}&title={title}&live_revision_id={revisionId}
```

**Response**:
```json
{
    "tracked": true,
    "page": { "id": 123, "lang": "fi", "title": "Urho_Kekkonen" },
    "counterparts": [...],
    "summary": { "conflict": 2, "missing_here": 3, "only_here": 1, "agree": 8 },
    "snapshot_freshness": "fresh",
    "analysis_state": "complete"
}
```

## Content Script (content.js)

### Page Detection
```javascript
// Check if we're on a supported Wikipedia page
function isSupportedPage() {
    const hostname = window.location.hostname;
    const lang = hostname.split('.')[0];
    return ['fi', 'sv', 'en'].includes(lang) && 
           hostname.endsWith('.wikipedia.org');
}
```

### Get Page Info
```javascript
function getPageInfo() {
    // Try to get wiki page ID from HTML
    const pageId = document.querySelector('meta[property="mw:PageProp/PageID"]')?.content;
    
    // Get title from HTML
    const title = document.querySelector('h1#firstHeading')?.textContent;
    
    // Get language from hostname
    const lang = window.location.hostname.split('.')[0];
    
    // Get revision ID from URL or API
    const revisionId = getRevisionId();
    
    return { lang, title, pageId, revisionId };
}
```

### Fetch Badge Data
```javascript
async function fetchBadgeData() {
    const { lang, title, pageId, revisionId } = getPageInfo();
    
    // Build URL with spaces as underscores
    const encodedTitle = title?.replace(/\s+/g, '_') || '';
    
    const baseUrl = await getApiBaseUrl();
    const url = `${baseUrl}/api/badge?lang=${lang}&title=${encodeURIComponent(encodedTitle)}`;
    
    try {
        const response = await fetch(url);
        if (!response.ok) {
            if (response.status === 404) {
                return { tracked: false };
            }
            throw new Error(`API error: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch badge data:', error);
        return { tracked: false, error: error.message };
    }
}
```

### Resolve Page
```javascript
async function resolvePage() {
    const { lang, title, pageId, revisionId } = getPageInfo();
    
    const baseUrl = await getApiBaseUrl();
    const url = `${baseUrl}/api/extension/resolve?lang=${lang}&wiki_page_id=${pageId || ''}&title=${encodeURIComponent(title || '')}&live_revision_id=${revisionId || ''}`;
    
    try {
        const response = await fetch(url);
        if (!response.ok) {
            if (response.status === 404) {
                return { tracked: false };
            }
            throw new Error(`API error: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Failed to resolve page:', error);
        return { tracked: false, error: error.message };
    }
}
```

### Render Badge
```javascript
function renderBadge(data) {
    // Remove existing badge
    removeExistingBadge();
    
    if (!data.tracked || !data.counterparts?.length) {
        return; // Don't show badge for untracked pages
    }
    
    // Create badge element
    const badge = document.createElement('div');
    badge.className = 'ovr-badge';
    badge.id = 'ovr-badge';
    
    // Pill with counts
    const pill = document.createElement('span');
    pill.className = 'ovr-pill';
    
    // Determine badge color based on status
    const totalIssues = (data.conflicts || 0) + (data.missing_here || 0) + (data.only_here || 0);
    if (data.conflicts > 0) {
        pill.classList.add('conflict');
    } else if (data.missing_here > 0 || data.only_here > 0) {
        pill.classList.add('gap');
    } else {
        pill.classList.add('agree');
    }
    
    // Build badge text
    const parts = [];
    if (data.conflicts > 0) {
        parts.push(`${data.conflicts} conflict${data.conflicts > 1 ? 's' : ''}`);
    }
    if (data.missing_here > 0) {
        parts.push(`${data.missing_here} missing`);
    }
    if (data.only_here > 0) {
        parts.push(`${data.only_here} only here`);
    }
    
    pill.textContent = parts.join(' · ') || 'verified';
    badge.appendChild(pill);
    
    // Add link text
    const linkText = document.createElement('span');
    linkText.className = 'ovr-link-text';
    if (data.counterparts) {
        const counterpartLangs = data.counterparts.map(c => c.lang).filter(l => l !== data.page?.lang);
        linkText.textContent = `Compare with ${counterpartLangs.join(' · ')}`;
    } else {
        linkText.textContent = 'Compare';
    }
    badge.appendChild(linkText);
    
    // Create deep link
    const webBaseUrl = await getWebBaseUrl();
    const pageTitle = data.page?.title?.replace(/\s+/g, '_') || '';
    const pageLang = data.page?.lang || '';
    const deepLink = `${webBaseUrl}/#/page/${pageLang}/${encodeURIComponent(pageTitle)}`;
    
    badge.onclick = () => {
        window.open(deepLink, '_blank');
    };
    
    // Insert badge after first heading
    const firstHeading = document.querySelector('h1#firstHeading');
    if (firstHeading) {
        firstHeading.after(badge);
    }
}
```

### Inline Highlighting
```javascript
async function highlightClaims(data) {
    if (!data.tracked || !data.analysis_state === 'complete') {
        return;
    }
    
    // Get claim positions from API (not yet implemented in v2)
    // For now, only highlight on exact match pages
    if (data.snapshot_freshness !== 'exact') {
        return; // Only highlight on exact match
    }
    
    // This would require server to return claim positions
    // which is not in the current page-centric contract
    
    // For now, skip inline highlighting (X1 in assumptions.md)
    // Only show badge
}
```

### Findings Tray
```javascript
// Not yet implemented in v2
// Would show ambiguous/unanchored claims
// Marker popover links to comparison view
```

## Background Script (background.js)

### Badge Management
```javascript
// Update badge text and tooltip
function updateBadge(data) {
    if (!data) {
        chrome.action.setBadgeText({ text: '' });
        chrome.action.setBadgeBackgroundColor({ color: '#666666' });
        return;
    }
    
    const totalIssues = (data.conflicts || 0) + (data.missing_here || 0) + (data.only_here || 0);
    
    if (totalIssues > 0) {
        chrome.action.setBadgeText({ text: totalIssues > 99 ? '99+' : String(totalIssues) });
        chrome.action.setBadgeBackgroundColor({ 
            color: data.conflicts > 0 ? '#ff4444' : '#ffaa44' 
        });
    } else {
        chrome.action.setBadgeText({ text: '✓' });
        chrome.action.setBadgeBackgroundColor({ color: '#44cc44' });
    }
    
    // Set tooltip
    const tooltip = buildTooltip(data);
    chrome.action.setTitle({ title: tooltip });
}

function buildTooltip(data) {
    if (!data.tracked) {
        return 'One Verifiable Record: Page not tracked';
    }
    
    const parts = [
        `One Verifiable Record: ${data.page?.title || 'Page'}`,
        `Language: ${data.page?.lang || 'unknown'}`
    ];
    
    if (data.conflicts > 0) {
        parts.push(`${data.conflicts} conflict${data.conflicts > 1 ? 's' : ''}`);
    }
    if (data.missing_here > 0) {
        parts.push(`${data.missing_here} missing on this page`);
    }
    if (data.only_here > 0) {
        parts.push(`${data.only_here} only on this page`);
    }
    if (data.agree > 0) {
        parts.push(`${data.agree} agree`);
    }
    
    return parts.join(' | ');
}
```

### Message Handling
```javascript
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch (message.type) {
        case 'GET_BADGE':
            // Handle badge request
            break;
        case 'UPDATE_BADGE':
            updateBadge(message.data);
            break;
        case 'OPEN_COMPARISON':
            // Open comparison view
            break;
    }
});
```

## Popup (popup.html, popup.js, popup.css)

### Configuration Options
1. **API Base URL** - Where the FastAPI server runs
2. **Web App URL** - Where the web app runs
3. **Badge Enabled** - Toggle extension on/off
4. **Hide/Show Inline Findings** - Toggle inline highlighting

### Storage
- Use `chrome.storage.sync` for settings
- Settings persist across browsers (when signed in)

### Implementation
```javascript
// popup.js
async function loadSettings() {
    const result = await chrome.storage.sync.get([
        'apiBaseUrl',
        'webBaseUrl',
        'badgeEnabled',
        'inlineFindingsEnabled'
    ]);
    
    return {
        apiBaseUrl: result.apiBaseUrl || 'http://localhost:8000',
        webBaseUrl: result.webBaseUrl || 'http://localhost:8080',
        badgeEnabled: result.badgeEnabled !== false,
        inlineFindingsEnabled: result.inlineFindingsEnabled || false
    };
}

async function saveSettings(settings) {
    await chrome.storage.sync.set(settings);
    
    // Update badge
    chrome.runtime.sendMessage({ type: 'UPDATE_SETTINGS', settings });
    
    // Refresh current tab
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { type: 'REFRESH_BADGE' });
    }
}

// Handle form submission
document.getElementById('settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const settings = {
        apiBaseUrl: document.getElementById('api-url').value,
        webBaseUrl: document.getElementById('web-url').value,
        badgeEnabled: document.getElementById('badge-enabled').checked,
        inlineFindingsEnabled: document.getElementById('inline-findings').checked
    };
    
    await saveSettings(settings);
    window.close();
});
```

### Popup HTML
```html
<!DOCTYPE html>
<html>
<head>
    <title>One Verifiable Record</title>
    <link rel="stylesheet" href="popup.css">
</head>
<body>
    <div class="popup">
        <header>
            <h1>One Verifiable Record</h1>
            <p class="description">Wikipedia cross-language consistency checker</p>
        </header>
        
        <form id="settings-form">
            <div class="setting">
                <label for="api-url">API Base URL</label>
                <input type="url" id="api-url" placeholder="http://localhost:8000">
            </div>
            
            <div class="setting">
                <label for="web-url">Web App URL</label>
                <input type="url" id="web-url" placeholder="http://localhost:8080">
            </div>
            
            <div class="setting">
                <label>
                    <input type="checkbox" id="badge-enabled" checked>
                    Show badge on Wikipedia pages
                </label>
            </div>
            
            <div class="setting">
                <label>
                    <input type="checkbox" id="inline-findings">
                    Show inline findings (experimental)
                </label>
            </div>
            
            <button type="submit">Save</button>
        </form>
        
        <footer>
            <a href="http://localhost:8080" target="_blank">Open Web App</a>
            <a href="https://github.com/..." target="_blank">GitHub</a>
        </footer>
    </div>
    
    <script src="popup.js" type="module"></script>
</body>
</html>
```

## Configuration Management

### Getting API Base URL
```javascript
async function getApiBaseUrl() {
    const settings = await loadSettings();
    return settings.apiBaseUrl;
}

async function getWebBaseUrl() {
    const settings = await loadSettings();
    return settings.webBaseUrl;
}
```

### Handling HTTPS Permissions
```javascript
// When user saves a new HTTPS host
async function requestHostPermission(host) {
    try {
        await chrome.permissions.request({ 
            origins: [`${host}/*`] 
        });
        return true;
    } catch (error) {
        console.error('Permission request failed:', error);
        return false;
    }
}
```

## Installation & Development

### Development Mode
1. Open `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select `extension/` directory
4. Extension will auto-reload on file changes

### Debugging
- **Popup**: Right-click extension icon → Inspect
- **Content Script**: Inspect Wikipedia page → Console
- **Background**: chrome://extensions → Inspect views: service worker

### Testing
1. Install extension in development mode
2. Navigate to a Wikipedia page (fi/sv/en)
3. Check badge appears if page is tracked
4. Click badge to open comparison view
5. Verify deep link works

## Fallback Behavior

### Untracked Pages
- No badge shown
- No inline highlighting
- No findings tray

### No Counterparts
- Badge shows "Not available" or similar
- Link disabled or hidden

### API Unavailable
- Badge shows error state
- Retry after delay
- Log to console

### Old API Version
- Check for `gaps` field in badge response (X1 in assumptions.md)
- If missing, omit gaps segment
- Conflicts fall back to `counts.contradicted`
- "Verified" stays as `counts.supported`
- Deep link hidden if no `qid` in response

## Page-Centric v2 Changes

### What Changed
- No more QID-based operations
- `wikidata_qid` is import-time only, never shown
- Badge deep links to `#/page/{lang}/{title}` (not `#/merge/{qid}`)
- Resolve endpoint uses page info, not QID

### Migration
- Old deep links (`#/merge/Q...`) redirect to `#/`
- Extension needs updating to use new route format
- Content.js: update deep link generation

## Important Assumptions (See docs/assumptions.md)

### X1: Badge Fallback
If the server's badge response has no `gaps` field (older server):
- Badge omits the gaps segment
- Conflicts fall back to `counts.contradicted`
- "Verified" stays as `counts.supported`
- Deep link hidden if response has no `qid`

### V-W10: Extension Badge
- No badge when `/badge` returns no counterparts
- Pill = conflicts / missing here / only here / agree
- Red if conflicts, amber if only missing-here, else green
- Link text: "Compare with sv · en"
- Link URL: `${webBase}/#/page/{lang}/{title}` (spaces as `_`)

## Security Considerations

### Content Security Policy
- Extension has its own CSP in manifest
- No inline scripts (use separate files)
- No eval()

### Data Handling
- Don't store user data unnecessarily
- Clear sensitive data from memory
- Use HTTPS for all API calls in production

### Permissions
- Request minimal permissions
- Explain why each permission is needed
- Handle permission denials gracefully

## Performance

### Content Script
- Run at `document_idle` (after page load)
- Debounce API calls
- Cache responses when appropriate
- Clean up event listeners on page change

### Badge Updates
- Only fetch when tab is active
- Cache badge data for short period
- Use chrome.storage for cross-tab synchronization

## Before You Start

1. Read root AGENTS.md
2. Read docs/assumptions.md (especially extension assumptions X1, V-W10)
3. Read docs/page-contract.md (API contract for badge/resolve)
4. Understand Manifest V3 differences from Manifest V2

## Common Tasks

### Adding a New Feature
1. Update manifest.json if needed (permissions, etc.)
2. Add code to appropriate file (content.js, background.js, popup.js)
3. Add styles if needed
4. Test in browser
5. Reload extension to pick up changes

### Modifying Badge Behavior
1. Update content.js badge rendering
2. Update background.js badge management
3. Test on various Wikipedia pages
4. Check edge cases (untracked, no counterparts, API errors)

### Adding Inline Highlighting
1. Implement claim position tracking in server
2. Add highlighting code to content.js
3. Use Custom Highlight API
4. Test on exact match pages only

## Gotchas

1. **Manifest V3**: No background page, use service worker
2. **Content Scripts**: Don't have access to chrome.* APIs directly
3. **Message Passing**: Use chrome.runtime.sendMessage / chrome.tabs.sendMessage
4. **CORS**: Extension can make CORS requests to API
5. **HTTPS**: Need explicit permission for HTTPS hosts
6. **Storage**: chrome.storage.sync has limits (~100KB)

## Browser Compatibility

- Chrome (primary target)
- Edge (Chromium-based, should work)
- Firefox (with webExtensions polyfill)
- Safari (limited support)

## Final Notes

The extension is a **satellite**, not the main product:
- It enhances the Wikipedia reading experience
- It drives traffic to the web app
- It provides quick access to comparison view
- It does NOT edit Wikipedia
- It does NOT imply unmarked text was checked

The main functionality is in the web app. The extension's job is to:
1. Show badge with status
2. Link to comparison view
3. Optionally highlight claims (when exact match)

Focus on getting the badge working reliably first.
