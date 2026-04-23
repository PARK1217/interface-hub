"""감사 로그 기록 헬퍼.

모든 변경 액션 (Create/Update/Delete/Execute/Resolve 등) 처리 시 호출.
record_audit() 는 절대 raise 하지 않음 — 감사 로그 실패가 본 비즈니스
로직을 막으면 안 됨 (best-effort 기록).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User

log = logging.getLogger("noahub.audit")


def record_audit(
    db: Session,
    *,
    actor: User | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """감사 로그 1행 기록. 실패해도 raise 안 함.

    Args:
        actor: 행위자. 익명 (로그인 실패 등) 이면 None.
        action: "interface.create" / "auth.login_failed" / "call_log.retry" 등 점 표기법.
        resource_type/resource_id: 영향받은 자원 (예: "interface", 42).
        before/after: 변경 전/후 JSON 스냅샷. 시크릿/비밀번호는 호출자가 마스킹.
        request: FastAPI Request. IP/User-Agent 자동 추출.
    """
    try:
        ip = ua = None
        if request is not None:
            client = request.client
            ip = client.host if client else None
            ua = request.headers.get("user-agent", "")[:255] if request.headers else None

        row = AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_username=actor.username if actor else "(anonymous)",
            actor_role=actor.role.value if actor and actor.role else None,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            before_value=before,
            after_value=after,
            ip=ip,
            user_agent=ua,
        )
        db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("감사 로그 기록 실패: action=%s", action)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass