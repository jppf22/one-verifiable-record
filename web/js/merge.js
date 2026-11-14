/* Comparison view: one page ("this page") against its counterpart pages in the other
 * language editions. Header (this page + counterpart cards), summary bar + meter, filters,
 * rows rendered as a two-sided diff (this page | counterpart pages per language), verdict
 * rows with receipts, and the re-check banner. Payload: GET /api/pages/{id}/compare
 * (docs/page-contract.md).
 * Bus: emits page:loaded, history:open, recheck:done (manual); handles recheck:done
 * and page:reload. */
import { api } from "./api.js";
import { $, $$, esc, toast, fmtTime, wikiUrl, safeUrl, pageRevUrl, sortByLang, LANG_NAMES } from "./util.js";
import { on, emit } from "./bus.js";
import { replaceQuery, pageHref } from "./router.js";
import * as reviewUi from "./review.js";

export const STATUS = {
  conflict: { label: "Conflict", plural: "conflicts", one: "conflict" },
  possible_conflict: { label: "Possible conflict", plural: "possible conflicts", one: "possible conflict" },
  missing_here: { label: "Missing here", plural: "missing here", one: "missing here" },
  only_here: { label: "Only here", plural: "only here", one: "only here" },
  possible_match: { label: "Possible match", plural: "possible matches", one: "possible match" },
  agree: { label: "Agree", plural: "agree", one: "agree" },
};
export const STATUS_ORDER = ["conflict", "possible_conflict", "missing_here", "only_here", "possible_match", "agree"];
const STATUS_HINT = {
  conflict: "this page and a counterpart page contradict each other",
  possible_conflict: "a likely contradiction needs more evidence or review",
  missing_here: "a counterpart page states it, this page doesn't",
  only_here: "this page states it, no counterpart page does",
  possible_match: "a likely equivalent fact needs more evidence or review",
  agree: "every counterpart page that has the fact says the same thing",
};
const VERDICT_CLASS = {
  contradicted: "bad", possible_conflict: "warn", possible_match: "warn",
  supported: "ok", definition_mismatch: "warn", unverifiable: "warn",
};
const SIM_TOOLTIP = "Similarity = 1 − cosine distance via MariaDB VEC_DISTANCE_COSINE over an HNSW index";
const RECHECK_KEY = (pageId) => `ovr.recheck.page.${pageId}`;
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const emptyFilters = () => ({ statuses: new Set(), kinds: new Set(), text: "", unreviewed: false });

const state = {
  routeKey: null,       // "lang:title" of the current route
  route: null,
  pageId: null,
  data: null,           // compare payload
  loadSeq: 0,
  filters: emptyFilters(),
  focusedKey: null,
  openDrawers: new Set(),
  linkIndex: new Map(),   // link id -> {link, row}
  claimIndex: new Map(),  // claim id -> claim
  rechecking: false,
};

/* ---------------- helpers ---------------- */

const humanise = (s) => String(s || "").replace(/_/g, " ");
const thisPage = () => state.data?.page || null;
const hereLang = () => thisPage()?.lang || state.route?.lang || "";

/** Counterpart languages, in the payload's order (fi, sv, en), plus any extra seen in rows. */
function counterpartLangs(data) {
  const seen = new Set();
  const out = [];
  for (const c of sortByLang(data.counterparts || [])) if (c.lang && c.lang !== data.page?.lang && !seen.has(c.lang)) { seen.add(c.lang); out.push(c.lang); }
  const extra = new Set();
  for (const r of data.rows || []) for (const l of Object.keys(r.there || {})) if (!seen.has(l) && l !== data.page?.lang) extra.add(l);
  return [...out, ...sortByLang([...extra], (x) => x)];
}

function counterpartByLang(lang) {
  return (state.data?.counterparts || []).find((c) => c.lang === lang) || null;
}

/** "the sv page" (used in the dashed cells and notes). */
const pageName = (lang) => `the ${lang} page`;

function rowClaims(r) {
  return [...(r.here || []), ...Object.values(r.there || {}).flat()];
}

/** Links that carry a verdict (ungraded "unmatched" candidates are hidden). */
function gradedLinks(r) { return (r.links || []).filter((l) => l.verdict && l.verdict !== "unmatched"); }

function computeSummary(data) {
  const s = { conflict: 0, possible_conflict: 0, missing_here: 0, only_here: 0,
    possible_match: 0, agree: 0, links_total: 0, links_reviewed: 0 };
  for (const r of data.rows || []) {
    if (r.status in s) s[r.status]++;
    for (const l of gradedLinks(r)) { s.links_total++; if (l.review) s.links_reviewed++; }
  }
  return s;
}

function summaryOf(data) { return { ...computeSummary(data), ...(data.summary || {}) }; }

function indexData(data) {
  state.linkIndex.clear();
  state.claimIndex.clear();
  for (const r of data.rows || []) {
    for (const cl of rowClaims(r)) state.claimIndex.set(Number(cl.id), cl);
    for (const l of r.links || []) state.linkIndex.set(Number(l.id), { link: l, row: r });
  }
}

