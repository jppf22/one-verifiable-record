/* "Biography of a claim": one vertical timeline built from MariaDB
 * System-Versioned Tables (FOR SYSTEM_TIME ALL). It interleaves claim versions,
 * verdict versions of every link touching the claim, and human reviews.
 * Owner: panels. Listens to bus `history:open {claimId}`. */
import { api } from "./api.js";
import { on } from "./bus.js";
import { $, esc, fmtTime, toast } from "./util.js";

const SOURCE_LABEL = { deterministic: "deterministic", llm: "LLM", human: "human review" };
const VERDICT_CLASS = {
  contradicted: "bad", supported: "ok", definition_mismatch: "warn",
  unverifiable: "warn", unmatched: "muted",
};
const TYPE_ORDER = { claim: 0, verdict: 1, review: 2 };

let dialog;
let returnFocus = null;
let requestSeq = 0;

export function init() {
  dialog = $("#history-dialog");
  if (!dialog) return;
  dialog.classList.add("hist-dialog");
  dialog.setAttribute("aria-labelledby", "hist-title");
  dialog.addEventListener("close", () => {
    requestSeq++; // drop any in-flight response
    if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
    returnFocus = null;
  });
  // Click on the backdrop closes the dialog.
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener("click", onDialogClick);
  on("history:open", (p) => { if (p && p.claimId != null) open(p.claimId); });
}

async function open(claimId) {
  const seq = ++requestSeq;
  if (!dialog.open) {
    returnFocus = document.activeElement;
    dialog.showModal();
  }
  render(claimId, `<div class="hist-state" role="status">
      <span class="hist-spinner" aria-hidden="true"></span>
      Reading <code>FOR SYSTEM_TIME ALL</code> history…
    </div>`);
  dialog.querySelector(".hist-close")?.focus();

  let data;
  try {
    data = await api.claimHistory(claimId);
  } catch (err) {
    if (seq !== requestSeq) return;
    render(claimId, `<div class="hist-state error" role="alert">
        Could not load the history of claim #${esc(claimId)}: ${esc(err.message)}
        <div><button type="button" class="hist-retry" data-claim="${esc(claimId)}">Retry</button></div>
      </div>`);
    return;
  }
  if (seq !== requestSeq) return;
  const events = buildEvents(data);
  const body = events.length
    ? summary(data) + `<ol class="hist-timeline">${events.map(renderEvent).join("")}
        <li class="hist-event hist-now" aria-label="now"><span class="hist-dot" aria-hidden="true"></span>
          <div class="hist-card"><span class="hist-when">now</span></div></li></ol>`
    : `<div class="hist-state">No system-versioned rows were found for claim #${esc(claimId)}.</div>`;
  render(claimId, body, data);
}

function render(claimId, bodyHtml, data = null) {
  const current = data?.claim?.length ? data.claim[data.claim.length - 1] : null;
  dialog.innerHTML = `<div class="hist-inner">
    <div class="hist-head">
      <div>
        <h2 id="hist-title">Biography of a claim <span class="muted">#${esc(claimId)}</span></h2>
        ${current ? `<p class="hist-current">${esc(current.statement_text)}</p>` : ""}
      </div>
      <button type="button" class="hist-close" aria-label="Close history">×</button>
    </div>
    <div class="hist-tools">
      <span class="hist-powered">Powered by MariaDB <b>system-versioned tables</b>.
        Every row version is kept by the database itself.</span>
      ${data ? `<button type="button" class="hist-sql-toggle" aria-expanded="false" aria-controls="hist-sql">Show SQL</button>` : ""}
    </div>
    ${data ? `<div id="hist-sql" class="hist-sql" hidden>${sqlBlock(claimId, data)}</div>` : ""}
    <div class="hist-body">${bodyHtml}</div></div>`;
}

function onDialogClick(e) {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.classList.contains("hist-close")) dialog.close();
  else if (t.classList.contains("hist-retry")) open(t.dataset.claim);
  else if (t.classList.contains("hist-sql-toggle")) {
    const box = dialog.querySelector("#hist-sql");
    const show = box.hidden;
    box.hidden = !show;
    t.setAttribute("aria-expanded", String(show));
    t.textContent = show ? "Hide SQL" : "Show SQL";
  } else if (t.classList.contains("hist-sql-copy")) {
    const text = dialog.querySelector("#hist-sql pre")?.textContent || "";
    navigator.clipboard?.writeText(text).then(
      () => toast("SQL copied", "ok"),
      () => toast("Copy failed. Select the text manually.", "warn"),
    );
  }
}

/* ---------- data → events ---------- */

const tsKey = (ts) => (ts == null ? "" : String(ts).replace(" ", "T"));
function isOpenEnded(rowEnd) {
  if (!rowEnd) return true;
  const y = parseInt(String(rowEnd).slice(0, 4), 10);
  return !Number.isFinite(y) || y >= 2038;
}

