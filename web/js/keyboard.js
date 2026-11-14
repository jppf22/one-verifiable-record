/* Reviewer keyboard flow. Keys are ignored while typing in form fields. */
import { $, $$, esc, toast } from "./util.js";
import { emit } from "./bus.js";
import * as merge from "./merge.js";
import * as reviewUi from "./review.js";
import { focusSearch, close as closeSearch } from "./search.js";

const SHORTCUTS = [
  ["/", "Focus search"],
  ["j / k", "Next / previous row"],
  ["g j / g k", "Next / previous conflict"],
  ["g m", "Next gap (missing here or only here)"],
  ["g a", "Next agreeing row"],
  ["a", "Vote the first button (\u201cReal conflict\u201d, \u201cSame fact\u201d\u2026) on the focused row's first unreviewed verdict"],
  ["d", "Vote the second button (\u201cFalse alarm\u201d, \u201cThey conflict\u201d\u2026)"],
  ["f", "Vote the second button with a note"],
  ["n", "Vote with a note"],
  ["o", "Open / close its receipts"],
  ["h", "History (biography) of the focused row's first claim (this page's, if any)"],
  ["r", "Re-check this page against its counterparts"],
  ["?", "Show this help"],
  ["Esc", "Close dialogs and menus"],
];

function isTyping(t) {
  return t instanceof HTMLElement &&
    (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
}

function openHelp() {
  const dlg = $("#help-dialog");
  if (!dlg.open) dlg.showModal();
}

function needRow() {
  if (!merge.isActive()) return null;
  let r = merge.focusedRow();
  if (!r) { merge.moveFocus(1); r = merge.focusedRow(); }
  if (!r) toast("No row to act on.", "info", 2000);
  return r;
}

function reviewFocused(decision) {
  const r = needRow();
  if (!r) return;
  const link = merge.firstUnreviewedLink(r);
  if (!link) { toast("Every verdict in this row is already reviewed.", "info", 2500); return; }
  if (decision === "fix") reviewUi.openFix(link.id);
  else if (decision === "note") reviewUi.openForm(link.id, "agree");
  else reviewUi.review(link.id, decision);
}

const G_TIMEOUT_MS = 1200;
let gPending = 0; // timestamp of a pending "g" prefix

const G_MOVES = { j: ["conflict", 1], k: ["conflict", -1], m: ["gap", 1], a: ["agree", 1] };
const G_EMPTY = { conflict: "No conflict rows in view.", gap: "No gap rows in view.", agree: "No agreeing rows in view." };

/** Second key of a "g x" sequence. Returns true if handled. */
function onPrefixed(e) {
  const move = G_MOVES[e.key];
  if (!move || !merge.isActive()) return false;
  e.preventDefault();
  if (!merge.moveFocusToGroup(...move)) toast(G_EMPTY[move[0]], "info", 2000);
  return true;
}

function onKey(e) {
  if (e.key === "Escape") {
    const open = $$("dialog[open]");
    if (open.length) { open.forEach((d) => d.close()); return; }
    closeSearch();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
  if (e.key === "?") { e.preventDefault(); openHelp(); return; }
  if ($("dialog[open]")) return; // don't act behind a modal

  if (gPending && Date.now() - gPending < G_TIMEOUT_MS) {
    gPending = 0;
    if (onPrefixed(e)) return;
  }
  gPending = 0;
  if (e.key === "g" && merge.isActive()) { gPending = Date.now(); return; }

  switch (e.key) {
    case "/": e.preventDefault(); focusSearch(); break;
    case "j": if (merge.isActive()) { e.preventDefault(); merge.moveFocus(1); } break;
    case "k": if (merge.isActive()) { e.preventDefault(); merge.moveFocus(-1); } break;
    case "a": reviewFocused("agree"); break;
    case "d": reviewFocused("disagree"); break;
    case "f": e.preventDefault(); reviewFocused("fix"); break;
    case "n": e.preventDefault(); reviewFocused("note"); break;
    case "o": {
      const r = needRow();
      if (r && !merge.toggleFocusedReceipts()) toast("This row has no verdict receipts.", "info", 2000);
      break;
    }
    case "h": {
      const r = needRow();
      const first = r && merge.firstClaim(r);
      if (first) emit("history:open", { claimId: Number(first.id) });
      break;
    }
    case "r": if (merge.isActive()) merge.recheckNow(); break;
    default: break;
  }
}

export function init() {
  const dlg = $("#help-dialog");
  dlg.setAttribute("aria-labelledby", "help-title");
  dlg.innerHTML = `
    <div class="dialog-head">
      <h3 id="help-title">Keyboard shortcuts</h3>
      <button type="button" class="small" data-close aria-label="Close">✕</button>
    </div>
    <dl class="shortcuts">
      ${SHORTCUTS.map(([k, d]) => `<dt>${k.split(" / ").map((x) => x.split(" ").map((y) => `<kbd>${esc(y)}</kbd>`).join(" ")).join(" / ")}</dt><dd>${esc(d)}</dd>`).join("")}
    </dl>
    <p class="muted">Shortcuts are ignored while you type in a text field.</p>`;
  dlg.addEventListener("click", (e) => {
    if (e.target === dlg || e.target.closest("[data-close]")) dlg.close();
  });
  document.addEventListener("keydown", onKey);
}

export { openHelp };
