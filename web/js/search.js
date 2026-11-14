/* Live page title search: debounced as-you-type (min 2 chars), keyboard selectable
 * results (ArrowUp/ArrowDown/Enter, Esc closes), loading / empty / error states.
 * One result per page (title + language) with its counterpart pages as chips. */
import { api } from "./api.js";
import { $, $$, esc, debounce, sortByLang } from "./util.js";
import { openPage } from "./router.js";

const MIN_CHARS = 2;

let seq = 0;          // ignore out-of-order responses
let items = [];       // page results currently shown
let active = -1;      // highlighted option index
let lastQuery = "";

const input = () => $("#search-input");
const list = () => $("#results");

function setOpen(open) {
  const box = list();
  box.hidden = !open;
  input().setAttribute("aria-expanded", String(open));
  if (!open) setActive(-1);
}

function setActive(i) {
  const opts = $$(".result", list());
  active = opts.length ? Math.max(-1, Math.min(i, opts.length - 1)) : -1;
  opts.forEach((o, idx) => {
    o.classList.toggle("active", idx === active);
    o.setAttribute("aria-selected", String(idx === active));
  });
  const inp = input();
  if (active >= 0) {
    inp.setAttribute("aria-activedescendant", opts[active].id);
    opts[active].scrollIntoView({ block: "nearest" });
  } else {
    inp.removeAttribute("aria-activedescendant");
  }
}

/** Normalise API rows ({page, counterparts, conflicts}) and drop pages without counterparts. */
function normalise(rows) {
  return (rows || [])
    .filter((r) => r && r.page && (r.counterparts || []).length)
    .map((r) => ({ ...r, counterparts: sortByLang(r.counterparts || []) }));
}

/** Counterpart chips, shared with the landing list. */
export function counterpartChips(counterparts) {
  return (counterparts || []).map((c) =>
    `<span class="cp-chip"><span class="lang-tag">${esc(c.lang)}</span>${esc(c.title)}</span>`).join("");
}

function renderState(html) {
  items = [];
  list().innerHTML = html;
  setOpen(true);
}

function renderResults(rows) {
  items = rows;
  if (!rows.length) {
    renderState(`<p class="results-state muted">No pages match “${esc(lastQuery)}”.
      Only imported pages that have a counterpart in another language can be compared.</p>`);
    return;
  }
  list().innerHTML = rows.map((r, i) => {
    const n = Number(r.conflicts) || 0;
    return `<div class="result" role="option" id="result-${i}" data-index="${i}" aria-selected="false">
        <span class="result-main">
          <span><span class="lang-tag">${esc(r.page.lang)}</span><b>${esc(r.page.title)}</b></span>
          <span class="counterparts"><span class="muted">vs.</span> ${counterpartChips(r.counterparts)}</span>
        </span>
        <span class="result-count ${n ? "has-conflicts" : ""}">${n ? `${esc(n)} conflict${n === 1 ? "" : "s"}` : "no conflicts"}</span>
      </div>`;
  }).join("");
  setOpen(true);
  setActive(0);
}

async function runSearch(q) {
  lastQuery = q;
  const my = ++seq;
  renderState(`<p class="results-state muted"><span class="spinner" aria-hidden="true"></span> Searching…</p>`);
  try {
    const rows = await api.searchPages(q);
    if (my !== seq) return;
    renderResults(normalise(rows));
  } catch (err) {
    if (my !== seq) return;
    renderState(`<div class="results-state error-state" role="alert">
        <span>Search failed: ${esc(err.message)}</span>
        <button type="button" class="retry" data-retry>Retry</button>
      </div>`);
  }
}

const debounced = debounce((q) => runSearch(q), 250);

function onInput() {
  const q = input().value.trim();
  if (q.length < MIN_CHARS) {
    seq++; // cancel pending
    items = [];
    list().innerHTML = q.length
      ? `<p class="results-state muted">Type at least ${MIN_CHARS} characters…</p>` : "";
    setOpen(Boolean(q.length));
    return;
  }
  debounced(q);
}

function choose(i) {
  const r = items[i];
  if (!r) return;
  close();
  input().blur();
  openPage(r.page.lang, r.page.title);
}

export function close() { setOpen(false); }

export function focusSearch() {
  const inp = input();
  inp.focus();
  inp.select();
  if (list().innerHTML.trim()) setOpen(true);
}

export function init() {
  const inp = input();
  const box = list();
  inp.setAttribute("role", "combobox");
  inp.setAttribute("aria-autocomplete", "list");
  inp.setAttribute("aria-controls", "results");
  inp.setAttribute("aria-expanded", "false");
  box.hidden = true;

  inp.addEventListener("input", onInput);
  inp.addEventListener("focus", () => { if (box.innerHTML.trim()) setOpen(true); });
  inp.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (box.hidden) setOpen(true); setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    else if (e.key === "Escape") {
      if (!box.hidden) { e.preventDefault(); e.stopPropagation(); close(); }
      else inp.blur();
    }
  });

  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = inp.value.trim();
    if (active >= 0 && items[active]) return choose(active);
    if (items.length === 1) return choose(0);
    if (q.length >= MIN_CHARS) runSearch(q);
  });

  // mousedown (not click) so the input's blur doesn't close the list first
  box.addEventListener("mousedown", (e) => {
    const opt = e.target.closest(".result");
    if (opt) { e.preventDefault(); choose(Number(opt.dataset.index)); }
    if (e.target.closest("[data-retry]")) { e.preventDefault(); runSearch(lastQuery); }
  });
  box.addEventListener("mousemove", (e) => {
    const opt = e.target.closest(".result");
    if (opt && Number(opt.dataset.index) !== active) setActive(Number(opt.dataset.index));
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#search-form")) close();
  });
}
