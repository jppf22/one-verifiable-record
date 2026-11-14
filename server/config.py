"""Server configuration (env-driven)."""
from pydantic_settings import BaseSettings


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
    embed_model: str = "intfloat/multilingual-e5-large"
    # Verda's gateway has no embeddings endpoint: we serve e5 ourselves (embedder/)
    embed_base_url: str = "http://embedder:8001/v1"

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

    # Verda Inference pacing (server/llm.py). The account allows 40 requests per
    # minute shared by every process using the key; 1.6 s between call starts
    # stays under it. Calls that cannot finish within the budget are left undone.
    llm_min_interval_s: float = 1.6
    llm_max_attempts: int = 4
    llm_timeout_s: float = 90.0
    llm_budget_s: float = 150.0
    # Same-fact gate (server/gate.py): candidates per claim sent to the LLM, and
    # claims per batched call (~50 output tokens and ~0.6 s per claim).
    gate_max_candidates: int = 4
    gate_batch_size: int = 20
    
    # Public demo protection. The web app proxies /api, so these limits cover
    # browser writes without putting a costly LLM endpoint directly on port 8000.
    post_rate_limit_per_minute: int = 12


settings = Settings()
