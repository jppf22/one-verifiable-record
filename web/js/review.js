/* Inline human review on each verdict row. The buttons are worded for the verdict
 * being reviewed (a conflict gets "Real conflict" / "False alarm", a match gets
 * "Same fact" / "They conflict"), but they post the same three decisions to
 * /api/reviews: agree (confirm the verdict), disagree (overturn it) and fix
 * ("it's a real conflict", used for verdicts that have no opposite).
 * The server re-checks the page and returns a RecheckResult, forwarded as
 * `recheck:done` (reason "review"). One review never sets the verdict: the server
 * applies policy R1 (override: true), and only a quorum of distinct reviewers writes a
 * human verdict (docs/review-consensus.md). */
import { api } from "./api.js";
import { $, esc, toast, ensureReviewer, fmtTime } from "./util.js";
import { emit } from "./bus.js";
import * as consensus from "./consensus.js";

/* Per verdict family: which decisions are offered and how they read. */
const CHOICES = {
  conflict: {
    agree: { label: "Real conflict", icon: "✓", hint: "The two pages really state different things" },
    disagree: { label: "False alarm", icon: "✗", hint: "Both pages state the same thing: the machine was wrong" },
  },
  match: {
    agree: { label: "Same fact", icon: "✓", hint: "Both pages state the same thing" },
    disagree: { label: "They conflict", icon: "✗", hint: "The pages actually state different things" },
  },
  other: {
    agree: { label: "Not comparable", icon: "✓", hint: "Right: these two claims are about different things" },
    fix: { label: "Real conflict", icon: "✗", hint: "Same fact, and the two pages contradict each other" },
  },
};
/* What a decided vote turns the verdict into (mirrors server/consensus.py). */
const OUTCOME = {
  conflict: { agree: "a confirmed conflict", disagree: "an agreement" },
  match: { agree: "a confirmed match", disagree: "a conflict" },
  other: { agree: "confirmed as not comparable", fix: "a conflict" },
};
const GENERIC = { agree: "agreed", disagree: "disagreed", fix: "said it is a real conflict" };

export function family(verdict) {
  if (verdict === "contradicted" || verdict === "possible_conflict") return "conflict";
  if (verdict === "supported" || verdict === "possible_match") return "match";
  return "other";
}
const choicesFor = (link) => CHOICES[family(link?.verdict)];
const negative = (fam) => (fam === "other" ? "fix" : "disagree");

/** Keyboard "d"/"f" mean "the second choice" whatever the verdict family. */
function normalize(link, decision) {
  const ch = choicesFor(link);
  if (ch[decision]) return decision;
  return decision === "agree" ? "agree" : negative(family(link?.verdict));
}

let ctx = { getLink: () => null, getPageId: () => null };
const pending = new Set();

/** merge.js provides lookups so this module needs no import cycle. */
export function bind(context) { ctx = { ...ctx, ...context }; }

export function isPending(linkId) { return pending.has(Number(linkId)); }

function decisionLabel(link, decision) {
  return choicesFor(link)[decision]?.label.toLowerCase() || GENERIC[decision] || decision;
}

export function stampHtml(review, { optimistic = false, link = null } = {}) {
  if (!review) return "";
  const icon = { agree: "✓", disagree: "✗", fix: "✗" }[review.decision] || "•";
  return `<div class="review-stamp ${esc(review.decision)}${optimistic ? " optimistic" : ""}">
      <span class="stamp-icon" aria-hidden="true">${esc(icon)}</span>
      <span>${optimistic ? "Saving vote by" : "Last vote by"} <b>${esc(review.reviewer)}</b>:
        “${esc(decisionLabel(link, review.decision))}”</span>
      <span class="muted">${optimistic ? "re-checking…" : esc(fmtTime(review.created_at))}</span>
      ${review.note ? `<q class="review-note">${esc(review.note)}</q>` : ""}
    </div>`;
}

