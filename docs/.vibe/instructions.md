# Documentation Context for One Verifiable Record

This directory contains the living documentation for the project. All agents should read and update these files as they work.

## Document Hierarchy

### Must Read (In Order)
1. **project-context.md** - Project background and handoff notes
   - What we're building and why
   - Hard constraints (MariaDB challenge requirements)
   - Verdict model
   - Judging criteria
   - 48h build order

2. **assumptions.md** - Decisions log (MOST IMPORTANT)
   - User-confirmed decisions (from project owner)
   - Assumed decisions (by coordinators/agents)
   - Deployment & data pipeline decisions
   - Server, web, panels assumptions
   - Page-centric rework decisions (V1-V6)
   - Open questions that need owner input

3. **page-contract.md** - API contract (v2, page-centric)
   - Data model
   - Page object structure
   - All endpoints (methods, paths, returns)
   - Comparison payload structure
   - Frontend routes
   - Status model

4. **frontend-contract.md** - Frontend ⇄ API contract
   - Endpoint specifications
   - Bus events (cross-module interface)
   - Frontend module layout
   - Page regions
   - Contract change requests

5. **frontend-direction.md** - Frontend improvement plan
   - What the project is
   - Where frontend falls short
   - Proposed improvements (P1-P4)
   - Constraints

6. **browser-extension-roadmap.md** - Extension plans
   - Current state
   - Roadmap items
   - Technical considerations

7. **deploy-verda.md** - Deployment guide
   - Verda cloud setup
   - Docker compose configuration
   - Deployment steps

## How to Use These Docs

### For Agents Starting Work
1. Read `project-context.md` to understand the project
2. Read `assumptions.md` to understand current decisions
3. Read the relevant contract file for your area
4. Update `assumptions.md` when you make decisions

### For Adding Decisions
- User-confirmed decisions: Add to "User-confirmed" section with date
- Agent decisions: Add to "Assumed" section with your identifier
- Open questions: Add to "Open questions" section

### For API Changes
- Add to "Contract change requests" in `frontend-contract.md`
- Notify coordinator before implementing

## Document Maintenance Rules

1. **assumptions.md is the source of truth** for all decisions
2. **page-contract.md is the source of truth** for API specifications
3. **frontend-contract.md is the source of truth** for frontend-API interface
4. All changes must be additive when possible (backwards compatible)
5. Breaking changes must be clearly marked and coordinated

## Quick Reference

### Current Architecture Decisions
- **Page-centric v2**: Comparison is over pages, not entities/QIDs
- **No QID in API**: wikidata_qid is import-time only, never shown
- **Status model**: conflict, possible_conflict, missing_here, only_here, possible_match, agree
- **Verdict model**: supported, contradicted, unverifiable, definition_mismatch, unmatched

### Current Implementation State
- Schema: Complete (db/schema.sql)
- API server: Skeleton with core endpoints
- Web app: Merge view, review, SVT timeline, patch export
- Extension: Badge, inline highlighting
- Embedding: GPU job + embedder service
- Claim extraction: LLM (when available) + rules fallback

### Blockers & Issues
- Verda project not subscribed to LLM models (using rules-based extraction)
- Re-import with changed revisions not yet implemented
- Human review override not yet implemented (re-check only)

## agent Handoff Checklist

When handing off work to another agent:
- [ ] Update assumptions.md with any decisions made
- [ ] Update the relevant AGENTS.md if instructions changed
- [ ] Note any open questions in assumptions.md
- [ ] Document any workarounds or temporary solutions
- [ ] Commit all changes with descriptive messages
