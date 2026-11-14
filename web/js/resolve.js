/* "Fix it in every language": shown on a conflict that reviewers confirmed (policy R1:
 * verdict contradicted, source human). A reviewer writes the correct version once, in
 * English, with a published source. POST /api/links/{id}/resolution stores it
 * (SYSTEM VERSIONED `resolutions`) and drafts it for every page of the comparison set,
 * machine-translated for fi/sv. Each draft is copied into Wikipedia's editor by a human:
 * nothing is ever submitted to Wikipedia from here.
 * Bus: emits suggestions:added {pageId, drafts} and recheck:done (after "Mark exported"). */
import { api } from "./api.js";
import { $, esc, toast, ensureReviewer, fmtTime, safeUrl, LANG_NAMES } from "./util.js";
import { emit } from "./bus.js";
import { editLink, copy } from "./suggestions.js";

let ctx = { getLink: () => null, getPageId: () => null, contextFor: () => ({ claims: [], langs: [] }) };
const cache = new Map();   // link id -> {resolution, drafts}
const openForms = new Set();
const busy = new Set();

export function bind(context) { ctx = { ...ctx, ...context }; }

export function eligible(link) {
  return link?.verdict === "contradicted" && link?.verdict_source === "human";
}

const langList = (langs) => langs.map((l) => `<span class="lang-tag">${esc(l)}</span>`).join(" ");

function currentlyHtml(claims) {
  return `<ul class="resolve-current">${claims.map((cl) =>
    `<li><span class="lang-tag">${esc(cl.lang)}</span><span lang="${esc(cl.lang)}">${esc(cl.statement_text)}</span></li>`).join("")}</ul>`;
}

function formHtml(link, { claims, langs }) {
  const prev = cache.get(Number(link.id))?.resolution || link.resolution;
  const en = claims.find((c) => c.lang === "en");
  const text = prev?.correct_text || en?.statement_text || "";
  const id = esc(link.id);
  return `<form class="resolve-form" data-link-id="${id}">
      <p class="muted">The pages currently say:</p>
      ${currentlyHtml(claims)}
      <label for="resolve-text-${id}"><b>What it should say</b> <span class="muted">(in English, one sentence)</span></label>
      <textarea id="resolve-text-${id}" name="correct_text" rows="2" minlength="10" maxlength="2000" required>${esc(text)}</textarea>
      <label for="resolve-src-${id}"><b>Source</b> <span class="muted">(a published source; another Wikipedia is not a source)</span></label>
      <input id="resolve-src-${id}" name="source_url" type="url" inputmode="url"
        placeholder="https://…" value="${esc(prev?.source_url || "")}">
      <div class="fix-actions">
        <button type="submit" class="primary">Draft it for ${esc(langs.join(", "))}</button>
        <button type="button" data-resolve-cancel="${id}">Cancel</button>
        <span class="muted">Ctrl+Enter submits · Esc cancels</span>
      </div>
    </form>`;
}

function draftHtml(d) {
  const url = editLink(d);
  const done = d.status && d.status !== "draft";
  return `<li class="resolve-draft${done ? " is-exported" : ""}" data-draft-id="${esc(d.id)}">
      <div class="resolve-draft-head">
        <span class="lang-tag">${esc(d.lang)}</span><b>${esc(d.title)}</b>
        <span class="muted">${esc(LANG_NAMES[d.lang] || d.lang)}</span>
        ${d.untranslated ? `<span class="sg-chip warn" title="The translation service was unavailable: the English text is in the draft">translate before use</span>` : ""}
        <span class="sg-chip status-${esc(d.status || "draft")}">${esc(d.status || "draft")}</span>
      </div>
      <pre class="resolve-pre">${esc(d.wikitext)}</pre>
      <div class="resolve-buttons">
        <button type="button" data-resolve-copy="${esc(d.id)}">Copy</button>
        ${url ? `<a class="button-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer"
            title="Opens Wikipedia's editor with an edit summary filled in. Paste the draft, preview, save. Nothing is submitted for you.">Open edit page ↗</a>` : ""}
        <button type="button" data-resolve-export="${esc(d.id)}" ${done ? "disabled" : ""}>${done ? "Exported ✓" : "Mark exported"}</button>
      </div>
    </li>`;
}

