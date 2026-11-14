const defaults = {
  apiBase: "http://localhost:8000",
  webBase: "http://localhost:8080",
  enabled: true,
  inlineFindings: true,
  selectedKinds: [],
};

const BASE_KINDS = ["date", "number", "quantity", "entity_relation", "factoid"];
const KIND_LABELS = {
  date: "Date",
  number: "Number",
  quantity: "Quantity",
  entity_relation: "Relation",
  factoid: "Fact",
};

const apiInput = document.getElementById("apiBase");
const webInput = document.getElementById("webBase");
const enabledInput = document.getElementById("enabled");
const inlineInput = document.getElementById("inlineFindings");
const kindContainer = document.getElementById("kindFilters");
const clearKindsBtn = document.getElementById("clearKinds");
const openWebLink = document.getElementById("openWeb");
const status = document.getElementById("status");

let selectedKinds = new Set();
let availableKinds = [...BASE_KINDS];

function kindKey(k) {
  return String(k || "").trim().toLowerCase();
}

function kindLabel(k) {
  const key = kindKey(k);
  if (KIND_LABELS[key]) return KIND_LABELS[key];
  const s = key.replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Fact";
}

function setStatus(message, error = false) {
  status.textContent = message;
  status.className = error ? "error" : "";
}

function normaliseUrl(value, field) {
  let url;
  try {
    url = new URL(value.trim());
  } catch (_) {
    throw new Error(`${field} must be a complete URL`);
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error(`${field} must use HTTPS (or HTTP for localhost)`);
  }
  if (url.protocol === "http:" && !["localhost", "127.0.0.1"].includes(url.hostname)) {
    throw new Error(`${field} must use HTTPS outside local development`);
  }
  return url.href.replace(/\/+$/, "");
}

function originPattern(apiBase) {
  const url = new URL(apiBase);
  // Keep the port: Chrome match patterns treat "http://localhost/*" as port 80
  // only, so a dev API on :8000 would be unreachable. "http://localhost:8000/*"
  // (or :* in the manifest) is what actually grants the fetch.
  return `${url.protocol}//${url.host}/*`;
}

function updateOpenWebHref(rawWebBase) {
  try {
    openWebLink.href = normaliseUrl(rawWebBase, "Web app URL");
  } catch (_) {
    openWebLink.href = defaults.webBase;
  }
}

function renderKindChips() {
  kindContainer.replaceChildren();
  clearKindsBtn.hidden = selectedKinds.size === 0;
  for (const k of availableKinds) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.dataset.kind = k;
    chip.textContent = kindLabel(k);
    chip.setAttribute("aria-pressed", String(selectedKinds.has(k)));
    chip.addEventListener("click", async () => {
      if (selectedKinds.has(k)) selectedKinds.delete(k);
      else selectedKinds.add(k);
      renderKindChips();
      await chrome.storage.sync.set({ selectedKinds: [...selectedKinds] });
      setStatus("Updated type filter");
    });
    kindContainer.append(chip);
  }
}

function mergeAvailableKinds(extraKinds) {
  const merged = new Set(BASE_KINDS);
  for (const k of extraKinds || []) {
    const clean = kindKey(k);
    if (clean) merged.add(clean);
  }
  for (const k of selectedKinds) {
    if (k) merged.add(k);
  }
  availableKinds = [...merged];
}

chrome.storage.sync.get(defaults, (cfg) => {
  apiInput.value = cfg.apiBase;
  webInput.value = cfg.webBase;
  enabledInput.checked = Boolean(cfg.enabled);
  inlineInput.checked = cfg.inlineFindings !== false;
  selectedKinds = new Set(
    Array.isArray(cfg.selectedKinds) ? cfg.selectedKinds.map(kindKey).filter(Boolean) : [],
  );
  updateOpenWebHref(cfg.webBase);
  mergeAvailableKinds([]);
  renderKindChips();

  try {
    chrome.runtime.sendMessage({ type: "ovr:get-known-kinds" }, (reply) => {
      void chrome.runtime.lastError;
      if (reply?.kinds?.length) {
        mergeAvailableKinds(reply.kinds);
        renderKindChips();
      }
    });
  } catch (_) {
    // Base kinds are already rendered above.
  }
});

enabledInput.addEventListener("change", async () => {
  await chrome.storage.sync.set({ enabled: enabledInput.checked });
  setStatus(enabledInput.checked ? "Badge enabled" : "Badge paused");
});

inlineInput.addEventListener("change", async () => {
  await chrome.storage.sync.set({ inlineFindings: inlineInput.checked });
  setStatus(inlineInput.checked ? "Inline highlights enabled" : "Inline highlights hidden");
});

clearKindsBtn.addEventListener("click", async () => {
  selectedKinds.clear();
  renderKindChips();
  await chrome.storage.sync.set({ selectedKinds: [] });
  setStatus("Showing all claim types");
});

webInput.addEventListener("input", () => updateOpenWebHref(webInput.value));

document.getElementById("save").addEventListener("click", async () => {
  try {
    const apiBase = normaliseUrl(apiInput.value, "API URL");
    const webBase = normaliseUrl(webInput.value, "Web app URL");
    const pattern = originPattern(apiBase);
    const hasPermission = await chrome.permissions.contains({ origins: [pattern] });
    if (!hasPermission) {
      const granted = await chrome.permissions.request({ origins: [pattern] });
      if (!granted) throw new Error("Permission for the API host was not granted");
    }
    await chrome.storage.sync.set({
      apiBase,
      webBase,
      enabled: enabledInput.checked,
      inlineFindings: inlineInput.checked,
      selectedKinds: [...selectedKinds],
    });
    updateOpenWebHref(webBase);
    setStatus("Saved");
  } catch (error) {
    setStatus(error.message || "Could not save settings", true);
  }
});
