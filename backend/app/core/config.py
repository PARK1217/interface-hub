from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://noahub:noahub_pw@postgres:5432/noahub"
    redis_url: str = "redis://redis:6379/0"

    # 32-byte AES-GCM key (base64-encoded). Default is a dev key; rotate in production.
    secret_key: str = Field(default="TFpc0WGtwO1qZ22SZzL4fowraSc0F+BkO2E4J8q4YLY=")

    # Notifier (Phase 2)
    slack_webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    alert_email_from: str = "alert@noahub.local"
    alert_email_to: str | None = None

    # AI (Phase 3)
    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"

    # Detection thresholds (defaults; per-interface overrides live in DB)
    default_response_ms_threshold: int = 3000
    default_failure_rate_threshold: float = 0.10  # 10%

    # Ingest API: if set, requires X-Ingest-Key header on POST /api/call-logs/ingest.
    # Leave blank for open ingest (development).
    ingest_api_key: str | None = None

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()