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

    # Discovery-oriented alignment. These are intentionally generous: vector
    # search proposes candidates, while semantic grading decides whether they
    # are the same fact or a possible/confirmed conflict.
    match_distance: float = 0.28
    nearest_k: int = 12
    counterpart_min_confidence: float = 0.90
    top_k: int = 8
    # Public demo protection. The web app proxies /api, so these limits cover
    # browser writes without putting a costly LLM endpoint directly on port 8000.
    post_rate_limit_per_minute: int = 12


settings = Settings()
