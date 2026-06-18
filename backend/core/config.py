"""
Configuration management using pydantic-settings.

All settings are loaded from environment variables with sensible defaults.
Secrets (API keys, passwords) MUST NOT appear in code or commits.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────
    llm_api_key: str
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_embed_model: str = "text-embedding-3-small"
    chat_model: str = "gpt-4o"          # Model for conversational replies
    max_context_tokens: int = 128000    # Max tokens kept in conversation context window
    available_models: list[str] = ["gpt-4o", "gpt-4o-mini", "claude-sonnet-4-6", "claude-opus-4-8"]

    # ── Search ───────────────────────────────────────────────────────
    search_provider: str = "tavily"
    search_api_key: str = ""

    # ── PostgreSQL ───────────────────────────────────────────────────
    postgres_user: str = "deepresearch"
    postgres_password: str = "research_dev"
    postgres_db: str = "deepresearch"
    postgres_port: int = 5432
    postgres_host: str = "localhost"

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── MongoDB ──────────────────────────────────────────────────────
    mongo_root_user: str = "admin"
    mongo_root_password: str = "research_dev"
    mongo_db: str = "deepresearch"
    mongo_port: int = 27017
    mongo_host: str = "localhost"

    @property
    def mongo_url(self) -> str:
        return (
            f"mongodb://{self.mongo_root_user}:{self.mongo_root_password}"
            f"@{self.mongo_host}:{self.mongo_port}"
            f"/{self.mongo_db}?authSource=admin"
        )

    # ── Elasticsearch ────────────────────────────────────────────────
    es_port: int = 9200
    es_host: str = "localhost"

    @property
    def es_url(self) -> str:
        return f"http://{self.es_host}:{self.es_port}"

    # ── JWT ──────────────────────────────────────────────────────────
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # ── Application ──────────────────────────────────────────────────
    backend_port: int = 8000
    frontend_port: int = 3000
    log_level: str = "INFO"
    max_task_execution_seconds: int = 7200  # 2 hours
    max_concurrent_tasks_per_user: int = 3
    max_gap_rounds: int = 3

    # ── CORS ─────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000"]


# Singleton instance
settings = Settings()
