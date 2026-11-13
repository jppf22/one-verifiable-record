// One Verifiable Record — service worker.
// Seeds the default configuration on install. The popup lets the user
// change the API/web base URLs (e.g. the Verda VM address for the demo).
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.set({
    apiBase: "http://localhost:8000",
    webBase: "http://localhost:8080",
    enabled: true,
  });
});
