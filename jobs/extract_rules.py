#!/usr/bin/env python3
"""Deprecated compatibility entry point.

This project no longer uses a regex/predefined-rule fact extractor. Regexes
silently discard the long tail of claims and encode language-specific ideas of
what counts as a fact. Use ``extract_claims.py`` instead: it processes every
article chunk with the structured, entity-aware AI extractor.
"""
import sys


def main() -> None:
    sys.exit(
        "extract_rules.py is retired; use jobs/extract_claims.py for full-article "
        "AI-based claim extraction."
    )


if __name__ == "__main__":
    main()
