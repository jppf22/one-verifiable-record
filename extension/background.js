// One Verifiable Record — Manifest V3 service worker.
// Network access, toolbar badge updates, and the short-lived resolve cache live
// here rather than in the content script, so every Wikipedia tab shares one
// configuration and permission boundary.
const DEFAULTS = Object.freeze({
  apiBase: "http://localhost:8000",
  webBase: "http://localhost:8080",
  enabled: true,
  inlineFindings: true,
  selectedKinds: [],
});
const BASE_KINDS = ["date", "number", "quantity", "entity_relation", "factoid"];
const CACHE_TTL_MS = 5 * 60 * 1000;
const NEGATIVE_CACHE_TTL_MS = 60 * 1000;
const cache = new Map();
const observedKinds = new Set(BASE_KINDS);

function normaliseBase(value) {
  const url = new URL(String(value || ""));
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("unsupported protocol");
  return url.href.replace(/\/+$/, "");
}

function normaliseKinds(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  for (const item of list) {
    const k = String(item || "").trim().toLowerCase();
    if (k) seen.add(k);
  }
  return [...seen];
}

async function settings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return {
    apiBase: normaliseBase(stored.apiBase),
    webBase: normaliseBase(stored.webBase),
    enabled: Boolean(stored.enabled),
    inlineFindings: stored.inlineFindings !== false,
    selectedKinds: normaliseKinds(stored.selectedKinds),
  };
}

function cacheKey(cfg, candidate) {
  return [
    cfg.apiBase,
    candidate.lang,
    candidate.wikiPageId || "title",
    candidate.title,
    candidate.revisionId || "unknown",
  ].join("|");
}

function recordObservedKinds(data) {
  for (const a of data?.annotations || []) {
    if (a?.kind) observedKinds.add(String(a.kind).trim().toLowerCase());
  }
  for (const m of data?.missing_here || []) {
    if (m?.kind) observedKinds.add(String(m.kind).trim().toLowerCase());
  }
}

// X1 fallback: support older servers that expose GET /api/badge without
// /api/extension/resolve. Preserves both page-centric v2 badge payloads and
// legacy QID badge payloads (omitting missing gaps, falling back to counts).
async function resolveViaLegacyBadge(cfg, candidate, signal) {
  const url = new URL(`${cfg.apiBase}/api/badge`);
  url.searchParams.set("lang", candidate.lang);
  url.searchParams.set("title", String(candidate.title || "").trim().replace(/\s+/g, "_"));
  const response = await fetch(url, { signal, credentials: "omit" });
  if (response.status === 404) {
    return { visible: false, data: { tracked: false, reason: "not_imported" } };
  }
  if (!response.ok) throw new Error(`Badge API returned ${response.status}`);
  const raw = await response.json();

  // Page-centric v2 /api/badge response
  if (raw?.page) {
    const cps = Array.isArray(raw.counterparts) ? raw.counterparts : [];
    if (!cps.length) {
      return { visible: false, data: { tracked: false, reason: "no_counterparts" } };
    }
    const analysedRev = raw.page.revision_id ?? null;
    const freshness = candidate.revisionId == null
      ? "unknown"
      : analysedRev === candidate.revisionId
        ? "current"
        : "stale";
    return {
      visible: true,
      data: {
        tracked: true,
        page: raw.page,
        counterparts: cps,
        analysed_revision_id: analysedRev,
        live_revision_id: candidate.revisionId ?? null,
        freshness,
        analysis_status: "ready",
        conflicts: raw.conflicts ?? raw.counts?.contradicted ?? 0,
        possible_conflicts: raw.possible_conflicts ?? 0,
        missing_here: raw.missing_here ?? 0,
        only_here: raw.only_here ?? 0,
        possible_matches: raw.possible_matches ?? 0,
        agree: raw.agree ?? raw.counts?.supported ?? 0,
      },
    };
  }

  // Legacy v1 /api/badge response (X1 in docs/assumptions.md)
  const hasGaps = typeof raw?.gaps === "number";
  return {
    visible: true,
    data: {
      tracked: true,
      legacy_badge: true,
      legacy_qid: raw?.qid || null,
      page: null,
      counterparts: [],
      analysed_revision_id: null,
      live_revision_id: candidate.revisionId ?? null,
      freshness: "unknown",
      analysis_status: "ready",
      conflicts: raw?.conflicts ?? raw?.counts?.contradicted ?? 0,
      possible_conflicts: 0,
      gaps: hasGaps ? raw.gaps : null,
      omit_gaps: !hasGaps,
      missing_here: 0,
      only_here: 0,
      possible_matches: 0,
      agree: raw?.agree ?? raw?.counts?.supported ?? 0,
    },
  };
}

