"""Review consensus, policy R1 (docs/review-consensus.md).

A human decision becomes binding only when enough distinct reviewers agree:

- one vote per reviewer per link (their latest review; names compared case-insensitively);
- agree confirms the machine verdict, disagree opposes it, fix ("the page text is
  wrong") confirms a conflict verdict and opposes any other verdict;
- confirmed: >= 2 confirming votes with a lead of >= 2; a confirmed possible_conflict
  becomes contradicted and a confirmed possible_match becomes supported;
- overturned: >= 3 opposing votes with a lead of >= 2;
- votes on both sides without a decision: disputed.

Votes are pinned with system versioning. The "subject" of a vote is the latest machine
verdict (claim_links FOR SYSTEM_TIME ALL, verdict_source <> 'human') at the time the
vote was cast. A vote counts only if that verdict equals the current machine subject,
and if neither claim has a newer version (claims ROW_START) than the vote.

``enforce`` keeps claim_links consistent with the tally: a binding state writes the
decided verdict with verdict_source='human'; a link that is human-decided but no longer
binding (new opposing votes) gets its machine verdict back. Only real changes are
written, so every row in the link's SVT history is a meaningful step.
"""
from db import execute, placeholders, query, query_one
from verdicts import HUMAN_DECISION_VERDICT, SOURCE_HUMAN

CONFIRM_QUORUM = 2
OVERTURN_QUORUM = 3
MARGIN = 2
POLICY = {"name": "R1", "confirm_quorum": CONFIRM_QUORUM,
          "overturn_quorum": OVERTURN_QUORUM, "margin": MARGIN}

CONFLICT_VERDICTS = {"contradicted", "possible_conflict"}
CONFIRMED_VERDICT = {"possible_conflict": "contradicted", "possible_match": "supported"}
BINDING = {"confirmed", "overturned"}


def role(decision: str, verdict: str) -> str | None:
    if decision == "agree":
        return "confirm"
    if decision == "disagree":
        return "oppose"
    if decision == "fix":
        return "confirm" if verdict in CONFLICT_VERDICTS else "oppose"
    return None


def overturn_target(subject: str, opposing: list) -> str | None:
    """Verdict that replaces an overturned one, or None if reviewers didn't say."""
    flipped = HUMAN_DECISION_VERDICT["disagree"].get(subject)
    if flipped:
        return flipped
    # unverifiable / definition_mismatch: only "fix" says which way (a real conflict)
    if opposing and all(v["decision"] == "fix" for v in opposing):
        return "contradicted"
    return None


def decide(votes: list, subject: str | None) -> dict:
    """Pure policy. votes: [{reviewer, decision, subject_at_vote, claim_changed}], in
    the order they were cast. Returns the tally (votes annotated with role/counted)."""
    latest: dict = {}
    for v in votes:
        latest[str(v.get("reviewer") or "").strip().lower()] = v
    confirm = oppose = 0
    opposing = []
    out = []
    for v in votes:
        mine = latest.get(str(v.get("reviewer") or "").strip().lower()) is v
        if not mine:
            reason = "superseded"
        elif v.get("claim_changed"):
            reason = "claim_changed"
        elif v.get("subject_at_vote") != subject:
            reason = "verdict_changed"
        else:
            reason = None
        r = role(v["decision"], v.get("subject_at_vote") or subject or "")
        if reason is None:
            if r == "confirm":
                confirm += 1
            else:
                oppose += 1
                opposing.append(v)
        out.append({**v, "role": r, "counted": reason is None, "stale_reason": reason})

    if confirm >= CONFIRM_QUORUM and confirm - oppose >= MARGIN:
        state = "confirmed"
    elif oppose >= OVERTURN_QUORUM and oppose - confirm >= MARGIN:
        state = "overturned"
    elif confirm and oppose:
        state = "disputed"
    elif confirm:
        state = "pending_confirm"
    elif oppose:
        state = "pending_overturn"
    else:
        state = "unreviewed"

    target = None
    if state == "confirmed":
        # confirming a *possible* verdict settles it: the buttons read "Real
        # conflict" / "Same fact", so the decided verdict is the definite one
        target = CONFIRMED_VERDICT.get(subject, subject)
    elif state == "overturned":
        target = overturn_target(subject, opposing)
    return {"policy": POLICY, "state": state, "confirm": confirm, "oppose": oppose,
            "subject_verdict": subject, "target_verdict": target,
            "binding": state in BINDING and target is not None, "votes": out}


def _subject_at(versions: list, ts) -> dict | None:
    """Latest machine (non-human) graded version that started at or before ts."""
    best = None
    for v in versions:  # sorted by ROW_START
        if v["ROW_START"] > ts:
            break
        if v["verdict_source"] != SOURCE_HUMAN and v["verdict"] != "unmatched":
            best = v
    return best