function buildEvents(data) {
  const events = [];
  const claims = [...(data.claim || [])].sort((a, b) => tsKey(a.ROW_START).localeCompare(tsKey(b.ROW_START)));
  claims.forEach((row, i) => events.push({
    type: "claim", start: row.ROW_START, end: row.ROW_END, row,
    prev: i > 0 ? claims[i - 1] : null, n: i + 1, of: claims.length,
  }));

  const byLink = new Map();
  for (const row of data.links || []) {
    if (!byLink.has(row.id)) byLink.set(row.id, []);
    byLink.get(row.id).push(row);
  }
  for (const rows of byLink.values()) {
    rows.sort((a, b) => tsKey(a.ROW_START).localeCompare(tsKey(b.ROW_START)));
    rows.forEach((row, i) => events.push({
      type: "verdict", start: row.ROW_START, end: row.ROW_END, row, prev: i > 0 ? rows[i - 1] : null,
    }));
  }
  for (const row of data.reviews || []) {
    events.push({ type: "review", start: row.ROW_START || row.created_at, end: row.ROW_END, row });
  }
  return events.sort((a, b) =>
    tsKey(a.start).localeCompare(tsKey(b.start)) || TYPE_ORDER[a.type] - TYPE_ORDER[b.type]);
}

function summary(data) {
  const links = new Set((data.links || []).map((l) => l.id));
  const sources = new Set((data.links || []).map((l) => l.verdict_source));
  if ((data.reviews || []).length) sources.add("human");
  const step = (key) =>
    `<span class="hist-step src-${key}${sources.has(key) ? " on" : ""}">${esc(SOURCE_LABEL[key])}</span>`;
  return `<div class="hist-summary">
      <span><b>${(data.claim || []).length}</b> claim version(s)</span>
      <span><b>${(data.links || []).length}</b> verdict version(s) across <b>${links.size}</b> link(s)</span>
      <span><b>${(data.reviews || []).length}</b> review(s)</span>
      <span class="hist-pipeline" aria-label="Verdict sources seen">
        ${step("deterministic")}<span aria-hidden="true">→</span>${step("llm")}<span aria-hidden="true">→</span>${step("human")}
      </span>
    </div>`;
}

function whenRange(ev) {
  const current = isOpenEnded(ev.end);
  return `<span class="hist-when">
      <time datetime="${esc(tsKey(ev.start))}">${esc(fmtTime(ev.start))}</time>
      → ${current ? `<span class="hist-current-chip">current</span>`
                  : `<time datetime="${esc(tsKey(ev.end))}">${esc(fmtTime(ev.end))}</time>`}
    </span>`;
}

function renderEvent(ev) {
  const r = ev.row;
  const live = isOpenEnded(ev.end) ? " live" : "";
  if (ev.type === "claim") {
    let body;
    if (!ev.prev) {
      body = `<p class="hist-text">${esc(r.statement_text)}</p>`;
    } else if (ev.prev.statement_text !== r.statement_text) {
      body = `<p class="hist-text hist-diff">${wordDiff(ev.prev.statement_text, r.statement_text)}</p>`;
    } else {
      const changed = ["kind", "value_normalized", "model_version"].filter((k) => (ev.prev[k] ?? null) !== (r[k] ?? null));
      body = `<p class="hist-text">${esc(r.statement_text)}</p>
        <p class="muted">Statement unchanged${changed.length ? `; changed: ${changed.map((k) =>
          `<code>${esc(k)}</code> ${esc(ev.prev[k] ?? "∅")} → ${esc(r[k] ?? "∅")}`).join(", ")}` : ""}.</p>`;
    }
    const meta = [r.kind && `kind ${esc(r.kind)}`, r.model_version && `extractor ${esc(r.model_version)}`]
      .filter(Boolean).join(" · ");
    return `<li class="hist-event t-claim${live}">
        <span class="hist-dot" aria-hidden="true">¶</span>
        <div class="hist-card">
          <div class="hist-meta">
            <span class="hist-type">${ev.prev ? "Claim edited" : "Claim extracted"}</span>
            <span class="muted">version ${ev.n} of ${ev.of}</span>${whenRange(ev)}
          </div>
          ${body}
          ${meta ? `<p class="muted">${meta}</p>` : ""}
        </div></li>`;
  }

  if (ev.type === "verdict") {
    const cls = VERDICT_CLASS[r.verdict] || "muted";
    const change = ev.prev && ev.prev.verdict !== r.verdict
      ? `<span class="hist-verdict ${VERDICT_CLASS[ev.prev.verdict] || "muted"} was">${esc(ev.prev.verdict)}</span><span aria-hidden="true">→</span>`
      : "";
    const sourceChange = ev.prev && ev.prev.verdict_source !== r.verdict_source
      ? `${esc(SOURCE_LABEL[ev.prev.verdict_source] || ev.prev.verdict_source)} → ` : "";
    const sim = r.similarity != null && !isNaN(r.similarity)
      ? `<span class="hist-sim" title="1 − cosine distance via VEC_DISTANCE_COSINE over the HNSW vector index">sim ${Number(r.similarity).toFixed(3)}</span>`
      : "";
    return `<li class="hist-event t-verdict v-${cls}${live}">
        <span class="hist-dot" aria-hidden="true">◆</span>
        <div class="hist-card">
          <div class="hist-meta">
            <span class="hist-type">${ev.prev ? "Verdict re-graded" : "Verdict computed"}</span>
            <span class="muted">link #${esc(r.id)} · claims ${esc(r.claim_a)} ↔ ${esc(r.claim_b)}</span>${whenRange(ev)}
          </div>
          <p class="hist-verdict-line">${change}<span class="hist-verdict ${cls}">${esc(r.verdict)}</span>
            <span class="hist-src src-${esc(r.verdict_source)}">${sourceChange}${esc(SOURCE_LABEL[r.verdict_source] || r.verdict_source)}</span>
            ${sim}</p>
          ${r.rationale ? `<p class="hist-text">${esc(r.rationale)}</p>` : ""}
          ${r.evidence ? `<details class="hist-evidence"><summary>Evidence (receipts)</summary><blockquote>${esc(r.evidence)}</blockquote></details>` : ""}
        </div></li>`;
  }

  // review
  const dcls = { agree: "ok", disagree: "bad", fix: "warn" }[r.decision] || "muted";
  return `<li class="hist-event t-review${live}">
      <span class="hist-dot" aria-hidden="true">✓</span>
      <div class="hist-card">
        <div class="hist-meta">
          <span class="hist-type">Human review</span>
          <span class="muted">link #${esc(r.claim_link_id)} · by ${esc(r.reviewer)}</span>${whenRange(ev)}
        </div>
        <p class="hist-verdict-line"><span class="hist-verdict ${dcls}">${esc(r.decision)}</span>
          <span class="hist-src src-human">human review</span></p>
        ${r.note ? `<p class="hist-text">“${esc(r.note)}”</p>` : ""}
      </div></li>`;
}

