/* Merge view: header, summary bar + consistency meter, filters, clusters rendered as a
 * fi | sv | en diff, verdict rows with receipts, and the re-check banner.
 * Bus: emits article:loaded, history:open, recheck:done (manual); handles recheck:done
 * and merge:reload. See docs/frontend-contract.md. */
import { api } from "./api.js";
import { $, $$, esc, toast, fmtTime, wikiUrl, LANGS, LANG_NAMES } from "./util.js";
import { on, emit } from "./bus.js";
import { replaceQuery } from "./router.js";
import * as reviewUi from "./review.js";

const STATUS = {
  conflict: { label: "Conflict", plural: "conflicts", one: "conflict" },
  gap: { label: "Gap", plural: "gaps", one: "gap" },
  agree: { label: "Agree", plural: "agree", one: "agree" },
};
const STATUS_ORDER = ["conflict", "gap", "agree"];
const VERDICT_CLASS = {
  contradicted: "bad", supported: "ok", definition_mismatch: "warn", unverifiable: "warn",
};
const SIM_TOOLTIP = "Similarity = 1 − cosine distance via MariaDB VEC_DISTANCE_COSINE over an HNSW index";
const RECHECK_KEY = (qid) => `ovr.recheck.${qid}`;
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  qid: null,
  data: null,
  loading: false,
  loadSeq: 0,
  filters: { statuses: new Set(), kinds: new Set(), text: "", unreviewed: false },
  focusedKey: null,
  openDrawers: new Set(),
  linkIndex: new Map(),   // link id -> {link, cluster}
  claimIndex: new Map(),  // claim id -> claim
  rechecking: false,
};

/* ---------------- helpers ---------------- */

/** Only allow http(s) links from the API into href attributes. */
function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : null; }

function langsOf(data) {
  const present = new Set((data.articles || []).map((a) => a.lang));
  for (const c of data.clusters || []) for (const cl of c.claims || []) present.add(cl.lang);
  const ordered = LANGS.filter((l) => present.has(l));
  return [...ordered, ...[...present].filter((l) => l && !LANGS.includes(l)).sort()];
}

/** Links that carry a verdict (ungraded "unmatched" candidates are hidden). */
function gradedLinks(c) { return (c.links || []).filter((l) => l.verdict && l.verdict !== "unmatched"); }

function computeSummary(data) {
  const s = { conflict: 0, gap: 0, agree: 0, links_total: 0, links_reviewed: 0 };
  for (const c of data.clusters || []) {
    if (c.status in s) s[c.status]++;
    for (const l of gradedLinks(c)) { s.links_total++; if (l.review) s.links_reviewed++; }
  }
  return s;
}

function summaryOf(data) {
  const computed = computeSummary(data);
  return { ...computed, ...(data.summary || {}) };
}

function indexData(data) {
  state.linkIndex.clear();
  state.claimIndex.clear();
  for (const c of data.clusters || []) {
    for (const cl of c.claims || []) state.claimIndex.set(Number(cl.id), cl);
    for (const l of c.links || []) state.linkIndex.set(Number(l.id), { link: l, cluster: c });
  }
}

function claimUrl(cl) { return safeUrl(cl.url) || safeUrl(wikiUrl(cl.lang, cl.revision_id)); }

/* ---------------- public API (keyboard.js) ---------------- */

export function currentQid() { return state.qid; }
export function getLink(id) { return state.linkIndex.get(Number(id))?.link || null; }

function visibleCards() { return $$("#clusters .cluster[data-key]"); }

export function focusCluster(key, { scroll = true } = {}) {
  const card = key && $(`#clusters .cluster[data-key="${CSS.escape(key)}"]`);
  $$("#clusters .cluster.is-focused").forEach((c) => c.classList.remove("is-focused"));
  if (!card) { state.focusedKey = null; return; }
  state.focusedKey = key;
  card.classList.add("is-focused");
  card.focus({ preventScroll: true });
  if (scroll) card.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
}

