/* Grounded Q&A over the article's passages in every language edition.
 * Owner: panels. Renders into #qa on bus `article:loaded`. */
import { api } from "./api.js";
import { on } from "./bus.js";
import { $, esc, LANGS, LANG_NAMES, wikiUrl } from "./util.js";

const SUGGESTED = [
  "When was it founded?",
  "What numbers do the editions disagree on?",
  "What are the key dates and figures?",
  "What does one edition mention that the others leave out?",
];
const HISTORY_MAX = 6;
const STORE_KEY = (qid) => `ovr.qa.${qid}`;

let root;
let ctx = null; // { qid, langs, current, loading, error }
let seq = 0;

export function init() {
  root = $("#qa");
  if (!root) return;
  root.classList.add("qa");
  root.addEventListener("submit", onSubmit);
  root.addEventListener("click", onClick);
  on("article:loaded", ({ qid, data } = {}) => {
    const langs = [...new Set((data?.articles || []).map((a) => a.lang))]
      .sort((a, b) => rank(a) - rank(b));
    if (ctx && ctx.qid === qid) { ctx.langs = langs; renderShell(true); return; }
    seq++;
    ctx = { qid, langs, current: null, loading: false, error: null };
    renderShell(false);
  });
}

const rank = (l) => { const i = LANGS.indexOf(l); return i < 0 ? LANGS.length : i; };

/* ---------- session history ---------- */

function loadHistory() {
  try { return JSON.parse(sessionStorage.getItem(STORE_KEY(ctx.qid))) || []; } catch { return []; }
}
function saveHistory(list) {
  try { sessionStorage.setItem(STORE_KEY(ctx.qid), JSON.stringify(list.slice(0, HISTORY_MAX))); } catch { /* quota/private mode */ }
}

/* ---------- rendering ---------- */

function renderShell(keepInput) {
  const prevQ = keepInput ? root.querySelector("#qa-input")?.value : "";
  const prevLang = keepInput ? root.querySelector("#qa-lang")?.value : "";
  root.innerHTML = `
    <h2 id="qa-title">Ask the article</h2>
    <p class="muted">Answers are grounded in passages retrieved with MariaDB vector search across
      ${ctx.langs.length ? ctx.langs.map((l) => esc(l)).join(" / ") : "every"} edition(s), with a citation for each source.</p>
    <form id="qa-form" class="qa-form" aria-labelledby="qa-title">
      <label class="sr-only" for="qa-input">Question</label>
      <input id="qa-input" name="q" type="text" minlength="2" maxlength="500" required
        placeholder="Ask a question about this article…" autocomplete="off">
      <label class="sr-only" for="qa-lang">Language</label>
      <select id="qa-lang" name="lang" title="Restrict sources to one edition">
        <option value="">All editions</option>
        ${ctx.langs.map((l) => `<option value="${esc(l)}">${esc(l)} · ${esc(LANG_NAMES[l] || l)}</option>`).join("")}
      </select>
      <button type="submit" class="primary qa-ask">Ask</button>
    </form>
    <div class="qa-suggested" aria-label="Suggested questions">
      ${SUGGESTED.map((q) => `<button type="button" class="qa-suggest chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
    </div>
    <div id="qa-answer" class="qa-answer" aria-live="polite"></div>
    <div class="qa-history"></div>`;
  if (prevQ) root.querySelector("#qa-input").value = prevQ;
  if (prevLang && ctx.langs.includes(prevLang)) root.querySelector("#qa-lang").value = prevLang;
  renderAnswer();
  renderHistory();
}

function renderAnswer() {
  const box = root.querySelector("#qa-answer");
  if (!box) return;
  if (ctx.loading) {
    box.innerHTML = `<div class="qa-state" role="status"><span class="qa-spinner" aria-hidden="true"></span>
      Retrieving passages and composing an answer…</div>`;
  } else if (ctx.error) {
    box.innerHTML = `<div class="qa-state error" role="alert">Could not answer: ${esc(ctx.error)}
      <button type="button" class="qa-retry">Retry</button></div>`;
  } else if (ctx.current) {
    box.innerHTML = answerCard(ctx.current, "cur");
  } else {
    box.innerHTML = "";
  }
  root.querySelector(".qa-ask")?.toggleAttribute("disabled", ctx.loading);
}

function renderHistory() {
  const host = root.querySelector(".qa-history");
  if (!host) return;
  const list = loadHistory().filter((h) => !ctx.current || h.at !== ctx.current.at);
  host.innerHTML = list.length ? `
    <h3>Earlier this session</h3>
    ${list.map((h, i) => `<details class="qa-past">
        <summary><span>${esc(h.question)}</span>${h.lang ? ` <span class="qa-lang-tag">${esc(h.lang)}</span>` : ""}</summary>
        ${answerCard(h, `h${i}`)}
      </details>`).join("")}
    <button type="button" class="qa-clear linkish">Clear history</button>` : "";
}

function citeUrl(c) {
  if (typeof c.url === "string" && /^https:\/\/[a-z-]+\.wikipedia\.org\//.test(c.url)) return c.url;
  return wikiUrl(c.lang, c.revision_id, c.title);
}

function answerCard(item, prefix) {
  const cites = item.citations || [];
  const byLang = {};
  cites.forEach((c, i) => { (byLang[c.lang] ||= []).push(i + 1); });
  return `<article class="qa-card">
      <p class="qa-q"><span class="muted">Q${item.lang ? ` (${esc(item.lang)} only)` : ""}:</span> ${esc(item.question)}</p>
      <div class="qa-a">${esc(item.answer || "No answer was returned.").replace(/\n/g, "<br>")}</div>
      ${cites.length ? `
        <div class="qa-cites-head muted">${cites.length} source passage(s):
          ${Object.entries(byLang).map(([l, n]) => `<span class="qa-lang-tag">${esc(l)} ×${n.length}</span>`).join(" ")}</div>
        <ul class="qa-cites">
          ${cites.map((c, i) => {
            const id = `qa-${prefix}-c${i}`;
            const url = citeUrl(c);
            return `<li>
              <button type="button" class="qa-cite chip lang-${esc(c.lang)}" aria-expanded="false" aria-controls="${id}">
                <b>[${i + 1}] ${esc(c.lang)}</b> ${esc(c.title)}${c.revision_id ? ` <span class="muted">rev ${esc(c.revision_id)}</span>` : ""}
              </button>
              <div class="qa-passage" id="${id}" hidden>
                <blockquote lang="${esc(c.lang)}">${esc(c.text || "(no passage text returned)")}</blockquote>
                ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open this revision on ${esc(c.lang)}.wikipedia.org ↗</a>` : ""}
              </div></li>`;
          }).join("")}
        </ul>` : `<p class="muted">No source passages were retrieved, so treat this answer as unsupported.</p>`}
    </article>`;
}

