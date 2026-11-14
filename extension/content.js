// Snapshot-safe Wikipedia annotation layer (Manifest V3 content script).
// Paints exact claim matches via the CSS Custom Highlight API and isolates all
// interactive controls inside Shadow DOM without mutating Wikipedia text nodes.
(() => {
  const ROOT = "ovr-extension-root";
  const MARKERS = "ovr-extension-markers";
  const STYLE = "ovr-extension-highlights";
  const LANGS = new Set(["fi", "sv", "en"]);
  const NAMES = [
    "ovr-conflict",
    "ovr-possible-conflict",
    "ovr-only-here",
    "ovr-possible-match",
    "ovr-agree",
  ];
  // Exclude infoboxes, navigation, references, TOC, thumbnails, and metadata boxes
  // so repeated sidebar facts never make unique article-prose claims look ambiguous.
  const OMIT = [
    "script", "style", "noscript", "nav", "aside", "form",
    ".mw-editsection", ".reference", "sup.reference", ".mw-references-wrap", "ol.references",
    "#catlinks", ".navbox", ".vertical-navbox", "[role='navigation']",
    ".infobox", ".infobox_v2", ".infobox_v3", ".sidebar", ".shortdescription",
    ".hatnote", ".ambox", ".mbox-small", ".sistersitebox",
    ".toc", "#toc", ".mw-indicators", "figure", "figcaption", ".thumb",
    "#ovr-extension-root", "#ovr-extension-markers",
  ].join(",");
  const NON_ARTICLE_PREFIXES = new Set([
    "special", "talk", "user", "user talk", "wikipedia", "wikipedia talk",
    "file", "file talk", "mediawiki", "mediawiki talk", "template", "template talk",
    "help", "help talk", "category", "category talk", "portal", "portal talk",
    "draft", "draft talk", "module", "module talk", "timedtext", "media",
    // Finnish namespaces
    "toiminnot", "keskustelu", "käyttäjä", "keskustelu käyttäjästä",
    "keskustelu wikipediasta", "tiedosto", "keskustelu tiedostosta",
    "järjestelmäviesti", "malline", "keskustelu mallineesta",
    "ohje", "keskustelu ohjeesta", "luokka", "keskustelu luokasta",
    "teemasivu", "moduuli",
    // Swedish namespaces
    "diskussion", "användare", "användardiskussion", "wikipediadiskussion",
    "fil", "fildiskussion", "mediawikidiskussion", "mall", "malldiskussion",
    "hjälp", "hjälpdiskussion", "kategori", "kategoridiskussion",
    "portaldiskussion", "modul",
  ]);

  let lastUrl = "";
  let serial = 0;
  let queued = false;
  let updating = false;
  let rerun = false;
  let result = null;
  let markerShadow = null;
  let markers = [];
  let annotatedItems = [];
  let popover = null;
  let popoverTrigger = null;
  let jumpIndex = -1;

  const STATUS_LABELS = {
    conflict: "Confirmed conflict",
    possible_conflict: "Possible conflict; needs review",
    only_here: "Only in this language edition",
    possible_match: "Possible match; needs review",
    missing_here: "Missing on this page",
    agree: "Agrees across editions",
  };
  const KIND_LABELS = {
    date: "Date",
    number: "Number",
    quantity: "Quantity",
    entity_relation: "Relation",
    factoid: "Fact",
  };
  const ANCHOR_LABELS = {
    anchored: "Marked inline",
    ambiguous: "Ambiguous in text (retained in tray)",
    unanchored: "Unanchored wording (retained in tray)",
    missing_here: "Not on this page",
  };

  const label = (s) => STATUS_LABELS[s] || "Finding";
  const kindKey = (k) => String(k || "factoid").trim().toLowerCase() || "factoid";
  const kindLabel = (k) => {
    const key = kindKey(k);
    if (KIND_LABELS[key]) return KIND_LABELS[key];
    const s = key.replace(/_/g, " ").trim();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Fact";
  };
  const humanise = (s) => String(s || "claim").replace(/_/g, " ").trim() || "claim";
  const highlightName = (s) => `ovr-${String(s).replace(/_/g, "-")}`;
  const esc = (v) => {
    const e = document.createElement("span");
    e.textContent = String(v ?? "");
    return e.innerHTML;
  };
  const integer = (v) => {
    const n = Number.parseInt(String(v ?? ""), 10);
    return Number.isSafeInteger(n) && n > 0 ? n : null;
  };
  const normal = (s) => String(s || "").normalize("NFKC").replace(/\s+/gu, " ").trim();

  function meta(names) {
    for (const name of names) {
      const e = document.querySelector(`meta[property="${name}"],meta[name="${name}"]`);
      if (e?.content) return e.content;
    }
    return null;
  }

  // Extract MediaWiki RLCONF fields from inline <script> text safely in the MV3
  // isolated world without evaluating page JS.
  function parseMediaWikiConfig() {
    const out = {
      ns: null,
      action: null,
      isArticle: null,
      articleId: null,
      revisionId: null,
      curRevisionId: null,
      title: null,
    };
    for (const script of document.querySelectorAll("script:not([src])")) {
      const txt = script.textContent || "";
      if (!txt.includes("RLCONF") && !txt.includes("wgArticleId")) continue;
      const nsMatch = txt.match(/"wgNamespaceNumber"\s*:\s*(-?\d+)/);
      if (nsMatch) out.ns = Number.parseInt(nsMatch[1], 10);
      const actMatch = txt.match(/"wgAction"\s*:\s*"([^"]+)"/);
      if (actMatch) out.action = actMatch[1];
      const isArtMatch = txt.match(/"wgIsArticle"\s*:\s*(true|false)/);
      if (isArtMatch) out.isArticle = isArtMatch[1] === "true";
      const idMatch = txt.match(/"wgArticleId"\s*:\s*(\d+)/);
      if (idMatch) out.articleId = integer(idMatch[1]);
      const revMatch = txt.match(/"wgRevisionId"\s*:\s*(\d+)/);
      if (revMatch) out.revisionId = integer(revMatch[1]);
      const curRevMatch = txt.match(/"wgCurRevisionId"\s*:\s*(\d+)/);
      if (curRevMatch) out.curRevisionId = integer(curRevMatch[1]);
      const titleMatch = txt.match(/"wgTitle"\s*:\s*("(?:[^"\\]|\\.)*")/);
      if (titleMatch) {
        try { out.title = JSON.parse(titleMatch[1]); } catch (_) { /* ignore */ }
      }
      break;
    }
    return out;
  }

  function permalinkRevisionId() {
    const href = document.querySelector("#t-permalink a[href]")?.getAttribute("href");
    if (!href) return null;
    try {
      return integer(new URL(href, location.origin).searchParams.get("oldid"));
    } catch (_) {
      return null;
    }
  }

  function canonicalTitle() {
    const href = document.querySelector('link[rel="canonical"][href]')?.getAttribute("href");
    if (!href) return null;
    try {
      const u = new URL(href, location.origin);
      if (u.pathname.startsWith("/wiki/")) {
        return decodeURIComponent(u.pathname.slice(6)).replace(/_/g, " ").trim();
      }
      return u.searchParams.get("title")?.replace(/_/g, " ").trim() || null;
    } catch (_) {
      return null;
    }
  }

  function urlTitle(url) {
    try {
      if (url.pathname.startsWith("/wiki/")) {
        return decodeURIComponent(url.pathname.slice(6)).replace(/_/g, " ").trim();
      }
      if (url.pathname === "/w/index.php") {
        return (url.searchParams.get("title") || "").replace(/_/g, " ").trim() || null;
      }
    } catch (_) { /* malformed percent-encoding */ }
    return null;
  }

  function hasBlockedPrefix(rawTitle) {
    const idx = rawTitle.indexOf(":");
    if (idx <= 0) return false;
    const prefix = rawTitle.slice(0, idx).replace(/_/g, " ").trim().toLowerCase();
    return NON_ARTICLE_PREFIXES.has(prefix);
  }

  function candidate() {
    let url;
    try { url = new URL(location.href); } catch (_) { return null; }
    const lang = url.hostname.split(".")[0].toLowerCase();
    if (!LANGS.has(lang) || url.hostname.toLowerCase() !== `${lang}.wikipedia.org`) return null;
    if (!url.pathname.startsWith("/wiki/") && url.pathname !== "/w/index.php") return null;

    // Exclude diffs, edit screens, history views, and VisualEditor views.
    const urlAction = url.searchParams.get("action");
    if (urlAction && urlAction !== "view") return null;
    if (url.searchParams.has("diff") || url.searchParams.has("veaction")) return null;
    if (url.searchParams.get("printable") === "yes") return null;

    const mw = parseMediaWikiConfig();
    if (mw.ns !== null && mw.ns !== 0) return null;
    if (mw.action && mw.action !== "view") return null;
    if (mw.isArticle === false && !url.searchParams.has("oldid")) return null;

    // Fallback namespace/action guards when RLCONF is absent.
    const bodyClasses = [...(document.body?.classList || [])];
    if (mw.ns === null && bodyClasses.some((c) => /^ns--?\d+$/.test(c)) && !bodyClasses.includes("ns-0")) {
      return null;
    }
    if (!mw.action && bodyClasses.some((c) => c.startsWith("action-")) && !bodyClasses.includes("action-view")) {
      return null;
    }

    const rawUrlTitle = urlTitle(url);
    if (mw.ns === null && !bodyClasses.includes("ns-0") && rawUrlTitle && hasBlockedPrefix(rawUrlTitle)) {
      return null;
    }

    const headingTitle = document
      .querySelector("#firstHeading .mw-page-title-main, h1#firstHeading")
      ?.textContent?.trim();
    const title = (mw.title || canonicalTitle() || rawUrlTitle || headingTitle || "")
      .replace(/_/g, " ")
      .trim();
    if (!title) return null;
    if (mw.ns === null && !bodyClasses.includes("ns-0") && hasBlockedPrefix(title)) return null;

    const wikiPageId =
      mw.articleId ||
      integer(url.searchParams.get("curid")) ||
      integer(meta(["mw:pageId", "mw-page-id", "mw:PageProp/PageID"]));

    const revisionId =
      integer(url.searchParams.get("oldid")) ||
      mw.revisionId ||
      permalinkRevisionId() ||
      mw.curRevisionId ||
      integer(meta(["mw:revisionId", "mw-revision-id"]));

    return { lang, title, wikiPageId, revisionId };
  }

  function send(message) {
    return new Promise((done) => {
      try {
        chrome.runtime.sendMessage(message, (data) => {
          done(chrome.runtime.lastError ? null : data || null);
        });
      } catch (_) {
        done(null);
      }
    });
  }

  function article() {
    return (
      document.querySelector("#mw-content-text .mw-parser-output") ||
      document.querySelector("#mw-content-text") ||
      document.querySelector("main .mw-body-content") ||
      document.querySelector("main")
    );
  }

  // Build one normalized prose string with source DOM offsets so unique exact
  // matches map cleanly back to DOM Ranges even across inline markup boundaries.
  function indexText() {
    const root = article();
    if (!root) return null;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return !node.nodeValue?.trim() || node.parentElement?.closest(OMIT)
          ? NodeFilter.FILTER_REJECT
          : NodeFilter.FILTER_ACCEPT;
      },
    });
    const firstLeadPara = [...root.querySelectorAll("p")].find(
      (p) => !p.closest(OMIT) && normal(p.textContent).length >= 20,
    );
    const chars = [];
    const points = [];
    let leadEnd = 0;
    let node;
    while ((node = walker.nextNode())) {
      const inLead = Boolean(firstLeadPara && firstLeadPara.contains(node));
      let offset = 0;
      for (const raw of node.nodeValue) {
        const from = offset;
        offset += raw.length;
        for (const ch of raw.normalize("NFKC")) {
          if (/\s/u.test(ch)) {
            if (!chars.length || chars.at(-1) === " ") continue;
            chars.push(" ");
            points.push({ node, from, to: offset });
          } else {
            chars.push(ch);
            points.push({ node, from, to: offset });
          }
        }
      }
      if (inLead) leadEnd = chars.length;
    }
    while (chars.at(-1) === " ") {
      chars.pop();
      points.pop();
    }
    return { text: chars.join(""), points, leadEnd: Math.min(leadEnd, chars.length) };
  }

  function locateMatches(haystack, rawNeedle, baseOffset = 0) {
    const needle = normal(rawNeedle);
    if (!haystack || !needle) return { status: "none" };
    const first = haystack.indexOf(needle);
    if (first < 0) return { status: "none" };
    const second = haystack.indexOf(needle, first + 1);
    if (second >= 0) return { status: "ambiguous" };
    return { status: "unique", start: baseOffset + first, length: needle.length };
  }

  function buildRange(index, start, length) {
    const a = index?.points[start];
    const b = index?.points[start + length - 1];
    if (!a || !b) return null;
    try {
      const range = document.createRange();
      range.setStart(a.node, a.from);
      range.setEnd(b.node, b.to);
      return range;
    } catch (_) {
      return null;
    }
  }

  // Returns { range, anchorState: "anchored" | "ambiguous" | "unanchored" }.
  function anchorClaim(index, claim) {
    if (!index || !claim) return { range: null, anchorState: "unanchored" };
    let sawAmbiguous = false;

    const text = normal(claim.text);
    if (text) {
      let m = locateMatches(index.text, text);
      if (m.status === "none" && text.endsWith(".") && text.length >= 12) {
        m = locateMatches(index.text, text.slice(0, -1));
      }
      if (m.status === "unique") {
        const range = buildRange(index, m.start, m.length);
        if (range) return { range, anchorState: "anchored" };
      } else if (m.status === "ambiguous") {
        sawAmbiguous = true;
      }
    }

    const value = normal(claim.value);
    const usableValue = value.length >= 4 && /[\p{L}\p{N}]/u.test(value);

    // If a stored context passage uniquely identifies a paragraph/sentence,
    // disambiguate repeated text/values inside that exact context window.
    const ctx = normal(claim.context);
    if (ctx.length >= 12) {
      const ctxMatch = locateMatches(index.text, ctx);
      if (ctxMatch.status === "unique") {
        const slice = index.text.slice(ctxMatch.start, ctxMatch.start + ctxMatch.length);
        for (const candidateNeedle of [text, usableValue ? value : ""]) {
          if (!candidateNeedle) continue;
          const sub = locateMatches(slice, candidateNeedle, ctxMatch.start);
          if (sub.status === "unique") {
            const range = buildRange(index, sub.start, sub.length);
            if (range) return { range, anchorState: "anchored" };
          }
        }
      }
    }

    // Fallback for generated assertions (e.g. rules-extracted dates): anchor on
    // a unique, non-trivial verbatim value in the article prose, or uniquely in
    // the opening lead paragraph when later body sections repeat a lead date.
    if (usableValue) {
      const vm = locateMatches(index.text, value);
      if (vm.status === "unique") {
        const range = buildRange(index, vm.start, vm.length);
        if (range) return { range, anchorState: "anchored" };
      } else if (vm.status === "ambiguous") {
        if (index.leadEnd > 0) {
          const leadMatch = locateMatches(index.text.slice(0, index.leadEnd), value, 0);
          if (leadMatch.status === "unique") {
            const range = buildRange(index, leadMatch.start, leadMatch.length);
            if (range) return { range, anchorState: "anchored" };
          }
        }
        sawAmbiguous = true;
      }
    }

    return { range: null, anchorState: sawAmbiguous ? "ambiguous" : "unanchored" };
  }

  function ensurePaint() {
    if (document.getElementById(STYLE)) return;
    const style = document.createElement("style");
    style.id = STYLE;
    // X2: soft teal with thin underline for `agree`; bolder underlines for issues.
    style.textContent = [
      "::highlight(ovr-conflict){background:#f8d7da;text-decoration:underline 2px solid #b32424}",
      "::highlight(ovr-possible-conflict){background:#fff0c2;text-decoration:underline 2px dashed #a65b00}",
      "::highlight(ovr-only-here){background:#dcecff;text-decoration:underline 2px solid #36c}",
      "::highlight(ovr-possible-match){background:#eee3ff;text-decoration:underline 2px dotted #6e40c9}",
      "::highlight(ovr-agree){background:#e2fbf5;text-decoration:underline 1px solid #14866d}",
    ].join("");
    document.head.append(style);
  }

  function closePopover(restoreFocus = false) {
    if (!popover) return;
    const trigger = popoverTrigger;
    popover.remove();
    popover = null;
    popoverTrigger = null;
    if (restoreFocus && trigger?.isConnected) trigger.focus();
  }

  function clearMarks() {
    if (CSS.highlights) NAMES.forEach((n) => CSS.highlights.delete(n));
    markers = [];
    closePopover(false);
    document.getElementById(MARKERS)?.remove();
    markerShadow = null;
  }

  function ensureOverlayHost() {
    if (markerShadow && document.getElementById(MARKERS)) return markerShadow;
    document.getElementById(MARKERS)?.remove();
    const host = document.createElement("div");
    host.id = MARKERS;
    document.body.append(host);
    markerShadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host { all: initial; }
      .m {
        position: fixed;
        z-index: 2147483646;
        width: 17px;
        height: 17px;
        border: 1.75px solid;
        border-radius: 50%;
        padding: 0;
        background: #fff;
        font: 700 10px/1 system-ui, sans-serif;
        cursor: pointer;
        box-shadow: 0 1px 4px rgba(0, 0, 0, 0.28);
        display: inline-flex;
        align-items: center;
        justify-content: center;
      }
      .m:hover { transform: scale(1.1); }
      .m:focus-visible { outline: 3px solid #36c; outline-offset: 2px; }
      .m[hidden] { display: none !important; }
      .conflict { border-color: #b32424; color: #b32424; background: #fff5f5; }
      .possible_conflict { border-color: #a65b00; color: #a65b00; border-style: dashed; background: #fffbeb; }
      .only_here { border-color: #36c; color: #36c; background: #f2f7ff; }
      .possible_match { border-color: #6e40c9; color: #6e40c9; border-style: dotted; background: #faf5ff; }
      .agree { border-color: #14866d; color: #14866d; background: #f2fdf9; }
      .pop {
        position: fixed;
        z-index: 2147483647;
        width: min(380px, calc(100vw - 24px));
        max-height: calc(100vh - 24px);
        overflow: auto;
        box-sizing: border-box;
        padding: 14px;
        border: 1px solid #a2a9b1;
        border-radius: 8px;
        background: #fff;
        color: #202122;
        box-shadow: 0 8px 28px rgba(0, 0, 0, 0.28);
        font: 13.5px/1.45 system-ui, sans-serif;
      }
      .pop:focus { outline: 2px solid #36c; outline-offset: -2px; }
      .pop-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
      .pop h2 { font-size: 15px; margin: 0; line-height: 1.3; }
      .close-x {
        border: 1px solid #c8ccd1;
        border-radius: 4px;
        background: #f8f9fa;
        color: #202122;
        width: 24px;
        height: 24px;
        padding: 0;
        font: 700 14px/1 system-ui, sans-serif;
        cursor: pointer;
        flex: none;
      }
      .pop p { margin: 7px 0; }
      .meta-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 6px 0; font-size: 12px; color: #54595d; }
      .tag { padding: 1px 7px; border-radius: 999px; background: #eaecf0; color: #202122; font-weight: 600; }
      .stmt { padding: 6px 8px; background: #f8f9fa; border-radius: 4px; border-left: 3px solid #72777d; }
      .cp { border-left: 3px solid #c8ccd1; padding-left: 8px; margin: 6px 0; font-size: 13px; }
      .receipt-preview {
        margin: 8px 0;
        padding: 6px 8px;
        border-radius: 4px;
        background: #f8f9fa;
        border: 1px solid #eaecf0;
        font-size: 12px;
        color: #404244;
      }
      .actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
      .pop button:not(.close-x), .pop a.btn {
        padding: 5px 9px;
        border: 1px solid #72777d;
        border-radius: 4px;
        background: #f8f9fa;
        color: #202122;
        font: 600 12px/1.3 system-ui, sans-serif;
        text-decoration: none;
        cursor: pointer;
      }
      .pop a.btn.primary { background: #36c; border-color: #36c; color: #fff; }
      .pop button:focus-visible, .pop a:focus-visible { outline: 2px solid #36c; outline-offset: 2px; }
      .receipts { border-top: 1px solid #eaecf0; margin-top: 10px; padding-top: 8px; color: #404244; font-size: 12px; }
      .rev-links { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
      .rev-links a { color: #36c; text-decoration: none; font-weight: 600; }
      .rev-links a:hover { text-decoration: underline; }
    `;
    markerShadow.append(style);
    return markerShadow;
  }

  function safeWikiUrl(raw) {
    try {
      const u = new URL(String(raw || ""));
      return u.protocol === "https:" && /^[a-z0-9-]+\.wikipedia\.org$/i.test(u.hostname)
        ? u.href
        : null;
    } catch (_) {
      return null;
    }
  }

  // Page-centric v2 route builder (#/page/{lang}/{title}?focus=r-...).
  function compareUrl(web, page, params = {}) {
    if (!web || !page?.lang || !page?.title) return null;
    try {
      const base = new URL(web);
      if (base.protocol !== "https:" && base.protocol !== "http:") return null;
      const slug = encodeURIComponent(String(page.title).trim().replace(/\s+/g, "_"));
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(params || {})) {
        if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
      }
      const query = [...qs].length ? `?${qs.toString()}` : "";
      return `${base.href.replace(/\/+$/, "")}/#/page/${encodeURIComponent(page.lang)}/${slug}${query}`;
    } catch (_) {
      return null;
    }
  }

  function legacyQidUrl(web, qid) {
    if (!web || !qid) return null;
    try {
      const base = new URL(web);
      if (base.protocol !== "https:" && base.protocol !== "http:") return null;
      return `${base.href.replace(/\/+$/, "")}/#/merge/${encodeURIComponent(qid)}`;
    } catch (_) {
      return null;
    }
  }

  function sortedCounterpartsForFinding(a) {
    const cps = [...(a.counterparts || [])];
    const targetPred = a.claim?.predicate || a.predicate || "";
    if (!targetPred) return cps;
    return cps.sort((x, y) => {
      const xm = x.predicate === targetPred ? 0 : 1;
      const ym = y.predicate === targetPred ? 0 : 1;
      return xm - ym;
    });
  }

  function openPop(a, button) {
    if (!result?.data) return;
    ensureOverlayHost();
    closePopover(false);

    const page = result.data.page;
    const p = document.createElement("section");
    p.className = "pop";
    p.setAttribute("role", "dialog");
    p.setAttribute("aria-label", `${label(a.status)} — ${kindLabel(a.kind)}`);
    p.tabIndex = -1;

    const cps = sortedCounterpartsForFinding(a);
    const cpHtml = cps.length
      ? cps.map((c) => `<div class="cp"><b>${esc(c.lang)}</b>: ${esc(c.text || c.value || "")}</div>`).join("")
      : "<p>No counterpart wording stored.</p>";

    const focusUrl = compareUrl(result.webBase, page, a.row_key ? { focus: a.row_key } : {});
    const reviewUrl = compareUrl(result.webBase, page, a.row_key ? { focus: a.row_key, review: "1" } : { review: "1" });
    const canPatch = ["conflict", "possible_conflict", "missing_here"].includes(a.status);
    const patchUrl = canPatch
      ? compareUrl(result.webBase, page, a.row_key ? { focus: a.row_key, panel: "suggestions" } : { panel: "suggestions" })
      : null;

    const verdictRank = {
      contradicted: 0,
      possible_conflict: 1,
      supported: 2,
      possible_match: 3,
      definition_mismatch: 4,
      unverifiable: 5,
    };
    const links = [...(a.links || [])].sort(
      (x, y) => (verdictRank[x.verdict] ?? 9) - (verdictRank[y.verdict] ?? 9),
    );
    const topLink = links[0] || null;
    const confVal = topLink?.verdict_confidence ?? topLink?.confidence;
    const simVal = topLink?.similarity;
    const previewMeta = [
      topLink?.verdict ? `Verdict: <b>${esc(humanise(topLink.verdict))}</b>` : null,
      simVal != null ? `sim ${esc(Number(simVal).toFixed(2))}` : null,
      confVal != null ? `conf ${esc(Number(confVal).toFixed(2))}` : null,
      `rev ${esc(a.claim?.revision_id || result.data.analysed_revision_id || "unknown")}`,
    ].filter(Boolean).join(" · ");

    const receiptsHtml = links.length
      ? links.map((l) => {
          const metaBits = [
            l.similarity != null ? `sim ${Number(l.similarity).toFixed(3)}` : null,
            (l.verdict_confidence ?? l.confidence) != null
              ? `conf ${Number(l.verdict_confidence ?? l.confidence).toFixed(2)}`
              : null,
          ].filter(Boolean).join(" · ");
          return `<p><b>${esc(humanise(l.verdict))}</b>${metaBits ? ` <span>(${esc(metaBits)})</span>` : ""}${l.rationale ? ` — ${esc(l.rationale)}` : ""}${l.evidence ? `<br>${esc(l.evidence)}` : ""}</p>`;
        }).join("")
      : "<p>No stored link receipt for this finding (single-edition or unlinked row).</p>";

    const seenRevUrls = new Set();
    const revisionLinks = [a.claim, ...cps]
      .map((c) => c && { lang: c.lang, href: safeWikiUrl(c.revision_url) })
      .filter((c) => c?.href && !seenRevUrls.has(c.href) && seenRevUrls.add(c.href))
      .map((c) => `<a target="_blank" rel="noopener" href="${esc(c.href)}">Open ${esc(c.lang)} on Wikipedia (analysed revision) ↗</a>`)
      .join("");

    const pred = humanise(a.claim?.predicate || a.predicate || "claim");
    const anchorNote = a.anchorState && a.anchorState !== "anchored"
      ? `<span class="tag">${esc(ANCHOR_LABELS[a.anchorState] || a.anchorState)}</span>`
      : "";

    p.innerHTML = `
      <div class="pop-head">
        <h2>${esc(label(a.status))}</h2>
        <button type="button" class="close-x" data-close aria-label="Close finding">×</button>
      </div>
      <div class="meta-row">
        <span class="tag">${esc(kindLabel(a.kind))}</span>
        <span><b>${esc(pred)}</b></span>
        ${anchorNote}
      </div>
      <p class="stmt">${esc(a.claim?.text || "This claim is stated on counterpart page(s) and is not present on this page.")}</p>
      ${a.claim?.context ? `<p>${esc(a.claim.context)}</p>` : ""}
      <div class="receipt-preview">${previewMeta}</div>
      <p><b>Counterpart wording</b></p>
      ${cpHtml}
      <div class="actions">
        <button type="button" data-receipts aria-expanded="false">Read receipts</button>
        ${focusUrl ? `<a class="btn primary" target="_blank" rel="noopener" href="${esc(focusUrl)}">Open focused comparison</a>` : ""}
        ${reviewUrl ? `<a class="btn" target="_blank" rel="noopener" href="${esc(reviewUrl)}">Review this claim</a>` : ""}
        ${patchUrl ? `<a class="btn" target="_blank" rel="noopener" href="${esc(patchUrl)}">Draft a patch</a>` : ""}
        <button type="button" data-copy>Copy claim</button>
        <button type="button" data-close>Close</button>
      </div>
      <div class="receipts" hidden>
        ${receiptsHtml}
        ${revisionLinks ? `<div class="rev-links">${revisionLinks}</div>` : ""}
      </div>
    `;

    markerShadow.append(p);
    popover = p;
    popoverTrigger = button || null;

    // Position inside viewport, flipping above the trigger when near bottom.
    const r = button?.getBoundingClientRect?.() || { top: 80, bottom: 100, left: 24 };
    const popRect = p.getBoundingClientRect();
    const fitsBelow = r.bottom + 8 + popRect.height <= innerHeight - 12;
    const top = fitsBelow
      ? Math.max(12, r.bottom + 8)
      : Math.max(12, Math.min(r.top - popRect.height - 8, innerHeight - popRect.height - 12));
    const left = Math.max(12, Math.min(r.left, innerWidth - popRect.width - 12));
    p.style.top = `${top}px`;
    p.style.left = `${left}px`;

    p.querySelectorAll("[data-close]").forEach((x) => {
      x.addEventListener("click", () => closePopover(true));
    });
    p.querySelector("[data-receipts]")?.addEventListener("click", (e) => {
      const rcp = p.querySelector(".receipts");
      rcp.hidden = !rcp.hidden;
      e.currentTarget.setAttribute("aria-expanded", String(!rcp.hidden));
      e.currentTarget.textContent = rcp.hidden ? "Read receipts" : "Hide receipts";
    });
    p.querySelector("[data-copy]")?.addEventListener("click", (e) => {
      const btn = e.currentTarget;
      const parts = [
        a.claim?.text ? `${page?.lang || "here"}: ${a.claim.text}` : null,
        ...cps.map((c) => `${c.lang}: ${c.text || c.value}`),
      ].filter(Boolean);
      navigator.clipboard?.writeText(parts.join("\n")).then(
        () => { btn.textContent = "Copied"; },
        () => { btn.textContent = "Copy failed"; },
      );
    });
    p.focus();
  }

  function reposition() {
    const placed = [];
    for (const m of markers) {
      const { range, button } = m;
      if (button.dataset.filtered === "1") {
        button.hidden = true;
        continue;
      }
      const rects = [...range.getClientRects()].filter((r) => r.width && r.height);
      const rect = rects.at(-1) || rects[0];
      if (!rect || rect.bottom < 0 || rect.top > innerHeight) {
        button.hidden = true;
        continue;
      }
      button.hidden = false;
      // Place like a compact superscript at the end of the highlighted span,
      // nudging horizontally if two claims share the same line/coords.
      let top = Math.max(2, Math.round(rect.top - 8));
      let left = Math.min(innerWidth - 22, Math.max(2, Math.round(rect.right - 3)));
      while (placed.some((p) => Math.abs(p.top - top) < 15 && Math.abs(p.left - left) < 18)) {
        left = Math.min(innerWidth - 22, left + 18);
        if (left >= innerWidth - 22) { top += 16; break; }
      }
      placed.push({ top, left });
      button.style.top = `${top}px`;
      button.style.left = `${left}px`;
    }
  }

  function selectedKindsSet() {
    return new Set((result?.selectedKinds || []).map(kindKey));
  }

  function isKindActive(kind, activeSet = selectedKindsSet()) {
    return activeSet.size === 0 || activeSet.has(kindKey(kind));
  }

  // Compute anchor states for all annotations, and when inlineEnabled is true
  // paint CSS Custom Highlights + keyboard-reachable inline markers.
  function renderMarks(data) {
    clearMarks();
    annotatedItems = [];
    if (!data?.tracked || data.freshness !== "current" || data.analysis_status !== "ready") {
      return;
    }
    const index = indexText();
    const canPaint = Boolean(result?.inlineEnabled && CSS.highlights && typeof Highlight !== "undefined");
    if (canPaint) ensurePaint();
    if (result?.inlineEnabled) ensureOverlayHost();

    const activeKinds = selectedKindsSet();
    const found = new Map(NAMES.map((n) => [n, []]));

    for (const raw of data.annotations || []) {
      const k = kindKey(raw.kind);
      const { range, anchorState } = index
        ? anchorClaim(index, raw.claim)
        : { range: null, anchorState: "unanchored" };
      const item = { ...raw, kind: k, anchorState, marker: null };
      annotatedItems.push(item);

      const hName = highlightName(raw.status);
      if (!result?.inlineEnabled || !range || !found.has(hName)) continue;

      const b = document.createElement("button");
      b.type = "button";
      b.className = `m ${raw.status}`;
      b.textContent = raw.status === "agree" ? "✓" : raw.status === "conflict" ? "!" : "?";
      const aria = `${label(raw.status)} — ${kindLabel(k)}: ${humanise(raw.claim?.predicate || "claim")}`;
      b.setAttribute("aria-label", aria);
      b.setAttribute("aria-haspopup", "dialog");
      b.title = aria;
      if (!isKindActive(k, activeKinds)) b.dataset.filtered = "1";
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openPop(item, b);
      });
      markerShadow.append(b);

      const markerObj = { range, button: b, name: hName, status: raw.status, kind: k, item };
      item.marker = markerObj;
      markers.push(markerObj);
      if (isKindActive(k, activeKinds)) found.get(hName).push(range);
    }

    if (canPaint) {
      found.forEach((ranges, name) => {
        if (ranges.length) CSS.highlights.set(name, new Highlight(...ranges));
      });
    }
    reposition();
  }

  function jumpableConflictMarkers() {
    return markers.filter(
      (m) =>
        (m.status === "conflict" || m.status === "possible_conflict") &&
        m.button.dataset.filtered !== "1",
    );
  }

  function focusMarker(m) {
    if (!m) return false;
    m.range.startContainer?.parentElement?.scrollIntoView({ block: "center", behavior: "smooth" });
    setTimeout(() => {
      reposition();
      m.button.focus();
    }, 280);
    return true;
  }

  function jumpToNextConflict() {
    const targets = jumpableConflictMarkers();
    if (!targets.length) return false;
    jumpIndex = (jumpIndex + 1) % targets.length;
    return focusMarker(targets[jumpIndex]);
  }

  // Hide/show markers and repaint CSS.highlights for the active set of claim kinds.
  // An empty set means "all kinds" (X5).
  function applyKindFilter(kinds) {
    if (CSS.highlights && typeof Highlight !== "undefined") {
      const found = new Map(NAMES.map((n) => [n, []]));
      for (const m of markers) {
        if (isKindActive(m.kind, kinds) && found.has(m.name)) {
          found.get(m.name).push(m.range);
        }
      }
      NAMES.forEach((n) => {
        const ranges = found.get(n);
        if (ranges.length) CSS.highlights.set(n, new Highlight(...ranges));
        else CSS.highlights.delete(n);
      });
    }
    for (const m of markers) {
      m.button.dataset.filtered = isKindActive(m.kind, kinds) ? "" : "1";
    }
    reposition();
  }

  function summaryPartsText(d) {
    if (d.legacy_badge) {
      const parts = [];
      if (d.conflicts > 0) parts.push(`${d.conflicts} conflict${d.conflicts === 1 ? "" : "s"}`);
      if (!d.omit_gaps && typeof d.gaps === "number" && d.gaps > 0) {
        parts.push(`${d.gaps} gap${d.gaps === 1 ? "" : "s"}`);
      }
      if (d.agree > 0) parts.push(`${d.agree} verified`);
      return parts.join(" · ") || "0 conflicts";
    }
    const parts = [];
    const conflicts = (d.conflicts || 0) + (d.possible_conflicts || 0);
    if (conflicts > 0) parts.push(`${conflicts} conflict${conflicts === 1 ? "" : "s"}`);
    if (d.missing_here_count > 0) parts.push(`${d.missing_here_count} missing here`);
    if (d.only_here > 0) parts.push(`${d.only_here} only here`);
    if (d.agree > 0) parts.push(`${d.agree} agree`);
    return parts.join(" · ") || "tracked";
  }

  function pillToneClass(d) {
    if (d.analysis_status === "not_ready") return "not_ready";
    const conflicts = (d.conflicts || 0) + (d.possible_conflicts || 0);
    const missing = d.omit_gaps
      ? 0
      : typeof d.gaps === "number"
        ? d.gaps
        : d.missing_here_count || 0;
    if (conflicts > 0) return "tone_conflict";
    if (missing > 0 || (d.possible_matches || 0) > 0) return "tone_amber";
    if (d.freshness === "current" || (d.agree || 0) > 0 || (d.only_here || 0) > 0) {
      return "tone_agree";
    }
    return d.freshness || "stale";
  }

  function renderBadge(res) {
    const prevTrayOpen = Boolean(
      document.getElementById(ROOT)?.shadowRoot?.querySelector(".tray:not([hidden])"),
    );
    document.getElementById(ROOT)?.remove();
    if (!res?.visible || !res.data?.tracked) return;

    const d = res.data;
    if (!d.legacy_badge && !(d.counterparts || []).length) return;

    const target =
      document.querySelector("#firstHeading") ||
      document.querySelector("main h1") ||
      document.querySelector("h1");
    if (!target) return;

    const available =
      d.freshness === "current" &&
      d.analysis_status === "ready" &&
      !d.annotations_unavailable;

    const host = document.createElement("div");
    host.id = ROOT;
    const shadow = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host { all: initial; }
      .wrap {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
        margin: 8px 0 14px;
        color: #202122;
        font: 13px/1.4 system-ui, sans-serif;
      }
      .pill, button {
        border: 1px solid transparent;
        border-radius: 999px;
        padding: 4px 11px;
        font: 600 12.5px/1.35 system-ui, sans-serif;
      }
      .pill { background: #eaecf0; color: #202122; }
      .tone_conflict { background: #fce8e6; color: #a51d2d; border-color: #f5c2c7; }
      .tone_amber { background: #fff3cd; color: #7a4f01; border-color: #ffecb5; }
      .tone_agree { background: #d5fdf4; color: #005a4f; border-color: #9ef0dc; }
      .stale, .unknown { background: #eaf3ff; color: #1b4d7a; border-color: #b6d4fe; }
      .not_ready { background: #f8f9fa; color: #54595d; border-color: #c8ccd1; }
      button { background: #36c; color: #fff; cursor: pointer; }
      button.secondary { background: #f8f9fa; color: #202122; border-color: #a2a9b1; }
      button:focus-visible, a:focus-visible { outline: 2px solid #36c; outline-offset: 2px; }
      .open { color: #36c; font-weight: 650; text-decoration: none; }
      .open:hover { text-decoration: underline; }
      .chips { display: flex; gap: 6px; flex-wrap: wrap; width: 100%; margin-top: 2px; align-items: center; }
      .chips .lbl { color: #54595d; font: 600 12px system-ui, sans-serif; }
      .chip { background: #f8f9fa; color: #202122; border: 1px solid #c8ccd1; padding: 3px 9px; font-size: 12px; }
      .chip[aria-pressed="true"] { background: #36c; color: #fff; border-color: #36c; }
      .tray {
        width: min(680px, 100%);
        margin-top: 8px;
        padding: 10px 12px;
        border: 1px solid #a2a9b1;
        border-radius: 6px;
        background: #fff;
        color: #202122;
        font: 13px/1.45 system-ui, sans-serif;
        box-sizing: border-box;
      }
      .tray-note { color: #54595d; font-size: 12px; margin: 0 0 8px; }
      .kind-group { margin: 10px 0; }
      .kind-head { font: 700 13px system-ui, sans-serif; margin: 4px 0; color: #202122; }
      .finding {
        margin: 7px 0;
        padding: 7px 9px;
        border-left: 4px solid #36c;
        background: #f8f9fa;
        border-radius: 0 4px 4px 0;
      }
      .finding.conflict { border-color: #b32424; }
      .finding.possible_conflict, .finding.missing_here { border-color: #a65b00; }
      .finding.possible_match { border-color: #6e40c9; }
      .finding.agree { border-color: #14866d; }
      .finding-head { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 3px; }
      .anchor-pill {
        font-size: 11px;
        padding: 1px 6px;
        border-radius: 999px;
        background: #eaecf0;
        color: #404244;
        font-weight: 600;
      }
      .anchor-pill.unanchored, .anchor-pill.ambiguous, .anchor-pill.missing_here {
        background: #fff3cd;
        color: #664d03;
      }
      .finding-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; align-items: center; }
      .tray-btn {
        border-radius: 4px;
        padding: 3px 8px;
        font: 600 11.5px/1.3 system-ui, sans-serif;
        background: #fff;
        color: #202122;
        border: 1px solid #a2a9b1;
      }
      .tray-btn.primary { background: #36c; color: #fff; border-color: #36c; }
      .tray-link { color: #36c; font: 600 12px system-ui, sans-serif; text-decoration: none; }
      .tray-link:hover { text-decoration: underline; }
      .muted { color: #54595d; font-size: 12px; }
    `;
    shadow.append(style);

    const wrap = document.createElement("div");
    wrap.className = "wrap";

    // Heading status pill (X2 + V-W10 + X1 + stale/unknown clarity).
    const pill = document.createElement("span");
    pill.className = `pill ${pillToneClass(d)}`;
    if (available) {
      const anns = annotatedItems.length ? annotatedItems : (d.annotations || []);
      const agree = anns.filter((a) => a.status === "agree").length;
      const issues = anns.length - agree;
      const unanchored = annotatedItems.filter((a) => a.anchorState !== "anchored").length;
      const missingLen = (d.missingRows || []).length;
      const parts = [
        `One Verifiable Record: ${anns.length} claims marked (${issues} to check, ${agree} agree)`,
      ];
      if (missingLen > 0) parts.push(`${missingLen} missing here`);
      if (!res.inlineEnabled) parts.push("inline hidden");
      else if (unanchored > 0) parts.push(`${unanchored} in tray`);
      pill.textContent = parts.join(", ");
    } else if (d.legacy_badge) {
      pill.textContent = `One Verifiable Record: ${summaryPartsText(d)}`;
    } else if (d.analysis_status !== "ready") {
      pill.textContent = "One Verifiable Record: comparison is not ready for this snapshot";
    } else if (d.freshness === "stale") {
      pill.textContent = `One Verifiable Record (rev ${d.analysed_revision_id}): ${summaryPartsText(d)} — live page changed since analysis`;
    } else if (d.freshness === "unknown") {
      pill.textContent = `One Verifiable Record: ${summaryPartsText(d)} — inline marks need a known live revision`;
    } else {
      pill.textContent = `One Verifiable Record: ${summaryPartsText(d)}`;
    }
    wrap.append(pill);

    // Toggle inline findings (persists globally in chrome.storage.sync).
    if (available) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = res.inlineEnabled ? "secondary" : "";
      toggle.textContent = res.inlineEnabled ? "Hide inline findings" : "Show inline findings";
      toggle.addEventListener("click", async () => {
        const reply = await send({ type: "ovr:set-inline-enabled", enabled: !result.inlineEnabled });
        if (reply?.ok) {
          result.inlineEnabled = reply.inlineEnabled;
          renderMarks(d);
          renderBadge(result);
        }
      });
      wrap.append(toggle);
    }

    // Deep link to page-centric comparison route (#/page/{lang}/{title}, V-W10),
    // with X1 fallback when talking to a legacy QID badge endpoint.
    const cpLangs = (d.counterparts || [])
      .map((c) => c.lang)
      .filter((l) => l && l !== d.page?.lang);
    const comparisonHref = d.page
      ? compareUrl(res.webBase, d.page)
      : d.legacy_qid
        ? legacyQidUrl(res.webBase, d.legacy_qid)
        : null;
    if (comparisonHref) {
      const link = document.createElement("a");
      link.className = "open";
      link.href = comparisonHref;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = cpLangs.length
        ? `Compare with ${cpLangs.join(" · ")}`
        : "Open comparison";
      wrap.append(link);
    }

    // On stale pages, offer a 1-click jump to the exact analysed Wikipedia snapshot
    // where inline highlights are safe and active.
    if (d.freshness === "stale") {
      const revHref = safeWikiUrl(d.page?.url);
      if (revHref) {
        const revLink = document.createElement("a");
        revLink.className = "open";
        revLink.href = revHref;
        revLink.textContent = `Open analysed revision (${d.analysed_revision_id}) ↗`;
        wrap.append(revLink);
      }
    }

    // Findings tray: retains ambiguous/unanchored local claims and missing_here
    // rows alongside anchored findings, with keyboard-reachable popover/deep-link controls.
    const missingItems = (d.missingRows || []).map((m) => ({
      ...m,
      kind: kindKey(m.kind),
      anchorState: "missing_here",
      marker: null,
    }));
    const allFindings = [...annotatedItems, ...missingItems];

    let tray = null;
    if (available || allFindings.length > 0) {
      const trayId = "ovr-findings-tray";
      const show = document.createElement("button");
      show.type = "button";
      show.className = "secondary";
      const trayOnlyCount = allFindings.filter((f) => f.anchorState !== "anchored").length;
      show.textContent = trayOnlyCount > 0
        ? `Findings (${allFindings.length} · ${trayOnlyCount} tray-only)`
        : `Findings (${allFindings.length})`;
      show.setAttribute("aria-expanded", String(prevTrayOpen));
      show.setAttribute("aria-controls", trayId);
      wrap.append(show);

      tray = document.createElement("div");
      tray.className = "tray";
      tray.id = trayId;
      tray.hidden = !prevTrayOpen;

      const buildTrayContents = () => {
        tray.replaceChildren();
        const activeKinds = selectedKindsSet();
        const visibleFindings = allFindings.filter((a) => isKindActive(a.kind, activeKinds));
        if (!visibleFindings.length) {
          const empty = document.createElement("span");
          empty.className = "muted";
          empty.textContent = allFindings.length
            ? "No findings match the active claim-type filter."
            : "No inline findings are available for this snapshot.";
          tray.append(empty);
          return;
        }

        const note = document.createElement("p");
        note.className = "tray-note";
        note.textContent =
          "Ambiguous, paraphrased, and missing-on-this-page findings stay reachable here without guessing an inline text position.";
        tray.append(note);

        const groups = new Map();
        for (const item of visibleFindings) {
          const k = kindKey(item.kind);
          if (!groups.has(k)) groups.set(k, []);
          groups.get(k).push(item);
        }

        for (const [k, items] of groups.entries()) {
          const groupEl = document.createElement("div");
          groupEl.className = "kind-group";
          const head = document.createElement("div");
          head.className = "kind-head";
          head.innerHTML = `${esc(kindLabel(k))} <span class="muted">(${items.length})</span>`;
          groupEl.append(head);

          for (const item of items) {
            const card = document.createElement("div");
            card.className = `finding ${item.status}`;
            const snippet =
              item.claim?.text ||
              (item.counterparts?.[0]
                ? `${item.counterparts[0].lang}: ${item.counterparts[0].text || item.counterparts[0].value}`
                : "No local source text");
            const predText = humanise(item.claim?.predicate || item.predicate || "claim");
            card.innerHTML = `
              <div class="finding-head">
                <b>${esc(label(item.status))}</b>
                <span class="muted">· ${esc(predText)}</span>
                <span class="anchor-pill ${esc(item.anchorState)}">${esc(ANCHOR_LABELS[item.anchorState] || item.anchorState)}</span>
              </div>
              <div>${esc(snippet)}</div>
            `;

            const actions = document.createElement("div");
            actions.className = "finding-actions";

            if (item.marker && res.inlineEnabled && item.marker.button.dataset.filtered !== "1") {
              const jumpBtn = document.createElement("button");
              jumpBtn.type = "button";
              jumpBtn.className = "tray-btn";
              jumpBtn.textContent = "Jump to inline mark";
              jumpBtn.addEventListener("click", () => focusMarker(item.marker));
              actions.append(jumpBtn);
            }

            const inspectBtn = document.createElement("button");
            inspectBtn.type = "button";
            inspectBtn.className = "tray-btn primary";
            inspectBtn.setAttribute("aria-haspopup", "dialog");
            inspectBtn.textContent = "Inspect & receipts";
            inspectBtn.addEventListener("click", (e) => {
              e.stopPropagation();
              openPop(item, inspectBtn);
            });
            actions.append(inspectBtn);

            const focusHref = compareUrl(
              res.webBase,
              d.page,
              item.row_key ? { focus: item.row_key } : {},
            );
            if (focusHref) {
              const deep = document.createElement("a");
              deep.className = "tray-link";
              deep.href = focusHref;
              deep.target = "_blank";
              deep.rel = "noopener";
              deep.textContent = "Open focused comparison ↗";
              actions.append(deep);
            }

            if (["conflict", "possible_conflict", "missing_here"].includes(item.status)) {
              const patchHref = compareUrl(
                res.webBase,
                d.page,
                item.row_key
                  ? { focus: item.row_key, panel: "suggestions" }
                  : { panel: "suggestions" },
              );
              if (patchHref) {
                const patchLink = document.createElement("a");
                patchLink.className = "tray-link";
                patchLink.href = patchHref;
                patchLink.target = "_blank";
                patchLink.rel = "noopener";
                patchLink.textContent = "Draft a patch ↗";
                actions.append(patchLink);
              }
            }

            card.append(actions);
            groupEl.append(card);
          }
          tray.append(groupEl);
        }
      };

      buildTrayContents();
      show.addEventListener("click", () => {
        tray.hidden = !tray.hidden;
        show.setAttribute("aria-expanded", String(!tray.hidden));
      });
      host._rebuildTray = buildTrayContents;
    }

    // X5: "Next conflict (N)" button + per-type filter chips synced with storage.
    if (available && res.inlineEnabled) {
      const jumpBtn = document.createElement("button");
      jumpBtn.type = "button";
      const refreshJumpBtn = () => {
        const count = jumpableConflictMarkers().length;
        jumpBtn.hidden = count === 0;
        jumpBtn.textContent = `Next conflict (${count})`;
      };
      refreshJumpBtn();
      jumpBtn.addEventListener("click", () => jumpToNextConflict());
      wrap.append(jumpBtn);

      const annKinds = [
        ...new Set(allFindings.map((a) => kindKey(a.kind))),
      ];
      if (annKinds.length >= 1) {
        const chips = document.createElement("div");
        chips.className = "chips";
        const lbl = document.createElement("span");
        lbl.className = "lbl";
        lbl.textContent = "Show types:";
        chips.append(lbl);

        const activeKinds = selectedKindsSet();
        const chipButtons = new Map();

        const syncKindSelection = async (nextSet) => {
          const arr = [...nextSet];
          result.selectedKinds = arr;
          for (const [k, btn] of chipButtons.entries()) {
            btn.setAttribute("aria-pressed", String(nextSet.has(k)));
          }
          resetBtn.hidden = nextSet.size === 0;
          applyKindFilter(nextSet);
          refreshJumpBtn();
          host._rebuildTray?.();
          await send({ type: "ovr:set-selected-kinds", kinds: arr });
        };

        for (const k of annKinds) {
          const c = document.createElement("button");
          c.type = "button";
          c.className = "chip";
          c.textContent = kindLabel(k);
          c.setAttribute("aria-pressed", String(activeKinds.has(k)));
          c.addEventListener("click", () => {
            const next = selectedKindsSet();
            if (next.has(k)) next.delete(k);
            else next.add(k);
            syncKindSelection(next);
          });
          chipButtons.set(k, c);
          chips.append(c);
        }

        const resetBtn = document.createElement("button");
        resetBtn.type = "button";
        resetBtn.className = "chip";
        resetBtn.textContent = "All types";
        resetBtn.hidden = activeKinds.size === 0;
        resetBtn.addEventListener("click", () => syncKindSelection(new Set()));
        chips.append(resetBtn);

        wrap.append(chips);
      }
    }

    shadow.append(wrap);
    if (tray) shadow.append(tray);
    target.after(host);
  }

  async function update() {
    queued = false;
    if (updating) {
      rerun = true;
      return;
    }
    updating = true;
    try {
      const c = candidate();
      const url = location.href;
      const token = ++serial;
      lastUrl = url;
      clearMarks();
      document.getElementById(ROOT)?.remove();
      result = null;
      if (!c) {
        send({ type: "ovr:clear-badge" });
        return;
      }

      const summary = await send({ type: "ovr:resolve", candidate: c });
      if (token !== serial || url !== location.href || !summary?.visible || !summary.data?.tracked) {
        return;
      }

      const details = summary.data.legacy_badge
        ? null
        : await send({ type: "ovr:annotations", candidate: c });
      if (token !== serial || url !== location.href) return;

      const base = summary.data;
      const annData = details?.visible && details.data ? details.data : null;
      result = {
        visible: true,
        webBase: details?.webBase || summary.webBase,
        inlineEnabled: details?.inlineEnabled ?? summary.inlineEnabled ?? true,
        selectedKinds: details?.selectedKinds || summary.selectedKinds || [],
        data: {
          ...base,
          page: annData?.page || base.page,
          counterparts: base.counterparts || [],
          freshness: annData?.freshness || base.freshness,
          analysis_status: annData?.analysis_status || base.analysis_status,
          annotations: Array.isArray(annData?.annotations) ? annData.annotations : [],
          missingRows: Array.isArray(annData?.missing_here) ? annData.missing_here : [],
          missing_here_count: typeof base.missing_here === "number"
            ? base.missing_here
            : Array.isArray(annData?.missing_here)
              ? annData.missing_here.length
              : 0,
          annotations_unavailable: !annData,
        },
      };

      renderMarks(result.data);
      renderBadge(result);
    } finally {
      updating = false;
      if (rerun) {
        rerun = false;
        schedule();
      }
    }
  }

  function schedule() {
    if (updating) {
      rerun = true;
      return;
    }
    if (!queued) {
      queued = true;
      setTimeout(update, 60);
    }
  }

  // Global Escape & outside-click dismissal for the marker popover.
  addEventListener("keydown", (e) => {
    if (e.key === "Escape" && popover) {
      e.stopPropagation();
      closePopover(true);
    }
  });
  addEventListener("pointerdown", (e) => {
    if (!popover) return;
    const path = e.composedPath?.() || [];
    if (path.includes(popover) || (popoverTrigger && path.includes(popoverTrigger))) return;
    closePopover(false);
  });

  schedule();
  addEventListener("popstate", schedule);
  addEventListener("hashchange", schedule);
  addEventListener("scroll", reposition, true);
  addEventListener("resize", reposition);

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync") return;
    if ("apiBase" in changes || "enabled" in changes || "webBase" in changes) {
      schedule();
      return;
    }
    if (!result || location.href !== lastUrl) return;
    let changed = false;
    if ("inlineFindings" in changes) {
      const nextInline = changes.inlineFindings.newValue !== false;
      if (nextInline !== result.inlineEnabled) {
        result.inlineEnabled = nextInline;
        changed = true;
      }
    }
    if ("selectedKinds" in changes) {
      const nextKinds = Array.isArray(changes.selectedKinds.newValue)
        ? changes.selectedKinds.newValue.map(kindKey)
        : [];
      if (nextKinds.join(",") !== (result.selectedKinds || []).map(kindKey).join(",")) {
        result.selectedKinds = nextKinds;
        changed = true;
      }
    }
    if (changed) {
      renderMarks(result.data);
      renderBadge(result);
    }
  });

  // Wikipedia mutates constantly, including when this extension adds/removes
  // its own hosts. Only a page identity change may fetch again; ordinary DOM
  // changes merely reposition existing markers.
  new MutationObserver(() => {
    if (location.href !== lastUrl) schedule();
    else reposition();
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
