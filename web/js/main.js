/* App shell: wires every module, the reviewer chip and route -> view switching. */
import { $, esc, getReviewer, setReviewer } from "./util.js";
import * as router from "./router.js";
import * as search from "./search.js";
import * as merge from "./merge.js";
import * as review from "./review.js";
import * as keyboard from "./keyboard.js";
import * as history from "./history.js";
import * as suggestions from "./suggestions.js";
import * as qa from "./qa.js";

function renderReviewer() {
  const name = getReviewer();
  $("#reviewer-slot").innerHTML = `
    <button type="button" class="reviewer-chip${name ? "" : " unset"}" id="reviewer-chip"
      title="${name ? "Change your reviewer name" : "Set the name shown on your reviews"}">
      <span class="avatar" aria-hidden="true">${esc((name || "?").slice(0, 1).toUpperCase())}</span>
      <span>${name ? `Reviewing as <b>${esc(name)}</b>` : "Set reviewer name"}</span>
    </button>
    <button type="button" class="small help-btn" data-open-help title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts">?</button>`;
}

function initReviewerChip() {
  renderReviewer();
  $("#reviewer-slot").addEventListener("click", (e) => {
    if (e.target.closest("[data-open-help]")) { keyboard.openHelp(); return; }
    if (!e.target.closest("#reviewer-chip")) return;
    const next = prompt("Your display name for reviews (stored only in this browser). Leave empty to clear:", getReviewer());
    if (next !== null) setReviewer(next);
  });
  document.addEventListener("reviewer:changed", renderReviewer);
  window.addEventListener("storage", (e) => { if (e.key === "ovr.reviewer") renderReviewer(); });
}

function showRoute(route, prev) {
  const isMerge = route.name === "merge";
  document.body.dataset.route = route.name;
  $("#article-picker").hidden = isMerge;
  $("#merge-view").hidden = !isMerge;
  search.close();
  if (isMerge) {
    merge.open(route.qid, route.query);
    if (prev?.qid !== route.qid) window.scrollTo(0, 0);
  } else {
    merge.close();
    if (prev && prev.name !== "picker") $("#search-input").focus();
  }
}

function safeInit(name, mod) {
  try { mod.init?.(); } catch (err) { console.error(`[main] ${name}.init failed`, err); }
}

router.init();
initReviewerChip();
safeInit("search", search);
safeInit("merge", merge);
safeInit("review", review);
safeInit("keyboard", keyboard);
safeInit("history", history);
safeInit("suggestions", suggestions);
safeInit("qa", qa);
router.onRoute(showRoute);
router.start();