function resolvedHtml(link, r, loaded) {
  const src = safeUrl(r.source_url);
  return `<div class="resolve-summary">
      <p>Correct version by <b>${esc(r.author)}</b> <span class="muted">${esc(fmtTime(r.created_at))}</span></p>
      <blockquote lang="en">${esc(r.correct_text)}</blockquote>
      <p class="muted">${src ? `Source: <a href="${esc(src)}" target="_blank" rel="noopener">${esc(src)}</a>` : `<span class="warn-text">No source given: add one in Wikipedia's editor before saving.</span>`}</p>
    </div>
    ${loaded
      ? (loaded.drafts.length ? `<ol class="resolve-drafts">${loaded.drafts.map(draftHtml).join("")}</ol>`
        : `<p class="muted">No drafts: every page already says this.</p>`)
      : `<button type="button" data-resolve-load="${esc(link.id)}">Show the ${esc(r.drafts ?? "")} language draft${r.drafts === 1 ? "" : "s"}</button>`}
    <button type="button" class="link-btn" data-resolve-open="${esc(link.id)}">Rewrite the correct version</button>`;
}

/** The panel for one verdict row ("" unless reviewers confirmed the conflict). */
export function html(link, context = ctx.contextFor(link)) {
  if (!eligible(link)) return "";
  const id = Number(link.id);
  const loaded = cache.get(id);
  const r = loaded?.resolution || link.resolution;
  const body = openForms.has(id)
    ? formHtml(link, context)
    : r ? resolvedHtml(link, r, loaded)
      : `<p>Reviewers confirmed this conflict. Write what the pages should say, once, in English.
           We draft the fix for ${langList(context.langs)} (machine-translated where needed), and you paste
           each draft into Wikipedia yourself.</p>
         <button type="button" class="primary" data-resolve-open="${esc(id)}">Write the correct version</button>`;
  return `<section class="resolve" data-resolve-for="${esc(id)}" aria-label="Fix this conflict in every language">
      <h4>Fix it in every language</h4>${body}
    </section>`;
}

function rerender(linkId) {
  const host = $(`.resolve[data-resolve-for="${CSS.escape(String(linkId))}"]`);
  const link = ctx.getLink(linkId);
  if (host && link) host.outerHTML = html(link);
}

async function load(linkId) {
  try {
    const res = await api.resolution(linkId);
    cache.set(Number(linkId), res);
    rerender(linkId);
  } catch (err) { toast(`Couldn't load the drafts: ${err.message}`, "error"); }
}

async function submit(form) {
  const linkId = Number(form.dataset.linkId);
  if (busy.has(linkId)) return;
  const author = ensureReviewer();
  if (!author) { toast("A reviewer name is needed.", "warn"); return; }
  const correct_text = form.querySelector("[name=correct_text]").value.trim();
  const source_url = form.querySelector("[name=source_url]").value.trim();
  if (correct_text.length < 10) { toast("Write the full correct sentence (at least 10 characters).", "warn"); return; }
  // Don't re-render while busy: that would reset what the reviewer typed.
  busy.add(linkId);
  const btn = form.querySelector("button[type=submit]");
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" aria-hidden="true"></span> Drafting and translating…`;
  let ok = false;
  try {
    const res = await api.createResolution(linkId, { author, correct_text, source_url: source_url || undefined });
    cache.set(linkId, res);
    openForms.delete(linkId);
    const link = ctx.getLink(linkId);
    if (link) link.resolution = { ...res.resolution, drafts: res.drafts.length };
    const untranslated = res.drafts.filter((d) => d.untranslated).length;
    toast(`${res.drafts.length} draft${res.drafts.length === 1 ? "" : "s"} ready${untranslated ? ` (${untranslated} need translating by hand)` : ""}. Paste each one into Wikipedia.`, "ok", 6000);
    emit("suggestions:added", { pageId: ctx.getPageId(), drafts: res.drafts });
    ok = true;
  } catch (err) {
    toast(`Couldn't draft the fix: ${err.message}`, "error", 7000);
  } finally {
    busy.delete(linkId);
    if (ok) rerender(linkId);
    else { btn.disabled = false; btn.innerHTML = label; }
  }
}

function findDraft(id) {
  for (const v of cache.values()) {
    const d = v.drafts.find((x) => String(x.id) === String(id));
    if (d) return d;
  }
  return null;
}

async function exportDraft(btn, id) {
  const d = findDraft(id);
  if (!d) return;
  btn.disabled = true;
  btn.textContent = "Exporting + re-checking…";
  try {
    const res = await api.exportSuggestion(d.id);
    d.status = "exported";
    toast(`Draft #${d.id} (${d.lang}) marked exported`, "ok");
    if (res?.recheck) emit("recheck:done", { pageId: ctx.getPageId(), result: res.recheck, reason: "export" });
    else rerender(d.claim_link_id);
  } catch (err) {
    toast(`Export failed: ${err.message}`, "error");
    btn.disabled = false;
    btn.textContent = "Mark exported";
  }
}

export function init() {
  const host = $("#rows");
  host.addEventListener("click", (e) => {
    const t = e.target;
    const open = t.closest("[data-resolve-open]");
    if (open) {
      const id = Number(open.dataset.resolveOpen);
      openForms.add(id);
      rerender(id);
      $(`.resolve[data-resolve-for="${id}"] textarea`)?.focus();
      return;
    }
    const cancel = t.closest("[data-resolve-cancel]");
    if (cancel) { const id = Number(cancel.dataset.resolveCancel); openForms.delete(id); rerender(id); return; }
    const ld = t.closest("[data-resolve-load]");
    if (ld) { ld.disabled = true; load(ld.dataset.resolveLoad); return; }
    const cp = t.closest("[data-resolve-copy]");
    if (cp) { copy(findDraft(cp.dataset.resolveCopy)?.wikitext || ""); return; }
    const ex = t.closest("[data-resolve-export]");
    if (ex) exportDraft(ex, ex.dataset.resolveExport);
  });
  host.addEventListener("submit", (e) => {
    const form = e.target.closest(".resolve-form");
    if (!form) return;
    e.preventDefault();
    submit(form);
  });
  host.addEventListener("keydown", (e) => {
    const form = e.target.closest(".resolve-form");
    if (!form) return;
    if (e.key === "Escape") { e.stopPropagation(); openForms.delete(Number(form.dataset.linkId)); rerender(form.dataset.linkId); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
  });
}
