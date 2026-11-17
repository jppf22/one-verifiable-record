"""Server configuration (env-driven)."""
from enum import Enum
from typing import Optional
from pydantic_settings import BaseSettings


class SimplificationMode(str, Enum):
    """Five toggleable simplification modes for the codebase."""
    NONE = "none"              # Full functionality
    MINIMAL = "minimal"        # Core comparison only: no LLM, no reviews, no patches, no Q&A
    READ_ONLY = "read_only"    # Disable all write operations: no POST endpoints, only GET
    SINGLE_LANGUAGE = "single_language"  # Focus on one language only, no cross-language comparison
    DETERMINISTIC = "deterministic"  # Skip LLM layer, use only deterministic verdicts
    NO_EXTENSION = "no_extension"  # Remove all extension-specific functionality


class Settings(BaseSettings):
    database_host: str = "mariadb"
    database_port: int = 3306
    database_user: str = "ovr"
    database_password: str = "ovr_pass"
    database_name: str = "wikipedia"

    # Verda Inference API (OpenAI-compatible endpoints assumed)
    verda_api_key: str = ""
    verda_base_url: str = "https://inference.verda.com/v1"
    llm_model: str = "kimi-k3-1-pro"
    # Kimi K3 can spend its whole response budget on hidden reasoning.  A low
    # effort leaves room for the structured claim/verdict JSON we require.
    llm_reasoning_effort: str = "low"
    embed_model: str = "intfloat/multilingual-e5-large"
    # Verda's gateway has no embeddings endpoint: we serve e5 ourselves (embedder/)
    embed_base_url: str = "http://embedder:8001/v1"
    # Optional remote Verda GPU embedder used only by the on-demand job route.
    # It is bearer-protected; MariaDB remains accessible only on the app VM.
    gpu_embed_base_url: str = ""
    gpu_embed_token: str = ""

    # alignment: max cosine distance to consider two semantic candidates per
    # other language. Exact canonical-predicate candidates are included even
    # when they fall outside this threshold, then verified by the LLM.
    match_distance: float = 0.35
    # One best semantic fallback per other language; all exact dynamic-predicate
    # candidates are still retained. This keeps high-recall matching practical
    # for claim-rich articles.
    nearest_k: int = 1
    verdict_concurrency: int = 12
    top_k: int = 8
    # Public demo protection. The web app proxies /api, so these limits cover
    # browser writes without putting a costly LLM endpoint directly on port 8000.
    post_rate_limit_per_minute: int = 12

    # Page counterpart resolution: minimum page_links confidence (pages.py).
    counterpart_min_confidence: float = 0.5

    # Federation / self-hosting (docs/federation.md). When enabled, this
    # instance signs its /api/discoveries/feed records and can pull peer feeds.
    federation_enabled: bool = False
    instance_name: str = "local"
    # PEM-encoded ed25519 private key; an ephemeral key is generated when empty.
    instance_private_key: str = ""
    # Hex ed25519 public key; derived from the private key when empty.
    instance_public_key: str = ""
    # Default pull remote when no `instances` rows exist, e.g. https://ovr.example.org
    canonical_instance_url: str = ""
    discovery_sync_interval_sec: int = 3600

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
