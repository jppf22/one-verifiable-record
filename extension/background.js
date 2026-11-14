// One Verifiable Record — Manifest V3 service worker.
// Network access and the short-lived badge cache live here rather than in the
// content script, so every Wikipedia tab shares one configuration and policy.
const DEFAULTS = Object.freeze({
  apiBase: "http://localhost:8000",
  webBase: "http://localhost:8080",
  enabled: true,
  inlineFindings: true,
});
const CACHE_TTL_MS = 5 * 60 * 1000;
const NEGATIVE_CACHE_TTL_MS = 60 * 1000;
const cache = new Map();

function normaliseBase(value) {
  const url = new URL(String(value || ""));
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error("unsupported protocol");
  return url.href.replace(/\/+$/, "");
}

async function settings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return {
    apiBase: normaliseBase(stored.apiBase),
    webBase: normaliseBase(stored.webBase),
    enabled: Boolean(stored.enabled),
    inlineFindings: stored.inlineFindings !== false,
  };
}

function cacheKey(cfg, candidate) {
  return [cfg.apiBase, candidate.lang, candidate.wikiPageId || "title", candidate.title, candidate.revisionId || "unknown"].join("|");
}

async function resolve(candidate) {
  let cfg;
  try {
    cfg = await settings();
  } catch (_) {
    return { visible: false };
  }
  if (!cfg.enabled || !candidate?.lang || !candidate?.title) return { visible: false };

  const key = cacheKey(cfg, candidate);
  const cached = cache.get(key);
  if (cached && cached.expiresAt > Date.now()) return { ...cached.value, webBase: cfg.webBase };

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
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    const data = await response.json();
    const value = { visible: Boolean(data?.tracked), data };
    cache.set(key, { value, expiresAt: Date.now() + (data?.tracked ? CACHE_TTL_MS : NEGATIVE_CACHE_TTL_MS) });
    return { ...value, webBase: cfg.webBase };
  } catch (_) {
    // A missing/offline companion must never interfere with Wikipedia.
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
    return { visible: Boolean(data?.tracked), data, webBase: cfg.webBase, inlineEnabled: cfg.inlineFindings };
  } catch (_) {
    return { visible: false };
  } finally {
    clearTimeout(timeout);
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.sync.get(DEFAULTS);
  await chrome.storage.sync.set({ ...DEFAULTS, ...current });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "ovr:resolve") resolve(message.candidate).then(sendResponse);
  else if (message?.type === "ovr:annotations") annotations(message.candidate).then(sendResponse);
  else if (message?.type === "ovr:set-inline-enabled") {
    chrome.storage.sync.set({ inlineFindings: Boolean(message.enabled) })
      .then(() => sendResponse({ ok: true, inlineEnabled: Boolean(message.enabled) }));
  } else return undefined;
  return true;
});

chrome.storage.onChanged.addListener(() => cache.clear());