/** One line under the buttons: how many more votes settle this verdict, and what it becomes. */
export function nextStepHtml(link) {
  const fam = family(link.verdict);
  if (link.verdict_source === "human") {
    return `<p class="review-next decided">Settled by reviewers.${fam === "conflict" ? " Write the correct version below." : ""}
      New votes can still reopen it.</p>`;
  }
  const t = link.consensus || {};
  const p = t.policy || { confirm_quorum: 2, overturn_quorum: 3, margin: 2 };
  const c = t.confirm || 0, o = t.oppose || 0;
  const needC = Math.max(p.confirm_quorum - c, p.margin + o - c, 0);
  const needO = Math.max(p.overturn_quorum - o, p.margin + c - o, 0);
  const ch = choicesFor(link);
  const neg = negative(fam);
  const votes = (n) => `${n} more vote${n === 1 ? "" : "s"}`;
  return `<p class="review-next" title="One vote per reviewer: voting again replaces your earlier vote. Nothing is edited on Wikipedia.">
      ${votes(needC)} for “${esc(ch.agree.label)}” make this ${esc(OUTCOME[fam].agree)};
      ${votes(needO)} for “${esc(ch[neg].label)}” make it ${esc(OUTCOME[fam][neg])}.</p>`;
}

/** Review controls for one verdict row (link). The note form is built on demand. */
export function controlsHtml(link) {
  const busy = isPending(link.id);
  const ch = choicesFor(link);
  const keys = { agree: "a", disagree: "d", fix: "d" };
  const buttons = Object.entries(ch).map(([key, d]) =>
    `<button type="button" class="review-btn ${key === "agree" ? "yes" : "no"}" data-review="${key}" data-link-id="${esc(link.id)}"
       title="${esc(d.hint)} (key: ${keys[key]})" ${busy ? "disabled" : ""}>
       <span aria-hidden="true">${d.icon}</span> ${esc(d.label)}</button>`).join("");
  return `<div class="review" data-review-for="${esc(link.id)}">
      <div class="review-stamp-slot">${stampHtml(link.review, { optimistic: busy, link })}</div>
      <div class="consensus-slot" data-link-id="${esc(link.id)}"></div>
      <div class="review-actions" role="group" aria-label="Your vote on this verdict">
        <span class="muted review-label">Your vote:</span>${buttons}
        <button type="button" class="link-btn" data-review-note="${esc(link.id)}"
          title="Vote with a note for other reviewers (key: n)" ${busy ? "disabled" : ""}>+ note</button>
      </div>
      ${nextStepHtml(link)}
    </div>`;
}

function formHtml(link, decision) {
  const id = esc(link.id);
  const radios = Object.entries(choicesFor(link)).map(([key, d]) =>
    `<label class="decision-opt ${key === "agree" ? "yes" : "no"}"><input type="radio" name="decision" value="${key}" ${key === decision ? "checked" : ""}>
       <span aria-hidden="true">${d.icon}</span> ${esc(d.label)}</label>`).join("");
  return `<form class="fix-form" data-link-id="${id}">
      <fieldset class="decision-pick"><legend class="sr-only">Your vote</legend>${radios}</fieldset>
      <label class="sr-only" for="fix-note-${id}">Note</label>
      <textarea id="fix-note-${id}" name="note" rows="2" maxlength="2000"
        placeholder="Why? Cite a non-Wikipedia source if you have one (optional)"></textarea>
      <div class="fix-actions">
        <button type="submit" class="primary">Submit vote</button>
        <button type="button" data-fix-cancel>Cancel</button>
        <span class="muted">Ctrl+Enter submits · Esc cancels</span>
      </div>
    </form>`;
}

function syncForm(form) {
  const link = ctx.getLink(form.dataset.linkId);
  const decision = form.querySelector("input[name=decision]:checked")?.value || "agree";
  form.querySelector("button[type=submit]").textContent = `Vote “${choicesFor(link)[decision]?.label || decision}”`;
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
  if (slot) slot.innerHTML = stampHtml(review, { optimistic, link: ctx.getLink(linkId) });
}