function claimUrl(cl) { return safeUrl(cl.url) || safeUrl(wikiUrl(cl.lang, cl.revision_id)); }

/* ---------------- public API (keyboard.js, review.js) ---------------- */

export function currentPageId() { return state.pageId; }
export function getLink(id) { return state.linkIndex.get(Number(id))?.link || null; }

function visibleCards() { return $$("#rows .row-card[data-key]"); }

export function focusRow(key, { scroll = true } = {}) {
  const card = key && $(`#rows .row-card[data-key="${CSS.escape(key)}"]`);
  $$("#rows .row-card.is-focused").forEach((c) => c.classList.remove("is-focused"));
  if (!card) { state.focusedKey = null; return; }
  state.focusedKey = key;
  card.classList.add("is-focused");
  card.focus({ preventScroll: true });
  if (scroll) card.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
}

/** Apply a shareable ?focus=r-{claim-id} without consuming it from the URL. */
function applyQueryFocus(query) {
  const key = query?.get("focus") || "";
  if (!/^r-\d+$/.test(key)) return;
  // Rows must exist before they can receive the same focused state used by
  // keyboard navigation. The parameter stays intact for shareable links.
  requestAnimationFrame(() => {
    if (state.data && (state.data.rows || []).some((r) => r.key === key)) focusRow(key);
  });
}

export function moveFocus(delta) {
  const cards = visibleCards();
  if (!cards.length) return;
  const idx = cards.findIndex((c) => c.dataset.key === state.focusedKey);
  const next = idx < 0 ? (delta > 0 ? 0 : cards.length - 1) : Math.max(0, Math.min(cards.length - 1, idx + delta));
  focusRow(cards[next].dataset.key);
}

/** The focused row object (or null). */
export function focusedRow() {
  if (!state.data || !state.focusedKey) return null;
  return (state.data.rows || []).find((r) => r.key === state.focusedKey) || null;
}

/** First claim of a row: this page's claim if there is one, else the first counterpart claim. */
export function firstClaim(row) { return rowClaims(row)[0] || null; }

export function firstUnreviewedLink(row) {
  return gradedLinks(row).find((l) => !l.review) || null;
}

export function isActive() { return Boolean(state.routeKey) && !$("#compare-view").hidden; }

/* ---------------- loading ---------------- */

function skeleton() {
  const card = `<div class="row-card skeleton" aria-hidden="true">
      <div class="sk-line w30"></div>
      <div class="diff sk-grid"><div class="sk-cell"></div><div class="sk-cell"></div><div class="sk-cell"></div></div>
      <div class="sk-line w60"></div></div>`;
  $("#compare-header").innerHTML = `<div class="sk-line w40 tall" aria-hidden="true"></div>`;
  $("#summary-bar").innerHTML = `<div class="sk-line w100 tall" aria-hidden="true"></div>`;
  $("#filters").innerHTML = "";
  $("#rows").innerHTML = `<p class="sr-only" role="status">Loading the comparison…</p>${card.repeat(3)}`;
  $("#rows").setAttribute("aria-busy", "true");
}

function errorState(err) {
  const r = state.route || {};
  $("#compare-header").innerHTML = `<div class="compare-title"><h2><span class="lang-tag">${esc(r.lang)}</span>${esc(r.title)}</h2></div>`;
  $("#summary-bar").innerHTML = "";
  $("#filters").innerHTML = "";
  $("#rows").removeAttribute("aria-busy");
  const notFound = err.status === 404;
  $("#rows").innerHTML = `<div class="error-state" role="alert">
      <p><b>${notFound ? "This page can't be compared yet." : "Couldn't load the comparison."}</b></p>
      <p class="muted">${notFound
        ? `“${esc(r.title)}” on ${esc(r.lang)}.wikipedia.org isn't imported, or has no counterpart page in another language.`
        : esc(err.message)}</p>
      ${notFound ? "" : `<button type="button" class="primary" data-action="retry">Retry</button>`}
      <a href="#/" class="button-link">Back to search</a>
    </div>`;
}

function needsAlignment(data) {
  const rows = data.rows || [];
  if (!rows.length) return true;
  const anyLinks = rows.some((r) => (r.links || []).length);
  const anyUngraded = rows.some((r) => (r.links || []).some((l) => l.verdict === "unmatched"));
  return !anyLinks || anyUngraded;
}

async function fetchCompare({ quiet = false } = {}) {
  const my = ++state.loadSeq;
  const route = state.route;
  if (!quiet) skeleton();
  else $("#compare-view").classList.add("is-refreshing");
  try {
    if (state.pageId == null) {
      const resolved = await api.pageByTitle(route.lang, route.title);
      if (my !== state.loadSeq) return null;
      if (!resolved?.page) throw Object.assign(new Error("Page not found"), { status: 404 });
      if (!(resolved.counterparts || []).length) throw Object.assign(new Error("No counterpart pages"), { status: 404 });
      state.pageId = resolved.page.id;
    }
    const pageId = state.pageId;
    let data = await api.compare(pageId);
    if (!quiet && (data.counterparts || []).length && needsAlignment(data)) {
      // First visit: align + grade the comparison set, then fetch again. See W1 / V-W.
      $("#rows").insertAdjacentHTML("afterbegin",
        `<p class="muted align-note" role="status"><span class="spinner" aria-hidden="true"></span>
         Aligning this page's claims with its counterpart pages…</p>`);
      try { await api.align(pageId); data = await api.compare(pageId); } catch { /* keep first payload */ }
    }
    if (my !== state.loadSeq) return null;
    state.data = data;
    indexData(data);
    return data;
  } catch (err) {
    if (my !== state.loadSeq) return null;
    if (quiet) toast(`Couldn't refresh the comparison: ${err.message}`, "error", 6000);
    else { state.data = null; errorState(err); }
    return null;
  } finally {
    if (my === state.loadSeq) $("#compare-view").classList.remove("is-refreshing");
  }
}

