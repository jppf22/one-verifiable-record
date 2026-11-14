/* Hash router: "#/" (article picker) and "#/merge/{QID}[?status=conflict,gap]".
 * The legacy "/?qid=Q…" link (used by the browser extension) is redirected to "#/merge/Q…". */

const listeners = new Set();
let current = null;

const QID_RE = /^Q\d+$/i;

/** Parse location.hash into {name, qid?, query: URLSearchParams}. */
export function parse(hash = location.hash) {
  const raw = hash.replace(/^#/, "") || "/";
  const [path, qs = ""] = raw.split("?");
  const query = new URLSearchParams(qs);
  const m = path.match(/^\/merge\/([^/]+)\/?$/);
  if (m) {
    const qid = decodeURIComponent(m[1]).toUpperCase();
    if (QID_RE.test(qid)) return { name: "merge", qid, query };
  }
  return { name: "picker", query };
}

export function mergeHref(qid, query) {
  const qs = query && [...query].length ? `?${query}` : "";
  return `#/merge/${encodeURIComponent(qid)}${qs}`;
}

/** Navigate (adds a history entry, fires the route listeners). */
export function go(hash) {
  if (location.hash === hash) return;
  location.hash = hash;
}

export function openMerge(qid) { go(mergeHref(qid)); }
export function openPicker() { go("#/"); }

/** Update the query part of the current route WITHOUT a new history entry or re-route
 * (used to persist filters like ?status= in the URL). */
export function replaceQuery(params) {
  const r = parse();
  const query = new URLSearchParams(r.query);
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === "" || (Array.isArray(v) && !v.length)) query.delete(k);
    else query.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  const base = r.name === "merge" ? `#/merge/${encodeURIComponent(r.qid)}` : "#/";
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
  // Legacy deep link: /?qid=Q123 -> /#/merge/Q123 (assumption A5).
  const legacy = new URLSearchParams(location.search).get("qid");
  if (legacy && QID_RE.test(legacy.trim())) {
    history.replaceState(null, "", `/${mergeHref(legacy.trim().toUpperCase())}`);
  } else if (location.search && !location.hash) {
    history.replaceState(null, "", `/${location.hash || "#/"}`);
  }
  window.addEventListener("hashchange", dispatch);
}

/** Fire the initial route once every module is initialised. */
export function start() { dispatch(); }
