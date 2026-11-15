# Codebase Simplifications - 5 Toggleable Modes

This document describes five toggleable simplification modes for the One Verifiable Record codebase. Each mode reduces complexity by disabling or limiting specific features, making the system easier to understand, debug, test, or deploy in constrained environments.

## Overview

The simplification modes are implemented as feature flags controlled by the `SIMPLIFICATION_MODE` environment variable. They can also be toggled via the API at runtime (though some changes require a server restart to take full effect).

| Mode | Environment Variable | Effect |
|------|---------------------|--------|
| **Full Mode** | `SIMPLIFICATION_MODE=none` | All features enabled (default) |
| **Minimal Mode** | `SIMPLIFICATION_MODE=minimal` | Core comparison only, no LLM/reviews/patches |
| **Read-Only Mode** | `SIMPLIFICATION_MODE=read_only` | Block all write operations |
| **Single-Language Mode** | `SIMPLIFICATION_MODE=single_language` | Focus on one language only |
| **Deterministic Mode** | `SIMPLIFICATION_MODE=deterministic` | Skip LLM, use deterministic verdicts only |
| **No-Extension Mode** | `SIMPLIFICATION_MODE=no_extension` | Remove browser extension functionality |

## Configuration

### Environment Variables

```bash
# Set via environment (requires server restart)
export SIMPLIFICATION_MODE=minimal
export SINGLE_LANGUAGE=en  # Only used with single_language mode

# Or set in .env file
SIMPLIFICATION_MODE=read_only
SINGLE_LANGUAGE=fi
```

### API Configuration

```bash
# Get current simplification configuration
GET /api/simplification

# Get all available options
GET /api/simplification/options

# Set simplification mode (POST)
POST /api/simplification
Content-Type: application/json

{
  "mode": "read_only",
  "single_language": "en"  # Optional, only for single_language mode
}
```

### UI Toggle

A simplification mode toggle is available in the web interface (gear icon in the header). This provides a visual way to select and switch between modes. Note that some changes may require a server restart to take full effect.

## Mode Details

### 1. Minimal Mode (`minimal`)

**Purpose:** Focus on the core comparison functionality by removing all non-essential features.

**Blocks:**
- LLM operations (alignment, embedding, verdict grading)
- Human review system
- Patch generation and export
- Q&A functionality

**Allows:**
- Page comparison view
- Deterministic verdicts (dates, numbers, exact matches)
- Claim display and filtering
- Search functionality

**Use Cases:**
- Testing core comparison logic
- Debugging alignment issues
- Running in environments without LLM access
- Simplified demonstration of the comparison concept

**Configuration:**
```bash
SIMPLIFICATION_MODE=minimal
```

**Implementation Details:**
- Middleware blocks POST requests to `/api/align`, `/api/embed`, `/api/recheck`
- Verdicts module skips LLM layer entirely
- Review and patch endpoints return 403 Forbidden
- Q&A endpoint is blocked

---

### 2. Read-Only Mode (`read_only`)

**Purpose:** Prevent any data modification, making the system safe for public demonstrations.

**Blocks:**
- All POST requests to write endpoints:
  - `/api/align/{qid}` - Alignment
  - `/api/embed/{qid}` - Embedding
  - `/api/recheck/{qid}` - Re-check
  - `/api/reviews` - Human reviews
  - `/api/suggestions` - Patch suggestions

**Allows:**
- All GET requests (full read access)
- Viewing existing data
- Search and filtering
- Comparison views

**Use Cases:**
- Public demos and presentations
- Production deployments where data integrity is critical
- Shared environments where you want to prevent accidental changes
- Read-only API deployments

**Configuration:**
```bash
SIMPLIFICATION_MODE=read_only
```

**Implementation Details:**
- Middleware intercepts all POST requests to write endpoints
- Returns HTTP 403 Forbidden with explanation
- GET endpoints work normally
- Can be toggled without restart for immediate effect

---

### 3. Single-Language Mode (`single_language`)

**Purpose:** Focus on a single language edition, disabling cross-language comparison.

**Blocks:**
- Cross-language alignment
- Multi-language comparison view
- Interlanguage links

**Filters:**
- Only shows claims from the configured language
- Filters counterparts to only the configured language
- No cross-language links are displayed

**Configuration:**
```bash
SIMPLIFICATION_MODE=single_language
SINGLE_LANGUAGE=en  # or fi, sv
```

**Implementation Details:**
- Diff module filters claims by language
- Comparison set only includes pages in the configured language
- Cross-language links are hidden
- Can be configured via API with language parameter

**Use Cases:**
- Language-specific analysis
- Debugging single-language extraction
- Testing with only one language dataset
- Simplified view for non-multilingual users

---

### 4. Deterministic Mode (`deterministic`)

