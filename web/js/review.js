/* Inline human review on each verdict row: Agree / Disagree / Fix (+ required note).
 * Posts to /api/reviews; the server re-checks the page and returns a RecheckResult,
 * which is forwarded as `recheck:done` (reason "review"). A review never overrides the
 * machine verdict (user-confirmed decision #4). */
import { api } from "./api.js";
import { $, esc, toast, ensureReviewer, fmtTime } from "./util.js";
import { emit } from "./bus.js";
import * as consensus from "./consensus.js";

const DECISIONS = {
  agree: { label: "Agree", icon: "✓", hint: "The machine verdict is right" },
  disagree: { label: "Disagree", icon: "✗", hint: "The machine verdict is wrong" },
  fix: { label: "Fix", icon: "✎", hint: "Needs a fix: describe it in a note" },
};

let ctx = { getLink: () => null, getPageId: () => null };
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

/** Review controls for one verdict row (link). The note form is built on demand. */
export function controlsHtml(link) {
  const busy = isPending(link.id);
  const buttons = Object.entries(DECISIONS).map(([key, d]) =>
    `<button type="button" class="review-btn ${key}" data-review="${key}" data-link-id="${esc(link.id)}"
       title="${esc(d.hint)} (key: ${key[0]})" ${busy ? "disabled" : ""}>
       <span aria-hidden="true">${d.icon}</span> ${d.label}</button>`).join("");
  return `<div class="review" data-review-for="${esc(link.id)}">
      <div class="review-stamp-slot">${stampHtml(link.review, { optimistic: busy })}</div>
      <div class="consensus-slot" data-link-id="${esc(link.id)}"></div>
      <div class="review-actions" role="group" aria-label="Review this verdict">
        <span class="muted review-label">${link.review ? "Review again:" : "Your review:"}</span>${buttons}
        <button type="button" class="link-btn" data-review-note="${esc(link.id)}"
          title="Review with a note (key: n)" ${busy ? "disabled" : ""}>+ note</button>
      </div>
    </div>`;
}

function formHtml(linkId, decision) {
  const id = esc(linkId);
  const radios = Object.entries(DECISIONS).map(([key, d]) =>
    `<label class="decision-opt ${key}"><input type="radio" name="decision" value="${key}" ${key === decision ? "checked" : ""}>
       <span aria-hidden="true">${d.icon}</span> ${d.label}</label>`).join("");
  return `<form class="fix-form" data-link-id="${id}">
      <fieldset class="decision-pick"><legend class="sr-only">Decision</legend>${radios}</fieldset>
      <label class="sr-only" for="fix-note-${id}">Note</label>
      <textarea id="fix-note-${id}" name="note" rows="2" maxlength="2000"></textarea>
      <div class="fix-actions">
        <button type="submit" class="primary">Submit review</button>
        <button type="button" data-fix-cancel>Cancel</button>
        <span class="muted">Ctrl+Enter submits · Esc cancels</span>
      </div>
    </form>`;
}

function syncForm(form) {
  const decision = form.querySelector("input[name=decision]:checked")?.value || "fix";
  const ta = form.querySelector("textarea");
  ta.required = decision === "fix";
  ta.placeholder = decision === "fix"
    ? "What is wrong on the page, and what should it say? Cite a non-Wikipedia source if you have one. (required)"
    : "Optional note for other reviewers";
  form.querySelector("button[type=submit]").textContent = `Submit ${DECISIONS[decision].label.toLowerCase()}`;
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

/** Open the note form for a link, preselecting a decision (Fix needs a note). */
export function openForm(linkId, decision = "fix") {
  const host = rowEl(linkId);
  if (!host || isPending(linkId)) return;
  let form = host.querySelector(".fix-form");
  if (!form) { host.insertAdjacentHTML("beforeend", formHtml(linkId, decision)); form = host.querySelector(".fix-form"); }
  const radio = form.querySelector(`input[value="${decision}"]`);
  if (radio) radio.checked = true;
  syncForm(form);
  form.querySelector("textarea").focus();
}

export function openFix(linkId) { openForm(linkId, "fix"); }

function closeFix(linkId) { rowEl(linkId)?.querySelector(".fix-form")?.remove(); }

/** Submit a review. Returns true on success. */
export async function review(linkId, decision, note = "") {
  linkId = Number(linkId);
  if (!DECISIONS[decision] || pending.has(linkId)) return false;
  if (decision === "fix" && !note.trim()) { openFix(linkId); return false; }
  const reviewer = ensureReviewer();
  if (!reviewer) { toast("A reviewer name is needed to submit reviews.", "warn"); return false; }

  const pageId = ctx.getPageId();
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
    consensus.invalidate(linkId);
    pending.delete(linkId);
    setBusy(linkId, false);
    setStamp(linkId, link?.review || optimistic, false);
    closeFix(linkId);
    toast(`Review saved (${DECISIONS[decision].label.toLowerCase()}). Page re-checked.`, "ok");
    // The server re-checks the page of claim_a; we re-render the page we are looking at.
    if (res && res.recheck) emit("recheck:done", { pageId, result: res.recheck, reason: "review", linkIds: [linkId] });
    else emit("page:reload", { pageId });
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
  const host = $("#rows");
  host.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-review]");
    if (btn) {
      const { review: decision, linkId } = btn.dataset;
      if (decision === "fix") openFix(linkId);
      else review(linkId, decision);
      return;
    }
    const noteBtn = e.target.closest("[data-review-note]");
    if (noteBtn) { openForm(noteBtn.dataset.reviewNote, "agree"); return; }
    const cancel = e.target.closest("[data-fix-cancel]");
    if (cancel) closeFix(cancel.closest(".fix-form").dataset.linkId);
  });
  host.addEventListener("change", (e) => {
    const form = e.target.closest(".fix-form");
    if (form && e.target.name === "decision") syncForm(form);
  });
  host.addEventListener("submit", (e) => {
    const form = e.target.closest(".fix-form");
    if (!form) return;
    e.preventDefault();
    const decision = form.querySelector("input[name=decision]:checked")?.value || "fix";
    const note = form.querySelector("textarea").value;
    if (decision === "fix" && !note.trim()) { toast("A note is required for Fix.", "warn"); return; }
    review(form.dataset.linkId, decision, note);
  });
  host.addEventListener("keydown", (e) => {
    const form = e.target.closest(".fix-form");
    if (!form) return;
    if (e.key === "Escape") { e.stopPropagation(); closeFix(form.dataset.linkId); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit();
  });
}