/** Open (or re-filter) the comparison view for a route {lang, title, key}. */
export async function open(route, query = new URLSearchParams()) {
  applyQueryFilters(query);
  if (route.key === state.routeKey && state.data) { renderAll(); applyQueryFocus(query); return; }
  state.routeKey = route.key;
  state.route = route;
  state.pageId = null;
  state.data = null;
  state.focusedKey = null;
  state.openDrawers.clear();
  hideBanner();
  document.title = `${route.title} (${route.lang}) · One Verifiable Record`;
  const key = route.key;
  const data = await fetchCompare();
  if (!data || key !== state.routeKey) return;
  renderAll();
  applyQueryFocus(query);
  emit("page:loaded", { pageId: state.pageId, data });
  showCachedBanner(state.pageId);
}

export async function reload({ highlight = null } = {}) {
  if (!state.routeKey) return;
  const key = state.routeKey;
  const data = await fetchCompare({ quiet: Boolean(state.data) });
  if (!data || key !== state.routeKey) return;
  renderAll();
  emit("page:loaded", { pageId: state.pageId, data });
  if (highlight) highlightLinks(highlight);
}

export function close() {
  state.loadSeq++;
  state.routeKey = null;
  state.route = null;
  state.pageId = null;
  state.data = null;
  document.title = "One Verifiable Record";
}

/* ---------------- filters ---------------- */

function applyQueryFilters(query) {
  const statuses = (query.get("status") || "").split(",").filter((s) => s in STATUS);
  state.filters.statuses = new Set(statuses);
}

function persistFilters() {
  replaceQuery({ status: STATUS_ORDER.filter((s) => state.filters.statuses.has(s)) });
}

function rowKinds(r) {
  return [r.kind, ...rowClaims(r).map((cl) => cl.kind)].filter(Boolean);
}

function passes(r) {
  const f = state.filters;
  if (f.statuses.size && !f.statuses.has(r.status)) return false;
  if (f.kinds.size && !rowKinds(r).some((k) => f.kinds.has(k))) return false;
  if (f.unreviewed && !firstUnreviewedLink(r)) return false;
  if (f.text.trim()) {
    const t = f.text.trim().toLowerCase();
    const hay = [r.predicate, ...rowClaims(r).flatMap((cl) => [cl.statement_text, cl.value_text, cl.predicate])];
    if (!hay.some((x) => String(x || "").toLowerCase().includes(t))) return false;
  }
  return true;
}

function filtersActive() {
  const f = state.filters;
  return f.statuses.size || f.kinds.size || f.text.trim() || f.unreviewed;
}

function toggleStatus(s) {
  const set = state.filters.statuses;
  set.has(s) ? set.delete(s) : set.add(s);
  persistFilters();
  renderSummary();
  renderFilters();
  renderRows();
}

/* ---------------- rendering ---------------- */

function renderAll() {
  if (!state.data) return;
  renderHeader();
  renderSummary();
  renderFilters();
  renderRows();
}

function revLink(p, label = null) {
  const href = pageRevUrl(p);
  const text = label || (p.revision_id ? `rev ${p.revision_id}` : "live page");
  return href
    ? `<a class="receipt-link" href="${esc(href)}" target="_blank" rel="noopener"
         title="Open ${esc(p.lang)}.wikipedia.org at the imported revision">${esc(text)} ↗</a>`
    : `<span class="muted">${esc(text)}</span>`;
}

