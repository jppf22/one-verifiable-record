/* Patch suggestions: export-only wikitext drafts for human editors.
 * Owner: panels. Renders into #suggestions on bus `page:loaded` (drafts for this page and
 * its counterpart pages, from GET /api/pages/{id}/suggestions).
 * We NEVER edit Wikipedia: the only outbound action is a link a human clicks. */
import { api } from "./api.js";
import { on, emit } from "./bus.js";
import { $, esc, toast, LANGS, LANG_NAMES } from "./util.js";

const TYPE_LABEL = { wikitext: "Page text", citation: "Citation", talk_page: "Talk page" };
const TYPE_ORDER = ["wikitext", "citation", "talk_page"];
const LONG_CHARS = 420;
const LONG_LINES = 8;

let root;
let state = null; // { pageId, page, titles: {lang: title}, drafts: [], loading, error, notes: {id: html} }
let seq = 0;

export function init() {
  root = $("#suggestions");
  if (!root) return;
  root.classList.add("sg");
  root.addEventListener("click", onClick);
  on("page:loaded", ({ pageId, data } = {}) => {
    const titles = {};
    for (const p of [data?.page, ...(data?.counterparts || [])]) if (p) titles[p.lang] = p.title;
    const page = data?.page || null;
    // Same page reloaded (e.g. after a re-check): keep the drafts already shown.
    if (state && state.pageId === pageId) { state.titles = titles; state.page = page; render(); return; }
    seq++;
    state = { pageId, page, titles, drafts: [], loaded: false, loading: false, error: null, notes: {}, expanded: new Set() };
    render();
  });
}

/* ---------- rendering ---------- */

function render() {
  if (!state) { root.innerHTML = ""; return; }
  const { drafts, loading, error, loaded } = state;
  const count = drafts.length;
  const exported = drafts.filter((d) => d.status !== "draft").length;
  root.innerHTML = `
    <div class="sg-head">
      <h2 id="sg-title">Patch suggestions ${loaded ? `<span class="muted">(${count}${count ? `, ${exported} exported` : ""})</span>` : ""}</h2>
      <div class="sg-actions">
        <button type="button" class="sg-generate primary" ${loading ? "disabled aria-busy=\"true\"" : ""}>
          ${loading ? "Generating…" : loaded ? "Regenerate drafts" : "Generate drafts"}</button>
        ${count ? `<button type="button" class="sg-download-all">Download all (.wiki)</button>` : ""}
      </div>
    </div>
    <p class="sg-notice" role="note"><b>Export-only.</b> We never edit Wikipedia automatically.
      Drafts are wikitext for a human editor to review, copy and apply by hand.
      Marking a draft as exported re-checks this page against its counterparts and reports whether the conflict resurfaces.</p>
    ${loading && !count ? skeleton() : ""}
    ${error ? `<div class="sg-state error" role="alert">Could not generate drafts: ${esc(error)}
        <button type="button" class="sg-generate">Retry</button></div>` : ""}
    ${loaded && !count && !error ? `<div class="sg-state">No drafts: there are no conflicts or missing facts to patch on this page or its counterparts.</div>` : ""}
    ${count ? groups() : ""}`;
}

function skeleton() {
  return `<div class="sg-skeleton" role="status" aria-label="Generating drafts">
      <div></div><div></div><div></div></div>`;
}

function groups() {
  const byLang = new Map();
  for (const d of state.drafts) {
    if (!byLang.has(d.lang)) byLang.set(d.lang, []);
    byLang.get(d.lang).push(d);
  }
  const langs = [...byLang.keys()].sort((a, b) => rank(LANGS, a) - rank(LANGS, b));
  return langs.map((lang) => {
    const list = byLang.get(lang);
    const types = [...new Set(list.map((d) => d.patch_type))].sort((a, b) => rank(TYPE_ORDER, a) - rank(TYPE_ORDER, b));
    const isHere = state.page && state.page.lang === lang;
    return `<section class="sg-lang" aria-label="${esc(LANG_NAMES[lang] || lang)} drafts">
        <h3><span class="sg-lang-code">${esc(lang)}</span> ${isHere ? "This page" : `${esc(LANG_NAMES[lang] || "")} counterpart`}
          <span class="muted">${esc(titleFor({ lang }))} · ${list.length} draft(s)</span></h3>
        ${types.map((t) => `
          <div class="sg-type">
            <h4>${esc(TYPE_LABEL[t] || t)}</h4>
            ${list.filter((d) => d.patch_type === t).map(card).join("")}
          </div>`).join("")}
      </section>`;
  }).join("");
}

