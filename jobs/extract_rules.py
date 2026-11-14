#!/usr/bin/env python3
"""Rules-based claim extraction (cold path): extract birth/death dates from Wikipedia leads.

This is a fallback extractor when LLM (Verda) is unavailable. It uses language-specific
regex patterns to extract common factual claims (birth/death dates, founding dates, etc.)
from the first ~6,000 characters of each article.

Usage:
  python extract_rules.py [--lang fi,sv,en] [--max-claims 20] [--dry-run]
  python extract_rules.py --qids Q33,Q1757
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

import mariadb

# Supported languages and their patterns
LANGUAGES = {"fi", "sv", "en"}

# Date patterns for each language
# Finnish patterns
FI_DATE_PATTERNS = {
    "birth": [
        r"(?:Syntyi|s\.\s*)\s*(\d{1,4}(?:\.|\s|-)\d{0,2}(?:\.|\s|-)\d{0,4}|\d{4})",
        r"(?:syntynyt|s\.\s*)\s*(\d{1,4}(?:\.|\s|-)\d{0,2}(?:\.|\s|-)\d{0,4}|\d{4})",
    ],
    "death": [
        r"(?:Kuoli|k\.\s*)\s*(\d{1,4}(?:\.|\s|-)\d{0,2}(?:\.|\s|-)\d{0,4}|\d{4})",
        r"(?:kuollut|k\.\s*)\s*(\d{1,4}(?:\.|\s|-)\d{0,2}(?:\.|\s|-)\d{0,4}|\d{4})",
    ],
    "founded": [
        r"(?:Perustettiin|per\.\s*)\s*(\d{4})",
        r"(?:perustettu|per\.\s*)\s*(\d{4})",
    ],
}

# Swedish patterns
SV_DATE_PATTERNS = {
    "birth": [
        r"(?:F\.dd|Född|f\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
        r"(?:föddes|f\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
    ],
    "death": [
        r"(?:D\.öd|Död|d\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
        r"(?:avled|d\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
    ],
    "founded": [
        r"(?:Grundad|grundades|gr\.\s*)\s*(\d{4})",
        r"(?:bildad|bildades|b\.\s*)\s*(\d{4})",
    ],
}

# English patterns
EN_DATE_PATTERNS = {
    "birth": [
        r"(?:Born|B\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
        r"(?:birth|b\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
    ],
    "death": [
        r"(?:Died|D\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
        r"(?:death|d\.\s*)\s*(\d{1,4}(?:\s|-)\d{0,2}(?:\s|-)\d{0,4}|\d{4})",
    ],
    "founded": [
        r"(?:Founded|Established|est\.\s*)\s*(\d{4})",
        r"(?:founded|established|f\.\s*)\s*(\d{4})",
    ],
}

# Event keywords to skip (events, periods, etc.)
SKIP_KEYWORDS = {
    "fi": ["sota", "taistelu", "sodat", "vallankumous", "kausi", "jakso", 
           "hallitus", "kauden", "tapahtuma", "liitto", "sopimus"],
    "sv": ["krig", "strid", "kriget", "revolution", "period", "epok", 
           "regering", "händelse", "förbund", "avtal"],
    "en": ["war", "battle", "wars", "revolution", "period", "era",
           "government", "event", "treaty", "league"],
}

PATTERN_TYPES = {"birth": "birth_date", "death": "death_date", "founded": "founded"}


def get_date_patterns(lang: str, predicate: str) -> list:
    """Get date patterns for a language and predicate."""
    if lang == "fi":
        return FI_DATE_PATTERNS.get(predicate, [])
    elif lang == "sv":
        return SV_DATE_PATTERNS.get(predicate, [])
    elif lang == "en":
        return EN_DATE_PATTERNS.get(predicate, [])
    return []


def connect():
    return mariadb.connect(
        host=os.environ.get("DATABASE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DATABASE_PORT", "3306")),
        user=os.environ.get("DATABASE_USER", "ovr"),
        password=os.environ.get("DATABASE_PASSWORD", "ovr_pass"),
        database=os.environ.get("DATABASE_NAME", "wikipedia"),
        autocommit=False,
    )


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def parse_date(date_str: str) -> str | None:
    """Parse a date string into ISO 8601 format at the stated precision."""
    date_str = date_str.strip()
    
    # Already ISO-like
    if re.match(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$", date_str):
        return date_str
    
    # Finnish: 1. tammikuuta 2000 or 1.1.2000
    fi_patterns = [
        r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})",  # 1. 1. 2000
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})",          # 1.1.2000
        r"(\d{4})\.(\d{1,2})\.(\d{1,2})",          # 2000.1.1 (unlikely but possible)
    ]
    
    # Swedish: 1 januari 2000 or 1/1 2000
    sv_patterns = [
        r"(\d{1,2})\s+(\d{1,2})\s+(\d{4})",        # 1 1 2000
        r"(\d{1,2})/(\d{1,2})\s+(\d{4})",          # 1/1 2000
        r"(\d{1,2})\s+(\w+)\s+(\d{4})",            # 1 januari 2000
    ]
    
    # English: January 1, 2000 or 1/1/2000
    en_patterns = [
        r"(\d{1,2})/(\d{1,2})/(\d{4})",            # 1/1/2000
        r"(\d{4})/(\d{1,2})/(\d{1,2})",            # 2000/1/1
    ]
    
    for pattern in fi_patterns + sv_patterns + en_patterns:
        match = re.match(pattern, date_str)
        if match:
            # Extract year, month, day
            groups = match.groups()
            if len(groups) == 3:
                year, month, day = groups[0], groups[1], groups[2]
                # Simple validation
                try:
                    datetime(int(year), int(month) if month else 1, int(day) if day else 1)
                    # Return ISO format
                    if month and day:
                        return f"{year}-{int(month):02d}-{int(day):02d}"
                    elif month:
                        return f"{year}-{int(month):02d}"
                    else:
                        return year
                except ValueError:
                    continue
    
    # Just a year
    if re.match(r"^\d{4}$", date_str):
        return date_str
    
    return None


def normalize_date(date_str: str) -> str | None:
    """Normalize a date string to ISO 8601 at the stated precision."""
    # Handle uncertain dates (c., ca., ca, omkring, noin)
    uncertain_patterns = [
        r"(?:c\.?|ca\.?|ca|omkring|noin)\s*(\d{4})",
        r"(?:circa|c\.?|ca\.?)\s*(\d{4})",
    ]
    
    for pattern in uncertain_patterns:
        match = re.match(pattern, date_str, re.IGNORECASE)
        if match:
            return match.group(1)  # Return just the year, drop uncertainty
    
    return parse_date(date_str)


def should_skip_title(title: str, lang: str) -> bool:
    """Check if an article title indicates an event/period that should be skipped."""
    skip_words = SKIP_KEYWORDS.get(lang, [])
    title_lower = title.lower()
    return any(word.lower() in title_lower for word in skip_words)


def extract_from_text(text: str, lang: str, article_title: str, article_id: int, 
                      chunk_id: int, revision_id: int | None) -> list:
    """Extract claims from article text using rules."""
    claims = []
    
    # Skip if title indicates an event/period
    if should_skip_title(article_title, lang):
        return claims
    
    # Only process the lead section (first ~6000 chars)
    lead = text[:6000]
    
    # Try to extract dates for different predicates
    for predicate, patterns in {"birth": FI_DATE_PATTERNS, "death": FI_DATE_PATTERNS, 
                               "founded": FI_DATE_PATTERNS}.items():
        # Get patterns for this language
        lang_patterns = get_date_patterns(lang, predicate)
        if not lang_patterns:
            continue
        
        for pattern in lang_patterns:
            for match in re.finditer(pattern, lead, re.IGNORECASE):
                date_str = match.group(1)
                normalized = normalize_date(date_str)
                
                if normalized:
                    # Create a claim
                    claim = {
                        "article_id": article_id,
                        "chunk_id": chunk_id,
                        "kind": "date",
                        "subject": article_title,
                        "subject_entity": None,
                        "predicate": PATTERN_TYPES.get(predicate, predicate),
                        "predicate_entity": None,
                        "object_entity": None,
                        "value_text": date_str,
                        "value_normalized": normalized,
                        "value_canonical": json.dumps({"value": normalized, "precision": len(normalized.replace("-", "")), "calendar": "gregorian"}),
                        "qualifiers": None,
                        "statement_text": f"{article_title} {predicate} {date_str}",
                        "context_text": lead[:200],
                        "citation_urls": None,
                        "extraction_confidence": 0.8,
                        "data_quality_score": 0.7,
                        "revision_id": revision_id,
                        "model_version": "rules-v1",
                    }
                    claims.append(claim)
                    break  # Only take the first match for each predicate
    
    return claims


def extract_article(art: dict, conn) -> tuple:
    """Extract claims from an article using rules.
    
    Returns (article, n_claims, error).
    """
    try:
        cur = conn.cursor()
        
        # Check if article already has claims
        cur.execute("SELECT id FROM claims WHERE article_id = %s ORDER BY id", (art["id"],))
        existing = [r[0] for r in cur.fetchall()]
        
        if existing:
            return art, 0, "skipped (already has claims; use --reextract for full-page extraction)"
        
        # Get the first chunk (lead)
        cur.execute(
            "SELECT id, text FROM chunks WHERE article_id = %s ORDER BY chunk_idx LIMIT 1",
            (art["id"],),
        )
        chunk = cur.fetchone()
        
        if not chunk:
            return art, 0, "no chunks"
        
        chunk_id, text = chunk[0], chunk[1]
        
        # Extract claims
        claims = extract_from_text(text, art["lang"], art["title"], art["id"], chunk_id, art.get("revision_id"))
        
        if not claims:
            return art, 0, "no claims extracted"
        
        # Insert claims
        for claim in claims:
            cur.execute(
                """INSERT INTO claims (article_id, chunk_id, kind, subject, subject_entity,
                       predicate, predicate_entity, object_entity, value_text, value_normalized,
                       value_canonical, qualifiers, statement_text, context_text, citation_urls,
                       extraction_confidence, data_quality_score, revision_id, model_version)
                   VALUES (%(article_id)s, %(chunk_id)s, %(kind)s, %(subject)s, %(subject_entity)s,
                           %(predicate)s, %(predicate_entity)s, %(object_entity)s, %(value_text)s, %(value_normalized)s,
                           %(value_canonical)s, %(qualifiers)s, %(statement_text)s, %(context_text)s, %(citation_urls)s,
                           %(extraction_confidence)s, %(data_quality_score)s, %(revision_id)s, %(model_version)s)""",
                claim,
            )
        
        conn.commit()
        return art, len(claims), None
    
    except Exception as exc:
        conn.rollback()
        return art, 0, f"error: {exc}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--qids", help="comma-separated Wikidata QIDs")
    sel.add_argument("--langs", default="fi,sv,en", help="comma-separated languages")
    sel.add_argument("--all", action="store_true", help="process all articles")
    ap.add_argument("--max-claims", type=int, default=20, help="maximum claims per article")
    ap.add_argument("--dry-run", action="store_true", help="print claims, don't write")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    
    if args.qids:
        qids = [q.strip() for q in args.qids.split(",") if q.strip()]
        arts = rows(
            cur,
            f"SELECT id, lang, title, wikidata_qid, revision_id FROM articles "
            f"WHERE wikidata_qid IN ({','.join(['%s']*len(qids))}) ORDER BY wikidata_qid, lang",
            tuple(qids),
        )
    elif args.all:
        arts = rows(
            cur,
            "SELECT id, lang, title, wikidata_qid, revision_id FROM articles ORDER BY id",
        )
    else:
        langs = [l.strip() for l in args.langs.split(",") if l.strip()]
        arts = rows(
            cur,
            f"SELECT id, lang, title, wikidata_qid, revision_id FROM articles "
            f"WHERE lang IN ({','.join(['%s']*len(langs))}) ORDER BY lang, title",
            tuple(langs),
        )
    
    conn.close()
    
    print(f"Processing {len(arts)} articles", file=sys.stderr)
    
    conn = connect()
    total_claims = 0
    
    for art in arts:
        if art["lang"] not in LANGUAGES:
            continue
        
        art, n, err = extract_article(art, conn)
        total_claims += n
        status = err or f"{n} claims"
        print(f"{art['lang']} {art['title']}: {status}", file=sys.stderr)
    
    conn.close()
    print(f"Done: {total_claims} claims from {len(arts)} articles", file=sys.stderr)


if __name__ == "__main__":
    main()