function renderHeader() {
  const d = state.data;
  const p = d.page || {};
  const cps = sortByLang(d.counterparts || []);
  document.title = `${p.title || state.route.title} (${p.lang || state.route.lang}) · One Verifiable Record`;
  const cards = cps.map((c) => `
      <li class="cp-card">
        <span class="cp-head"><span class="lang-tag">${esc(c.lang)}</span><b>${esc(c.title)}</b></span>
        <span class="cp-meta muted">${esc(LANG_NAMES[c.lang] || c.lang)} · ${revLink(c)}</span>
        <a class="cp-switch" href="${esc(pageHref(c.lang, c.title))}"
           title="Compare from the ${esc(c.lang)} page's point of view">Make this page ⇄</a>
      </li>`).join("");
  $("#compare-header").innerHTML = `
    <div class="compare-title">
      <div class="this-page">
        <span class="eyebrow">This page</span>
        <h2><span class="lang-tag big">${esc(p.lang)}</span>${esc(p.title)}</h2>
        <span class="muted">${esc(LANG_NAMES[p.lang] || p.lang)} Wikipedia · ${revLink(p)}</span>
      </div>
      <div class="compare-actions">
        <button type="button" data-action="recheck" ${state.rechecking ? "disabled" : ""}
          title="Re-run alignment and verdicts for this page and its counterparts (key: r)">
          ${state.rechecking ? `<span class="spinner" aria-hidden="true"></span> Re-checking…` : "↻ Re-check page"}
        </button>
      </div>
    </div>
    <div class="counterparts-block">
      <span class="eyebrow">Compared with ${cps.length} counterpart page${cps.length === 1 ? "" : "s"}</span>
      <ul class="cp-cards">${cards || `<li class="muted">No counterpart pages.</li>`}</ul>
    </div>`;
}

function renderSummary() {
  const s = summaryOf(state.data);
  const total = STATUS_ORDER.reduce((n, k) => n + (s[k] || 0), 0);
  const segs = STATUS_ORDER.map((k) => {
    const n = s[k] || 0;
    if (!n) return "";
    const pct = total ? (n / total) * 100 : 0;
    const active = state.filters.statuses.has(k);
    return `<button type="button" class="seg ${k}${active ? " active" : ""}" style="flex-grow:${n}"
        data-status="${k}" aria-pressed="${active}"
        title="${n} ${STATUS[k].plural}: ${STATUS_HINT[k]} (${pct.toFixed(0)}%). Click to filter."><span>${n}</span></button>`;
  }).join("");
  const consistency = total ? Math.round(((s.agree || 0) / total) * 100) : 0;
  const reviewedPct = s.links_total ? Math.round((s.links_reviewed / s.links_total) * 100) : 0;
  const counts = STATUS_ORDER.map((k) =>
    `<button type="button" class="count ${k}${state.filters.statuses.has(k) ? " active" : ""}" data-status="${k}"
       aria-pressed="${state.filters.statuses.has(k)}" title="${esc(STATUS_HINT[k])}">
       <b>${s[k] || 0}</b> ${s[k] === 1 ? STATUS[k].one : STATUS[k].plural}</button>`).join("");
  $("#summary-bar").innerHTML = `
    <div class="summary-counts">${counts}
      <span class="consistency muted" title="Share of rows where this page agrees with every counterpart page that has the fact">${consistency}% consistent</span>
    </div>
    <div class="meter" role="group" aria-label="Consistency meter: click a segment to filter">${segs || `<div class="seg empty">No rows</div>`}</div>
    <div class="progress-row">
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${s.links_total}"
        aria-valuenow="${s.links_reviewed}" aria-label="Review progress"><div style="width:${reviewedPct}%"></div></div>
      <span class="muted"><b>${s.links_reviewed}</b> / ${s.links_total} verdicts reviewed</span>
    </div>`;
}

function renderFilters() {
  const d = state.data;
  const f = state.filters;
  const kinds = [...new Set((d.rows || []).flatMap(rowKinds))].sort();
  const statusChips = STATUS_ORDER.map((k) =>
    `<button type="button" class="chip ${k}" data-status="${k}" aria-pressed="${f.statuses.has(k)}">${STATUS[k].label}</button>`).join("");
  const kindChips = kinds.map((k) =>
    `<button type="button" class="chip kind" data-kind="${esc(k)}" aria-pressed="${f.kinds.has(k)}">${esc(humanise(k))}</button>`).join("");
  const shown = (d.rows || []).filter(passes).length;
  const existing = $("#filter-text");
  const hadFocus = existing && document.activeElement === existing;
  const caret = hadFocus ? existing.selectionStart : null;
  $("#filters").innerHTML = `
    <div class="filter-row">
      <div class="chip-group" role="group" aria-label="Filter by status">${statusChips}</div>
      ${kindChips ? `<div class="chip-group" role="group" aria-label="Filter by claim kind">${kindChips}</div>` : ""}
    </div>
    <div class="filter-row">
      <input id="filter-text" type="search" placeholder="Filter statements, values, predicates…" value="${esc(f.text)}" aria-label="Filter by statement text">
      <label class="toggle"><input type="checkbox" id="filter-unreviewed" ${f.unreviewed ? "checked" : ""}> Only unreviewed</label>
      <span class="muted filter-count" role="status">Showing ${shown} of ${(d.rows || []).length} rows</span>
      ${filtersActive() ? `<button type="button" class="link-btn" data-action="clear-filters">Clear filters</button>` : ""}
    </div>`;
  if (hadFocus) { const inp = $("#filter-text"); inp.focus(); inp.setSelectionRange(caret, caret); }
}

