import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

log = logging.getLogger("noahub.db")

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
    # 모든 PG 세션의 timezone 을 KST 로 강제. PG 의 TIMESTAMPTZ 는 내부적
    # 으로는 무조건 UTC 로 저장하지만 (PG의 강제 동작, 못 바꿈), NOW() /
    # CURRENT_TIMESTAMP / 조회 결과는 세션 timezone 으로 변환되어 나옴.
    # → 운영자/감사관이 psql 로 직접 조회해도 KST 로 보이고, API JSON 도
    # +09:00 오프셋이 붙어 나감. docker-compose 의 TZ=Asia/Seoul 환경
    # 변수와 같이 동작.
    connect_args={"options": "-c timezone=Asia/Seoul"},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """모든 테이블 생성. 운영 환경에서는 Alembic 마이그레이션으로 교체할 것."""
    from app import models  # noqa: F401  (모델 모듈 임포트해서 metadata 등록)

    Base.metadata.create_all(bind=engine)
    _apply_demo_migrations()


# 데모 한정 인라인 마이그레이션. 운영 배포 시 Alembic 으로 교체할 것.
# create_all() 은 새 테이블만 만들고 기존 테이블에 컬럼 추가는 못 하기 때문에
# 새 컬럼이 추가될 때마다 여기에 ADD COLUMN IF NOT EXISTS 한 줄씩 박아둠.
_DEMO_MIGRATIONS: list[str] = [
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS parent_log_id INTEGER REFERENCES call_logs(id) ON DELETE SET NULL",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS is_reprocessed BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS error_type VARCHAR(80)",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS error_trace TEXT",
    "CREATE INDEX IF NOT EXISTS ix_call_logs_parent_log_id ON call_logs(parent_log_id)",
    "CREATE INDEX IF NOT EXISTS ix_call_logs_is_reprocessed ON call_logs(is_reprocessed)",
    "CREATE INDEX IF NOT EXISTS ix_call_logs_error_type ON call_logs(error_type)",
    # 새 프로토콜 enum 값 추가 (PG ENUM 은 트랜잭션 안에서 까다로움 — IF NOT EXISTS 로 회피)
    "ALTER TYPE protocoltype ADD VALUE IF NOT EXISTS 'BATCH'",
    # 인터페이스 소프트 삭제 (deleted_at 만 마킹, hard delete 절대 X)
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_interfaces_deleted_at ON interfaces(deleted_at)",
    # call_logs 에 행위자 추적 (manual/reprocess/ingest 시 누가 했는지)
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS ix_call_logs_actor_user_id ON call_logs(actor_user_id)",
    # 계정 lockout (연속 실패 + 잠금 해제 시각)
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ",
    # 강제 비밀번호 변경 플래그
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE",
    # 세션 버전 (강제 로그아웃 — JWT payload.sv 와 비교)
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 1",
    # 알림 룰 (인터페이스별 음소거 + 채널 화이트리스트)
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS muted_until TIMESTAMPTZ",
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS alert_channels JSON DEFAULT '[\"in_app\",\"slack\",\"email\"]'",
    # AI 질의 결과 분류 (popular/history/suggestions 필터 용)
    "ALTER TABLE ai_query_logs ADD COLUMN IF NOT EXISTS outcome VARCHAR(30) NOT NULL DEFAULT 'success'",
    "CREATE INDEX IF NOT EXISTS ix_ai_query_logs_outcome ON ai_query_logs(outcome)",
    # 호출 안정성 (재시도 + timeout)
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS timeout_seconds DOUBLE PRECISION",
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS retry_max INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS retry_backoff_seconds DOUBLE PRECISION NOT NULL DEFAULT 1.0",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1",
    # 인터페이스 카테고리 (내부 핵심 / 외부 제휴 / 외부 규제기관)
    # PG 의 ENUM 은 트랜잭션 안에서 까다로워 IF NOT EXISTS 로 안전 추가.
    """DO $$ BEGIN
        CREATE TYPE interfacecategory AS ENUM ('INTERNAL_CORE', 'EXTERNAL_PARTNER', 'EXTERNAL_REGULATOR');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS category interfacecategory NOT NULL DEFAULT 'EXTERNAL_PARTNER'",
    "CREATE INDEX IF NOT EXISTS ix_interfaces_category ON interfaces(category)",
    # 호출 방향 (OUTBOUND/INBOUND)
    """DO $$ BEGIN
        CREATE TYPE interfacedirection AS ENUM ('OUTBOUND', 'INBOUND');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS direction interfacedirection NOT NULL DEFAULT 'OUTBOUND'",
    "CREATE INDEX IF NOT EXISTS ix_interfaces_direction ON interfaces(direction)",
    # 인증 키 만료일 + 마지막 임박 알림 dedup
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS auth_secret_expires_at TIMESTAMPTZ",
    "ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS auth_secret_warning_sent_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS ix_interfaces_auth_secret_expires_at ON interfaces(auth_secret_expires_at)",
    # 새 incident type — 인증 키 만료 임박 경고
    "ALTER TYPE incidenttype ADD VALUE IF NOT EXISTS 'SECRET_EXPIRY_WARNING'",
    # 전역 알림 룰 (severity 라우팅 + 근무시간 외 silence)
    """CREATE TABLE IF NOT EXISTS alert_rules (
        id SERIAL PRIMARY KEY,
        info_channels JSON NOT NULL DEFAULT '["in_app"]',
        warning_channels JSON NOT NULL DEFAULT '["in_app","slack"]',
        critical_channels JSON NOT NULL DEFAULT '["in_app","slack","email"]',
        quiet_hours_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        quiet_hours_start INTEGER NOT NULL DEFAULT 22,
        quiet_hours_end INTEGER NOT NULL DEFAULT 8,
        quiet_hours_skip_critical BOOLEAN NOT NULL DEFAULT TRUE,
        weekend_silence BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    )""",
    # 단일 행 보장 — id=1 로 upsert. 없으면 기본값으로 생성.
    """INSERT INTO alert_rules (id) VALUES (1) ON CONFLICT (id) DO NOTHING""",
]


def _apply_demo_migrations() -> None:
    # IF NOT EXISTS 절은 PG 전용 문법이라 SQLite/dev 모드에서는 스킵
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for stmt in _DEMO_MIGRATIONS:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # noqa: BLE001
                log.warning("데모 마이그레이션 스킵: %s — %s", stmt, e)