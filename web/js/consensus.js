/* Review consensus, policy R1 (docs/review-consensus.md). Renders the server's tally
 * (`link.consensus` in /compare, `consensus` in the POST /reviews response). The server
 * (server/consensus.py) is the only place the policy is computed and enforced: when a
 * quorum is reached it writes the verdict with verdict_source='human'. */
import { $$, esc, fmtTime } from "./util.js";

const DECISION_WORD = { agree: "confirmed the verdict", disagree: "rejected the verdict", fix: "said it is a real conflict" };
const STALE_WORD = {
  superseded: "replaced by a later vote from the same reviewer",
  verdict_changed: "cast on an earlier verdict",
  claim_changed: "cast before a claim changed",
};
const humanise = (s) => String(s || "").replace(/_/g, " ");

function headline(t) {
  const p = t.policy || {};
  switch (t.state) {
    case "confirmed": return { cls: "ok", text: `Consensus: verdict confirmed by ${t.confirm} reviewers` };
    case "overturned": return t.binding
      ? { cls: "bad", text: `Consensus: overturned by ${t.oppose} reviewers, now ${humanise(t.target_verdict)}` }
      : { cls: "warn", text: `${t.oppose} reviewers reject this verdict, but none said what it should be` };
    case "disputed": return { cls: "warn", text: `Disputed: ${t.confirm} confirm vs ${t.oppose} oppose (needs a lead of ${p.margin ?? 2})` };
    case "pending_confirm": return { cls: "pending", text: `${t.confirm} of ${p.confirm_quorum ?? 2} confirmations` };
    case "pending_overturn": return { cls: "pending", text: `${t.oppose} of ${p.overturn_quorum ?? 3} votes to overturn` };
    default: return { cls: "pending", text: "No current votes" };
  }
}

export function html(t) {
  if (!t) return "";
  const h = headline(t);
  const p = t.policy || {};
  const votes = [...(t.votes || [])].reverse(); // newest first
  const stale = votes.filter((v) => !v.counted).length;
  const list = votes.map((v) => `<li${v.counted ? "" : ` class="stale"`}>
      <b>${esc(v.reviewer)}</b> ${esc(DECISION_WORD[v.decision] || v.decision)}
      <span class="muted">${v.counted ? `(${esc(v.role)})`
        : `${esc(STALE_WORD[v.stale_reason] || "not counted")}${v.stale_reason === "verdict_changed" && v.subject_at_vote ? ` (${esc(humanise(v.subject_at_vote))})` : ""}, not counted`}</span>
      <span class="muted">${esc(fmtTime(v.cast_at))}</span>
      ${v.note ? `<q>${esc(v.note)}</q>` : ""}</li>`).join("");
  return `<details class="consensus ${h.cls}">
      <summary title="Policy ${esc(p.name || "R1")}: ${esc(p.confirm_quorum ?? 2)} distinct reviewers confirm a verdict, ${esc(p.overturn_quorum ?? 3)} overturn it. A vote counts only while the verdict it was cast on is current (checked with FOR SYSTEM_TIME).">
        <span class="consensus-dot" aria-hidden="true"></span>${esc(h.text)}
        ${stale ? `<span class="muted">· ${stale} not counted</span>` : ""}
      </summary>
      ${list ? `<ul class="votes">${list}</ul>` : ""}
      <p class="muted consensus-policy">Policy ${esc(p.name || "R1")}: once the quorum is reached the decision is written
        to the verdict (source: human). Votes are pinned to the verdict version they were cast on
        (SVT <code>FOR SYSTEM_TIME</code>); a changed claim reopens the vote.</p>
    </details>`;
}

/** Fill every .consensus-slot under root from the links' server-side tallies. */
export function hydrate(root, getLink) {
  for (const slot of $$(".consensus-slot[data-link-id]", root)) {
    const t = getLink(slot.dataset.linkId)?.consensus;
    const next = t ? html(t) : "";
    if (slot.dataset.rendered !== next) { slot.innerHTML = next; slot.dataset.rendered = next; }
  }
}
