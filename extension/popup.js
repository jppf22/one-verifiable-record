const defaults = {
  apiBase: "http://localhost:8000",
  webBase: "http://localhost:8080",
  enabled: true,
};

const apiInput = document.getElementById("apiBase");
const webInput = document.getElementById("webBase");
const enabledInput = document.getElementById("enabled");
const status = document.getElementById("status");

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

chrome.storage.sync.get(defaults, (cfg) => {
  apiInput.value = cfg.apiBase;
  webInput.value = cfg.webBase;
  enabledInput.checked = cfg.enabled;
});

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
    await chrome.storage.sync.set({ apiBase, webBase, enabled: enabledInput.checked });
    setStatus("Saved");
  } catch (error) {
    setStatus(error.message || "Could not save settings", true);
  }
});
