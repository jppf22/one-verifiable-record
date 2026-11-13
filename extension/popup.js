// Options popup: point the extension at the deployed stack.
const defaults = {
  apiBase: "http://localhost:8000",
  webBase: "http://localhost:8080",
  enabled: true,
};

chrome.storage.sync.get(defaults, (cfg) => {
  document.getElementById("apiBase").value = cfg.apiBase;
  document.getElementById("webBase").value = cfg.webBase;
  document.getElementById("enabled").checked = cfg.enabled;
});

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    apiBase: document.getElementById("apiBase").value.replace(/\/+$/, ""),
    webBase: document.getElementById("webBase").value.replace(/\/+$/, ""),
    enabled: document.getElementById("enabled").checked,
  });
  const status = document.getElementById("status");
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1200);
});