/** Open the note form for a link, preselecting a decision. */
export function openForm(linkId, decision = "agree") {
  const host = rowEl(linkId);
  const link = ctx.getLink(linkId);
  if (!host || isPending(linkId)) return;
  decision = normalize(link, decision);
  let form = host.querySelector(".fix-form");
  if (!form) { host.insertAdjacentHTML("beforeend", formHtml(link || { id: linkId }, decision)); form = host.querySelector(".fix-form"); }
  const radio = form.querySelector(`input[value="${decision}"]`);
  if (radio) radio.checked = true;
  syncForm(form);
  form.querySelector("textarea").focus();
}

/** Keyboard "f": the negative choice with a note. */
export function openFix(linkId) { openForm(linkId, "fix"); }

function closeFix(linkId) { rowEl(linkId)?.querySelector(".fix-form")?.remove(); }

/** Toast text: did this vote reach (or break) the R1 consensus? */
function outcomeText(link, decision, res) {
  const saved = `Vote saved (“${choicesFor(link)[decision]?.label || decision}”).`;
  const o = res.override;
  const t = res.consensus;
  if (o?.changed) return `${saved} Reviewers settled it: ${o.before.replace(/_/g, " ")} → ${o.after.replace(/_/g, " ")}.`;
  if (o?.binding && t?.current_source === "human") return `${saved} Reviewers settled it: the verdict is now a human decision.`;
  if (t?.state === "disputed") return `${saved} Reviewers disagree: this verdict is now disputed.`;
  if (t?.state === "pending_confirm") return `${saved} ${t.confirm} of ${t.policy.confirm_quorum} votes to confirm.`;
  if (t?.state === "pending_overturn") return `${saved} ${t.oppose} of ${t.policy.overturn_quorum} votes to overturn.`;
  return `${saved} Page re-checked.`;
}

/** Submit a vote. Returns true on success. */
export async function review(linkId, decision, note = "") {
  linkId = Number(linkId);
  const link = ctx.getLink(linkId);
  decision = normalize(link, decision);
  if (!["agree", "disagree", "fix"].includes(decision) || pending.has(linkId)) return false;
  const reviewer = ensureReviewer();
  if (!reviewer) { toast("A reviewer name is needed to vote.", "warn"); return false; }

  const pageId = ctx.getPageId();
  const previous = link ? link.review : null;
  const optimistic = { decision, reviewer, note: note.trim() || null, created_at: new Date().toISOString() };

  pending.add(linkId);
  setBusy(linkId, true);
  setStamp(linkId, optimistic, true);
  try {
    const res = await api.submitReview({
      claim_link_id: linkId, reviewer, decision, note: note.trim() || undefined, override: true,
    });
    if (link) { link.review = { id: res.review_id, ...optimistic }; if (res.consensus) link.consensus = res.consensus; }
    const slot = rowEl(linkId)?.querySelector(".consensus-slot");
    if (slot && res.consensus) slot.innerHTML = consensus.html(res.consensus);
    pending.delete(linkId);
    setBusy(linkId, false);
    setStamp(linkId, link?.review || optimistic, false);
    closeFix(linkId);
    toast(outcomeText(link, decision, res), res.override?.changed || res.override?.binding ? "ok" : "info", 5000);
    // The server re-checks the page of claim_a; we re-render the page we are looking at.
    if (res && res.recheck) emit("recheck:done", { pageId, result: res.recheck, reason: "review", linkIds: [linkId] });
    else emit("page:reload", { pageId });
    return true;
  } catch (err) {
    pending.delete(linkId);
    setBusy(linkId, false);
    setStamp(linkId, previous, false);
    toast(`Vote failed: ${err.message}`, "error", 6000);
    return false;
  }
}

export function init() {
  const host = $("#rows");
  host.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-review]");
    if (btn) { review(btn.dataset.linkId, btn.dataset.review); return; }
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
    const decision = form.querySelector("input[name=decision]:checked")?.value || "agree";
    review(form.dataset.linkId, decision, form.querySelector("textarea").value);
  });
  host.addEventListener("keydown", (e) => {
    const form = e.target.closest(".fix-form");
    if (!form) return;
    if (e.key === "Escape") { e.stopPropagation(); closeFix(form.dataset.linkId); }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit();
  });
}
