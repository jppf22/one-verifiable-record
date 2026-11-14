// Snapshot-safe Wikipedia annotation layer. It paints DOM Ranges and puts all
// controls in Shadow DOM; it never wraps or otherwise changes article text.
(() => {
  const ROOT = "ovr-extension-root", MARKERS = "ovr-extension-markers", STYLE = "ovr-extension-highlights";
  const LANGS = new Set(["fi", "sv", "en"]), NAMES = ["ovr-conflict", "ovr-possible-conflict", "ovr-only-here", "ovr-possible-match"];
  const OMIT = "script,style,nav,aside,form,.mw-editsection,.reference,sup.reference,.mw-references-wrap,#catlinks,.navbox,[role='navigation'],#ovr-extension-root,#ovr-extension-markers";
  let lastUrl = "", serial = 0, queued = false, updating = false, rerun = false, result = null, markerShadow = null, markers = [], popover = null;
  const label = (s) => ({ conflict: "Confirmed conflict", possible_conflict: "Possible conflict; needs review", only_here: "Only in this language edition", possible_match: "Possible match; needs review", missing_here: "Missing on this page" }[s] || "Finding");
  const highlight = (s) => `ovr-${String(s).replace(/_/g, "-")}`;
  const esc = (v) => { const e = document.createElement("span"); e.textContent = String(v || ""); return e.innerHTML; };
  const integer = (v) => { const n = Number.parseInt(String(v || ""), 10); return Number.isSafeInteger(n) && n > 0 ? n : null; };

  function meta(names) { for (const name of names) { const e = document.querySelector(`meta[property="${name}"],meta[name="${name}"]`); if (e?.content) return e.content; } return null; }
  function candidate() {
    const url = new URL(location.href), lang = url.hostname.split(".")[0]; if (!LANGS.has(lang) || !url.hostname.endsWith(".wikipedia.org")) return null;
    let title; try { title = url.pathname.startsWith("/wiki/") ? decodeURIComponent(url.pathname.slice(6)).replace(/_/g, " ") : url.pathname === "/w/index.php" ? url.searchParams.get("title") : null; } catch (_) { return null; }
    if (!title) return null;
    return { lang, title, wikiPageId: integer(meta(["mw:pageId", "mw-page-id"])), revisionId: integer(url.searchParams.get("oldid")) || integer(meta(["mw:revisionId", "mw-revision-id"])) };
  }
  function send(message) { return new Promise((done) => chrome.runtime.sendMessage(message, (data) => done(chrome.runtime.lastError ? null : data || null))); }
  function article() { return document.querySelector("#mw-content-text .mw-parser-output") || document.querySelector("#mw-content-text") || document.querySelector("main .mw-body-content") || document.querySelector("main"); }
  const normal = (s) => String(s || "").normalize("NFKC").replace(/\s+/gu, " ").trim();

  // Build one normalized string, with enough source offsets to turn a unique
  // exact match back into a Range even when it crosses text-node boundaries.
  function indexText() {
    const root = article(); if (!root) return null;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode(node) { return !node.nodeValue?.trim() || node.parentElement?.closest(OMIT) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; } });
    const chars = [], points = []; let node;
    while ((node = walker.nextNode())) { let offset = 0; for (const raw of node.nodeValue) { const from = offset; offset += raw.length; for (const ch of raw.normalize("NFKC")) { if (/\s/u.test(ch)) { if (!chars.length || chars.at(-1) === " ") continue; chars.push(" "); points.push({ node, from, to: offset }); } else { chars.push(ch); points.push({ node, from, to: offset }); } } } }
    while (chars.at(-1) === " ") { chars.pop(); points.pop(); }
    return { text: chars.join(""), points };
  }
  function findRange(index, statement) {
    const needle = normal(statement); if (!index || !needle) return null;
    const start = index.text.indexOf(needle); if (start < 0 || index.text.indexOf(needle, start + 1) >= 0) return null;
    const a = index.points[start], b = index.points[start + needle.length - 1]; if (!a || !b) return null;
    try { const range = document.createRange(); range.setStart(a.node, a.from); range.setEnd(b.node, b.to); return range; } catch (_) { return null; }
  }
  function anchorRange(index, claim) {
    // Newer imports preserve a literal source sentence, which remains the
    // preferred anchor. Older imports only have a generated assertion (for
    // example, “X was born on 1746.”), so safely fall back to its factual
    // value—but only for one unique, non-trivial exact article occurrence.
    const statement = findRange(index, claim?.text);
    if (statement) return statement;
    const value = normal(claim?.value);
    return value.length >= 4 && /[\p{L}\p{N}]/u.test(value) ? findRange(index, value) : null;
  }
  function ensurePaint() {
    if (document.getElementById(STYLE)) return;
    const style = document.createElement("style"); style.id = STYLE;
    style.textContent = "::highlight(ovr-conflict){background:#f8d7da;text-decoration:underline 2px solid #b32424}::highlight(ovr-possible-conflict){background:#fff0c2;text-decoration:underline 2px dashed #a65b00}::highlight(ovr-only-here){background:#dcecff;text-decoration:underline 2px solid #36c}::highlight(ovr-possible-match){background:#eee3ff;text-decoration:underline 2px dotted #6e40c9}";
    document.head.append(style);
  }
  function clearMarks() { if (CSS.highlights) NAMES.forEach((n) => CSS.highlights.delete(n)); markers = []; popover?.remove(); popover = null; document.getElementById(MARKERS)?.remove(); markerShadow = null; }
  function markerHost() {
    const host = document.createElement("div"); host.id = MARKERS; document.body.append(host); markerShadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style"); style.textContent = ":host{all:initial}.m{position:fixed;z-index:2147483646;width:18px;height:18px;border:2px solid;border-radius:50%;padding:0;background:#fff;font:700 11px/1 system-ui;cursor:pointer;box-shadow:0 1px 4px #0006}.m:focus-visible{outline:3px solid #36c;outline-offset:2px}.conflict{border-color:#b32424;color:#b32424}.possible_conflict{border-color:#a65b00;color:#a65b00;border-style:dashed}.only_here{border-color:#36c;color:#36c}.possible_match{border-color:#6e40c9;color:#6e40c9;border-style:dotted}.pop{position:fixed;z-index:2147483647;width:min(360px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;box-sizing:border-box;padding:14px;border:1px solid #a2a9b1;border-radius:8px;background:#fff;color:#202122;box-shadow:0 6px 24px #0005;font:14px/1.45 system-ui}.pop h2{font-size:16px;margin:0 28px 8px 0}.pop p{margin:7px 0}.cp{border-left:3px solid #eaecf0;padding-left:8px;margin:6px 0}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.pop button,.pop a{padding:6px 8px;border:1px solid #72777d;border-radius:4px;background:#f8f9fa;color:#202122;font:600 12px system-ui;text-decoration:none;cursor:pointer}.pop a.primary{background:#36c;border-color:#36c;color:#fff}.receipts{border-top:1px solid #eaecf0;margin-top:10px;padding-top:8px;color:#54595d;font-size:12px}"; markerShadow.append(style); return host;
  }
  function compareUrl(web, page, key = "") { try { const base = new URL(web), focus = key ? `?focus=${encodeURIComponent(key)}` : ""; return `${base.href.replace(/\/+$/, "")}/#/page/${encodeURIComponent(page.lang)}/${encodeURIComponent(String(page.title).replace(/ /g, "_"))}${focus}`; } catch (_) { return null; } }
  function openPop(a, button) {
    popover?.remove(); const p = document.createElement("section"); p.className = "pop"; p.tabIndex = -1;
    const cp = (a.counterparts || []).map((c) => `<div class="cp"><b>${esc(c.lang)}</b>: ${esc(c.text || c.value)}</div>`).join("") || "<p>No counterpart wording stored.</p>";
    const url = compareUrl(result.webBase, result.data.page, a.row_key);
    const receipts = (a.links || []).map((l) => `<p><b>${esc(l.verdict)}</b>${l.rationale ? ` — ${esc(l.rationale)}` : ""}${l.evidence ? `<br>${esc(l.evidence)}` : ""}</p>`).join("") || "<p>No stored receipt for this finding.</p>";
    const revisionLinks = [a.claim, ...(a.counterparts || [])].filter((c) => c?.revision_url).map((c) => `<a target="_blank" rel="noopener" href="${esc(c.revision_url)}">Open ${esc(c.lang)} analysed revision ↗</a>`).join("");
    p.innerHTML = `<button data-close aria-label="Close finding" style="float:right">×</button><h2>${esc(label(a.status))}</h2><p><b>${esc(a.claim?.predicate || a.predicate || "claim")}</b></p><p>${esc(a.claim?.text || "This claim is not present on this page.")}</p>${a.claim?.context ? `<p>${esc(a.claim.context)}</p>` : ""}<p>Analysed revision ${esc(a.claim?.revision_id || result.data.analysed_revision_id || "unknown")}</p><p><b>Counterpart wording</b></p>${cp}<div class="actions"><button data-receipts>Read receipts</button>${url ? `<a class="primary" target="_blank" rel="noopener" href="${esc(url)}">Open focused comparison</a>` : ""}<button data-close>Close</button></div><div class="receipts" hidden>${receipts}${revisionLinks}</div>`;
    markerShadow.append(p); popover = p; const r = button.getBoundingClientRect(); p.style.top = `${Math.max(12, Math.min(r.bottom + 8, innerHeight - 120))}px`; p.style.left = `${Math.max(12, Math.min(r.left, innerWidth - 372))}px`;
    p.querySelectorAll("[data-close]").forEach((x) => x.addEventListener("click", () => { p.remove(); popover = null; button.focus(); })); p.querySelector("[data-receipts]")?.addEventListener("click", (e) => { const rcp = p.querySelector(".receipts"); rcp.hidden = !rcp.hidden; e.currentTarget.textContent = rcp.hidden ? "Read receipts" : "Hide receipts"; }); p.focus();
  }
  function reposition() { markers.forEach(({ range, button }) => { const rect = [...range.getClientRects()].find((r) => r.width && r.height); button.hidden = !rect; if (rect) { button.style.top = `${Math.max(2, rect.top)}px`; button.style.left = `${Math.max(2, rect.left - 22)}px`; } }); }
  function renderMarks(data) {
    clearMarks(); if (!data?.tracked || data.freshness !== "current" || data.analysis_status !== "ready" || !result.inlineEnabled) return;
    const index = indexText(); if (!index) return;
    // A marker remains useful (and opens the same receipts) on Chromium builds
    // where the Custom Highlight paint registry is disabled or unavailable.
    const canPaint = Boolean(CSS.highlights && typeof Highlight !== "undefined");
    if (canPaint) ensurePaint();
    markerHost(); const found = new Map(NAMES.map((n) => [n, []]));
    for (const a of data.annotations || []) { const range = anchorRange(index, a.claim), name = highlight(a.status); if (!range || !found.has(name)) continue; found.get(name).push(range); const b = document.createElement("button"); b.type = "button"; b.className = `m ${a.status}`; b.textContent = "!"; b.setAttribute("aria-label", `${label(a.status)}: ${a.claim?.predicate || "claim"}`); b.title = b.getAttribute("aria-label"); b.addEventListener("click", () => openPop(a, b)); markerShadow.append(b); markers.push({ range, button: b }); }
    if (canPaint) found.forEach((ranges, name) => { if (ranges.length) CSS.highlights.set(name, new Highlight(...ranges)); });
    reposition();
  }
  function renderBadge(res) {
    document.getElementById(ROOT)?.remove(); if (!res?.visible || !res.data?.tracked) return;
    const target = document.querySelector("#firstHeading") || document.querySelector("main h1") || document.querySelector("h1"); if (!target) return;
    const d = res.data, available = d.freshness === "current" && d.analysis_status === "ready" && !d.annotations_unavailable, host = document.createElement("div"); host.id = ROOT; const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style"); style.textContent = ":host{all:initial}.wrap{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0 16px;color:#202122;font:13px/1.4 system-ui}.pill,button{border:0;border-radius:999px;padding:4px 10px;font:650 13px system-ui}.pill{background:#eaecf0}.current{background:#d5fdf4;color:#005a4f}.stale,.unknown{background:#eaf3ff;color:#1b4d7a}.not_ready{background:#fff3cd;color:#664d03}button{background:#36c;color:#fff;cursor:pointer}.open{color:#36c;font-weight:650;text-decoration:none}.tray{width:min(620px,100%);margin-top:8px;padding:10px;border:1px solid #a2a9b1;border-radius:6px;background:#fff}.finding{margin:7px 0;padding:6px 8px;border-left:4px solid #36c}.conflict{border-color:#b32424}.possible_conflict,.missing_here{border-color:#a65b00}.possible_match{border-color:#6e40c9}.muted{color:#54595d;font-size:12px}"; shadow.append(style);
    const wrap = document.createElement("div"); wrap.className = "wrap"; const pill = document.createElement("span"); pill.className = `pill ${available ? "current" : d.freshness || d.analysis_status}`; pill.textContent = !available ? d.annotations_unavailable ? "One Verifiable Record: inline findings are unavailable from this API" : d.freshness === "stale" ? "One Verifiable Record: inline findings are hidden because this page changed since analysis" : d.freshness === "unknown" ? "One Verifiable Record: inline findings need a known live revision" : "One Verifiable Record: comparison is not ready" : `One Verifiable Record: ${(d.annotations || []).length} inline findings, ${(d.missing_here || []).length} missing here`; wrap.append(pill);
    if (available) { const toggle = document.createElement("button"); toggle.textContent = res.inlineEnabled ? "Hide inline findings" : "Show inline findings"; toggle.addEventListener("click", async () => { const reply = await send({ type: "ovr:set-inline-enabled", enabled: !result.inlineEnabled }); if (reply?.ok) { result.inlineEnabled = reply.inlineEnabled; renderMarks(d); renderBadge(result); } }); wrap.append(toggle); }
    const comparison = compareUrl(res.webBase, d.page); if (comparison) { const link = document.createElement("a"); link.className = "open"; link.href = comparison; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Open comparison"; wrap.append(link); }
    const show = document.createElement("button"); show.textContent = "Findings"; wrap.append(show); const tray = document.createElement("div"); tray.className = "tray"; tray.hidden = true; const all = [...(d.annotations || []), ...(d.missing_here || [])]; tray.innerHTML = all.length ? all.map((a) => `<div class="finding ${esc(a.status)}"><b>${esc(label(a.status))}</b><br>${esc(a.claim?.text || a.counterparts?.[0]?.text || "No local source text")}</div>`).join("") : "<span class=muted>No inline findings are available for this snapshot.</span>"; show.addEventListener("click", () => { tray.hidden = !tray.hidden; show.setAttribute("aria-expanded", String(!tray.hidden)); }); shadow.append(wrap, tray); target.after(host);
  }
  async function update() {
    queued = false;
    if (updating) { rerun = true; return; }
    updating = true;
    try {
      const c = candidate(), url = location.href, token = ++serial; lastUrl = url; clearMarks(); document.getElementById(ROOT)?.remove(); if (!c) return;
      const summary = await send({ type: "ovr:resolve", candidate: c }); if (token !== serial || url !== location.href || !summary?.visible) return;
      const details = await send({ type: "ovr:annotations", candidate: c }); if (token !== serial || url !== location.href) return;
      result = details?.visible ? details : { ...summary, data: { ...summary.data, annotations: [], missing_here: [], annotations_unavailable: true }, inlineEnabled: false }; renderMarks(result.data); renderBadge(result);
    } finally {
      updating = false;
      if (rerun) { rerun = false; schedule(); }
    }
  }
  function schedule() { if (updating) { rerun = true; return; } if (!queued) { queued = true; setTimeout(update, 60); } }
  schedule(); addEventListener("popstate", schedule); addEventListener("hashchange", schedule); addEventListener("scroll", reposition, true); addEventListener("resize", reposition); chrome.storage.onChanged.addListener(schedule);
  // Wikipedia mutates constantly, including when this extension adds/removes
  // its own hosts. Only a page identity change may fetch again; ordinary DOM
  // changes merely move existing markers.
  new MutationObserver(() => { if (location.href !== lastUrl) schedule(); else reposition(); }).observe(document.documentElement, { childList: true, subtree: true });
})();