function clearTabBadge(tabId) {
  if (typeof tabId !== "number" || !chrome.action) return;
  chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  chrome.action.setTitle({ tabId, title: "One Verifiable Record" }).catch(() => {});
}

function updateTabBadge(tabId, result) {
  if (typeof tabId !== "number" || !chrome.action) return;
  if (!result?.visible || !result.data?.tracked) {
    clearTabBadge(tabId);
    return;
  }
  const d = result.data;
  const conflicts = (d.conflicts || 0) + (d.possible_conflicts || 0);
  const gaps = d.omit_gaps
    ? 0
    : typeof d.gaps === "number"
      ? d.gaps
      : (d.missing_here || 0) + (d.only_here || 0);
  const attention = conflicts + gaps + (d.possible_matches || 0);

  let text = "✓";
  let color = "#14866d";
  if (d.analysis_status === "not_ready") {
    text = "…";
    color = "#54595d";
  } else if (conflicts > 0) {
    text = conflicts > 99 ? "99+" : String(conflicts);
    color = "#b32424";
  } else if (attention > 0) {
    text = attention > 99 ? "99+" : String(attention);
    color = "#a65b00";
  }

  const titleParts = [
    `One Verifiable Record: ${d.page?.title || d.legacy_qid || "Tracked page"}`,
  ];
  if (d.conflicts > 0) titleParts.push(`${d.conflicts} conflict${d.conflicts === 1 ? "" : "s"}`);
  if (d.possible_conflicts > 0) titleParts.push(`${d.possible_conflicts} possible conflict${d.possible_conflicts === 1 ? "" : "s"}`);
  if (!d.omit_gaps) {
    if (typeof d.gaps === "number" && d.gaps > 0) titleParts.push(`${d.gaps} gap${d.gaps === 1 ? "" : "s"}`);
    else {
      if (d.missing_here > 0) titleParts.push(`${d.missing_here} missing here`);
      if (d.only_here > 0) titleParts.push(`${d.only_here} only here`);
    }
  }
  if (d.agree > 0) titleParts.push(`${d.agree} agree`);
  if (d.freshness === "stale") titleParts.push("snapshot behind live revision");

  chrome.action.setBadgeText({ tabId, text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ tabId, color }).catch(() => {});
  chrome.action.setTitle({ tabId, title: titleParts.join(" · ") }).catch(() => {});
}

