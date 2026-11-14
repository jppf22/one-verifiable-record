"""Resolutions: fix a reviewer-confirmed conflict in every language at once.

Once policy R1 has confirmed a conflict (claim_links.verdict = 'contradicted',
verdict_source = 'human', see consensus.py), a reviewer writes the correct version
once, in English, with a reliable source. The server then drafts the fix for every
page of the comparison set (fi / sv / en): the English text as written, other
languages machine-translated through the Verda Inference API. Each draft is a
`suggestions` row (resolution_id set) that a human copies into Wikipedia's editor.

Nothing is ever submitted to Wikipedia (cross-wiki automated editing is
prohibited). Another Wikipedia is not a source (WP:CIRCULAR), so source URLs on
wikipedia.org / wikidata.org are rejected. `resolutions` is SYSTEM VERSIONED.
"""
import datetime
import json
from urllib.parse import urlparse

import httpx

import consensus
from config import settings
from db import insert, placeholders, query, query_one
from diff import build_comparison
from pages import lang_key
from verdicts import SOURCE_HUMAN

LANG_NAMES = {"fi": "Finnish", "sv": "Swedish", "en": "English"}
NOT_A_SOURCE = ("wikipedia.org", "wikidata.org", "wikimedia.org")
MIN_TEXT, MAX_TEXT = 10, 2000
UNTRANSLATED_MARK = "Machine translation unavailable"


class ResolutionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def check_source(url: str | None) -> str | None:
    """Validate an optional source URL: http(s), and not a Wikimedia wiki."""
    url = (url or "").strip()
    if not url:
        return None
    if len(url) > 2048:
        raise ResolutionError(422, "source_url is too long")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ResolutionError(422, "source_url must be an http(s) URL")
    host = p.hostname.lower()
    if any(host == h or host.endswith("." + h) for h in NOT_A_SOURCE):
        raise ResolutionError(
            422, "Another Wikipedia or Wikidata is not a source (WP:CIRCULAR). "
                 "Cite the publication the fact comes from.")
    return url


def translate(text: str, lang: str) -> tuple[str, bool]:
    """(text in `lang`, translated?). English is returned as written. If the LLM
    is unavailable the English text comes back with translated=False."""
    if lang == "en":
        return text, True
    if not settings.verda_api_key:
        return text, False
    name = LANG_NAMES.get(lang, lang)
    try:
        r = httpx.post(
            f"{settings.verda_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.verda_api_key}"},
            json={
                "model": settings.llm_model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": (
                        f"Translate one corrected encyclopedic statement from English into "
                        f"{name} for {lang}.wikipedia.org. Keep every name, number and date "
                        f"exactly; write dates the way {name} Wikipedia does. Do not add "
                        'facts. Reply ONLY with JSON: {"text": "..."}')},
                    {"role": "user", "content": text},
                ],
            },
            timeout=45,
        )
        r.raise_for_status()
        out = str(json.loads(r.json()["choices"][0]["message"]["content"]).get("text") or "").strip()
        return (out, True) if out else (text, False)
    except Exception:  # degrade to the English text, flagged for a human translator
        return text, False


def _same(a: str, b: str) -> bool:
    norm = lambda s: " ".join(str(s or "").lower().split()).rstrip(".")
    return norm(a) == norm(b)


def draft_wikitext(lang: str, old: dict | None, text: str, translated: bool,
                   source_url: str | None, link_id: int, confirmations: int) -> str:
    """Wikitext a human pastes into the page. Plain `<ref>[url host]</ref>` works on
    every language edition (citation template names differ per wiki)."""
    ref = ""
    if source_url:
        host = urlparse(source_url).hostname or source_url
        ref = f"<ref>[{source_url} {host}] ({datetime.date.today().isoformat()})</ref>"
    lines = []
    if lang != "en" and not translated:
        lines.append(f"<!-- {UNTRANSLATED_MARK}: translate this text into "
                     f"{LANG_NAMES.get(lang, lang)} before saving. -->")
    origin = (f"One Verifiable Record, conflict #{link_id} confirmed by "
              f"{confirmations} reviewer{'s' if confirmations != 1 else ''}")
    if lang != "en" and translated:
        origin += "; machine-translated from English, check before saving"
    if old:
        lines += [f"'''Suggested correction''' ({origin})",
                  f'Replace: "{old["statement_text"]}"',
                  f'With: "{text}"{ref}']
    else:
        lines += [f"'''Missing fact''' ({origin})", f'Add: "{text}"{ref}']
    if not source_url:
        lines.append("<!-- Add a reliable published source before saving: another "
                     "Wikipedia is not a source (WP:CIRCULAR). -->")
    return "\n".join(lines) + "\n"