export function moveFocus(delta) {
  const cards = visibleCards();
  if (!cards.length) return;
  const idx = cards.findIndex((c) => c.dataset.key === state.focusedKey);
  const next = idx < 0 ? (delta > 0 ? 0 : cards.length - 1) : Math.max(0, Math.min(cards.length - 1, idx + delta));
  focusCluster(cards[next].dataset.key);
}

/** The focused cluster object (or null). */
export function focusedCluster() {
  if (!state.data || !state.focusedKey) return null;
  return state.data.clusters.find((c) => c.key === state.focusedKey) || null;
}

export function firstUnreviewedLink(cluster) {
  return gradedLinks(cluster).find((l) => !l.review) || null;
}

export function isActive() { return Boolean(state.qid) && !$("#merge-view").hidden; }

/* ---------------- loading ---------------- */

function skeleton() {
  const card = `<div class="cluster skeleton" aria-hidden="true">
      <div class="sk-line w30"></div>
      <div class="diff sk-grid"><div class="sk-cell"></div><div class="sk-cell"></div><div class="sk-cell"></div></div>
      <div class="sk-line w60"></div></div>`;
  $("#merge-header").innerHTML = `<div class="sk-line w40 tall" aria-hidden="true"></div>`;
  $("#summary-bar").innerHTML = `<div class="sk-line w100 tall" aria-hidden="true"></div>`;
  $("#filters").innerHTML = "";
  $("#clusters").innerHTML = `<p class="sr-only" role="status">Loading the merge view…</p>${card.repeat(3)}`;
  $("#clusters").setAttribute("aria-busy", "true");
}

function errorState(err, qid) {
  $("#merge-header").innerHTML = `<h2>${esc(qid)}</h2>`;
  $("#summary-bar").innerHTML = "";
  $("#filters").innerHTML = "";
  $("#clusters").removeAttribute("aria-busy");
  const notFound = err.status === 404;
  $("#clusters").innerHTML = `<div class="error-state" role="alert">
      <p><b>${notFound ? "This article isn't tracked yet." : "Couldn't load the merge view."}</b></p>
      <p class="muted">${esc(err.message)}</p>
      <button type="button" class="primary" data-action="retry">Retry</button>
      <a href="#/" class="button-link">Back to search</a>
    </div>`;
}

function needsAlignment(data) {
  const clusters = data.clusters || [];
  if (!clusters.length) return true;
  const anyLinks = clusters.some((c) => (c.links || []).length);
  const anyUngraded = clusters.some((c) => (c.links || []).some((l) => l.verdict === "unmatched"));
  return !anyLinks || anyUngraded;
}

async function fetchMerge(qid, { quiet = false } = {}) {
  const my = ++state.loadSeq;
  if (!quiet) skeleton();
  else $("#merge-view").classList.add("is-refreshing");
  try {
    let data = await api.merge(qid);
    if (!quiet && (data.articles || []).length && needsAlignment(data)) {
      // First visit: align + grade (legacy behaviour), then fetch again. See W1.
      $("#clusters").insertAdjacentHTML("afterbegin",
        `<p class="muted align-note" role="status"><span class="spinner" aria-hidden="true"></span>
         Aligning claims across language editions…</p>`);
      try { await api.align(qid); data = await api.merge(qid); } catch { /* keep first payload */ }
    }
    if (my !== state.loadSeq) return null;
    state.data = data;
    indexData(data);
    return data;
  } catch (err) {
    if (my !== state.loadSeq) return null;
    if (quiet) toast(`Couldn't refresh the merge view: ${err.message}`, "error", 6000);
    else { state.data = null; errorState(err, qid); }
    return null;
  } finally {
    if (my === state.loadSeq) $("#merge-view").classList.remove("is-refreshing");
  }
}

/** Open (or re-filter) the merge view for a QID. `query` is the route's URLSearchParams. */
export async function open(qid, query = new URLSearchParams()) {
  applyQueryFilters(query);
  if (qid === state.qid && state.data) { renderAll(); return; }
  state.qid = qid;
  state.data = null;
  state.focusedKey = null;
  state.openDrawers.clear();
  hideBanner();
  document.title = `${qid} · One Verifiable Record`;
  const data = await fetchMerge(qid);
  if (!data || qid !== state.qid) return;
  renderAll();
  emit("article:loaded", { qid, data });
  showCachedBanner(qid);
}