function card(d) {
  const text = String(d.wikitext ?? "");
  const long = text.length > LONG_CHARS || text.split("\n").length > LONG_LINES;
  const open = state.expanded.has(String(d.id));
  const done = d.status && d.status !== "draft";
  const editUrl = editLink(d);
  return `<article class="sg-draft${done ? " is-exported" : ""}" data-id="${esc(d.id)}">
      <header>
        <span class="sg-chip type">${esc(TYPE_LABEL[d.patch_type] || d.patch_type)}</span>
        <span class="sg-chip status-${esc(d.status || "draft")}">${esc(d.status || "draft")}</span>
        ${d.claim_link_id != null ? `<span class="muted">link #${esc(d.claim_link_id)}</span>` : ""}
        <span class="muted sg-id">draft #${esc(d.id)}</span>
      </header>
      <pre class="sg-pre${long && !open ? " collapsed" : ""}" id="sg-pre-${esc(d.id)}" tabindex="0">${esc(text)}</pre>
      ${long ? `<button type="button" class="sg-expand linkish" aria-expanded="${open}" aria-controls="sg-pre-${esc(d.id)}">${open ? "Collapse draft" : "Show full draft"}</button>` : ""}
      <div class="sg-buttons">
        <button type="button" class="sg-copy">Copy</button>
        <button type="button" class="sg-download">Download .wiki</button>
        ${editUrl ? `<a class="sg-edit button" href="${esc(editUrl)}" target="_blank" rel="noopener noreferrer"
            title="Opens Wikipedia's editor in a new tab. Nothing is submitted for you.">Open edit page on Wikipedia ↗</a>` : ""}
        <button type="button" class="sg-export${done ? "" : " primary"}" ${done ? "disabled" : ""}>
          ${done ? "Exported ✓" : "Mark exported"}</button>
      </div>
      ${state.notes[d.id] ? `<div class="sg-note" role="status">${state.notes[d.id]}</div>` : ""}
    </article>`;
}

const rank = (order, v) => { const i = order.indexOf(v); return i < 0 ? order.length : i; };
const titleFor = (d) => d.title || state.titles[d.lang] || "";

function editLink(d) {
  const title = titleFor(d);
  if (!title || !/^[a-z-]{2,12}$/.test(d.lang || "")) return null;
  const section = d.patch_type === "talk_page" ? `Talk:${title}` : title;
  return `https://${d.lang}.wikipedia.org/w/index.php?title=${encodeURIComponent(section.replace(/ /g, "_"))}&action=edit`
    + (d.patch_type === "talk_page" ? "&section=new" : "");
}

/* ---------- actions ---------- */

function onClick(e) {
  const btn = e.target.closest("button");
  if (!btn || !state) return;
  const cardEl = btn.closest(".sg-draft");
  const draft = cardEl ? state.drafts.find((d) => String(d.id) === cardEl.dataset.id) : null;

  if (btn.classList.contains("sg-generate")) generate();
  else if (btn.classList.contains("sg-download-all")) downloadAll();
  else if (!draft) return;
  else if (btn.classList.contains("sg-copy")) copy(draft.wikitext);
  else if (btn.classList.contains("sg-download")) download(fileName(draft), draft.wikitext);
  else if (btn.classList.contains("sg-export")) markExported(draft, btn);
  else if (btn.classList.contains("sg-expand")) {
    const pre = cardEl.querySelector(".sg-pre");
    const expand = pre.classList.toggle("collapsed") === false;
    btn.setAttribute("aria-expanded", String(expand));
    btn.textContent = expand ? "Collapse draft" : "Show full draft";
    if (expand) state.expanded.add(cardEl.dataset.id); else state.expanded.delete(cardEl.dataset.id);
  }
}

