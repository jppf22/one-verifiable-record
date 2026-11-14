// Options popup: point the extension at the deployed stack.
const defaults = {
  apiBase: "http://localhost:8080",
  webBase: "http://localhost:8080",
  enabled: true,
};

chrome.storage.sync.get(defaults, (cfg) => {
  document.getElementById("apiBase").value = cfg.apiBase;
  document.getElementById("webBase").value = cfg.webBase;
  document.getElementById("enabled").checked = cfg.enabled;
});

document.getElementById("save").addEventListener("click", async () => {
  const status = document.getElementById("status");
  const apiBase = document.getElementById("apiBase").value.replace(/\/+$/, "");
  const webBase = document.getElementById("webBase").value.replace(/\/+$/, "");
  let origin;
  try {
    const url = new URL(apiBase);
    if (!/^https?:$/.test(url.protocol) || url.username || url.password) throw new Error();
    // Chrome match patterns grant a host, not just a port. Ask only for the
    // configured deployment rather than broad permanent access.
    origin = `${url.protocol}//${url.hostname}/*`;
  } catch {
    status.textContent = "Enter a valid http(s) API URL";
    return;
  }
  if (!(await chrome.permissions.contains({ origins: [origin] })) &&
      !(await chrome.permissions.request({ origins: [origin] }))) {
    status.textContent = "Host permission was not granted";
    return;
  }
  await chrome.storage.sync.set({ apiBase, webBase, enabled: document.getElementById("enabled").checked });
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1200);
});
