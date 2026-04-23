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
    # Make every Postgres session render timestamps in KST. Storage is still
    # UTC internally (PG always normalizes timestamptz), but NOW() and display
    # use Asia/Seoul so raw SQL queries show what operators expect.
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
    """Create all tables. For real deployments use Alembic migrations."""
    from app import models  # noqa: F401  (ensure models are imported)

    Base.metadata.create_all(bind=engine)
    _apply_demo_migrations()


# Demo-only inline migrations. Replace with Alembic for production.
_DEMO_MIGRATIONS: list[str] = [
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS parent_log_id INTEGER REFERENCES call_logs(id) ON DELETE SET NULL",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS is_reprocessed BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS error_type VARCHAR(80)",
    "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS error_trace TEXT",
    "CREATE INDEX IF NOT EXISTS ix_call_logs_parent_log_id ON call_logs(parent_log_id)",
    "CREATE INDEX IF NOT EXISTS ix_call_logs_is_reprocessed ON call_logs(is_reprocessed)",
    "CREATE INDEX IF NOT EXISTS ix_call_logs_error_type ON call_logs(error_type)",
]


def _apply_demo_migrations() -> None:
    if engine.dialect.name != "postgresql":
        return  # IF NOT EXISTS on ADD COLUMN is Postgres-specific
    with engine.begin() as conn:
        for stmt in _DEMO_MIGRATIONS:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # noqa: BLE001
                log.warning("demo migration skipped: %s — %s", stmt, e)