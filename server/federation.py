"""Federated discoveries for self-hosted instances (docs/federation.md).

Every instance can publish a signed, append-only feed of its human reviews and
resolutions (GET /api/discoveries/feed) and pull peer feeds (POST
/api/discoveries/sync). A record is addressed by a content hash of its claim
link (both sides: lang, page_id, revision_id, statement_text), so importing it
into another instance that imported the same Wikipedia revisions lands on
exactly the same local claim_links row. Records are ed25519-signed by the
origin instance; imported reviews are stored with reviewer "<instance>:<name>"
and counted by consensus.py with the origin instance's trust weight (local
reviews always weigh 1.0). Discovery records never contain browsing data.
"""
import hashlib
import json
from typing import Optional

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from config import settings
from db import execute, insert, placeholders, query, query_one

FEED_PATH = "/api/discoveries/feed"
REVIEW_DECISIONS = ("agree", "disagree", "fix")

_PRIVATE_KEY: Optional[Ed25519PrivateKey] = None


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _side(lang, page_id, revision_id, statement_text) -> dict:
    return {"lang": lang, "page_id": page_id, "revision_id": revision_id,
            "statement": statement_text}


def claim_link_hash(a: dict, b: dict) -> str:
    pair = sorted((a, b), key=_canonical)
    return hashlib.sha256(_canonical(pair)).hexdigest()


def private_key() -> Ed25519PrivateKey:
    global _PRIVATE_KEY
    if _PRIVATE_KEY is None:
        pem = settings.instance_private_key.strip()
        if pem:
            _PRIVATE_KEY = serialization.load_pem_private_key(pem.encode(), password=None)
        else:
            _PRIVATE_KEY = Ed25519PrivateKey.generate()
    return _PRIVATE_KEY


def public_key_hex() -> str:
    configured = settings.instance_public_key.strip()
    if configured:
        return configured
    return private_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def sign_record(record: dict) -> str:
    return private_key().sign(_canonical(record)).hex()


def verify_record(record: dict, pubkey_hex: str) -> bool:
    signature = record.get("signature")
    if not signature or not pubkey_hex:
        return False
    payload = {k: v for k, v in record.items() if k != "signature"}
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        pub.verify(bytes.fromhex(signature), _canonical(payload))
        return True
    except Exception:
        return False


_SIDE_COLS = (
    "aa.lang AS a_lang, aa.page_id AS a_page, aa.revision_id AS a_rev, "
    "ca.statement_text AS a_text, "
    "ab.lang AS b_lang, ab.page_id AS b_page, ab.revision_id AS b_rev, "
    "cb.statement_text AS b_text"
)
_LINK_JOINS = (
    "JOIN claims ca ON ca.id = cl.claim_a JOIN articles aa ON aa.id = ca.article_id "
    "JOIN claims cb ON cb.id = cl.claim_b JOIN articles ab ON ab.id = cb.article_id"
)


def _record_sides(row: dict) -> tuple:
    a = _side(row["a_lang"], row["a_page"], row["a_rev"], row["a_text"])
    b = _side(row["b_lang"], row["b_page"], row["b_rev"], row["b_text"])
    return a, b


def feed(since: int = 0) -> dict:
    """Signed feed of this instance's reviews and resolutions with id > since."""
    records = []
    for row in query(
        f"""SELECT r.id AS record_id, r.reviewer, r.decision, r.note, r.created_at,
                   cl.id AS link_id, {_SIDE_COLS}
            FROM reviews r
            JOIN claim_links cl ON cl.id = r.claim_link_id
            {_LINK_JOINS}
            WHERE r.id > %s ORDER BY r.id""",
        (since,),
    ):
        a, b = _record_sides(row)
        rec = {
            "type": "review", "id": row["record_id"], "link_hash": claim_link_hash(a, b),
            "a": a, "b": b, "reviewer": row["reviewer"], "decision": row["decision"],
            "note": row["note"], "created_at": str(row["created_at"]),
        }
        rec["signature"] = sign_record(rec)
        records.append(rec)
    for row in query(
        f"""SELECT r.id AS record_id, r.author, r.correct_text, r.source_url, r.created_at,
                   cl.id AS link_id, {_SIDE_COLS}
            FROM resolutions r
            JOIN claim_links cl ON cl.id = r.claim_link_id
            {_LINK_JOINS}
            WHERE r.id > %s ORDER BY r.id""",
        (since,),
    ):
        a, b = _record_sides(row)
        rec = {
            "type": "resolution", "id": row["record_id"], "link_hash": claim_link_hash(a, b),
            "a": a, "b": b, "author": row["author"], "correct_text": row["correct_text"],
            "source_url": row["source_url"], "created_at": str(row["created_at"]),
        }
        rec["signature"] = sign_record(rec)
        records.append(rec)
    return {
        "instance": settings.instance_name,
        "public_key": public_key_hex(),
        "records": records,
    }