/* ---------- actions ---------- */

function onSubmit(e) {
  if (e.target.id !== "qa-form") return;
  e.preventDefault();
  const q = root.querySelector("#qa-input").value.trim();
  const lang = root.querySelector("#qa-lang").value || null;
  if (q.length < 2) { root.querySelector("#qa-input").focus(); return; }
  ask(q, lang);
}

function onClick(e) {
  const btn = e.target.closest("button");
  if (!btn || !ctx) return;
  if (btn.classList.contains("qa-suggest")) {
    root.querySelector("#qa-input").value = btn.dataset.q;
    ask(btn.dataset.q, root.querySelector("#qa-lang").value || null);
  } else if (btn.classList.contains("qa-cite")) {
    const panel = root.querySelector(`#${CSS.escape(btn.getAttribute("aria-controls"))}`);
    const show = panel.hidden;
    panel.hidden = !show;
    btn.setAttribute("aria-expanded", String(show));
  } else if (btn.classList.contains("qa-retry") && ctx.last) {
    ask(ctx.last.q, ctx.last.lang);
  } else if (btn.classList.contains("qa-clear")) {
    saveHistory([]);
    renderHistory();
  }
}

async function ask(q, lang) {
  const my = ++seq;
  const qid = ctx.qid;
  ctx.last = { q, lang };
  ctx.loading = true; ctx.error = null; ctx.current = null;
  renderAnswer();
  renderHistory();
  try {
    const res = await api.qa(qid, q, lang);
    if (my !== seq) return;
    const item = { question: res.question || q, lang, answer: res.answer, citations: res.citations || [], at: Date.now() };
    ctx.current = item;
    saveHistory([item, ...loadHistory()]);
  } catch (err) {
    if (my !== seq) return;
    ctx.error = err.message;
  }
  ctx.loading = false;
  renderAnswer();
  renderHistory();
}