function claimCell(cl) {
  const url = claimUrl(cl);
  const rev = cl.revision_id
    ? (url ? `<a href="${esc(url)}" target="_blank" rel="noopener" class="receipt-link"
              title="Open the exact revision this claim was read from">rev ${esc(cl.revision_id)} ↗</a>`
           : `<span>rev ${esc(cl.revision_id)}</span>`)
    : `<span class="muted">rev unknown</span>`;
  return `<div class="claim" data-claim-id="${esc(cl.id)}">
      ${cl.value_text ? `<p class="value"><span class="muted">${esc(humanise(cl.predicate) || "value")}:</span> <b>${esc(cl.value_text)}</b></p>` : ""}
      <p class="statement" lang="${esc(cl.lang)}">${esc(cl.statement_text)}</p>
      <div class="claim-meta">
        <span class="kind-tag">${esc(humanise(cl.kind || "claim"))}</span>
        ${rev}
        <button type="button" class="small" data-history="${esc(cl.id)}" title="Biography of this claim (key: h)">History</button>
      </div>
    </div>`;
}

function missingCell(text, tone) {
  return `<div class="missing-cell ${tone}">${esc(text)}</div>`;
}

function diffHtml(r, cpLangs) {
  const here = r.here || [];
  const hl = hereLang();
  const left = `
      <div class="diff-col here" data-lang="${esc(hl)}">
        <div class="col-head"><span class="lang-tag">${esc(hl)}</span> this page</div>
        ${here.length ? here.map(claimCell).join("")
          : missingCell("Not on this page", r.status === "missing_here" ? "tone-missing" : "tone-muted")}
      </div>`;
  const right = cpLangs.map((lang) => {
    const claims = (r.there || {})[lang] || [];
    const tone = r.status === "only_here" ? "tone-only" : "tone-muted";
    return `
      <div class="diff-col there" data-lang="${esc(lang)}">
        <div class="col-head"><span class="lang-tag">${esc(lang)}</span> ${esc(counterpartByLang(lang)?.title || "")}</div>
        ${claims.length ? claims.map(claimCell).join("") : missingCell(`Not on ${pageName(lang)}`, tone)}
      </div>`;
  }).join("");
  return left + right;
}

/** Short explanation next to the row badge, from this page's point of view. */
function rowNote(r, cpLangs) {
  const present = cpLangs.filter((l) => ((r.there || {})[l] || []).length);
  const list = (ls) => ls.map(esc).join(", ");
  if (r.status === "missing_here") return present.length ? `stated on ${list(present)}, not on this page` : "not on this page";
  if (r.status === "only_here") return "no counterpart page states this";
  if (r.status === "conflict") {
    // Only links that touch this page say who contradicts *this page*; a
    // contradiction between two counterparts is reported separately.
    const against = new Set();
    const between = new Set();
    for (const l of r.links || []) {
      if (l.verdict !== "contradicted") continue;
      const a = state.claimIndex.get(Number(l.claim_a));
      const b = state.claimIndex.get(Number(l.claim_b));
      if (!a || !b) continue;
      if (a.lang === hereLang()) against.add(b.lang);
      else if (b.lang === hereLang()) against.add(a.lang);
      else between.add(sortByLang([a.lang, b.lang], (x) => x).map(esc).join(" vs "));
    }
    const parts = [];
    if (against.size) parts.push(`contradicted by ${list(sortByLang([...against], (x) => x))}`);
    if (between.size) parts.push(`counterparts disagree: ${[...between].join(", ")}`);
    return parts.join(" · ");
  }
  if (r.status === "possible_conflict") return "likely incompatible claims; needs more evidence";
  if (r.status === "possible_match") return present.length ? `likely matches ${list(present)}; needs more evidence` : "likely match";
  if (r.status === "agree") return present.length ? `agrees with ${list(present)}` : "";
  return "";
}

function evidenceHtml(link) {
  const a = state.claimIndex.get(Number(link.claim_a));
  const b = state.claimIndex.get(Number(link.claim_b));
  const parts = (link.evidence || "").split(" || ").map((p) => p.trim()).filter(Boolean);
  const sides = [a, b];
  const passages = parts.length
    ? parts.map((p, i) => {
        const cl = parts.length === 2 ? sides[i] : null;
        return `<blockquote${cl ? ` lang="${esc(cl.lang)}"` : ""}>
            ${cl ? `<span class="lang-tag">${esc(cl.lang)}</span>` : ""}${esc(p)}</blockquote>`;
      }).join("")
    : `<p class="muted">No evidence passage stored for this verdict.</p>`;
  const revLinks = sides.filter(Boolean).map((cl) => {
    const url = claimUrl(cl);
    const whose = cl.lang === hereLang() ? "this page" : `the ${cl.lang} page`;
    return url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener">View ${esc(whose)} at revision ${esc(cl.revision_id)} ↗</a>`
      : `<span class="muted">${esc(whose)}: revision unknown</span>`;
  }).join("");
  return `<div class="receipts">
      <h4>Evidence</h4>${passages}
      <div class="receipt-links">${revLinks}</div>
      <p class="muted receipt-meta">Link #${esc(link.id)} · similarity ${link.similarity != null ? esc(Number(link.similarity).toFixed(3)) : "n/a"}
        · semantic confidence ${link.candidate_confidence != null ? esc(Number(link.candidate_confidence).toFixed(2)) : "n/a"}
        · relation ${esc(link.relation || "unknown")}
        · computed ${esc(fmtTime(link.computed_at))} · source ${esc(link.verdict_source || "unknown")}</p>
    </div>`;
}