def remotes() -> list:
    """Peer instances to pull: `instances` rows plus the configured canonical URL."""
    out = [
        {"name": r["name"], "base_url": r["base_url"], "pubkey": r["pubkey"] or ""}
        for r in query(
            "SELECT name, base_url, pubkey FROM instances WHERE base_url <> '' ORDER BY name")
    ]
    canonical = settings.canonical_instance_url.strip().rstrip("/")
    if canonical and not any(r["base_url"].rstrip("/") == canonical for r in out):
        out.append({"name": "canonical", "base_url": canonical, "pubkey": ""})
    return out


def _find_link(rec: dict) -> Optional[int]:
    a, b = rec.get("a") or {}, rec.get("b") or {}
    if claim_link_hash(a, b) != rec.get("link_hash"):
        return None
    row = query_one(
        f"""SELECT cl.id FROM claim_links cl
            {_LINK_JOINS}
            WHERE (aa.lang = %s AND aa.page_id = %s AND ca.statement_text = %s
                   AND ab.lang = %s AND ab.page_id = %s AND cb.statement_text = %s)
               OR (aa.lang = %s AND aa.page_id = %s AND ca.statement_text = %s
                   AND ab.lang = %s AND ab.page_id = %s AND cb.statement_text = %s)
            LIMIT 1""",
        (a.get("lang"), a.get("page_id"), a.get("statement"),
         b.get("lang"), b.get("page_id"), b.get("statement"),
         b.get("lang"), b.get("page_id"), b.get("statement"),
         a.get("lang"), a.get("page_id"), a.get("statement")),
    )
    return row["id"] if row else None


def import_record(origin: str, rec: dict) -> bool:
    """Import one verified feed record. Returns False when skipped/rejected."""
    rtype, rid = rec.get("type"), rec.get("id")
    if rtype not in ("review", "resolution") or rid is None:
        return False
    if query_one(
        "SELECT 1 AS ok FROM discovery_imports "
        "WHERE origin_instance = %s AND record_type = %s AND origin_record_id = %s",
        (origin, rtype, rid),
    ):
        return False
    link_id = _find_link(rec)
    if link_id is None:
        return False
    if rtype == "review":
        if rec.get("decision") not in REVIEW_DECISIONS:
            return False
        local_id = insert(
            "INSERT INTO reviews (claim_link_id, reviewer, decision, note) "
            "VALUES (%s, %s, %s, %s)",
            (link_id, f"{origin}:{rec.get('reviewer', '')}"[:128], rec["decision"],
             rec.get("note")),
        )
        local_table = "reviews"
    else:
        local_id = insert(
            "INSERT INTO resolutions (claim_link_id, author, correct_text, source_url) "
            "VALUES (%s, %s, %s, %s)",
            (link_id, f"{origin}:{rec.get('author', '')}"[:128],
             rec.get("correct_text") or "", rec.get("source_url")),
        )
        local_table = "resolutions"
    insert(
        "INSERT INTO discovery_imports "
        "(origin_instance, record_type, origin_record_id, local_table, local_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (origin, rtype, rid, local_table, local_id),
    )
    try:
        import consensus
        consensus.enforce(link_id)
    except Exception:
        pass
    return True


