"""Patch generation: proposed per-page edits from a page's comparison view.

Policy: cross-wiki automated editing is prohibited by Wikipedia policy, so we
never apply anything. We generate exportable wikitext drafts (article text
suggestions, citation templates, talk-page summaries) for human editors.
"""
from db import execute, placeholders, query, query_one
from diff import build_comparison


def _citation(claim: dict) -> str:
    rev = claim.get("revision_id") or 0
    return (
        "{{cite web"
        f"|url=https://{claim['lang']}.wikipedia.org/w/index.php?oldid={rev}"
        f"|title={claim['lang']}.wikipedia.org (revision {rev})"
        "|access-date=2026-11-13}}"
    )


def conflict_patch(source: dict, target: dict) -> str:
    return (
        f"'''Suggested correction''' (cross-language audit, from the "
        f"{source['lang']} edition): \"{source['statement_text']}\" "
        f"<ref>{_citation(source)}</ref>\n"
        f"Replaces: \"{target['statement_text']}\"\n"
    )


def gap_patch(source: dict, target_lang: str) -> str:
    return (
        f"'''Missing fact''' (present in the {source['lang']} edition, absent here): "
        f"\"{source['statement_text']}\" <ref>{_citation(source)}</ref>\n"
    )


def talk_page_draft(page: dict, others: list, conflicts: int, missing: int,
                    extra: int) -> str:
    """Talk-page summary for one page of the comparison set.

    others: the pages it was compared with; missing = facts the others state
    that this page lacks; extra = facts only this page states."""
    compared = ", ".join(f"{o['lang']}: {o['title']}" for o in others) or "none"
    return (
        f"== Cross-language consistency audit (One Verifiable Record) ==\n"
        f"An automated comparison of this page ({page['lang']}: {page['title']}, "
        f"revision {page.get('revision_id') or 'unknown'}) with its other-language "
        f"counterparts ({compared}) found {conflicts} conflicting claim(s), "
        f"{missing} fact(s) stated there but missing here and {extra} fact(s) "
        f"stated only here. Every finding cites its source passage and revision "
        f"id; see the comparison view for receipts. Proposed fixes are listed "
        f"below for review. --~~~~"
    )


def _insert(article_id: int, link_id, patch_type: str, wikitext: str) -> bool:
    """Insert a draft unless an identical one already exists for this page.

    Returns True when a new row was created.
    """
    exists = query_one(
        """SELECT id FROM suggestions
           WHERE article_id = %s AND patch_type = %s AND wikitext = %s
           LIMIT 1""",
        (article_id, patch_type, wikitext),
    )
    if exists:
        return False
    execute(
        """INSERT INTO suggestions (article_id, claim_link_id, patch_type, wikitext)
           VALUES (%s, %s, %s, %s)""",
        (article_id, link_id, patch_type, wikitext),
    )
    return True


def generate_suggestions(page_id: int):
    """Drafts for page X and its counterpart pages, from X's comparison view.

    conflict rows     -> a correction for each side of every contradicted link
                         between this page and a counterpart
    missing_here rows -> a "missing fact" notice for X (one per row)
    only_here rows    -> a "missing fact" notice for every counterpart page
    plus one talk-page draft per page of the comparison set.
    Returns None if the page does not exist.
    """
    view = build_comparison(page_id)
    if view is None:
        return None
    page, cps = view["page"], view["counterparts"]
    pages_by_id = {p["id"]: p for p in [page] + cps}
    created = 0
    # talk-page counts per page: [conflicts, missing there, only there]
    tally = {pid: [0, 0, 0] for pid in pages_by_id}

    for row in view["rows"]:
        claims = row["here"] + [c for cs in row["there"].values() for c in cs]
        by_id = {c["id"]: c for c in claims}
        present = {c["page_id"] for c in claims}
        if row["status"] == "conflict":
            for link in row["links"]:
                if link["verdict"] != "contradicted":
                    continue
                src, tgt = by_id.get(link["claim_a"]), by_id.get(link["claim_b"])
                # only links that involve this page (counterpart-vs-counterpart
                # conflicts are drafted from those pages' own views)
                if not (src and tgt) or page_id not in (src["page_id"], tgt["page_id"]):
                    continue
                for source, target in ((src, tgt), (tgt, src)):
                    if target["page_id"] in pages_by_id:
                        created += _insert(target["page_id"], link["id"], "wikitext",
                                           conflict_patch(source, target))
            for pid in present:
                tally[pid][0] += 1
        elif row["status"] == "missing_here":
            # one notice per row, sourced from the first counterpart (fi, sv, en order)
            source = next((c for p in cps for c in row["there"].get(p["lang"], [])), None)
            if source is None:
                continue
            created += _insert(page_id, None, "wikitext",
                               gap_patch(source, page["lang"]))
            tally[page_id][1] += 1
            for pid in present:
                tally[pid][2] += 1
        elif row["status"] == "only_here":
            source = row["here"][0]
            for p in cps:
                created += _insert(p["id"], None, "wikitext", gap_patch(source, p["lang"]))
                tally[p["id"]][1] += 1
            tally[page_id][2] += 1

    for pid, p in pages_by_id.items():
        others = [page] if pid != page_id else cps
        c, m, x = tally[pid]
        created += _insert(pid, None, "talk_page", talk_page_draft(p, others, c, m, x))

    ids = list(pages_by_id)
    drafts = query(
        f"""SELECT s.id, s.patch_type, s.wikitext, s.status, a.lang, a.title,
                   s.article_id AS page_id, s.claim_link_id, s.resolution_id
            FROM suggestions s
            JOIN articles a ON a.id = s.article_id
            WHERE s.article_id IN ({placeholders(ids)})
            ORDER BY s.id DESC LIMIT 200""",
        tuple(ids),
    )
    return {"page_id": page_id, "created": created, "drafts": drafts}


def get_suggestion(suggestion_id: int):
    return query_one(
        """SELECT id, status, claim_link_id, article_id AS page_id
           FROM suggestions WHERE id = %s""",
        (suggestion_id,),
    )


def mark_exported(suggestion_id: int) -> int:
    """draft -> exported. A system-versioned UPDATE, so the export itself is
    part of the audit trail. Already exported/applied drafts are left alone."""
    return execute(
        "UPDATE suggestions SET status = 'exported' WHERE id = %s AND status = 'draft'",
        (suggestion_id,),
    )