/** "fi (this page) ↔ sv": the here-side first. */
function pairLabel(link) {
  const a = state.claimIndex.get(Number(link.claim_a));
  const b = state.claimIndex.get(Number(link.claim_b));
  let [x, y] = [a, b];
  if (b && b.lang === hereLang() && a?.lang !== hereLang()) [x, y] = [b, a];
  const label = (cl) => (cl ? `${esc(cl.lang)}${cl.lang === hereLang() ? " (this page)" : ""}` : "?");
  return `${label(x)} ↔ ${label(y)}`;
}

function verdictRow(link) {
  const cls = VERDICT_CLASS[link.verdict] || "warn";
  const sim = link.similarity != null
    ? `<span class="sim" title="${esc(SIM_TOOLTIP)}">sim ${esc(Number(link.similarity).toFixed(2))}</span>` : "";
  const semantic = link.candidate_confidence != null
    ? `<span class="sim" title="Semantic verdict confidence">conf ${esc(Number(link.candidate_confidence).toFixed(2))}</span>` : "";
  const open = state.openDrawers.has(Number(link.id));
  const drawerId = `receipts-${esc(link.id)}`;
  return `<div class="verdict-row ${cls}" data-link-id="${esc(link.id)}">
      <div class="verdict-head">
        <span class="verdict-badge ${cls}">${esc(humanise(link.verdict))}</span>
        <span class="pair">${pairLabel(link)}</span>
        <span class="source-tag ${esc(link.verdict_source)}">${esc(link.verdict_source || "unknown")}</span>
        ${link.review ? `<span class="source-tag human" title="A human reviewed this verdict">human-reviewed</span>` : ""}
        ${sim}
        ${semantic}
        <button type="button" class="small receipts-toggle" data-receipts="${esc(link.id)}"
          aria-expanded="${open}" aria-controls="${drawerId}">Receipts ${open ? "▴" : "▾"}</button>
      </div>
      ${link.rationale ? `<p class="rationale">${esc(link.rationale)}</p>` : ""}
      <div class="receipts-drawer" id="${drawerId}" ${open ? "" : "hidden"}>${open ? evidenceHtml(link) : ""}</div>
      ${reviewUi.controlsHtml(link)}
    </div>`;
}

function rowCard(r, cpLangs) {
  const links = gradedLinks(r);
  const reviewed = links.filter((l) => l.review).length;
  const kinds = [...new Set(rowKinds(r))];
  const predicate = r.predicate || rowClaims(r).find((cl) => cl.predicate)?.predicate;
  const label = STATUS[r.status]?.label || r.status;
  const note = rowNote(r, cpLangs);
  return `<article class="row-card ${esc(r.status)}${r.key === state.focusedKey ? " is-focused" : ""}"
      data-key="${esc(r.key)}" tabindex="-1" aria-label="${esc(label)} row${predicate ? `: ${esc(humanise(predicate))}` : ""}">
      <header>
        <span class="badge ${esc(r.status)}">${esc(label)}</span>
        ${predicate ? `<span class="predicate">${esc(humanise(predicate))}</span>` : ""}
        ${kinds.map((k) => `<span class="kind-tag">${esc(humanise(k))}</span>`).join("")}
        ${note ? `<span class="muted row-note">${note}</span>` : ""}
        ${links.length ? `<span class="muted row-progress">${reviewed} / ${links.length} reviewed</span>` : ""}
      </header>
      <div class="diff" style="--cps:${cpLangs.length}">${diffHtml(r, cpLangs)}</div>
      ${links.length ? `<div class="verdicts">${links.map(verdictRow).join("")}</div>` : ""}
    </article>`;
}

function legendHtml(cpLangs) {
  const p = thisPage() || {};
  return `<div class="diff-legend" aria-hidden="true" style="--cps:${cpLangs.length}">
      <span class="lg-here"><span class="lang-tag">${esc(p.lang)}</span><b>This page</b> <span class="lg-title">${esc(p.title)}</span></span>
      ${cpLangs.map((l) => `<span><span class="lang-tag">${esc(l)}</span><span class="lg-title">${esc(counterpartByLang(l)?.title || LANG_NAMES[l] || l)}</span></span>`).join("")}
    </div>`;
}

