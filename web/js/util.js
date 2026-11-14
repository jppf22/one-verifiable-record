/* Shared DOM + misc helpers. */
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** Build one element from an HTML string. Always esc() interpolated data! */
export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function esc(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export const LANGS = ["fi", "sv", "en"];
export const LANG_NAMES = { fi: "Finnish", sv: "Swedish", en: "English" };

/** Permanent link to an exact imported revision. Never fall back to live Wikipedia. */
export function wikiUrl(lang, revisionId, title) {
  if (revisionId) return `https://${lang}.wikipedia.org/w/index.php?oldid=${encodeURIComponent(revisionId)}`;
  return null;
}

/** Transient notification. kind: info | ok | warn | error */
export function toast(message, kind = "info", ms = 4000) {
  const host = $("#toasts");
  if (!host) return;
  const t = el(`<div class="toast ${esc(kind)}" role="status">${esc(message)}</div>`);
  host.appendChild(t);
  setTimeout(() => t.classList.add("leaving"), ms);
  setTimeout(() => t.remove(), ms + 400);
}

const REVIEWER_KEY = "ovr.reviewer";
export function getReviewer() { return localStorage.getItem(REVIEWER_KEY) || ""; }
export function setReviewer(name) {
  localStorage.setItem(REVIEWER_KEY, name.trim());
  document.dispatchEvent(new CustomEvent("reviewer:changed", { detail: name.trim() }));
}
/** Return the reviewer name, prompting once if unset (user-confirmed decision #1). */
export function ensureReviewer() {
  let name = getReviewer();
  if (!name) {
    name = (prompt("Your display name for reviews (stored only in this browser):") || "").trim();
    if (name) setReviewer(name);
  }
  return name || null;
}

export function fmtTime(ts) {
  if (!ts) return "now";
  const d = new Date(String(ts).replace(" ", "T"));
  if (isNaN(d) || d.getUTCFullYear() >= 2038) return "now"; // SVT open-ended ROW_END
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function debounce(fn, ms = 250) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** Sort key for language codes: fi, sv, en first, then alphabetical. */
export function langRank(l) { const i = LANGS.indexOf(l); return i < 0 ? LANGS.length : i; }
export function sortByLang(items, get = (x) => x.lang) {
  return [...items].sort((a, b) => langRank(get(a)) - langRank(get(b)) || String(get(a)).localeCompare(String(get(b))));
}

/** Only allow http(s) links from the API into href attributes. */
export function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : null; }

/** Permanent link to the page's imported revision, if the snapshot recorded one. */
export function pageRevUrl(p) {
  if (!p?.revision_id) return null;
  return safeUrl(p.url) || safeUrl(wikiUrl(p.lang, p.revision_id, p.title));
}
