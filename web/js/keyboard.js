/* Reviewer keyboard flow. Keys are ignored while typing in form fields. */
import { $, $$, esc, toast } from "./util.js";
import { emit } from "./bus.js";
import * as merge from "./merge.js";
import * as reviewUi from "./review.js";
import { focusSearch, close as closeSearch } from "./search.js";

const SHORTCUTS = [
  ["/", "Focus search"],
  ["j / k", "Next / previous claim cluster"],
  ["a", "Agree with the focused cluster's first unreviewed verdict"],
  ["d", "Disagree with it"],
  ["f", "Fix it (opens the note box)"],
  ["h", "History (biography) of the focused cluster's first claim"],
  ["r", "Re-check the page"],
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

function needCluster() {
  if (!merge.isActive()) return null;
  let c = merge.focusedCluster();
  if (!c) { merge.moveFocus(1); c = merge.focusedCluster(); }
  if (!c) toast("No cluster to act on.", "info", 2000);
  return c;
}

function reviewFocused(decision) {
  const c = needCluster();
  if (!c) return;
  const link = merge.firstUnreviewedLink(c);
  if (!link) { toast("Every verdict in this cluster is already reviewed.", "info", 2500); return; }
  if (decision === "fix") reviewUi.openFix(link.id);
  else reviewUi.review(link.id, decision);
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

  switch (e.key) {
    case "/": e.preventDefault(); focusSearch(); break;
    case "j": if (merge.isActive()) { e.preventDefault(); merge.moveFocus(1); } break;
    case "k": if (merge.isActive()) { e.preventDefault(); merge.moveFocus(-1); } break;
    case "a": reviewFocused("agree"); break;
    case "d": reviewFocused("disagree"); break;
    case "f": e.preventDefault(); reviewFocused("fix"); break;
    case "h": {
      const c = needCluster();
      const first = c && (c.claims || [])[0];
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
      ${SHORTCUTS.map(([k, d]) => `<dt>${k.split(" / ").map((x) => `<kbd>${esc(x)}</kbd>`).join(" / ")}</dt><dd>${esc(d)}</dd>`).join("")}
    </dl>
    <p class="muted">Shortcuts are ignored while you type in a text field.</p>`;
  dlg.addEventListener("click", (e) => {
    if (e.target === dlg || e.target.closest("[data-close]")) dlg.close();
  });
  document.addEventListener("keydown", onKey);
}

export { openHelp };