def sync() -> dict:
    """Pull every configured remote feed and import new signed records."""
    if not settings.federation_enabled:
        return {"ok": False, "reason": "federation_disabled"}
    summary = {"ok": True, "instances": []}
    for remote in remotes():
        state = query_one(
            "SELECT last_record_id FROM discovery_sync_state WHERE instance_name = %s",
            (remote["name"],),
        )
        since = int(state["last_record_id"]) if state else 0
        try:
            resp = httpx.get(remote["base_url"].rstrip("/") + FEED_PATH,
                             params={"since": since}, timeout=30.0)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            execute(
                "INSERT INTO discovery_sync_state (instance_name, last_record_id, "
                "last_sync_at, status) VALUES (%s, %s, CURRENT_TIMESTAMP, %s) "
                "ON DUPLICATE KEY UPDATE last_sync_at = CURRENT_TIMESTAMP, status = VALUES(status)",
                (remote["name"], since, f"error: {exc}"[:32]),
            )
            summary["instances"].append({"name": remote["name"], "ok": False, "error": str(exc)})
            continue
        pubkey = remote["pubkey"] or payload.get("public_key") or ""
        if remote["pubkey"] and payload.get("public_key") and remote["pubkey"] != payload.get("public_key"):
            summary["instances"].append(
                {"name": remote["name"], "ok": False, "error": "public key mismatch"})
            continue
        imported = skipped = rejected = 0
        max_id = since
        for rec in payload.get("records", []):
            max_id = max(max_id, int(rec.get("id") or 0))
            if pubkey and not verify_record(rec, pubkey):
                rejected += 1
                continue
            if import_record(remote["name"], rec):
                imported += 1
            else:
                skipped += 1
        execute(
            "INSERT INTO discovery_sync_state (instance_name, last_record_id, "
            "last_sync_at, status) VALUES (%s, %s, CURRENT_TIMESTAMP, 'ok') "
            "ON DUPLICATE KEY UPDATE last_record_id = VALUES(last_record_id), "
            "last_sync_at = CURRENT_TIMESTAMP, status = VALUES(status)",
            (remote["name"], max_id),
        )
        summary["instances"].append({
            "name": remote["name"], "ok": True, "imported": imported,
            "skipped": skipped, "rejected": rejected, "last_record_id": max_id,
        })
    return summary


def status() -> dict:
    return {
        "federation_enabled": settings.federation_enabled,
        "instance": {"name": settings.instance_name, "public_key": public_key_hex()},
        "dataset_versions": query(
            "SELECT id, dataset_hash, extractor_model, embedding_model, pipeline_version, "
            "created_at FROM dataset_versions ORDER BY id DESC LIMIT 20"),
        "remotes": remotes(),
        "sync_state": query(
            "SELECT instance_name, last_record_id, last_sync_at, status "
            "FROM discovery_sync_state ORDER BY instance_name"),
    }


def register_dataset_version(dataset_hash: str, extractor_model: str = "",
                             embedding_model: str = "",
                             pipeline_version: str = "") -> int:
    """Record the content identity of the imported snapshot (idempotent)."""
    existing = query_one(
        "SELECT id FROM dataset_versions WHERE dataset_hash = %s", (dataset_hash,))
    if existing:
        return existing["id"]
    return insert(
        "INSERT INTO dataset_versions "
        "(dataset_hash, extractor_model, embedding_model, pipeline_version) "
        "VALUES (%s, %s, %s, %s)",
        (dataset_hash, extractor_model, embedding_model or settings.embed_model,
         pipeline_version),
    )


def vote_weight(reviewer: str) -> float:
    """Consensus weight of a reviewer: origin instance trust weight, else 1.0."""
    name = str(reviewer or "").split(":", 1)[0]
    row = query_one(
        "SELECT trust_weight FROM instances WHERE name = %s", (name,))
    return float(row["trust_weight"]) if row else 1.0


def vote_weights(reviewers) -> dict:
    names = sorted({str(r or "").split(":", 1)[0] for r in reviewers})
    if not names:
        return {}
    rows = query(
        f"SELECT name, trust_weight FROM instances WHERE name IN ({placeholders(names)})",
        tuple(names),
    )
    return {r["name"]: float(r["trust_weight"]) for r in rows}
