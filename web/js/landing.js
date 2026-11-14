/* Landing page: the "Pages with conflicts" list from /api/pages/conflicted.
 * Each row is one page (title + language) with its counterpart pages and the
 * per-status counts relative to that page. Renders into #conflicted-pages. */
import { api } from "./api.js";
import { $, esc, sortByLang } from "./util.js";
import { pageHref } from "./router.js";
import { counterpartChips } from "./search.js";

const LIMIT = 50;
let loaded = false;
let loading = false;

const host = () => $("#conflicted-pages");

function count(n, cls, label) {
  n = Number(n) || 0;
  return `<span class="lp-count ${cls}${n ? "" : " zero"}" title="${esc(label)}"><b>${esc(n)}</b> ${esc(label)}</span>`;
}

function render(rows) {
  const list = (rows || []).filter((r) => r && r.page);
  if (!list.length) {
    host().innerHTML = `<p class="muted lp-state">No compared pages yet. Search for a page above to compare it
      with its counterparts.</p>`;
    return;
  }
  host().innerHTML = `<ul class="lp-list">${list.map((r) => {
    const cps = sortByLang(r.counterparts || []);
    return `<li><a class="lp-row${Number(r.conflicts) ? " has-conflicts" : ""}" href="${esc(pageHref(r.page.lang, r.page.title))}">
        <span class="lp-title"><span class="lang-tag">${esc(r.page.lang)}</span><b>${esc(r.page.title)}</b></span>
        <span class="lp-counts">
          ${count(r.conflicts, "conflict", Number(r.conflicts) === 1 ? "conflict" : "conflicts")}
          ${count(r.missing_here, "missing_here", "missing here")}
          ${count(r.only_here, "only_here", "only here")}
          ${count(r.agree, "agree", "agree")}
        </span>
        <span class="counterparts"><span class="muted">vs.</span>
          ${counterpartChips(cps)}</span>
      </a></li>`;
  }).join("")}</ul>`;
}

export async function load({ force = false } = {}) {
  if (!host() || loading || (loaded && !force)) return;
  loading = true;
  host().innerHTML = `<p class="muted lp-state" role="status"><span class="spinner" aria-hidden="true"></span> Loading pages…</p>`;
  try {
    render(await api.conflictedPages(LIMIT));
    loaded = true;
  } catch (err) {
    host().innerHTML = `<div class="lp-state error-state" role="alert">
        <span>Couldn't load the pages with conflicts: ${esc(err.message)}</span>
        <button type="button" class="small" data-lp-retry>Retry</button></div>`;
  } finally {
    loading = false;
  }
}

export function init() {
  host()?.addEventListener("click", (e) => {
    if (e.target.closest("[data-lp-retry]")) load({ force: true });
  });
}
