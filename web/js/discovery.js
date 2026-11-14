/* Landing-page discovery: pages are entities (not language-edition rows).
 * It surfaces verified, conflict-heavy work first and deliberately includes
 * non-biographical topics so the demo is useful beyond people articles. */
import { api } from "./api.js";
import { $, esc } from "./util.js";
import { openMerge } from "./router.js";

let loaded = false;

function editions(card) {
  return (card.editions || []).map((edition) =>
    `<span class="lang-tag">${esc(edition.lang)}</span>${esc(edition.title)}`).join("");
}

function card(item, { conflicts = false } = {}) {
  const count = Number(item.conflicts || 0);
  const meta = conflicts
    ? `<span class="conflict-count">${count} conflict${count === 1 ? "" : "s"}</span>`
    : `<span class="topic-type">${esc(item.topic_type || "Topic")}</span>`;
  return `<button type="button" class="discover-card" data-qid="${esc(item.qid)}">
      <span class="discover-card-title">${esc(item.title)}</span>
      <span class="discover-card-meta">${meta}<span class="discover-editions">${editions(item)}</span></span>
    </button>`;
}

function render(data) {
  const high = data.high_conflicts || [];
  const diverse = data.diverse || [];
  $("#discovery").innerHTML = `
    <div class="discovery-grid">
      <section class="discovery-section" aria-labelledby="high-conflicts-heading">
        <div class="discovery-heading"><h2 id="high-conflicts-heading">Most conflicts</h2>
          <p class="muted">Pages with the most contradicted claim links.</p></div>
        ${high.length ? `<div class="discover-list">${high.map((item) => card(item, { conflicts: true })).join("")}</div>`
          : `<p class="discover-empty muted">No conflicts have been graded yet.</p>`}
      </section>
      <section class="discovery-section" aria-labelledby="more-topics-heading">
        <div class="discovery-heading"><h2 id="more-topics-heading">Explore more topics</h2>
          <p class="muted">Places, organizations, events, works, and other non-biographical pages.</p></div>
        ${diverse.length ? `<div class="discover-list">${diverse.map((item) => card(item)).join("")}</div>`
          : `<p class="discover-empty muted">More topic types appear once claims have been extracted.</p>`}
      </section>
    </div>`;
}

async function load() {
  const host = $("#discovery");
  host.innerHTML = `<p class="muted discovery-loading"><span class="spinner" aria-hidden="true"></span> Finding pages to explore…</p>`;
  try {
    render(await api.discover());
    loaded = true;
  } catch (err) {
    host.innerHTML = `<p class="muted">Couldn't load page discovery: ${esc(err.message)}
      <button type="button" class="link-btn" data-discovery-retry>Retry</button></p>`;
  }
}

export function init() {
  const host = $("#discovery");
  host.addEventListener("click", (event) => {
    const page = event.target.closest("[data-qid]");
    if (page) openMerge(page.dataset.qid);
    if (event.target.closest("[data-discovery-retry]")) load();
  });
  if (!loaded) load();
}