async function resolve(candidate, tabId) {
  let cfg;
  try {
    cfg = await settings();
  } catch (_) {
    clearTabBadge(tabId);
    return { visible: false };
  }
  if (!cfg.enabled || !candidate?.lang || !candidate?.title) {
    clearTabBadge(tabId);
    return { visible: false };
  }

  const key = cacheKey(cfg, candidate);
  const cached = cache.get(key);
  if (cached && cached.expiresAt > Date.now()) {
    const out = {
      ...cached.value,
      webBase: cfg.webBase,
      inlineEnabled: cfg.inlineFindings,
      selectedKinds: cfg.selectedKinds,
    };
    updateTabBadge(tabId, out);
    return out;
  }

  const url = new URL(`${cfg.apiBase}/api/extension/resolve`);
  url.searchParams.set("lang", candidate.lang);
  if (candidate.wikiPageId) url.searchParams.set("wiki_page_id", String(candidate.wikiPageId));
  // The API resolves page id first and only then uses this title fallback.
  url.searchParams.set("title", candidate.title);
  if (candidate.revisionId) url.searchParams.set("live_revision_id", String(candidate.revisionId));

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(url, { signal: controller.signal, credentials: "omit" });
    let value;
    if (response.status === 404) {
      value = await resolveViaLegacyBadge(cfg, candidate, controller.signal);
    } else {
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      const data = await response.json();
      const hasCounterparts = Array.isArray(data?.counterparts) && data.counterparts.length > 0;
      value = { visible: Boolean(data?.tracked && hasCounterparts), data };
    }
    cache.set(key, {
      value,
      expiresAt: Date.now() + (value.visible ? CACHE_TTL_MS : NEGATIVE_CACHE_TTL_MS),
    });
    const out = {
      ...value,
      webBase: cfg.webBase,
      inlineEnabled: cfg.inlineFindings,
      selectedKinds: cfg.selectedKinds,
    };
    updateTabBadge(tabId, out);
    return out;
  } catch (_) {
    // A missing/offline companion must never interfere with Wikipedia.
    clearTabBadge(tabId);
    return { visible: false };
  } finally {
    clearTimeout(timeout);
  }
}

async function annotations(candidate) {
  let cfg;
  try {
    cfg = await settings();
  } catch (_) {
    return { visible: false };
  }
  if (!cfg.enabled || !candidate?.lang || !candidate?.title) return { visible: false };
  const url = new URL(`${cfg.apiBase}/api/extension/annotations`);
  url.searchParams.set("lang", candidate.lang);
  // Send both stable id and title: the server resolves the id first and title
  // is its documented fallback for skins with incomplete metadata.
  if (candidate.wikiPageId) url.searchParams.set("wiki_page_id", String(candidate.wikiPageId));
  url.searchParams.set("title", candidate.title);
  if (candidate.revisionId) url.searchParams.set("live_revision_id", String(candidate.revisionId));

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(url, { signal: controller.signal, credentials: "omit" });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    const data = await response.json();
    recordObservedKinds(data);
    return {
      visible: Boolean(data?.tracked),
      data,
      webBase: cfg.webBase,
      inlineEnabled: cfg.inlineFindings,
      selectedKinds: cfg.selectedKinds,
    };
  } catch (_) {
    return { visible: false };
  } finally {
    clearTimeout(timeout);
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.sync.get(DEFAULTS);
  await chrome.storage.sync.set({
    ...DEFAULTS,
    ...current,
    selectedKinds: normaliseKinds(current.selectedKinds),
  });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender?.tab?.id;
  if (message?.type === "ovr:resolve") {
    resolve(message.candidate, tabId).then(sendResponse);
  } else if (message?.type === "ovr:annotations") {
    annotations(message.candidate).then(sendResponse);
  } else if (message?.type === "ovr:clear-badge") {
    clearTabBadge(tabId);
    sendResponse({ ok: true });
  } else if (message?.type === "ovr:set-inline-enabled") {
    const inlineFindings = Boolean(message.enabled);
    chrome.storage.sync.set({ inlineFindings })
      .then(() => sendResponse({ ok: true, inlineEnabled: inlineFindings }));
  } else if (message?.type === "ovr:set-selected-kinds") {
    const selectedKinds = normaliseKinds(message.kinds);
    chrome.storage.sync.set({ selectedKinds })
      .then(() => sendResponse({ ok: true, selectedKinds }));
  } else if (message?.type === "ovr:get-known-kinds") {
    sendResponse({ kinds: [...observedKinds] });
  } else {
    return undefined;
  }
  return true;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync") return;
  if ("apiBase" in changes || "enabled" in changes) {
    cache.clear();
  }
});
