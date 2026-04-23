from collections.abc import Generator

from sqlalchemy.orm import Session

from app.core.database import get_db

__all__ = ["get_db", "DbSession"]


def DbSession() -> Generator[Session, None, None]:  # noqa: N802 — FastAPI Depends-friendly alias
    yield from get_db()