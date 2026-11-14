"""Pre-seed one honest review vote on a few clear-cut conflicts for the live demo.

Policy R1 (docs/review-consensus.md) needs 2 distinct reviewers to confirm a verdict.
With one seeded vote, a judge's single "Agree" completes the quorum live and the
verdict becomes a human decision. The seed reviewer is labelled as a demo seed.

Links are found by page and predicate through the public API, not by id, so the same
script works on any deployment. Dry run by default; --apply posts the reviews.
Idempotent: a link where the seed reviewer already has a vote is skipped.

  python jobs/seed_demo_votes.py                    # dry run against localhost:8080
  python jobs/seed_demo_votes.py --apply --api http://<host>:8080/api
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

REVIEWER = "OVR team (demo seed)"

# (lang, title of claim_a's side, counterpart lang, predicate). Each pair states two
# different values in the stored revisions; the receipts show both sentences.
TARGETS = [
    ("en", "Daniel Tilas", "sv", "date_of_death"),       # 17 vs 27 October 1772
    ("fi", "Bertel Gripenberg", "sv", "death_date"),     # 5 vs 6 May 1947
    ("en", "Algar von Heiroth", "fi", "death_date"),     # 1988 vs 1985
]
NOTE = ("Pre-seeded demo vote: read both revisions in the receipts; the editions state "
        "different values, so the conflict is real. Needs one more reviewer (policy R1).")


def call(api, path, body=None):
    req = urllib.request.Request(
        api + path, data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def find_link(api, lang, title, other_lang, predicate):
    q = urllib.parse.urlencode({"lang": lang, "title": title})
    page = call(api, f"/pages/by-title?{q}")["page"]
    view = call(api, f"/pages/{page['id']}/compare")
    claims = {}
    for row in view["rows"]:
        for cl in row.get("here", []) + [c for cs in row.get("there", {}).values() for c in cs]:
            claims[cl["id"]] = cl
    for row in view["rows"]:
        for link in row.get("links", []):
            a, b = claims.get(link["claim_a"]), claims.get(link["claim_b"])
            if not a or not b or link["verdict"] != "contradicted":
                continue
            if {a["lang"], b["lang"]} == {lang, other_lang} and a.get("predicate") == b.get("predicate") == predicate:
                return link, a, b
    return None, None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8080/api")
    ap.add_argument("--apply", action="store_true", help="post the reviews (default: dry run)")
    args = ap.parse_args()
    api = args.api.rstrip("/")
    failures = 0
    for lang, title, other, predicate in TARGETS:
        link, a, b = find_link(api, lang, title, other, predicate)
        if not link:
            print(f"SKIP  {lang}:{title} {predicate}: no contradicted {lang}-{other} link found")
            failures += 1
            continue
        tally = call(api, f"/links/{link['id']}/consensus")
        mine = [v for v in tally["votes"] if v["reviewer"].strip().lower() == REVIEWER.lower() and v["counted"]]
        label = f"link #{link['id']} {title} {predicate}: {a['lang']} {a['value_text']!r} vs {b['lang']} {b['value_text']!r}"
        if mine:
            print(f"KEEP  {label} (already seeded; state {tally['state']})")
            continue
        if not args.apply:
            print(f"WOULD {label} (state now {tally['state']})")
            continue
        res = call(api, "/reviews", {"claim_link_id": link["id"], "reviewer": REVIEWER,
                                     "decision": "agree", "note": NOTE, "override": True})
        print(f"SEED  {label} -> {res['consensus']['state']}, {res['consensus']['confirm']} of "
              f"{res['consensus']['policy']['confirm_quorum']} confirmations")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
