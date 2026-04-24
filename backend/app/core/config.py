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

    # AI 프로바이더 (Phase 3) — 멀티 지원
    # AI_PROVIDER 로 선택: 'fallback' | 'mistral' | 'anthropic' | 'huggingface' | 'openai'
    # 'fallback' 또는 해당 프로바이더 키가 없으면 TF-IDF fallback 모드.
    ai_provider: str = "fallback"
    ai_model: str | None = None  # 비우면 프로바이더별 기본 모델 사용
    ai_max_tokens: int = 2000

    # 프로바이더별 API 키
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    mistral_api_key: str | None = None
    huggingface_api_key: str | None = None

    # 임베딩 (LangChain+FAISS RAG 사용 시 — 현재는 TF-IDF 기본)
    embedding_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"  # legacy — ai_model 우선

    # Phase B.8 — AI 응답 Redis 캐싱 TTL (초). 0 으로 두면 캐시 비활성.
    ai_cache_ttl_seconds: int = 3600  # 1시간

    # Detection thresholds (defaults; per-interface overrides live in DB)
    default_response_ms_threshold: int = 3000
    default_failure_rate_threshold: float = 0.10  # 10%

    # Ingest API: if set, requires X-Ingest-Key header on POST /api/call-logs/ingest.
    # Leave blank for open ingest (development).
    ingest_api_key: str | None = None

    # JWT (Phase A 인증)
    # 토큰 서명 키. 운영에서는 별도 32바이트 시크릿 사용 권장 (지금은 SECRET_KEY 재사용).
    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 240  # 4시간

    # Phase B.1 계정 lockout — 금감원 전자금융감독규정 권고
    # N회 연속 실패 시 M분간 잠금. 0 으로 두면 lockout 비활성.
    lockout_threshold: int = 5
    lockout_minutes: int = 30

    # Phase B.2 비밀번호 정책 — 금감원 권고 (8자 이상 + 3종 조합)
    password_min_length: int = 8
    password_require_complexity: bool = True  # 영문/숫자/특수문자 중 3종

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()