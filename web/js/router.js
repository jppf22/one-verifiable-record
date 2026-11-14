/* Hash router: "#/" (landing: search + pages with conflicts) and
 * "#/page/{lang}/{title}[?status=conflict,missing_here]" (comparison view of one page
 * against its counterpart pages). Titles are URL-encoded with spaces as "_".
 * Legacy links ("/?qid=…", "#/merge/…") are redirected to "#/". */

const listeners = new Set();
let current = null;

const LANG_RE = /^[a-z][a-z-]{1,11}$/;

function decodeTitle(s) {
  try { return decodeURIComponent(s).replace(/_/g, " ").trim(); } catch { return ""; }
}
export function encodeTitle(title) {
  return encodeURIComponent(String(title).trim().replace(/ /g, "_"));
}

/** Parse location.hash into {name: "landing"|"page", lang?, title?, query}. */
export function parse(hash = location.hash) {
  const raw = hash.replace(/^#/, "") || "/";
  const qi = raw.indexOf("?");
  const path = qi < 0 ? raw : raw.slice(0, qi);
  const query = new URLSearchParams(qi < 0 ? "" : raw.slice(qi + 1));
  const m = path.match(/^\/page\/([^/]+)\/(.+?)\/?$/);
  if (m) {
    const lang = decodeTitle(m[1]).toLowerCase();
    const title = decodeTitle(m[2]);
    if (LANG_RE.test(lang) && title) return { name: "page", lang, title, key: `${lang}:${title}`, query };
  }
  return { name: "landing", query };
}

export function pageHref(lang, title, query) {
  const qs = query && [...query].length ? `?${query}` : "";
  return `#/page/${encodeURIComponent(lang)}/${encodeTitle(title)}${qs}`;
}

/** Navigate (adds a history entry, fires the route listeners). */
export function go(hash) {
  if (location.hash === hash) return;
  location.hash = hash;
}

export function openPage(lang, title) { go(pageHref(lang, title)); }
export function openLanding() { go("#/"); }

/** Update the query part of the current route WITHOUT a new history entry or re-route
 * (used to persist filters like ?status= in the URL). */
export function replaceQuery(params) {
  const r = parse();
  const query = new URLSearchParams(r.query);
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === "" || (Array.isArray(v) && !v.length)) query.delete(k);
    else query.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  const base = r.name === "page" ? pageHref(r.lang, r.title) : "#/";
  const qs = [...query].length ? `?${query}` : "";
  history.replaceState(history.state, "", `${location.pathname}${base}${qs}`);
  current = parse();
}

export function currentRoute() { return current || parse(); }

/** Subscribe to route changes: fn(route, previousRoute). */
export function onRoute(fn) { listeners.add(fn); return () => listeners.delete(fn); }

function dispatch() {
  const prev = current;
  current = parse();
  for (const fn of listeners) {
    try { fn(current, prev); } catch (err) { console.error("[router] listener failed", err); }
  }
}

export function init() {
  // Legacy deep links (/?qid=…, #/merge/…) no longer map to a page: send them to the landing.
  const legacyHash = /^#\/merge\//.test(location.hash);
  if (location.search || legacyHash) {
    history.replaceState(null, "", `${location.pathname}${legacyHash || !location.hash ? "#/" : location.hash}`);
  }
  window.addEventListener("hashchange", () => {
    if (/^#\/merge\//.test(location.hash)) { history.replaceState(null, "", `${location.pathname}#/`); }
    dispatch();
  });
}

/** Fire the initial route once every module is initialised. */
export function start() { dispatch(); }