async function generate() {
  const my = ++seq;
  const pageId = state.pageId;
  state.loading = true; state.error = null;
  render();
  try {
    const res = await api.suggestions(pageId);
    if (my !== seq) return;
    state.drafts = res.drafts || [];
    state.loaded = true;
    if (res.created) toast(`${res.created} new draft(s) generated`, "ok");
  } catch (err) {
    if (my !== seq) return;
    state.error = err.message;
  }
  state.loading = false;
  render();
  root.querySelector(".sg-generate")?.focus();
}

async function markExported(draft, btn) {
  const my = seq;
  const pageId = state.pageId;
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  btn.textContent = draft.claim_link_id != null ? "Exporting + re-checking…" : "Exporting…";
  let res;
  try {
    res = await api.exportSuggestion(draft.id);
  } catch (err) {
    if (my !== seq) return;
    toast(`Export failed: ${err.message}`, "error");
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    btn.textContent = "Mark exported";
    return;
  }
  if (my !== seq) return;
  draft.status = "exported";
  const recheck = res?.recheck ?? null;
  state.notes[draft.id] = recheckNote(draft, recheck);
  render();
  root.querySelector(`.sg-draft[data-id="${CSS.escape(String(draft.id))}"] .sg-copy`)?.focus();
  toast(`Draft #${draft.id} marked exported`, "ok");
  // The server re-checks the draft's page; refresh the comparison we are looking at.
  if (recheck) emit("recheck:done", { pageId, result: recheck, reason: "export" });
}

function recheckNote(draft, r) {
  if (!r) return `Marked exported. This draft is not tied to a single link, so no re-check ran.`;
  const link = (r.links || []).find((l) => String(l.id) === String(draft.claim_link_id));
  if (!link) return `Re-checked the page: no change reported for link #${esc(draft.claim_link_id)}.`;
  if (link.resurfaced) {
    return `<b class="bad">Conflict resurfaces.</b> Link #${esc(link.id)} is still <code>${esc(link.after)}</code>
      in the stored revisions. Apply the patch on Wikipedia, then re-import the page.`;
  }
  if (link.changed) {
    return `<b class="ok">Verdict changed:</b> <code>${esc(link.before ?? "new link")}</code> → <code>${esc(link.after)}</code> for link #${esc(link.id)}.`;
  }
  return `Re-checked link #${esc(link.id)}: verdict <code>${esc(link.after)}</code>${link.regraded ? "" : " (not regraded: no newer source revision)"}.`;
}

/* ---------- export helpers ---------- */

function fileName(d) {
  const safe = (s) => String(s ?? "").replace(/[^A-Za-z0-9._-]+/g, "_");
  return `${safe(d.lang)}-${safe(titleFor(d))}-${safe(d.patch_type)}-${safe(d.id)}.wiki`;
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text ?? ""], { type: "text/plain;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadAll() {
  const parts = state.drafts.map((d) =>
    `<!-- ===== draft #${d.id} · ${d.lang} · ${d.patch_type} · ${titleFor(d)} · status: ${d.status || "draft"} ===== -->\n${d.wikitext ?? ""}\n`);
  const p = state.page || {};
  const header = `<!-- One Verifiable Record patch drafts for ${p.lang}:${p.title} and its counterpart pages. Export-only: review and apply by hand. -->\n\n`;
  download(`${`${p.lang || "page"}-${p.title || state.pageId}`.replace(/[^A-Za-z0-9_-]+/g, "_")}-patches.wiki`, header + parts.join("\n"));
}

async function copy(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text ?? "");
    } else {
      const ta = Object.assign(document.createElement("textarea"), { value: text ?? "" });
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;opacity:0;top:0;left:0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      if (!ok) throw new Error("execCommand failed");
    }
    toast("Draft copied to clipboard", "ok");
  } catch {
    toast("Copy failed. Select the text and copy it manually.", "warn");
  }
}
