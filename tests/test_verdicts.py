"""Deterministic verdict layer + simplification-mode LLM skip (no network, no DB)."""
import pytest

from config import SimplificationMode, settings
import verdicts


def claim(kind, value, predicate="birth_date", subject="Daniel Tilas"):
    return {"kind": kind, "value_normalized": value, "predicate": predicate,
            "subject": subject, "predicate_entity": None, "object_entity": None,
            "value_canonical": None, "qualifiers": None}


def test_date_coarser_precision_agrees():
    assert verdicts.deterministic_verdict(claim("date", "1900"), claim("date", "1900-09-03")) == "supported"


def test_date_mismatch_contradicted():
    assert verdicts.deterministic_verdict(claim("date", "1900"), claim("date", "1901")) == "contradicted"


def test_equal_numbers_supported():
    assert verdicts.deterministic_verdict(claim("number", "674500"), claim("number", "674500")) == "supported"


def test_differing_numbers_go_to_llm():
    assert verdicts.deterministic_verdict(claim("number", "100"), claim("number", "200")) is None


def test_different_predicates_never_compared():
    a = claim("date", "1900", predicate="birth_date")
    b = claim("date", "1900", predicate="death_date")
    assert verdicts.deterministic_verdict(a, b) is None


def test_kind_mismatch_goes_to_llm():
    assert verdicts.deterministic_verdict(claim("date", "1900"), claim("factoid", "1900")) is None


def test_alias_predicates_match():
    a = claim("date", "1900", predicate="date_of_birth")
    b = claim("date", "1900", predicate="birth_date")
    assert verdicts.deterministic_verdict(a, b) == "supported"


@pytest.fixture()
def deterministic_mode(monkeypatch):
    monkeypatch.setattr(settings, "simplification_mode", SimplificationMode.DETERMINISTIC)
    yield


def test_llm_layer_skipped_in_deterministic_mode(deterministic_mode):
    a = claim("factoid", None, predicate="occupation", subject=None)
    b = claim("factoid", None, predicate="occupation", subject=None)
    verdict, relation, confidence, rationale, available = verdicts.llm_verdict(a, b)
    assert available is False and verdict is None
    assert "simplified mode" in rationale
