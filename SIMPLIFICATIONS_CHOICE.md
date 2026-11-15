# Choose Your Simplification Mode

I've created **5 toggleable simplification modes** for your codebase. Each mode reduces complexity in a different way. Review the options below and let me know which one you want to use, or ask me to enable a specific one.

---

## 📋 Quick Comparison Table

| Mode | Best For | Blocks | Complexity Reduction | Speed | Use Case |
|------|----------|--------|---------------------|-------|----------|
| **Full Mode** | Development | Nothing | 0% | Normal | Default, all features |
| **Minimal Mode** | Core testing | LLM, Reviews, Patches, Q&A | ~60% | Fast | Focus on comparison logic |
| **Read-Only Mode** | Public demos | All POST/Write operations | ~40% | Normal | Safe for presentations |
| **Single-Language Mode** | Language focus | Cross-language features | ~50% | Fast | One language analysis |
| **Deterministic Mode** | Offline/No LLM | LLM verdict layer | ~30% | Very Fast | No LLM dependency |
| **No-Extension Mode** | API-only | Extension endpoints | ~10% | Normal | Headless deployments |

---

## 🎯 Detailed Mode Descriptions

### 1️⃣ **Minimal Mode** (`SIMPLIFICATION_MODE=minimal`)

**🎯 What it does:** Strips everything down to the core comparison functionality.

**✅ Keeps:**
- Page comparison view
- Deterministic verdicts (dates, numbers, exact matches)
- Search and basic navigation
- Claim display and filtering

**❌ Removes:**
- All LLM operations (alignment, embedding, verdict grading)
- Human review system
- Patch generation and export
- Q&A functionality

**🔥 Pros:**
- Fastest operation (no LLM calls)
- Simplest code path
- Easy to debug comparison logic
- Low resource usage

**⚠️ Cons:**
- No human review capability
- No patch export
- No Q&A
- Less accurate for complex claims

**💡 Best for:** Testing core comparison logic, debugging alignment, running without LLM access

---

### 2️⃣ **Read-Only Mode** (`SIMPLIFICATION_MODE=read_only`)

**🎯 What it does:** Makes the entire system read-only - you can view everything but change nothing.

**✅ Allows:**
- All GET requests
- Full read access to all data
- Viewing existing comparisons
- Search and filtering

**❌ Blocks:**
- POST /api/align - Run alignment
- POST /api/embed - Run embedding
- POST /api/recheck - Re-check articles
- POST /api/reviews - Submit reviews
- POST /api/suggestions - Export patches

**🔥 Pros:**
- Safe for public demos
- Prevents accidental data modification
- Full data visibility
- Good for presentations

**⚠️ Cons:**
- No way to update or correct data
- Reviews and rechecks disabled
- Can't generate new patches

**💡 Best for:** Public demonstrations, presentations, production deployments where data integrity is critical

---

### 3️⃣ **Single-Language Mode** (`SIMPLIFICATION_MODE=single_language`)

**🎯 What it does:** Focuses on a single language edition (fi, sv, or en), removing all cross-language comparison.

**✅ Keeps:**
- All features for the selected language
- Full LLM and deterministic verdicts
- Reviews, patches, Q&A

**❌ Filters out:**
- Claims from other languages
- Cross-language alignment
- Multi-language comparison view
- Interlanguage links

**✅ Configurable:**
- Set `SINGLE_LANGUAGE=fi` for Finnish
- Set `SINGLE_LANGUAGE=sv` for Swedish
- Set `SINGLE_LANGUAGE=en` for English

**🔥 Pros:**
- Simpler data model
- Faster queries
- Easier to understand
- Reduced complexity

**⚠️ Cons:**
- Loses the main value proposition (cross-language comparison)
- No cross-language matching
- Limited use case

**💡 Best for:** Language-specific analysis, debugging single-language extraction, testing with one language dataset

---

### 4️⃣ **Deterministic Mode** (`SIMPLIFICATION_MODE=deterministic`)

**🎯 What it does:** Uses only deterministic comparison logic, completely skipping the LLM layer.

