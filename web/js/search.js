/* Live article search: debounced as-you-type (min 2 chars), keyboard selectable
 * results (ArrowUp/ArrowDown/Enter, Esc closes), loading / empty / error states. */
import { api } from "./api.js";
import { $, $$, esc, debounce, LANGS } from "./util.js";
import { openMerge } from "./router.js";

const MIN_CHARS = 2;
const QID_RE = /^Q\d+$/i;

let seq = 0;          // ignore out-of-order responses
let items = [];       // grouped results currently shown
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

/** Group per-language article rows into one result per Wikidata entity. */
function group(rows) {
  const byQid = new Map();
  for (const r of rows) {
    const key = r.wikidata_qid || `article-${r.id}`;
    if (!byQid.has(key)) byQid.set(key, { qid: r.wikidata_qid, editions: [] });
    byQid.get(key).editions.push(r);
  }
  const order = (l) => { const i = LANGS.indexOf(l); return i < 0 ? 99 : i; };
  for (const g of byQid.values()) g.editions.sort((a, b) => order(a.lang) - order(b.lang));
  return [...byQid.values()].filter((g) => g.qid);
}

function renderState(html) {
  items = [];
  list().innerHTML = html;
  setOpen(true);
}

function renderResults(groups) {
  items = groups;
  if (!groups.length) {
    renderState(`<p class="results-state muted">No tracked articles match “${esc(lastQuery)}”.
      Only imported articles can be searched.</p>`);
    return;
  }
  list().innerHTML = groups.map((g, i) => {
    const main = g.editions.find((e) => e.lang === "en") || g.editions[0];
    const editions = g.editions.map((e) =>
      `<span class="ed"><span class="lang-tag">${esc(e.lang)}</span>${esc(e.title)}</span>`).join("");
    return `<div class="result" role="option" id="result-${i}" data-index="${i}" aria-selected="false">
        <span class="result-main"><b>${esc(main.title)}</b><span class="editions">${editions}</span></span>
        <span class="muted qid">${esc(g.qid)}</span>
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
    const rows = await api.searchArticles(q);
    if (my !== seq) return;
    renderResults(group(rows || []));
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
  const g = items[i];
  if (!g) return;
  close();
  input().blur();
  openMerge(g.qid);
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
    if (QID_RE.test(q)) { close(); inp.blur(); return openMerge(q.toUpperCase()); }
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
