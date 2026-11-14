/* Review consensus (policy R1, docs/review-consensus.md). Shows how far a verdict is from
 * becoming a binding human decision. Display only: the server does not enforce it yet.
 *
 * Votes come from GET /api/claims/{claim_a}/history, which returns the link's versions
 * (claim_links FOR SYSTEM_TIME ALL) and its reviews with ROW_START. A vote counts only if
 * the verdict that was current when it was cast is still the current verdict. A vote cast
 * on an earlier verdict is listed as stale. One vote per reviewer: their latest review. */
import { api } from "./api.js";
import { $$, esc, fmtTime } from "./util.js";

export const POLICY = {
  confirmQuorum: 2,   // distinct reviewers to confirm the machine verdict
  overturnQuorum: 3,  // distinct reviewers to overturn it (higher bar)
  margin: 2,          // lead needed when both sides have votes
};

const CONFLICT_VERDICTS = new Set(["contradicted", "possible_conflict"]);
const cache = new Map(); // link id -> {key, tally}
const inflight = new Map(); // claim id -> Promise<history>

/** Does a decision confirm or oppose the verdict it was cast on?
 * agree = the verdict is right; disagree = it is wrong; fix = the page text needs a change,
 * which confirms a conflict verdict and opposes any other verdict. */
export function role(decision, verdict) {
  if (decision === "agree") return "confirm";
  if (decision === "disagree") return "oppose";
  if (decision === "fix") return CONFLICT_VERDICTS.has(verdict) ? "confirm" : "oppose";
  return null;
}

/** Pure policy: votes [{reviewer, decision, verdictAtVote}] + current verdict -> tally. */
export function decide(votes, verdict) {
  const latest = new Map();
  for (const v of votes) latest.set(String(v.reviewer || "").trim().toLowerCase(), v); // votes sorted by time
  const counted = [];
  const stale = [];
  for (const v of latest.values()) (v.verdictAtVote === verdict ? counted : stale).push(v);
  let confirm = 0, oppose = 0;
  for (const v of counted) role(v.decision, verdict) === "confirm" ? confirm++ : oppose++;
  const { confirmQuorum, overturnQuorum, margin } = POLICY;
  let state = "unreviewed";
  if (confirm >= confirmQuorum && confirm - oppose >= margin) state = "confirmed";
  else if (oppose >= overturnQuorum && oppose - confirm >= margin) state = "overturned";
  else if (confirm && oppose) state = "disputed";
  else if (confirm) state = "pending_confirm";
  else if (oppose) state = "pending_overturn";
  return { state, confirm, oppose, counted, stale, verdict };
}

/** Build votes for one link from a claim history payload (SVT rows). */
export function votesFromHistory(history, linkId) {
  const id = Number(linkId);
  const versions = (history.links || []).filter((l) => Number(l.id) === id)
    .sort((a, b) => String(a.ROW_START).localeCompare(String(b.ROW_START)));
  const at = (ts) => versions.find((v) => String(v.ROW_START) <= ts && ts < String(v.ROW_END)) || null;
  return (history.reviews || [])
    .filter((r) => Number(r.claim_link_id) === id)
    .sort((a, b) => String(a.ROW_START).localeCompare(String(b.ROW_START)))
    .map((r) => ({ reviewer: r.reviewer, decision: r.decision, note: r.note, at: r.ROW_START,
      verdictAtVote: at(String(r.ROW_START))?.verdict || null }));
}

const keyOf = (link) => `${link.verdict}|${link.review?.id ?? ""}`;

function history(claimId) {
  if (!inflight.has(claimId)) {
    const p = api.claimHistory(claimId).finally(() => setTimeout(() => inflight.delete(claimId), 0));
    inflight.set(claimId, p);
  }
  return inflight.get(claimId);
}

export async function tally(link) {
  const hit = cache.get(Number(link.id));
  if (hit && hit.key === keyOf(link)) return hit.tally;
  const h = await history(Number(link.claim_a));
  const t = decide(votesFromHistory(h, link.id), link.verdict);
  cache.set(Number(link.id), { key: keyOf(link), tally: t });
  return t;
}

export function invalidate(linkId) { cache.delete(Number(linkId)); }

const DECISION_WORD = { agree: "agreed", disagree: "disagreed", fix: "asked for a fix" };

function headline(t) {
  const { confirmQuorum, overturnQuorum, margin } = POLICY;
  switch (t.state) {
    case "confirmed": return { cls: "ok", text: `Consensus: verdict confirmed by ${t.confirm} reviewers` };
    case "overturned": return { cls: "bad", text: `Consensus: machine verdict overturned by ${t.oppose} reviewers` };
    case "disputed": return { cls: "warn", text: `Disputed: ${t.confirm} confirm vs ${t.oppose} oppose (needs a lead of ${margin})` };
    case "pending_confirm": return { cls: "pending", text: `${t.confirm} of ${confirmQuorum} confirmations` };
    case "pending_overturn": return { cls: "pending", text: `${t.oppose} of ${overturnQuorum} votes to overturn` };
    default: return { cls: "pending", text: "No current votes" };
  }
}

export function html(t) {
  const h = headline(t);
  const vote = (v, isStale) => `<li${isStale ? ` class="stale"` : ""}>
      <b>${esc(v.reviewer)}</b> ${esc(DECISION_WORD[v.decision] || v.decision)}
      ${isStale ? `<span class="muted">on an earlier verdict (${esc(String(v.verdictAtVote || "unknown").replace(/_/g, " "))}), not counted</span>`
        : `<span class="muted">(${esc(role(v.decision, t.verdict))})</span>`}
      <span class="muted">${esc(fmtTime(v.at))}</span>
      ${v.note ? `<q>${esc(v.note)}</q>` : ""}</li>`;
  const list = [...t.counted.map((v) => vote(v, false)), ...t.stale.map((v) => vote(v, true))].join("");
  return `<details class="consensus ${h.cls}">
      <summary title="Policy R1 (proposed, not yet enforced by the server): ${POLICY.confirmQuorum} distinct reviewers confirm a verdict, ${POLICY.overturnQuorum} overturn it. A vote counts only while the verdict it was cast on is current (checked with FOR SYSTEM_TIME).">
        <span class="consensus-dot" aria-hidden="true"></span>${esc(h.text)}
        ${t.stale.length ? `<span class="muted">· ${t.stale.length} stale vote${t.stale.length === 1 ? "" : "s"}</span>` : ""}
      </summary>
      ${list ? `<ul class="votes">${list}</ul>` : ""}
      <p class="muted consensus-policy">Proposed policy R1, shown for review. Votes are pinned to the verdict
        version they were cast on (SVT <code>FOR SYSTEM_TIME</code>); a new verdict reopens the vote.</p>
    </details>`;
}

/** Fill every visible .consensus-slot under root. Only links with a review are fetched. */
export async function hydrate(root, getLink) {
  const slots = $$(".consensus-slot[data-link-id]", root).filter((s) => !s.closest("[hidden]"));
  const queue = slots.map((s) => ({ slot: s, link: getLink(s.dataset.linkId) })).filter((x) => x.link?.review);
  const worker = async () => {
    for (let job = queue.shift(); job; job = queue.shift()) {
      try {
        const t = await tally(job.link);
        if (job.slot.isConnected) job.slot.innerHTML = html(t);
      } catch { /* history unavailable: leave the slot empty */ }
    }
  };
  await Promise.all(Array.from({ length: 4 }, worker));
}