def tallies(link_ids) -> dict:
    """{link_id: tally} for the given links that have at least one review.
    Three queries in total, whatever the number of links."""
    ids = sorted({int(i) for i in link_ids})
    if not ids:
        return {}
    reviews = query(
        f"""SELECT id AS review_id, claim_link_id, reviewer, decision, note,
                   ROW_START AS cast_at
            FROM reviews WHERE claim_link_id IN ({placeholders(ids)})
            ORDER BY ROW_START, id""",
        tuple(ids),
    )
    if not reviews:
        return {}
    reviewed = sorted({r["claim_link_id"] for r in reviews})
    versions = query(
        f"""SELECT id, claim_a, claim_b, verdict, verdict_source, ROW_START, ROW_END
            FROM claim_links FOR SYSTEM_TIME ALL
            WHERE id IN ({placeholders(reviewed)})
            ORDER BY id, ROW_START""",
        tuple(reviewed),
    )
    by_link: dict = {}
    for v in versions:
        by_link.setdefault(v["id"], []).append(v)
    claim_ids = sorted({c for vs in by_link.values() for c in (vs[-1]["claim_a"], vs[-1]["claim_b"])})
    claim_start = {r["id"]: r["ROW_START"] for r in query(
        f"SELECT id, ROW_START FROM claims WHERE id IN ({placeholders(claim_ids)})",
        tuple(claim_ids))} if claim_ids else {}

    votes_by_link: dict = {}
    for r in reviews:
        votes_by_link.setdefault(r["claim_link_id"], []).append(r)
    out = {}
    for link_id, vs in by_link.items():
        current = vs[-1]
        subject_row = _subject_at(vs, current["ROW_START"])
        subject = subject_row["verdict"] if subject_row else None
        changed_at = max((claim_start.get(c) for c in (current["claim_a"], current["claim_b"])
                          if claim_start.get(c) is not None), default=None)
        votes = []
        for r in votes_by_link.get(link_id, []):
            at = _subject_at(vs, r["cast_at"])
            votes.append({
                "review_id": r["review_id"], "reviewer": r["reviewer"],
                "decision": r["decision"], "note": r["note"], "cast_at": r["cast_at"],
                "subject_at_vote": at["verdict"] if at else None,
                "claim_changed": changed_at is not None and changed_at > r["cast_at"],
            })
        t = decide(votes, subject)
        t["current_verdict"] = current["verdict"]
        t["current_source"] = current["verdict_source"]
        out[link_id] = t
    return out


def tally(link_id: int) -> dict:
    """Tally of one link (an 'unreviewed' tally if it has no reviews)."""
    t = tallies([link_id]).get(int(link_id))
    if t:
        return t
    row = query_one("SELECT verdict, verdict_source FROM claim_links WHERE id = %s", (link_id,))
    t = decide([], row["verdict"] if row else None)
    if row:
        t["current_verdict"], t["current_source"] = row["verdict"], row["verdict_source"]
    return t


def enforce(link_id: int) -> dict | None:
    """Make claim_links match the R1 tally. Returns {id, state, before, after, changed}
    or None if the link does not exist."""
    current = query_one(
        "SELECT id, verdict, verdict_source FROM claim_links WHERE id = %s", (link_id,))
    if not current:
        return None
    t = tally(link_id)
    before = current["verdict"]
    result = {"id": link_id, "state": t["state"], "before": before, "after": before,
              "changed": False, "binding": t["binding"]}
    if t["binding"]:
        want = t["target_verdict"]
        if before == want and current["verdict_source"] == SOURCE_HUMAN:
            return result
        n = t["confirm"] if t["state"] == "confirmed" else t["oppose"]
        execute(
            """UPDATE claim_links
               SET verdict = %s, verdict_source = %s, verdict_confidence = 1.0,
                   rationale = %s, computed_at = CURRENT_TIMESTAMP
               WHERE id = %s""",
            (want, SOURCE_HUMAN,
             f"R1 consensus: machine verdict '{t['subject_verdict']}' {t['state']} "
             f"by {n} reviewers", link_id),
        )
        result.update(after=want, changed=before != want)
        return result
    if current["verdict_source"] != SOURCE_HUMAN:
        return result
    # Human-decided, but the votes no longer bind: restore the latest machine version.
    machine = query_one(
        """SELECT verdict, verdict_source, verdict_confidence, rationale
           FROM claim_links FOR SYSTEM_TIME ALL
           WHERE id = %s AND verdict_source <> %s AND verdict <> 'unmatched'
           ORDER BY ROW_START DESC LIMIT 1""",
        (link_id, SOURCE_HUMAN),
    )
    if not machine:
        return result
    execute(
        """UPDATE claim_links
           SET verdict = %s, verdict_source = %s, verdict_confidence = %s,
               rationale = %s, computed_at = CURRENT_TIMESTAMP
           WHERE id = %s""",
        (machine["verdict"], machine["verdict_source"], machine["verdict_confidence"],
         machine["rationale"], link_id),
    )
    result.update(after=machine["verdict"], changed=before != machine["verdict"])
    return result