function renderRows() {
  const d = state.data;
  const host = $("#rows");
  host.removeAttribute("aria-busy");
  const cpLangs = counterpartLangs(d);
  const all = d.rows || [];
  const shown = all.filter(passes);
  if (!(d.counterparts || []).length) {
    host.innerHTML = `<div class="empty-state"><p><b>This page has no counterpart pages.</b></p>
      <p class="muted">There is nothing to compare it with: no page in another language links to it.</p>
      <a href="#/" class="button-link">Back to search</a></div>`;
    return;
  }
  if (!all.length) {
    host.innerHTML = `<div class="empty-state"><p><b>No claims extracted for this page yet.</b></p>
      <p class="muted">Import this page and its counterparts, then re-check the page.</p></div>`;
    return;
  }
  if (!shown.length) {
    host.innerHTML = `<div class="empty-state"><p>No rows match the current filters.</p>
      <button type="button" data-action="clear-filters">Clear filters</button></div>`;
    return;
  }
  host.innerHTML = legendHtml(cpLangs) + shown.map((r) => rowCard(r, cpLangs)).join("");
  if (state.focusedKey && !shown.some((r) => r.key === state.focusedKey)) state.focusedKey = null;
}

function toggleDrawer(linkId) {
  linkId = Number(linkId);
  const link = getLink(linkId);
  const row = $(`.verdict-row[data-link-id="${linkId}"]`);
  if (!link || !row) return;
  const drawer = row.querySelector(".receipts-drawer");
  const btn = row.querySelector(".receipts-toggle");
  const open = drawer.hidden;
  if (open) { state.openDrawers.add(linkId); drawer.innerHTML = evidenceHtml(link); }
  else state.openDrawers.delete(linkId);
  drawer.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
  btn.textContent = `Receipts ${open ? "▴" : "▾"}`;
}

/* ---------------- re-check ---------------- */

export async function recheckNow() {
  if (state.pageId == null || state.rechecking) return;
  const pageId = state.pageId;
  state.rechecking = true;
  if (state.data) renderHeader();
  try {
    const result = await api.recheck(pageId);
    emit("recheck:done", { pageId, result, reason: "manual" });
  } catch (err) {
    toast(`Re-check failed: ${err.message}`, "error", 6000);
  } finally {
    state.rechecking = false;
    if (state.data && state.pageId === pageId) renderHeader();
  }
}

function hideBanner() {
  const b = $("#recheck-banner");
  b.hidden = true;
  b.innerHTML = "";
}

function linkOutcome(l) {
  if (l.before == null) return { cls: l.after === "contradicted" ? "bad" : "neutral", text: "New link found" };
  if (l.resurfaced) return { cls: "bad", text: "Conflict resurfaced" };
  if (l.before === "contradicted" && l.after !== "contradicted") return { cls: "ok", text: "Resolved on re-check" };
  if (l.changed) return { cls: "warn", text: "Verdict changed" };
  return { cls: "neutral", text: l.regraded === false ? "Kept (no newer revision)" : "Unchanged" };
}

function renderBanner(result, { reason, savedAt, cached = false } = {}) {
  const b = $("#recheck-banner");
  if (!result) { hideBanner(); return; }
  const before = result.before || {};
  const after = result.after || {};
  const diff = STATUS_ORDER.map((k) => {
    const x = before[k] ?? 0, y = after[k] ?? 0, d = y - x;
    const delta = d ? `<span class="delta ${d > 0 ? "up" : "down"}">${d > 0 ? "+" : ""}${d}</span>` : "";
    return `<span class="diff-count ${k}">${STATUS[k].label}: <b>${esc(x)}</b> → <b>${esc(y)}</b> ${delta}</span>`;
  }).join("");
  const links = result.links || [];
  const anyResurfaced = links.some((l) => l.resurfaced);
  const items = links.map((l) => {
    const o = linkOutcome(l);
    const entry = state.linkIndex.get(Number(l.id));
    const a = entry && state.claimIndex.get(Number(entry.link.claim_a));
    const bb = entry && state.claimIndex.get(Number(entry.link.claim_b));
    const pair = entry && a && bb ? pairLabel(entry.link) : "";
    const lead = a?.lang === hereLang() || !bb ? a : bb;
    const text = lead?.statement_text || "";
    const snippet = text ? esc(text.slice(0, 90)) + (text.length > 90 ? "…" : "") : "";
    return `<li class="outcome ${o.cls}">
        <b>${o.text}</b>
        <span class="muted">link #${esc(l.id)} ${pair} · ${esc(l.before ?? "new")} → ${esc(l.after)}</span>
        ${snippet ? `<span class="snippet">“${snippet}”</span>` : ""}
        ${entry ? `<button type="button" class="link-btn" data-goto-row="${esc(entry.row.key)}">Show</button>` : ""}
      </li>`;
  }).join("");
  const reasonText = { review: "after your review", export: "after a patch export", manual: "on request" }[reason] || "";
  b.className = anyResurfaced ? "has-resurfaced" : "";
  b.innerHTML = `
    <div class="banner-head">
      <span class="banner-title"><b>Page re-checked</b> ${esc(reasonText)}</span>
      <span class="muted">last re-checked ${esc(fmtTime(result.checked_at || savedAt))}${cached ? " (earlier this session)" : ""}</span>
      <button type="button" class="link-btn" data-action="dismiss-banner" aria-label="Dismiss re-check summary">Dismiss</button>
    </div>
    <div class="banner-diff">${diff}
      ${result.new_links ? `<span class="muted">${esc(result.new_links)} new candidate link${result.new_links === 1 ? "" : "s"}</span>` : ""}
    </div>
    ${items ? `<ul class="outcomes">${items}</ul>` : `<p class="muted">No verdict changed.</p>`}
    ${anyResurfaced ? `<p class="banner-note">Re-checks run against the revisions currently in the database.
      A conflict keeps resurfacing until the fix is applied on Wikipedia and the page is re-imported.</p>` : ""}`;
  b.hidden = false;
}