def _link(link_id: int):
    return query_one(
        """SELECT cl.id, cl.claim_a, cl.claim_b, cl.verdict, cl.verdict_source,
                  ca.article_id AS page_id
           FROM claim_links cl JOIN claims ca ON ca.id = cl.claim_a
           WHERE cl.id = %s""",
        (link_id,),
    )


def _drafts(resolution_id: int) -> list:
    rows = query(
        """SELECT s.id, s.article_id AS page_id, a.lang, a.title, s.patch_type,
                  s.wikitext, s.status, s.claim_link_id, s.resolution_id
           FROM suggestions s JOIN articles a ON a.id = s.article_id
           WHERE s.resolution_id = %s""",
        (resolution_id,),
    )
    for r in rows:
        r["untranslated"] = UNTRANSLATED_MARK in (r["wikitext"] or "")
    return sorted(rows, key=lambda r: lang_key({"lang": r["lang"], "id": r["page_id"]}))


def _resolution(resolution_id: int) -> dict:
    res = query_one(
        """SELECT id, claim_link_id, author, correct_text, source_url, created_at
           FROM resolutions WHERE id = %s""",
        (resolution_id,),
    )
    return {"resolution": res, "drafts": _drafts(resolution_id)}


def latest(link_id: int) -> dict | None:
    """Latest resolution of a link with its drafts, or None."""
    row = query_one(
        "SELECT MAX(id) AS id FROM resolutions WHERE claim_link_id = %s", (link_id,))
    return _resolution(row["id"]) if row and row["id"] else None


def latest_many(link_ids) -> dict:
    """{link_id: {id, author, correct_text, source_url, created_at, drafts}}: the
    latest resolution of each link, one query (used by the compare payload)."""
    ids = sorted({int(i) for i in link_ids})
    if not ids:
        return {}
    rows = query(
        f"""SELECT r.id, r.claim_link_id, r.author, r.correct_text, r.source_url,
                   r.created_at,
                   (SELECT COUNT(*) FROM suggestions s WHERE s.resolution_id = r.id) AS drafts
            FROM resolutions r
            JOIN (SELECT claim_link_id, MAX(id) AS max_id FROM resolutions
                  WHERE claim_link_id IN ({placeholders(ids)})
                  GROUP BY claim_link_id) m ON m.max_id = r.id""",
        tuple(ids),
    )
    return {r.pop("claim_link_id"): r for r in rows}


def create(link_id: int, author: str, correct_text: str, source_url: str | None) -> dict:
    """Store the correct version and draft it for every page of the comparison set.
    Raises ResolutionError (404 / 409 / 422)."""
    author = (author or "").strip()
    text = " ".join((correct_text or "").split())
    if not author or len(author) > 128:
        raise ResolutionError(422, "author is required (max 128 characters)")
    if not MIN_TEXT <= len(text) <= MAX_TEXT:
        raise ResolutionError(422, f"correct_text must be {MIN_TEXT} to {MAX_TEXT} characters")
    link = _link(link_id)
    if not link:
        raise ResolutionError(404, "claim link not found")
    if link["verdict"] != "contradicted" or link["verdict_source"] != SOURCE_HUMAN:
        raise ResolutionError(
            409, "Only a conflict confirmed by reviewers (policy R1) can be resolved")
    source = check_source(source_url)

    view = build_comparison(link["page_id"], with_reviews=False)
    row = next((r for r in view["rows"] if any(l["id"] == link_id for l in r["links"])), None)
    claims = (row["here"] + [c for cs in row["there"].values() for c in cs]) if row else []
    in_link = {link["claim_a"], link["claim_b"]}
    confirmations = consensus.tally(link_id).get("confirm", 0)

    res_id = insert(
        """INSERT INTO resolutions (claim_link_id, author, correct_text, source_url)
           VALUES (%s, %s, %s, %s)""",
        (link_id, author, text, source),
    )
    skipped = []
    for page in sorted([view["page"]] + view["counterparts"], key=lang_key):
        mine = [c for c in claims if c["page_id"] == page["id"]]
        old = next((c for c in mine if c["id"] in in_link), mine[0] if mine else None)
        local, translated = translate(text, page["lang"])
        if old and _same(old["statement_text"], local):
            skipped.append({"page_id": page["id"], "lang": page["lang"], "reason": "already_correct"})
            continue
        insert(
            """INSERT INTO suggestions (article_id, claim_link_id, resolution_id, patch_type, wikitext)
               VALUES (%s, %s, %s, 'wikitext', %s)""",
            (page["id"], link_id, res_id,
             draft_wikitext(page["lang"], old, local, translated, source, link_id, confirmations)),
        )
    return {**_resolution(res_id), "skipped": skipped}