/* ---------- word-level diff (LCS) ---------- */

function wordDiff(a, b) {
  const A = String(a ?? "").split(/(\s+)/).filter(Boolean);
  const B = String(b ?? "").split(/(\s+)/).filter(Boolean);
  if (A.length * B.length > 250000) {
    return `<del class="hist-del">${esc(a)}</del> <ins class="hist-ins">${esc(b)}</ins>`;
  }
  const n = A.length, m = B.length;
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    }
  }
  const ops = [];
  const push = (op, tok) => {
    const last = ops[ops.length - 1];
    if (last && last.op === op) last.text += tok; else ops.push({ op, text: tok });
  };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { push("eq", A[i]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) push("del", A[i++]);
    else push("ins", B[j++]);
  }
  while (i < n) push("del", A[i++]);
  while (j < m) push("ins", B[j++]);
  return ops.map(({ op, text }) =>
    op === "eq" ? esc(text)
      : op === "del" ? `<del class="hist-del">${esc(text)}</del>`
        : `<ins class="hist-ins">${esc(text)}</ins>`).join("");
}

/* ---------- SQL shown to judges ---------- */

function sqlBlock(claimId, data) {
  const id = Number.parseInt(claimId, 10);
  const linkIds = [...new Set((data.links || []).map((l) => Number.parseInt(l.id, 10)).filter(Number.isFinite))];
  const first = data.claim?.[0]?.ROW_START;
  const asOf = first ? String(first).replace("T", " ").slice(0, 19).replace(/[^0-9 :-]/g, "") : "2026-11-14 12:00:00";
  const sql = `-- 1. Every version of the claim
SELECT id, kind, statement_text, value_normalized, model_version, ROW_START, ROW_END
  FROM claims FOR SYSTEM_TIME ALL
 WHERE id = ${id}
 ORDER BY ROW_START;

-- 2. Every verdict version of every link that touches it
SELECT id, claim_a, claim_b, verdict, verdict_source, similarity, evidence, rationale,
       ROW_START, ROW_END
  FROM claim_links FOR SYSTEM_TIME ALL
 WHERE claim_a = ${id} OR claim_b = ${id}
 ORDER BY ROW_START;

-- 3. Every human review on those links (reviews are versioned too)
SELECT id, claim_link_id, reviewer, decision, note, ROW_START, ROW_END
  FROM reviews FOR SYSTEM_TIME ALL
 WHERE claim_link_id IN (${linkIds.length ? linkIds.join(", ") : "NULL"})
 ORDER BY ROW_START;

-- Time travel: what did we believe at a given moment?
SELECT verdict, verdict_source
  FROM claim_links FOR SYSTEM_TIME AS OF TIMESTAMP '${asOf}'
 WHERE claim_a = ${id} OR claim_b = ${id};`;
  return `<pre><code>${esc(sql)}</code></pre>
    <button type="button" class="hist-sql-copy">Copy SQL</button>`;
}
