/* One Verifiable Record — merge view frontend. Talks to /api (proxied). */
"use strict";

const API = "/api";
let currentQid = null;

const STATUS_META = {
  conflict: { label: "Conflicted", cls: "conflict" },
  gap: { label: "Gap", cls: "gap" },
  agree: { label: "Agreeing", cls: "agree" },
};

const $ = (sel) => document.querySelector(sel);

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function esc(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/* ---------- search ---------- */

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#search-input").value.trim();
  if (!q) return;
  const rows = await getJSON(`${API}/articles?q=${encodeURIComponent(q)}`);
  const list = $("#results");
  list.innerHTML = "";
  if (!rows.length) {
    list.innerHTML = `<p class="muted">No tracked articles match. Import a dataset first.</p>`;
    return;
  }
  for (const r of rows) {
    const item = el(
      `<button class="result" data-qid="${esc(r.wikidata_qid)}">
         <b>${esc(r.title)}</b>
         <span class="muted">${esc(r.wikidata_qid)}</span>
       </button>`
    );
    item.addEventListener("click", () => openMerge(item.dataset.qid));
    list.appendChild(item);
  }
});

/* ---------- merge view ---------- */

async function openMerge(qid) {
  currentQid = qid;
  $("#article-picker").hidden = true;
  $("#merge-view").hidden = false;
  $("#clusters").innerHTML = `<p class="muted">Aligning claims across language editions…</p>`;
  try {
    await getJSON(`${API}/align/${encodeURIComponent(qid)}`, { method: "POST" });
  } catch (err) {
    /* alignment may already exist; the merge view below is source of truth */
  }
  const data = await getJSON(`${API}/merge/${encodeURIComponent(qid)}`);
  renderMerge(qid, data);
}

function renderMerge(qid, data) {
  const header = $("#merge-header");
  const title = data.articles[0]?.title || qid;
  const langs = data.articles.map((a) => a.lang).join(" / ");
  header.innerHTML = `
    <h2>${esc(title)}</h2>
    <p class="muted">${esc(langs)} · ${data.clusters.length} claim clusters</p>
    <div class="toolbar">
      <button id="btn-suggestions">Generate patch suggestions</button>
    </div>`;

  const clustersEl = $("#clusters");
  clustersEl.innerHTML = "";
  for (const c of data.clusters) {
    const meta = STATUS_META[c.status];
    const card = el(
      `<article class="cluster ${meta.cls}">
         <header>
           <span class="badge ${meta.cls}">${meta.label}</span>
           <span class="muted">${c.langs_present.map(esc).join(", ")}</span>
         </header>
         <div class="claim-grid"></div>
         <div class="links"></div>
       </article>`
    );
    const grid = card.querySelector(".claim-grid");
    for (const claim of c.claims) {
      const cell = el(
        `<div class="claim">
           <span class="lang">${esc(claim.lang)}</span>
           <p>${esc(claim.statement_text)}</p>
           <span class="muted">rev ${esc(claim.revision_id ?? "?")} · ${esc(claim.kind)}</span>
           <button class="history" data-id="${claim.id}">History</button>
         </div>`
      );
      cell.querySelector(".history").addEventListener("click", (ev) =>
        showHistory(ev.target.dataset.id)
      );
      grid.appendChild(cell);
    }
    const linksEl = card.querySelector(".links");
    for (const l of c.links) {
      if (l.verdict === "unmatched") continue;
      linksEl.appendChild(el(
        `<p class="verdict ${esc(l.verdict)}">
           <b>${esc(l.verdict)}</b> ${esc(l.rationale || "")}
         </p>`
      ));
    }
    clustersEl.appendChild(card);
  }
  $("#btn-suggestions").addEventListener("click", () => makeSuggestions(qid));
}

/* ---------- provenance (SYSTEM_TIME) ---------- */

async function showHistory(claimId) {
  const data = await getJSON(`${API}/claims/${claimId}/history`);
  const rows = (data.claim || []).map((r) =>
    `<tr><td>${esc(r.ROW_START)}</td><td>${esc(r.ROW_END ?? "now")}</td>
     <td>${esc(r.statement_text)}</td></tr>`
  ).join("");
  const linkRows = (data.links || []).map((r) =>
    `<tr><td>${esc(r.ROW_START)}</td><td>${esc(r.ROW_END ?? "now")}</td>
     <td>${esc(r.verdict)} (${esc(r.verdict_source)})</td></tr>`
  ).join("");
  $("#modal-body").innerHTML = `
    <h3>Biography of a claim</h3>
    <table>
      <thead><tr><th>From</th><th>Until</th><th>Statement</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <h3>Linked verdicts over time</h3>
    <table>
      <thead><tr><th>From</th><th>Until</th><th>Verdict</th></tr></thead>
      <tbody>${linkRows}</tbody>
    </table>`;
  $("#modal").showModal();
}

$("#modal-close").addEventListener("click", () => $("#modal").close());

/* ---------- patch suggestions ---------- */

async function makeSuggestions(qid) {
  const out = $("#suggestions");
  out.hidden = false;
  out.innerHTML = `<p class="muted">Generating…</p>`;
  const res = await getJSON(`${API}/suggestions/${encodeURIComponent(qid)}`);
  out.innerHTML = `
    <h2>Patch suggestions (${res.drafts.length})</h2>
    <p class="muted">Export-only: proposed wikitext for human editors.
    We never apply edits automatically.</p>` +
    res.drafts.map((d) =>
      `<div class="draft">
         <b>${esc(d.lang)} · ${esc(d.patch_type)}</b>
         <pre>${esc(d.wikitext)}</pre>
       </div>`
    ).join("");
}

/* ---------- grounded Q&A ---------- */

$("#qa-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!currentQid) return;
  const q = $("#qa-input").value.trim();
  if (!q) return;
  const box = $("#qa-answer");
  box.innerHTML = `<p class="muted">Retrieving sources…</p>`;
  const data = await getJSON(
    `${API}/qa?qid=${encodeURIComponent(currentQid)}&q=${encodeURIComponent(q)}`
  );
  box.innerHTML = `
    <p>${esc(data.answer)}</p>
    <ul class="citations">` +
    data.citations.map((c) =>
      `<li><a href="${esc(c.url)}" target="_blank" rel="noopener">
         [${esc(c.lang)}] ${esc(c.title)} (rev ${esc(c.revision_id)})
       </a></li>`
    ).join("") +
    `</ul>`;
});
