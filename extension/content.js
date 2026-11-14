// One Verifiable Record — Wikipedia companion badge.
// Reads the article title + language from the page, asks the API for a
// cross-language summary, and renders a badge under the heading that deep
// links into the merge view.
(async () => {
  const cfg = await chrome.storage.sync.get({
    apiBase: "http://localhost:8080",
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
  const num = (v) => (Number.isFinite(v) && v > 0 ? v : 0);
  // Prefer cluster-level counts; fall back to link-level for an older server.
  const conflicts = num(data.conflicts ?? counts.contradicted);
  const hasGaps = data.gaps !== undefined && data.gaps !== null;
  const gaps = num(data.gaps);
  const supported = num(counts.supported);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  const parts = [plural(conflicts, "conflict")];
  if (hasGaps) parts.push(plural(gaps, "gap"));
  parts.push(plural(supported, "verified cross-language claim"));

  const tone = conflicts > 0 ? "ovr-alert" : gaps > 0 ? "ovr-warn" : "ovr-ok";

  // Built with DOM APIs only: this is injected into Wikipedia pages.
  const badge = document.createElement("div");
  badge.className = "ovr-badge";

  const pill = document.createElement("span");
  pill.className = `ovr-pill ${tone}`;
  pill.textContent = `One Verifiable Record: ${parts.join(", ")}`;
  badge.append(pill);

  if (data.qid) {
    const link = document.createElement("a");
    link.className = "ovr-link";
    link.target = "_blank";
    link.rel = "noopener";
    link.href = `${cfg.webBase}/#/merge/${encodeURIComponent(data.qid)}`;
    link.textContent = "Open merge view";
    badge.append(link);
  }

  heading.after(badge);
})();