export async function reload({ highlight = null } = {}) {
  if (!state.qid) return;
  const qid = state.qid;
  const data = await fetchMerge(qid, { quiet: Boolean(state.data) });
  if (!data || qid !== state.qid) return;
  renderAll();
  emit("article:loaded", { qid, data });
  if (highlight) highlightLinks(highlight);
}

export function close() {
  state.loadSeq++;
  state.qid = null;
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

function passes(c) {
  const f = state.filters;
  if (f.statuses.size && !f.statuses.has(c.status)) return false;
  if (f.kinds.size && !(c.claims || []).some((cl) => f.kinds.has(cl.kind))) return false;
  if (f.unreviewed && !firstUnreviewedLink(c)) return false;
  if (f.text.trim()) {
    const t = f.text.trim().toLowerCase();
    if (!(c.claims || []).some((cl) => (cl.statement_text || "").toLowerCase().includes(t))) return false;
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
  renderClusters();
}

/* ---------------- rendering ---------------- */

function renderAll() {
  if (!state.data) return;
  renderHeader();
  renderSummary();
  renderFilters();
  renderClusters();
}

function renderHeader() {
  const d = state.data;
  const order = (l) => { const i = LANGS.indexOf(l); return i < 0 ? 99 : i; };
  const arts = [...(d.articles || [])].sort((a, b) => order(a.lang) - order(b.lang));
  const main = arts.find((a) => a.lang === "en") || arts[0];
  const title = main?.title || state.qid;
  document.title = `${title} · One Verifiable Record`;
  const editions = arts.map((a) => {
    const href = safeUrl(a.url) || safeUrl(wikiUrl(a.lang, a.revision_id, a.title));
    const label = `<span class="lang-tag">${esc(a.lang)}</span> ${esc(a.title)}`;
    const rev = a.revision_id ? `<span class="muted">rev ${esc(a.revision_id)}</span>` : "";
    return href
      ? `<a class="edition" href="${esc(href)}" target="_blank" rel="noopener"
           title="${esc(LANG_NAMES[a.lang] || a.lang)} edition at the imported revision">${label} ${rev} <span aria-hidden="true">↗</span></a>`
      : `<span class="edition">${label} ${rev}</span>`;
  }).join("");
  $("#merge-header").innerHTML = `
    <div class="merge-title">
      <div>
        <h2>${esc(title)} <span class="muted qid">${esc(state.qid)}</span></h2>
        <div class="editions">${editions}</div>
      </div>
      <div class="merge-actions">
        <button type="button" data-action="recheck" ${state.rechecking ? "disabled" : ""}
          title="Re-run alignment and verdicts for the whole page (key: r)">
          ${state.rechecking ? `<span class="spinner" aria-hidden="true"></span> Re-checking…` : "↻ Re-check page"}
        </button>
      </div>
    </div>`;
}

function renderSummary() {
  const s = summaryOf(state.data);
  const total = s.conflict + s.gap + s.agree;
  const segs = STATUS_ORDER.map((k) => {
    const n = s[k] || 0;
    if (!n) return "";
    const pct = total ? (n / total) * 100 : 0;
    const active = state.filters.statuses.has(k);
    return `<button type="button" class="seg ${k}${active ? " active" : ""}" style="flex-grow:${n}"
        data-status="${k}" aria-pressed="${active}"
        title="${n} ${STATUS[k].plural} (${pct.toFixed(0)}%). Click to filter."><span>${n}</span></button>`;
  }).join("");
  const consistency = total ? Math.round((s.agree / total) * 100) : 0;
  const reviewedPct = s.links_total ? Math.round((s.links_reviewed / s.links_total) * 100) : 0;
  const counts = STATUS_ORDER.map((k) =>
    `<button type="button" class="count ${k}${state.filters.statuses.has(k) ? " active" : ""}" data-status="${k}"
       aria-pressed="${state.filters.statuses.has(k)}">
       <b>${s[k] || 0}</b> ${s[k] === 1 ? STATUS[k].one : STATUS[k].plural}</button>`).join("");
  $("#summary-bar").innerHTML = `
    <div class="summary-counts">${counts}
      <span class="consistency muted" title="Share of claim clusters where every edition agrees">${consistency}% consistent</span>
    </div>
    <div class="meter" role="group" aria-label="Consistency meter: click a segment to filter">${segs || `<div class="seg empty">No clusters</div>`}</div>
    <div class="progress-row">
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${s.links_total}"
        aria-valuenow="${s.links_reviewed}" aria-label="Review progress"><div style="width:${reviewedPct}%"></div></div>
      <span class="muted"><b>${s.links_reviewed}</b> / ${s.links_total} links reviewed</span>
    </div>`;
}

function renderFilters() {
  const d = state.data;
  const f = state.filters;
  const kinds = [...new Set((d.clusters || []).flatMap((c) => (c.claims || []).map((cl) => cl.kind)).filter(Boolean))].sort();
  const statusChips = STATUS_ORDER.map((k) =>
    `<button type="button" class="chip ${k}" data-status="${k}" aria-pressed="${f.statuses.has(k)}">${STATUS[k].label}</button>`).join("");
  const kindChips = kinds.map((k) =>
    `<button type="button" class="chip kind" data-kind="${esc(k)}" aria-pressed="${f.kinds.has(k)}">${esc(k.replace(/_/g, " "))}</button>`).join("");
  const shown = (d.clusters || []).filter(passes).length;
  const existing = $("#filter-text");
  const hadFocus = existing && document.activeElement === existing;
  const caret = hadFocus ? existing.selectionStart : null;
  $("#filters").innerHTML = `
    <div class="filter-row">
      <div class="chip-group" role="group" aria-label="Filter by status">${statusChips}</div>
      ${kindChips ? `<div class="chip-group" role="group" aria-label="Filter by claim kind">${kindChips}</div>` : ""}
    </div>
    <div class="filter-row">
      <input id="filter-text" type="search" placeholder="Filter statements…" value="${esc(f.text)}" aria-label="Filter by statement text">
      <label class="toggle"><input type="checkbox" id="filter-unreviewed" ${f.unreviewed ? "checked" : ""}> Only unreviewed</label>
      <span class="muted filter-count" role="status">Showing ${shown} of ${(d.clusters || []).length} clusters</span>
      ${filtersActive() ? `<button type="button" class="link-btn" data-action="clear-filters">Clear filters</button>` : ""}
    </div>`;
  if (hadFocus) { const inp = $("#filter-text"); inp.focus(); inp.setSelectionRange(caret, caret); }
}

function claimCell(cl) {
  const url = claimUrl(cl);
  const rev = cl.revision_id
    ? (url ? `<a href="${esc(url)}" target="_blank" rel="noopener" class="receipt-link"
              title="Open the exact revision this claim was extracted from">rev ${esc(cl.revision_id)} ↗</a>`
           : `<span>rev ${esc(cl.revision_id)}</span>`)
    : `<span class="muted">rev unknown</span>`;
  return `<div class="claim" data-claim-id="${esc(cl.id)}">
      <p class="statement" lang="${esc(cl.lang)}">${esc(cl.statement_text)}</p>
      ${cl.value_text ? `<p class="value muted">value: <b>${esc(cl.value_text)}</b></p>` : ""}
      <div class="claim-meta">
        <span class="kind-tag">${esc((cl.kind || "claim").replace(/_/g, " "))}</span>
        ${rev}
        <button type="button" class="small" data-history="${esc(cl.id)}" title="Biography of this claim (key: h)">History</button>
      </div>
    </div>`;
}

function diffHtml(c, langs) {
  const byLang = new Map(langs.map((l) => [l, []]));
  for (const cl of c.claims || []) {
    if (!byLang.has(cl.lang)) byLang.set(cl.lang, []);
    byLang.get(cl.lang).push(cl);
  }
  return [...byLang.entries()].map(([lang, claims]) => `
      <div class="diff-col${claims.length ? "" : " missing"}" data-lang="${esc(lang)}">
        <div class="col-head"><span class="lang-tag">${esc(lang)}</span></div>
        ${claims.length ? claims.map(claimCell).join("")
          : `<div class="missing-cell">missing in ${esc(lang)}</div>`}
      </div>`).join("");
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
    return url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener">View ${esc(cl.lang)} revision ${esc(cl.revision_id)} on Wikipedia ↗</a>`
      : `<span class="muted">${esc(cl.lang)}: revision unknown</span>`;
  }).join("");
  return `<div class="receipts">
      <h4>Evidence</h4>${passages}
      <div class="receipt-links">${revLinks}</div>
      <p class="muted receipt-meta">Link #${esc(link.id)} · similarity ${link.similarity != null ? esc(Number(link.similarity).toFixed(3)) : "n/a"}
        · computed ${esc(fmtTime(link.computed_at))} · source ${esc(link.verdict_source || "unknown")}</p>
    </div>`;
}

