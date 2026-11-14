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


settings = Settings()
