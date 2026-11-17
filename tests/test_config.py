"""Settings resolution: defaults, generic provider env vars and OPENAI_* aliases."""
import pytest

from config import Settings

ENV_KEYS = [k for k in (
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_EXTRA_BODY", "LLM_REASONING_EFFORT",
    "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_API_KEY", "OPENAI_MODEL",
    "EMBED_BASE_URL", "EMBED_API_KEY", "EMBED_MODEL",
)]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_defaults_are_local_and_llm_free():
    s = Settings()
    assert s.llm_base_url == "" and s.llm_api_key == "" and s.llm_model == ""
    assert s.embed_base_url == "http://embedder:8001/v1"  # local compose service
    assert s.embed_model == "intfloat/multilingual-e5-large"
    assert s.llm_extra_body == {}


def test_llm_env_vars(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
    s = Settings()
    assert (s.llm_base_url, s.llm_api_key, s.llm_model) == (
        "http://localhost:11434/v1", "k", "qwen3:8b")


def test_openai_aliases(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    s = Settings()
    assert s.llm_base_url == "https://api.openai.com/v1"
    assert s.llm_api_key == "sk-test"
    assert s.llm_model == "gpt-4o-mini"


def test_llm_wins_over_openai_alias(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    assert Settings().llm_base_url == "http://localhost:11434/v1"


def test_extra_body_json(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"thinking": {"type": "disabled"}}')
    assert Settings().llm_extra_body == {"thinking": {"type": "disabled"}}


def test_embed_env(monkeypatch):
    monkeypatch.setenv("EMBED_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("EMBED_API_KEY", "ek")
    monkeypatch.setenv("EMBED_MODEL", "nomic-embed-text")
    s = Settings()
    assert (s.embed_base_url, s.embed_api_key, s.embed_model) == (
        "http://localhost:8000/v1", "ek", "nomic-embed-text")