function verdictRow(link) {
  const a = state.claimIndex.get(Number(link.claim_a));
  const b = state.claimIndex.get(Number(link.claim_b));
  const pair = `${esc(a?.lang || "?")} ↔ ${esc(b?.lang || "?")}`;
  const cls = VERDICT_CLASS[link.verdict] || "warn";
  const sim = link.similarity != null
    ? `<span class="sim" title="${esc(SIM_TOOLTIP)}">sim ${esc(Number(link.similarity).toFixed(2))}</span>` : "";
  const open = state.openDrawers.has(Number(link.id));
  const drawerId = `receipts-${esc(link.id)}`;
  return `<div class="verdict-row ${cls}" data-link-id="${esc(link.id)}">
      <div class="verdict-head">
        <span class="verdict-badge ${cls}">${esc((link.verdict || "").replace(/_/g, " "))}</span>
        <span class="pair">${pair}</span>
        <span class="source-tag ${esc(link.verdict_source)}">${esc(link.verdict_source || "unknown")}</span>
        ${link.review ? `<span class="source-tag human" title="A human reviewed this verdict">human-reviewed</span>` : ""}
        ${sim}
        <button type="button" class="small receipts-toggle" data-receipts="${esc(link.id)}"
          aria-expanded="${open}" aria-controls="${drawerId}">Receipts ${open ? "▴" : "▾"}</button>
      </div>
      ${link.rationale ? `<p class="rationale">${esc(link.rationale)}</p>` : ""}
      <div class="receipts-drawer" id="${drawerId}" ${open ? "" : "hidden"}>${open ? evidenceHtml(link) : ""}</div>
      ${reviewUi.controlsHtml(link)}
    </div>`;
}

