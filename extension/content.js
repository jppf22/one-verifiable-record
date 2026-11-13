// One Verifiable Record — Wikipedia companion badge.
// Reads the article title + language from the page, asks the API for a
// cross-language summary, and renders a badge under the heading that deep
// links into the merge view.
(async () => {
  const cfg = await chrome.storage.sync.get({
    apiBase: "http://localhost:8000",
    webBase: "http://localhost:8080",
    enabled: true,
  });
  if (!cfg.enabled) return;

  const match = location.pathname.match(/^\/wiki\/([^#?]+)/);
  if (!match) return;
  const lang = location.hostname.split(".")[0];
  const title = decodeURIComponent(match[1]).replace(/_/g, " ");

  let data;
  try {
    const res = await fetch(
      `${cfg.apiBase}/api/badge?lang=${encodeURIComponent(lang)}` +
      `&title=${encodeURIComponent(title)}`
    );
    if (!res.ok) return;
    data = await res.json();
  } catch (err) {
    return; // API unreachable; stay silent on the page
  }

  const heading = document.querySelector("#firstHeading") || document.querySelector("h1");
  if (!heading) return;

  const counts = data.counts || {};
  const conflicts = counts.contradicted || 0;
  const supported = counts.supported || 0;

  const badge = document.createElement("div");
  badge.className = "ovr-badge";
  badge.innerHTML = `
    <span class="ovr-pill ${conflicts > 0 ? "ovr-alert" : "ovr-ok"}">
      One Verifiable Record: ${conflicts} conflict${conflicts === 1 ? "" : "s"},
      ${supported} verified cross-language claim${supported === 1 ? "" : "s"}
    </span>
    <a class="ovr-link" target="_blank" rel="noopener"
       href="${cfg.webBase}/?qid=${encodeURIComponent(data.qid)}">
      Open merge view
    </a>`;
  heading.after(badge);
})();