**Purpose:** Use only deterministic comparison logic, skipping the LLM layer entirely.

**Blocks:**
- LLM verdict grading layer
- Cross-language entailment via LLM

**Uses:**
- Deterministic layer only:
  - Exact matches on `value_normalized`
  - Date comparison at coarser precision ("1900" matches "1900-09-03")
  - Number comparison with tolerance
  - Same predicate matching

**Falls back to:**
- `unverifiable` for claims that would normally go to LLM

**Configuration:**
```bash
SIMPLIFICATION_MODE=deterministic
```

**Implementation Details:**
- Verdicts module checks `is_deterministic` flag
- If true, `deterministic_verdict()` is used exclusively
- `llm_verdict()` returns immediately with "unverifiable"
- Claims that would normally be sent to LLM get "unverifiable" verdict

**Use Cases:**
- Offline use without LLM access
- Environments without Verda Inference API
- Testing deterministic logic
- Faster operation (no LLM network calls)
- More predictable/reproducible results

**Limitations:**
- Less accurate for complex claims
- No cross-language entailment
- More false positives/negatives
- Only works well with date/number claims

---

### 5. No-Extension Mode (`no_extension`)

**Purpose:** Remove all browser extension-specific functionality.

**Blocks:**
- Extension badge endpoint (`/api/badge`)
- Extension resolver endpoint (`/api/extension/resolve`)

**Use Cases:**
- Server-only deployments
- API-only deployments
- Headless environments
- Reducing attack surface
- Simplifying API surface

**Configuration:**
```bash
SIMPLIFICATION_MODE=no_extension
```

**Implementation Details:**
- Middleware blocks requests to extension-specific endpoints
- Returns HTTP 403 Forbidden for extension endpoints
- Does not affect web application functionality

---

## Comparison Matrix

| Feature | Full | Minimal | Read-Only | Single-Lang | Deterministic | No-Ext |
|---------|------|---------|-----------|-------------|---------------|--------|
| Page Comparison | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cross-Language | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| Deterministic Verdicts | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| LLM Verdicts | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ |
| Alignment | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Embedding | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Human Reviews | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Patch Export | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Q&A | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| POST /api/align | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| POST /api/recheck | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| POST /api/reviews | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| GET /api/badge | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| GET /api/extension/resolve | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |

## Recommendations

### For Development
- **Use Full Mode** for normal development
- **Use Deterministic Mode** when testing without LLM access
- **Use Read-Only Mode** when demonstrating to prevent accidental changes

### For Testing
- **Minimal Mode**: Test core comparison logic
- **Deterministic Mode**: Test deterministic verdict layer
- **Read-Only Mode**: Test GET endpoints without side effects
- **Single-Language Mode**: Test language-specific features

### For Deployment
- **Full Mode**: Production deployment with all features
- **Read-Only Mode**: Public demo deployments
- **No-Extension Mode**: Server-only deployments
- **Deterministic Mode**: Deployments without LLM access

### For Debugging
- **Single-Language Mode**: Debug language-specific issues
- **Minimal Mode**: Debug core comparison without LLM complexity
- **Deterministic Mode**: Debug deterministic verdict logic

## API Response Examples

### Get Current Configuration

```bash
curl http://localhost:8000/api/simplification
```

Response:
```json
{
  "current_mode": "none",
  "is_active": false,
  "modes": {
    "none": {
      "description": "Full functionality - all features enabled",
      "enabled": true,
      "blocks": []
    },
    "minimal": {
      "description": "Core comparison only - no LLM, no reviews, no patches, no Q&A",
      "enabled": false,
      "blocks": ["LLM operations", "Human reviews", "Patch generation", "Q&A"]
    },
    "read_only": {
      "description": "Disable all write operations - only GET requests allowed",
      "enabled": false,
      "blocks": ["POST /api/align", "POST /api/recheck", "POST /api/reviews", "POST /api/suggestions"]
    },
    "single_language": {
      "description": "Focus on one language only (en) - no cross-language comparison",
      "enabled": false,
      "current_language": "en",
      "blocks": ["Cross-language alignment", "Multi-language comparison"]
    },
    "deterministic": {
      "description": "Use only deterministic verdicts - skip LLM layer entirely",
      "enabled": false,
      "blocks": ["LLM verdict grading"],
      "uses": ["Deterministic layer only (dates, numbers, exact matches)"]
    },
    "no_extension": {
      "description": "Remove all browser extension functionality",
      "enabled": false,
      "blocks": ["Extension badge endpoint", "Extension resolver"]
    }
  },
  "environment": {
    "SIMPLIFICATION_MODE": "none",
    "SINGLE_LANGUAGE": "en"
  }
}
```

### Set Simplification Mode

```bash
curl -X POST http://localhost:8000/api/simplification \
  -H "Content-Type: application/json" \
  -d '{"mode": "read_only"}'
```

