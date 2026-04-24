from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.models import Interface, User, UserRole
from app.models.interface import InterfaceDirection
from app.schemas.call_log import CallLogOut
from app.services.audit import record_audit
from app.services.executor import execute_interface

router = APIRouter(prefix="/interfaces", tags=["executions"])


@router.post("/{interface_id}/execute", response_model=CallLogOut)
async def execute_now(
    interface_id: int,
    background: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    # OPERATOR 이상만 ▶ 수동 실행 가능. VIEWER 는 차단 (실행 = 외부 호출 발생).
    actor: User = Depends(require_role([UserRole.OPERATOR, UserRole.ADMIN])),
) -> CallLogOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "interface is deleted — restore first")
    # INBOUND 인터페이스는 외부가 우리를 부르는 구조라 능동 실행 불가.
    # ingest API (/api/call-logs/ingest) 로 결과만 적재 가능.
    if obj.direction == InterfaceDirection.INBOUND:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "INBOUND 인터페이스는 능동 실행할 수 없습니다. 외부 호출이 들어오면 "
            "/api/call-logs/ingest 로 결과를 적재하세요.",
        )
    log = await execute_interface(obj, db, triggered_by="manual", actor=actor)
    record_audit(
        db, actor=actor, action="interface.execute",
        resource_type="interface", resource_id=obj.id,
        after={"call_log_id": log.id, "status": log.status.value, "duration_ms": log.duration_ms},
        request=request,
    )
    return CallLogOut.model_validate(log)