function highlightLinks(linkIds) {
  const outcomes = new Map((linkIds || []).map((l) => [Number(l.id ?? l), l]));
  const keys = new Map();
  for (const [id, l] of outcomes) {
    const entry = state.linkIndex.get(id);
    if (!entry) continue;
    const cls = typeof l === "object" ? linkOutcome(l).cls : "neutral";
    keys.set(entry.row.key, keys.get(entry.row.key) === "bad" ? "bad" : cls);
  }
  for (const [key, cls] of keys) {
    const card = $(`#rows .row-card[data-key="${CSS.escape(key)}"]`);
    if (!card) continue;
    card.classList.remove("flash", "flash-bad", "flash-ok", "flash-warn", "flash-neutral");
    void card.offsetWidth; // restart animation
    card.classList.add("flash", `flash-${cls}`);
    setTimeout(() => card.classList.remove("flash", `flash-${cls}`), 2600);
  }
}

function cacheResult(pageId, result, reason) {
  try {
    sessionStorage.setItem(RECHECK_KEY(pageId), JSON.stringify({ result, reason, savedAt: new Date().toISOString() }));
  } catch { /* storage full or disabled */ }
}

function showCachedBanner(pageId) {
  try {
    const raw = sessionStorage.getItem(RECHECK_KEY(pageId));
    if (!raw) return;
    const { result, reason, savedAt } = JSON.parse(raw);
    renderBanner(result, { reason, savedAt, cached: true });
  } catch { /* corrupt cache: ignore */ }
}

async function onRecheckDone({ pageId, result, reason, linkIds } = {}) {
  if (pageId == null) return;
  if (result) cacheResult(pageId, result, reason);
  if (Number(pageId) !== Number(state.pageId)) return;
  const changed = [...(result?.links || [])];
  for (const id of linkIds || []) if (!changed.some((l) => Number(l.id) === Number(id))) changed.push(id);
  await reload({ highlight: changed });
  if (result) {
    renderBanner(result, { reason });
    if (reason === "manual") $("#recheck-banner").scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
  }
}

/* ---------------- init ---------------- */

function resetFilters() {
  state.filters = emptyFilters();
  persistFilters();
}

export function init() {
  reviewUi.bind({ getLink, getPageId: () => state.pageId });

  on("recheck:done", onRecheckDone);
  on("page:reload", ({ pageId } = {}) => { if (pageId == null || Number(pageId) === Number(state.pageId)) reload(); });

  const view = $("#compare-view");
  view.addEventListener("click", (e) => {
    const t = e.target;
    const action = t.closest("[data-action]")?.dataset.action;
    if (action === "retry") { const r = state.route; state.routeKey = null; open(r); return; }
    if (action === "recheck") { recheckNow(); return; }
    if (action === "dismiss-banner") { hideBanner(); sessionStorage.removeItem(RECHECK_KEY(state.pageId)); return; }
    if (action === "clear-filters") { resetFilters(); renderSummary(); renderFilters(); renderRows(); return; }
    const status = t.closest("[data-status]")?.dataset.status;
    if (status && t.closest("#summary-bar, #filters")) { toggleStatus(status); return; }
    const kind = t.closest("[data-kind]")?.dataset.kind;
    if (kind) {
      const k = state.filters.kinds;
      k.has(kind) ? k.delete(kind) : k.add(kind);
      renderFilters(); renderRows(); return;
    }
    const hist = t.closest("[data-history]");
    if (hist) { emit("history:open", { claimId: Number(hist.dataset.history) }); return; }
    const rec = t.closest("[data-receipts]");
    if (rec) { toggleDrawer(rec.dataset.receipts); return; }
    const go = t.closest("[data-goto-row]");
    if (go) {
      const key = go.dataset.gotoRow;
      if (!$(`#rows .row-card[data-key="${CSS.escape(key)}"]`)) { resetFilters(); renderAll(); }
      focusRow(key);
      return;
    }
    // clicking anywhere on a card (but not on a control) focuses it
    const card = t.closest("#rows .row-card[data-key]");
    if (card && !t.closest("a, button, input, textarea, label, form")) focusRow(card.dataset.key, { scroll: false });
  });

  view.addEventListener("input", (e) => {
    if (e.target.id === "filter-text") {
      state.filters.text = e.target.value;
      renderFilters(); // keeps focus + caret in the text box
      renderRows();
    }
  });
  view.addEventListener("change", (e) => {
    if (e.target.id === "filter-unreviewed") {
      state.filters.unreviewed = e.target.checked;
      renderFilters(); renderRows();
    }
  });
}