function clusterCard(c, langs) {
  const links = gradedLinks(c);
  const reviewed = links.filter((l) => l.review).length;
  const kinds = [...new Set((c.claims || []).map((cl) => cl.kind).filter(Boolean))];
  const missing = langs.filter((l) => !(c.claims || []).some((cl) => cl.lang === l));
  return `<article class="cluster ${esc(c.status)}${c.key === state.focusedKey ? " is-focused" : ""}"
      data-key="${esc(c.key)}" tabindex="-1" aria-label="${esc(STATUS[c.status]?.label || c.status)} cluster">
      <header>
        <span class="badge ${esc(c.status)}">${esc(STATUS[c.status]?.label || c.status)}</span>
        ${kinds.map((k) => `<span class="kind-tag">${esc(k.replace(/_/g, " "))}</span>`).join("")}
        ${c.status === "gap" && missing.length ? `<span class="muted">missing in ${missing.map(esc).join(", ")}</span>` : ""}
        ${links.length ? `<span class="muted cluster-progress">${reviewed} / ${links.length} reviewed</span>` : ""}
      </header>
      <div class="diff" style="--cols:${langs.length}">${diffHtml(c, langs)}</div>
      ${links.length ? `<div class="verdicts">${links.map(verdictRow).join("")}</div>` : ""}
    </article>`;
}