**✅ Uses:**
- Deterministic layer only:
  - Exact matches on `value_normalized`
  - Date comparison ("1900" matches "1900-09-03")
  - Number comparison with tolerance
  - Same predicate matching

**❌ Skips:**
- All LLM verdict grading
- Cross-language entailment
- Complex claim analysis

**🔥 Pros:**
- Very fast (no LLM network calls)
- Deterministic/reproducible results
- No LLM dependency
- Works offline
- Lower cost (no API calls)

**⚠️ Cons:**
- Less accurate for complex claims
- No cross-language entailment
- More false positives/negatives
- Only works well with date/number claims

**💡 Best for:** Offline use, environments without LLM access, testing deterministic logic, faster operation

---

### 5️⃣ **No-Extension Mode** (`SIMPLIFICATION_MODE=no_extension`)

**🎯 What it does:** Removes all browser extension-specific functionality.

**✅ Keeps:**
- Full web application
- All API endpoints (except extension-specific ones)
- All comparison and review features

**❌ Blocks:**
- GET /api/badge - Extension badge data
- GET /api/extension/resolve - Extension resolver

**🔥 Pros:**
- Reduced attack surface
- Cleaner API
- No extension dependencies
- Good for server-only deployments

**⚠️ Cons:**
- Browser extension won't work
- Losing a key integration point

**💡 Best for:** Server-only deployments, API-only use, headless environments

---

## 🚀 How to Enable

### Option 1: Environment Variable (Recommended)

```bash
# Set in your .env file or environment
SIMPLIFICATION_MODE=minimal
# For single-language mode, also set:
SINGLE_LANGUAGE=en

# Then restart your server
python -m server.main
```

### Option 2: API (Runtime)

```bash
# Get current mode
curl http://localhost:8000/api/simplification

# Set mode
curl -X POST http://localhost:8000/api/simplification \
  -H "Content-Type: application/json" \
  -d '{"mode": "read_only"}'

# Set mode with language (for single_language)
curl -X POST http://localhost:8000/api/simplification \
  -H "Content-Type: application/json" \
  -d '{"mode": "single_language", "single_language": "fi"}'
```

### Option 3: UI Toggle

1. Open the web application
2. Click the gear icon (⚙️) in the header
3. Select your desired mode from the menu
4. Some changes may require a server restart

---

## 📊 Files Modified

### Backend (server/)
- `config.py` - Added SimplificationMode enum and configuration
- `main.py` - Added middleware and API endpoints
- `verdicts.py` - Added deterministic mode support
- `diff.py` - Added single-language mode support

### Frontend (web/)
- `index.html` - Added simplification toggle button
- `js/simplification.js` - New UI module for toggle interface
- `js/api.js` - Added API methods for simplification
- `js/main.js` - Initialize simplification module
- `css/base.css` - Added styling for toggle UI

### Documentation
- `docs/simplifications.md` - Comprehensive documentation

---

## 🎯 My Recommendation

Based on the project's hackathon nature and the need to demonstrate MariaDB features (vector search and SVT), I recommend:

1. **For hackathon judging/demo:** **Read-Only Mode** - Safe, shows all features without risk of data corruption
2. **For development/debugging:** **Deterministic Mode** - Fast, no LLM dependency, still shows comparison
3. **For minimal complexity:** **Minimal Mode** - Most reduced, but loses key features
4. **For language-specific work:** **Single-Language Mode** - Good for testing individual language editions

---

## 💬 What Next?

**Tell me which mode you want to use, and I'll:**
1. Enable it in the configuration
2. Verify it's working correctly
3. Show you how to test it

**Or ask me to:**
- Enable a specific mode
- Show you the differences between modes
- Create a custom combination
- Explain any mode in more detail

---

## 🎨 Mode Decision Guide

Ask yourself:

- **Do you need to prevent any data changes?** → **Read-Only Mode**
- **Do you need the fastest possible operation?** → **Minimal Mode** or **Deterministic Mode**
- **Do you need to focus on one language?** → **Single-Language Mode**
- **Do you need to deploy without browser extension?** → **No-Extension Mode**
- **Do you need all features?** → **Full Mode** (default)

**What's your use case? Let me know and I'll help you pick the perfect mode!**
