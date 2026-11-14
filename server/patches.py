"""Patch generation: proposed per-language edits from conflicts and gaps.

Policy: cross-wiki automated editing is prohibited by Wikipedia policy, so we
never apply anything. We generate exportable wikitext drafts (article text
suggestions, citation templates, talk-page summaries) for human editors.
"""
from db import execute, query, query_one
from diff import build_merge_view


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


def talk_page_draft(qid: str, conflict_count: int, gap_count: int) -> str:
    return (
        f"== Cross-language consistency audit (One Verifiable Record) ==\n"
        f"An automated comparison of the fi/sv/en editions of this article "
        f"({qid}) found {conflict_count} conflicting claim(s) and "
        f"{gap_count} gap(s). Every finding cites its source passage and "
        f"revision id; see the merge view for receipts. Proposed fixes are "
        f"listed below for review. --~~~~"
    )


def _insert(article_id: int, link_id, patch_type: str, wikitext: str) -> bool:
    """Insert a draft unless an identical one already exists for this article.

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


def generate_suggestions(qid: str) -> dict:
    view = build_merge_view(qid)
    articles_by_lang = {a["lang"]: a for a in view["articles"]}
    conflicts = [c for c in view["clusters"] if c["status"] == "conflict"]
    gaps = [c for c in view["clusters"] if c["status"] == "gap"]
    created = 0

    # one proposed fix per (conflict, target language)
    for cluster in conflicts:
        claims_by_id = {c["id"]: c for c in cluster["claims"]}
        for link in cluster["links"]:
            if link["verdict"] != "contradicted":
                continue
            src = claims_by_id.get(link["claim_a"])
            tgt = claims_by_id.get(link["claim_b"])
            if not (src and tgt):
                continue
            for source, target in ((src, tgt), (tgt, src)):
                art = articles_by_lang.get(target["lang"])
                if not art:
                    continue
                created += _insert(
                    art["id"], link["id"], "wikitext", conflict_patch(source, target)
                )

    # one gap notice per (gap claim, language missing it)
    for cluster in gaps:
        for source in cluster["claims"]:
            for art in view["articles"]:
                if art["lang"] in cluster["langs_present"]:
                    continue
                created += _insert(
                    art["id"], None, "wikitext", gap_patch(source, art["lang"])
                )

    # one talk-page draft per language edition
    for art in view["articles"]:
        created += _insert(art["id"], None, "talk_page",
                           talk_page_draft(qid, len(conflicts), len(gaps)))

    drafts = query(
        """SELECT s.id, s.patch_type, s.wikitext, s.status, a.lang, a.title,
                  s.claim_link_id
           FROM suggestions s
           JOIN articles a ON a.id = s.article_id
           WHERE a.wikidata_qid = %s
           ORDER BY s.id DESC LIMIT 100""",
        (qid,),
    )
    return {"qid": qid, "created": created, "drafts": drafts}


def get_suggestion(suggestion_id: int):
    return query_one(
        """SELECT s.id, s.status, s.claim_link_id, a.wikidata_qid
           FROM suggestions s
           JOIN articles a ON a.id = s.article_id
           WHERE s.id = %s""",
        (suggestion_id,),
    )


def mark_exported(suggestion_id: int) -> int:
    """draft -> exported. A system-versioned UPDATE, so the export itself is
    part of the audit trail. Already exported/applied drafts are left alone."""
    return execute(
        "UPDATE suggestions SET status = 'exported' WHERE id = %s AND status = 'draft'",
        (suggestion_id,),
    )