function renderClusters() {
  const d = state.data;
  const host = $("#clusters");
  host.removeAttribute("aria-busy");
  const langs = langsOf(d);
  const all = d.clusters || [];
  const shown = all.filter(passes);
  if (!(d.articles || []).length) {
    host.innerHTML = `<div class="empty-state"><p><b>${esc(state.qid)} isn't tracked yet.</b></p>
      <p class="muted">None of its fi / sv / en editions have been imported.</p>
      <a href="#/" class="button-link">Back to search</a></div>`;
    return;
  }
  if (!all.length) {
    host.innerHTML = `<div class="empty-state"><p><b>No claims extracted for this article yet.</b></p>
      <p class="muted">Import the article editions, then re-check the page.</p></div>`;
    return;
  }
  if (!shown.length) {
    host.innerHTML = `<div class="empty-state"><p>No clusters match the current filters.</p>
      <button type="button" data-action="clear-filters">Clear filters</button></div>`;
    return;
  }
  host.innerHTML = `<div class="diff-legend" aria-hidden="true" style="--cols:${langs.length}">
      ${langs.map((l) => `<span><span class="lang-tag">${esc(l)}</span> ${esc(LANG_NAMES[l] || l)}</span>`).join("")}
    </div>` + shown.map((c) => clusterCard(c, langs)).join("");
  if (state.focusedKey && !shown.some((c) => c.key === state.focusedKey)) state.focusedKey = null;
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
  if (!state.qid || state.rechecking) return;
  const qid = state.qid;
  state.rechecking = true;
  if (state.data) renderHeader();
  try {
    const result = await api.recheck(qid);
    emit("recheck:done", { qid, result, reason: "manual" });
  } catch (err) {
    toast(`Re-check failed: ${err.message}`, "error", 6000);
  } finally {
    state.rechecking = false;
    if (state.data && state.qid === qid) renderHeader();
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
    return `<span class="diff-count ${k}">${STATUS[k].plural}: <b>${esc(x)}</b> → <b>${esc(y)}</b> ${delta}</span>`;
  }).join("");
  const links = result.links || [];
  const anyResurfaced = links.some((l) => l.resurfaced);
  const items = links.map((l) => {
    const o = linkOutcome(l);
    const entry = state.linkIndex.get(Number(l.id));
    const a = entry && state.claimIndex.get(Number(entry.link.claim_a));
    const bb = entry && state.claimIndex.get(Number(entry.link.claim_b));
    const pair = a && bb ? `${esc(a.lang)} ↔ ${esc(bb.lang)}` : "";
    const snippet = a ? esc((a.statement_text || "").slice(0, 90)) + ((a.statement_text || "").length > 90 ? "…" : "") : "";
    return `<li class="outcome ${o.cls}">
        <b>${o.text}</b>
        <span class="muted">link #${esc(l.id)} ${pair} · ${esc(l.before ?? "new")} → ${esc(l.after)}</span>
        ${snippet ? `<span class="snippet">“${snippet}”</span>` : ""}
        ${entry ? `<button type="button" class="link-btn" data-goto-cluster="${esc(entry.cluster.key)}">Show</button>` : ""}
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
      A conflict keeps resurfacing until the fix is applied on Wikipedia and the article is re-imported.</p>` : ""}`;
  b.hidden = false;
}

function highlightLinks(linkIds) {
  const outcomes = new Map((linkIds || []).map((l) => [Number(l.id ?? l), l]));
  const keys = new Map();
  for (const [id, l] of outcomes) {
    const entry = state.linkIndex.get(id);
    if (!entry) continue;
    const cls = typeof l === "object" ? linkOutcome(l).cls : "neutral";
    keys.set(entry.cluster.key, keys.get(entry.cluster.key) === "bad" ? "bad" : cls);
  }
  for (const [key, cls] of keys) {
    const card = $(`#clusters .cluster[data-key="${CSS.escape(key)}"]`);
    if (!card) continue;
    card.classList.remove("flash", "flash-bad", "flash-ok", "flash-warn", "flash-neutral");
    void card.offsetWidth; // restart animation
    card.classList.add("flash", `flash-${cls}`);
    setTimeout(() => card.classList.remove("flash", `flash-${cls}`), 2600);
  }
}

