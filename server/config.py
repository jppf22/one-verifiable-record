"""Server configuration (env-driven).

AI inference is provider-agnostic: any OpenAI-compatible ``/v1`` endpoint works,
whether it is a locally deployed model (Ollama, vLLM, LM Studio, llama.cpp,
text-generation-inference, ...) or a hosted API key (OpenAI, OpenRouter,
Together, Groq, Mistral, DeepSeek, ...). Configure it with:

  LLM_BASE_URL   e.g. http://localhost:11434/v1 (Ollama), http://localhost:8000/v1
                 (vLLM), https://api.openai.com/v1, https://openrouter.ai/api/v1, ...
                 OPENAI_BASE_URL / OPENAI_API_BASE are accepted as aliases.
  LLM_API_KEY    optional for local servers; required for hosted APIs.
                 OPENAI_API_KEY is accepted as an alias.
  LLM_MODEL      model id as known to that endpoint (e.g. qwen3:8b, gpt-4o-mini)
  LLM_EXTRA_BODY optional JSON merged into every chat request body, for
                 provider-specific parameters (e.g. {"thinking": {"type": "disabled"}})
  LLM_REASONING_EFFORT  sent only when non-empty (reasoning models)

Embeddings use any OpenAI-compatible ``/v1/embeddings`` endpoint; by default the
local ``embedder`` service from docker-compose (EMBED_BASE_URL), which can also
be a locally GPU-hosted or hosted embeddings server.
"""
from enum import Enum

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SimplificationMode(str, Enum):
    """Five toggleable simplification modes for the codebase."""
    NONE = "none"              # Full functionality
    MINIMAL = "minimal"        # Core comparison only: no LLM, no reviews, no patches, no Q&A
    READ_ONLY = "read_only"    # Disable all write operations: no POST endpoints, only GET
    SINGLE_LANGUAGE = "single_language"  # Focus on one language only, no cross-language comparison
    DETERMINISTIC = "deterministic"  # Skip LLM layer, use only deterministic verdicts
    NO_EXTENSION = "no_extension"  # Remove all extension-specific functionality


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_host: str = "mariadb"
    database_port: int = 3306
    database_user: str = "ovr"
    database_password: str = "ovr_pass"
    database_name: str = "wikipedia"

    # --- Chat LLM: any OpenAI-compatible /v1/chat/completions endpoint ------
    llm_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
    )
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_model: str = Field(default="", validation_alias=AliasChoices("LLM_MODEL", "OPENAI_MODEL"))
    # Provider-specific request parameters merged into every chat body (JSON).
    llm_extra_body: dict = Field(default_factory=dict, validation_alias="LLM_EXTRA_BODY")
    # Sent only when non-empty (e.g. "low"/"medium"/"high" for reasoning models).
    llm_reasoning_effort: str = ""

    # --- Embeddings: any OpenAI-compatible /v1/embeddings endpoint ----------
    # Default: the local embedder service from docker-compose. Point it at any
    # other OpenAI-compatible embeddings server (local GPU host or hosted API).
    # The model's dimensions must match VECTOR(1024) in db/schema.sql.
    embed_base_url: str = Field(
        default="http://embedder:8001/v1",
        validation_alias=AliasChoices("EMBED_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"),
    )
    embed_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("EMBED_API_KEY", "OPENAI_API_KEY"),
    )
    embed_model: str = "intfloat/multilingual-e5-large"

    # Discovery-oriented alignment. Vector search proposes tight candidates, while
    # gate.py + semantic grading decide whether they state the same fact.
    match_distance: float = 0.24
    nearest_k: int = 3
    semantic_match_distance: float = 0.14
    semantic_nearest_k: int = 2
    counterpart_min_confidence: float = 0.90
    top_k: int = 8
    
    # Alignment filtering to prevent uncorrelated counterparts
    # When enabled, requires matching predicates for alignment candidates
    strict_predicate_filter: bool = True
    # When enabled, requires matching subjects for alignment candidates
    strict_subject_filter: bool = False
    # Minimum similarity for candidate links (0.0-1.0, where 1.0 = identical)
    min_similarity: float = 0.70

    # LLM pacing (server/llm.py). 0 by default (local models need no pacing);
    # set LLM_MIN_INTERVAL_S for rate-limited hosted APIs (the client also
    # honours retry-after / x-ratelimit-* headers when a server sends them).
    # Calls that cannot finish within the budget are left undone.
    llm_min_interval_s: float = 0.0
    llm_max_attempts: int = 5
    llm_timeout_s: float = 180.0
    llm_budget_s: float = 600.0
    # Same-fact gate (server/gate.py): candidates per claim sent to the LLM, and
    # claims per batched call (~50 output tokens and ~0.6 s per claim).
    gate_max_candidates: int = 4
    gate_batch_size: int = 20
    
    # Public demo protection. The web app proxies /api, so these limits cover
    # browser writes without putting a costly LLM endpoint directly on port 8000.
    post_rate_limit_per_minute: int = 12

    # ========================================================================
    # SIMPLIFICATION MODES
    # ========================================================================
    # Enable one simplification mode at a time via environment variable
    # Options: none, minimal, read_only, single_language, deterministic, no_extension
    simplification_mode: SimplificationMode = SimplificationMode.NONE

    # For single_language mode: which language to focus on (fi, sv, en)
    single_language: str = "en"

    # For deterministic mode: skip LLM layer entirely
    force_deterministic_verdicts: bool = False

    # ========================================================================

    @property
    def is_minimal_mode(self) -> bool:
        return self.simplification_mode == SimplificationMode.MINIMAL

    @property
    def is_read_only_mode(self) -> bool:
        return self.simplification_mode == SimplificationMode.READ_ONLY

    @property
    def is_single_language_mode(self) -> bool:
        return self.simplification_mode == SimplificationMode.SINGLE_LANGUAGE

    @property
    def is_deterministic_mode(self) -> bool:
        return self.simplification_mode == SimplificationMode.DETERMINISTIC

    @property
    def is_no_extension_mode(self) -> bool:
        return self.simplification_mode == SimplificationMode.NO_EXTENSION

    def get_simplification_config(self) -> dict:
        """Get the full simplification configuration."""
        return {
            "mode": self.simplification_mode.value,
            "is_minimal": self.is_minimal_mode,
            "is_read_only": self.is_read_only_mode,
            "is_single_language": self.is_single_language_mode,
            "single_language": self.single_language,
            "is_deterministic": self.is_deterministic_mode,
            "is_no_extension": self.is_no_extension_mode,
        }


# Global settings instance
settings = Settings()


def get_simplification():
    """Get the current simplification configuration as a dict."""
    return settings.get_simplification_config()
