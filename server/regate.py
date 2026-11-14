"""Re-run the same-fact gate on pages whose links were graded one pair at a time.

Before gate.py, every vector candidate was graded with its own LLM call. On the
LLM-extracted pages that meant hundreds of calls, most answered with HTTP 429, and
both the errors and the "these are different facts" answers were stored as verdicts.
This command resets those machine verdicts to 'unmatched' and runs the gate +
grading again, page by page:
  - unreviewed LLM / fallback verdicts;
  - reviewed links only when their verdict is not a real model answer (an "LLM
    error" placeholder or the fallback source). Votes cast on a placeholder then show
    as stale under policy R1, which is accurate;
  - unreviewed same-property (deterministic) links whose subjects differ, e.g.
    "Daniel Tilas ennobled 1766" vs "Olof Tilas (his father) ennobled 1719".
It never touches human verdicts, the demo-seeded links, claims, reviews,
resolutions or suggestions.
Every UPDATE is a new system-versioned row, so the old verdicts stay in history.

Dry run by default:
  docker exec -w /app ovr-server python regate.py --title "Daniel Tilas"
  docker exec -w /app ovr-server python regate.py --title "Daniel Tilas" --apply
"""
import argparse

import gate
import verdicts
from db import execute, placeholders, query
from pages import comparison_set

RESET_WHERE = """cl.verdict NOT IN ('unmatched', 'different_fact') AND (
  (cl.verdict_source IN ('llm', 'deterministic-fallback')
   AND (NOT EXISTS (SELECT 1 FROM reviews r WHERE r.claim_link_id = cl.id)
        OR cl.verdict_source = 'deterministic-fallback'
        OR cl.rationale IS NULL OR cl.rationale LIKE 'LLM error%%'))
  OR (cl.verdict_source = 'deterministic'
      AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.claim_link_id = cl.id)
      AND ca.subject IS NOT NULL AND cb.subject IS NOT NULL AND ca.subject <> cb.subject))"""


def page_ids(title: str) -> list:
    rows = query("SELECT id, lang FROM articles WHERE title = %s ORDER BY FIELD(lang, 'en', 'fi', 'sv'), id",
                 (title,))
    if not rows:
        return []
    ids = comparison_set(rows[0]["id"])
    return sorted(set(ids) | {r["id"] for r in rows})


def links_to_reset(ids: list) -> list:
    ph = placeholders(ids)
    rows = query(
        f"""SELECT cl.id, cl.verdict, cl.verdict_source, ca.subject AS sa, cb.subject AS sb
            FROM claim_links cl
            JOIN claims ca ON ca.id = cl.claim_a JOIN claims cb ON cb.id = cl.claim_b
            WHERE ca.article_id IN ({ph}) AND cb.article_id IN ({ph}) AND {RESET_WHERE}""",
        tuple(ids) + tuple(ids))
    # deterministic links: only a real subject mismatch (gate's loose check), not
    # "Daniel Tilas" vs "Daniel Olofsson Tilas"
    return [r for r in rows if r["verdict_source"] != "deterministic"
            or not gate._same_subject({"subject": r["sa"]}, {"subject": r["sb"]})]


def all_affected_sets() -> list[list[int]]:
    """Distinct comparison sets across the whole DB that have fallback/error or
    subject-mismatched unreviewed links needing regating."""
    rows = query(
        f"""SELECT DISTINCT ca.article_id AS aid
            FROM claim_links cl
            JOIN claims ca ON ca.id = cl.claim_a
            JOIN claims cb ON cb.id = cl.claim_b
            WHERE (cl.verdict_source = 'deterministic-fallback'
                   OR cl.rationale LIKE 'LLM error%%'
                   OR (cl.verdict_source = 'deterministic'
                       AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.claim_link_id = cl.id)
                       AND ca.subject IS NOT NULL AND cb.subject IS NOT NULL
                       AND ca.subject <> cb.subject))"""
    )
    seen = set()
    sets = []
    for r in rows:
        aid = r["aid"]
        if aid in seen:
            continue
        ids = sorted(set(comparison_set(aid)))
        seen.update(ids)
        if links_to_reset(ids):
            sets.append(ids)
    return sets


def regate_ids(ids: list[int], apply: bool) -> None:
    reset = links_to_reset(ids)
    by = {}
    for r in reset:
        by[(r["verdict"], r["verdict_source"])] = by.get((r["verdict"], r["verdict_source"]), 0) + 1
    print(f"pages {ids}: {len(reset)} machine-graded links to regate: {by}", flush=True)
    if not apply or not reset:
        return
    link_ids = [r["id"] for r in reset]
    execute(f"""UPDATE claim_links SET verdict = 'unmatched', relation = 'unknown',
                    rationale = NULL, computed_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders(link_ids)})""", tuple(link_ids))
    for pid in ids:
        results = verdicts.grade_unmatched(pid)
        counts = {}
        for r in results:
            key = "deferred" if r.get("deferred") else r["after"]
            counts[key] = counts.get(key, 0) + 1
        print(f"  page {pid}: {counts}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--title", help="article title shared by the language editions")
    sel.add_argument("--all", action="store_true", help="regate every affected comparison set in MariaDB")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()
    if args.all:
        sets = all_affected_sets()
        print(f"found {len(sets)} comparison sets needing regate", flush=True)
        for ids in sets:
            regate_ids(ids, args.apply)
        return
    ids = page_ids(args.title)
    if not ids:
        raise SystemExit(f"no article titled {args.title!r}")
    regate_ids(ids, args.apply)
    if not args.apply:
        print("dry run: nothing written (use --apply)")
        return
    left = query(f"""SELECT cl.verdict, COUNT(*) n FROM claim_links cl
                     JOIN claims ca ON ca.id = cl.claim_a
                     WHERE ca.article_id IN ({placeholders(ids)}) GROUP BY cl.verdict""", tuple(ids))
    print("links now:", {r["verdict"]: r["n"] for r in left})


if __name__ == "__main__":
    main()
