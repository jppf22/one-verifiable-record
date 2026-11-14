/* Inline human review on each verdict row: Agree / Disagree / Fix (+ required note).
 * Posts to /api/reviews; the server re-checks the article and returns a RecheckResult,
 * which is forwarded as `recheck:done` (reason "review"). A review never overrides the
 * machine verdict (user-confirmed decision #4). */
import { api } from "./api.js";
import { $, esc, toast, ensureReviewer, fmtTime } from "./util.js";
import { emit } from "./bus.js";

const DECISIONS = {
  agree: { label: "Agree", icon: "✓", hint: "The machine verdict is right" },
  disagree: { label: "Disagree", icon: "✗", hint: "The machine verdict is wrong" },
  fix: { label: "Fix", icon: "✎", hint: "Needs a fix: describe it in a note" },
};

let ctx = { getLink: () => null, getQid: () => null };
const pending = new Set();

/** merge.js provides lookups so this module needs no import cycle. */
export function bind(context) { ctx = { ...ctx, ...context }; }

export function isPending(linkId) { return pending.has(Number(linkId)); }

export function stampHtml(review, { optimistic = false } = {}) {
  if (!review) return "";
  const d = DECISIONS[review.decision] || { label: review.decision, icon: "•" };
  return `<div class="review-stamp ${esc(review.decision)}${optimistic ? " optimistic" : ""}">
      <span class="stamp-icon" aria-hidden="true">${esc(d.icon)}</span>
      <span>${optimistic ? "Saving review by" : "Reviewed by"} <b>${esc(review.reviewer)}</b>:
        ${esc(d.label.toLowerCase())}</span>
      <span class="muted">${optimistic ? "re-checking…" : esc(fmtTime(review.created_at))}</span>
      ${review.note ? `<q class="review-note">${esc(review.note)}</q>` : ""}
    </div>`;
}

/** Review controls for one verdict row (link). */
export function controlsHtml(link) {
  const busy = isPending(link.id);
  const buttons = Object.entries(DECISIONS).map(([key, d]) =>
    `<button type="button" class="review-btn ${key}" data-review="${key}" data-link-id="${esc(link.id)}"
       title="${esc(d.hint)} (key: ${key[0]})" ${busy ? "disabled" : ""}>
       <span aria-hidden="true">${d.icon}</span> ${d.label}</button>`).join("");
  return `<div class="review" data-review-for="${esc(link.id)}">
      <div class="review-stamp-slot">${stampHtml(link.review, { optimistic: busy })}</div>
      <div class="review-actions" role="group" aria-label="Review this verdict">
        <span class="muted review-label">${link.review ? "Review again:" : "Your review:"}</span>${buttons}
      </div>
      <form class="fix-form" data-link-id="${esc(link.id)}" hidden>
        <label class="sr-only" for="fix-note-${esc(link.id)}">Describe the fix</label>
        <textarea id="fix-note-${esc(link.id)}" name="note" rows="2" required maxlength="2000"
          placeholder="What is wrong, and what should the sentence say? (required)"></textarea>
        <div class="fix-actions">
          <button type="submit" class="primary">Submit fix</button>
          <button type="button" data-fix-cancel>Cancel</button>
        </div>
      </form>
    </div>`;
}

function rowEl(linkId) { return $(`.review[data-review-for="${CSS.escape(String(linkId))}"]`); }

function setBusy(linkId, busy) {
  const row = rowEl(linkId);
  if (!row) return;
  row.classList.toggle("is-pending", busy);
  row.querySelectorAll("button, textarea").forEach((b) => { b.disabled = busy; });
}

function setStamp(linkId, review, optimistic) {
  const slot = rowEl(linkId)?.querySelector(".review-stamp-slot");
  if (slot) slot.innerHTML = stampHtml(review, { optimistic });
}

export function openFix(linkId) {
  const form = rowEl(linkId)?.querySelector(".fix-form");
  if (!form) return;
  form.hidden = false;
  form.querySelector("textarea").focus();
}

function closeFix(linkId) {
  const form = rowEl(linkId)?.querySelector(".fix-form");
  if (form) { form.hidden = true; form.reset(); }
}

/** Submit a review. Returns true on success. */
export async function review(linkId, decision, note = "") {
  linkId = Number(linkId);
  if (!DECISIONS[decision] || pending.has(linkId)) return false;
  if (decision === "fix" && !note.trim()) { openFix(linkId); return false; }
  const reviewer = ensureReviewer();
  if (!reviewer) { toast("A reviewer name is needed to submit reviews.", "warn"); return false; }

  const qid = ctx.getQid();
  const link = ctx.getLink(linkId);
  const previous = link ? link.review : null;
  const optimistic = { decision, reviewer, note: note.trim() || null, created_at: new Date().toISOString() };

  pending.add(linkId);
  setBusy(linkId, true);
  setStamp(linkId, optimistic, true);
  try {
    const res = await api.submitReview({
      claim_link_id: linkId, reviewer, decision, note: note.trim() || undefined,
    });
    if (link) link.review = { id: res.review_id, ...optimistic };
    pending.delete(linkId);
    setBusy(linkId, false);
    setStamp(linkId, link?.review || optimistic, false);
    closeFix(linkId);
    toast(`Review saved (${DECISIONS[decision].label.toLowerCase()}). Page re-checked.`, "ok");
    if (res && res.recheck) emit("recheck:done", { qid, result: res.recheck, reason: "review", linkIds: [linkId] });
    else emit("merge:reload", { qid });
    return true;
  } catch (err) {
    pending.delete(linkId);
    setBusy(linkId, false);
    setStamp(linkId, previous, false);
    toast(`Review failed: ${err.message}`, "error", 6000);
    return false;
  }
}

export function init() {
  const host = $("#clusters");
  host.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-review]");
    if (btn) {
      const { review: decision, linkId } = btn.dataset;
      if (decision === "fix") openFix(linkId);
      else review(linkId, decision);
      return;
    }
    const cancel = e.target.closest("[data-fix-cancel]");
    if (cancel) closeFix(cancel.closest(".fix-form").dataset.linkId);
  });
  host.addEventListener("submit", (e) => {
    const form = e.target.closest(".fix-form");
    if (!form) return;
    e.preventDefault();
    const note = form.querySelector("textarea").value;
    if (!note.trim()) { toast("A note is required for Fix.", "warn"); return; }
    review(form.dataset.linkId, "fix", note);
  });
  host.addEventListener("keydown", (e) => {
    const form = e.target.closest(".fix-form");
    if (!form) return;
    if (e.key === "Escape") { e.stopPropagation(); closeFix(form.dataset.linkId); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit();
  });
}
