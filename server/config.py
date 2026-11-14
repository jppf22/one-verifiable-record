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

    # alignment: max cosine distance to consider two claims a candidate match
    match_distance: float = 0.35
    nearest_k: int = 3
    top_k: int = 8


settings = Settings()