function cacheResult(qid, result, reason) {
  try {
    sessionStorage.setItem(RECHECK_KEY(qid), JSON.stringify({ result, reason, savedAt: new Date().toISOString() }));
  } catch { /* storage full or disabled */ }
}

function showCachedBanner(qid) {
  try {
    const raw = sessionStorage.getItem(RECHECK_KEY(qid));
    if (!raw) return;
    const { result, reason, savedAt } = JSON.parse(raw);
    renderBanner(result, { reason, savedAt, cached: true });
  } catch { /* corrupt cache: ignore */ }
}

async function onRecheckDone({ qid, result, reason, linkIds } = {}) {
  if (!qid) return;
  if (result) cacheResult(qid, result, reason);
  if (qid !== state.qid) return;
  const changed = [...(result?.links || [])];
  for (const id of linkIds || []) if (!changed.some((l) => Number(l.id) === Number(id))) changed.push(id);
  await reload({ highlight: changed });
  if (result) {
    renderBanner(result, { reason });
    if (reason === "manual") $("#recheck-banner").scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
  }
}

/* ---------------- init ---------------- */

export function init() {
  reviewUi.bind({ getLink, getQid: () => state.qid });

  on("recheck:done", onRecheckDone);
  on("merge:reload", ({ qid } = {}) => { if (!qid || qid === state.qid) reload(); });

  const view = $("#merge-view");
  view.addEventListener("click", (e) => {
    const t = e.target;
    const action = t.closest("[data-action]")?.dataset.action;
    if (action === "retry") { const q = state.qid; state.qid = null; open(q); return; }
    if (action === "recheck") { recheckNow(); return; }
    if (action === "dismiss-banner") { hideBanner(); sessionStorage.removeItem(RECHECK_KEY(state.qid)); return; }
    if (action === "clear-filters") {
      state.filters = { statuses: new Set(), kinds: new Set(), text: "", unreviewed: false };
      persistFilters(); renderSummary(); renderFilters(); renderClusters(); return;
    }
    const status = t.closest("[data-status]")?.dataset.status;
    if (status && t.closest("#summary-bar, #filters")) { toggleStatus(status); return; }
    const kind = t.closest("[data-kind]")?.dataset.kind;
    if (kind) {
      const k = state.filters.kinds;
      k.has(kind) ? k.delete(kind) : k.add(kind);
      renderFilters(); renderClusters(); return;
    }
    const hist = t.closest("[data-history]");
    if (hist) { emit("history:open", { claimId: Number(hist.dataset.history) }); return; }
    const rec = t.closest("[data-receipts]");
    if (rec) { toggleDrawer(rec.dataset.receipts); return; }
    const go = t.closest("[data-goto-cluster]");
    if (go) {
      const key = go.dataset.gotoCluster;
      if (!$(`#clusters .cluster[data-key="${CSS.escape(key)}"]`)) {
        state.filters = { statuses: new Set(), kinds: new Set(), text: "", unreviewed: false };
        persistFilters(); renderAll();
      }
      focusCluster(key);
      return;
    }
    // clicking anywhere on a card (but not on a control) focuses it
    const card = t.closest("#clusters .cluster[data-key]");
    if (card && !t.closest("a, button, input, textarea, label, form")) focusCluster(card.dataset.key, { scroll: false });
  });

  view.addEventListener("input", (e) => {
    if (e.target.id === "filter-text") {
      state.filters.text = e.target.value;
      renderFilters(); // keeps focus + caret in the text box
      renderClusters();
    }
  });
  view.addEventListener("change", (e) => {
    if (e.target.id === "filter-unreviewed") {
      state.filters.unreviewed = e.target.checked;
      renderFilters(); renderClusters();
    }
  });
}