Response:
```json
{
  "status": "ok",
  "message": "Simplification mode set to 'read_only'. Some changes require server restart.",
  "current_config": {
    "mode": "read_only",
    "is_read_only": true,
    "is_minimal": false,
    "is_single_language": false,
    "single_language": "en",
    "is_deterministic": false,
    "is_no_extension": false
  }
}
```

### Set Single-Language Mode with Language

```bash
curl -X POST http://localhost:8000/api/simplification \
  -H "Content-Type: application/json" \
  -d '{"mode": "single_language", "single_language": "fi"}'
```

## Error Messages

When a feature is blocked by a simplification mode, the API returns HTTP 403 Forbidden with a descriptive error message:

```json
{
  "error": "Read-only mode enabled",
  "detail": "POST requests are disabled in read-only mode. Endpoint: /api/align/Q42",
  "simplification": {
    "mode": "read_only",
    "is_read_only": true,
    "is_minimal": false,
    "is_single_language": false,
    "single_language": "en",
    "is_deterministic": false,
    "is_no_extension": false
  }
}
```

## Implementation Architecture

### Backend (server/)

1. **config.py**: Defines `SimplificationMode` enum and `Settings` class with simplification properties
2. **main.py**: 
   - Middleware (`simplification_middleware`) intercepts requests and blocks based on mode
   - New endpoints: `/api/simplification`, `/api/simplification/options`
3. **verdicts.py**: Respects deterministic mode by skipping LLM layer
4. **diff.py**: Respects single-language mode by filtering claims

### Frontend (web/)

1. **simplification.js**: UI module for toggle interface
2. **api.js**: API client methods for simplification endpoints
3. **main.js**: Initializes simplification module
4. **base.css**: Styling for simplification toggle UI
5. **index.html**: Added simplification toggle button to header

## Testing

Each simplification mode can be tested independently:

```bash
# Test minimal mode
SIMPLIFICATION_MODE=minimal python -m server.main
curl http://localhost:8000/api/simplification
# Should show minimal mode active

# Test that LLM is blocked
curl -X POST http://localhost:8000/api/align/Q42
# Should return 403

# Test read-only mode
SIMPLIFICATION_MODE=read_only python -m server.main
curl -X POST http://localhost:8000/api/recheck/Q42
# Should return 403

# Test deterministic mode
SIMPLIFICATION_MODE=deterministic python -m server.main
# Verdicts should be deterministic-only

# Test single-language mode
SIMPLIFICATION_MODE=single_language SINGLE_LANGUAGE=fi python -m server.main
# Comparison view should only show Finnish claims

# Test no-extension mode
SIMPLIFICATION_MODE=no_extension python -m server.main
curl http://localhost:8000/api/badge?lang=en&title=Test
# Should return 403
```

## Future Enhancements

1. **Persistent Configuration**: Store simplification mode in database for persistence across restarts
2. **Mode-Specific UI**: Show/hide UI elements based on active mode
3. **Mode Indicators**: Visual indicators in the UI showing which mode is active
4. **Performance Metrics**: Track performance improvements from each mode
5. **Mode-Specific Documentation**: Generate documentation tailored to each mode
6. **Mode Validation**: Validate mode compatibility (e.g., single_language + read_only)

## Troubleshooting

### Mode Not Taking Effect
- **Check environment variable**: `echo $SIMPLIFICATION_MODE`
- **Restart server**: Changes to environment variables require server restart
- **Check configuration**: `curl http://localhost:8000/api/simplification`
- **Check logs**: Look for errors during server startup

### 403 Errors After Enabling Mode
- **Verify mode is active**: Check `/api/simplification` endpoint
- **Check endpoint compatibility**: Some endpoints may be blocked in certain modes
- **Check middleware**: Ensure `simplification_middleware` is registered in FastAPI app

### UI Toggle Not Working
- **Check JavaScript console**: Look for errors in browser console
- **Verify API connectivity**: Ensure `/api/simplification` endpoint is accessible
- **Check CORS**: Ensure CORS is configured correctly for API requests

### Deterministic Mode Not Skipping LLM
- **Verify mode is set**: Check `settings.simplification_mode` in server
- **Check verdicts.py**: Ensure `get_simplification()` is being called
- **Check middleware**: Ensure deterministic flag is being set correctly

## Conclusion

The five simplification modes provide flexible ways to reduce codebase complexity for different use cases. Whether you need a minimal demo, a read-only public deployment, a single-language analysis, deterministic-only operation, or extension-free deployment, these modes make it easy to configure the system appropriately.

Each mode is implemented as a feature flag that can be toggled via environment variables or the API, making them easy to enable/disable as needed. The modes are designed to be mutually exclusive (only one active at a time), ensuring predictable behavior.
