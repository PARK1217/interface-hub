from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_role
from app.core.time import now_kst
from app.models import CallLog, Incident, Interface, User, UserRole
from app.models.call_log import CallStatus
from app.schemas.call_log import BulkRetryResponse, CallLogOut
from app.schemas.incident import IncidentCreate, IncidentOut, IncidentUpdate
from app.services.audit import record_audit
from app.services.executor import execute_interface
from app.services.incident_helpers import mark_related_handled, related_log_query

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _to_out(
    db: Session,
    incident: Incident,
    *,
    count: int | None = None,
    last_call_at=None,
) -> IncidentOut:
    itf = db.get(Interface, incident.interface_id) if (count is None or last_call_at is None) else None
    if count is None:
        count = len(db.scalars(related_log_query(incident, itf)).all())
    if last_call_at is None and itf is not None:
        # 가장 최근 호출 시각 1건만 — incident 가 묶고 있는 호출들 중 마지막
        from sqlalchemy import func as _func
        last_call_at = db.scalar(
            related_log_query(incident, itf)
            .with_only_columns(_func.max(CallLog.called_at))
            .order_by(None)
        )
    payload = IncidentOut.model_validate(incident)
    payload.related_log_count = count
    payload.last_call_at = last_call_at
    return payload


@router.get("", response_model=list[IncidentOut])
def list_incidents(
    interface_id: int | None = None,
    unresolved_only: bool = False,
    db: Session = Depends(get_db),
) -> list[IncidentOut]:
    stmt = select(Incident).order_by(Incident.detected_at.desc())
    if interface_id is not None:
        stmt = stmt.where(Incident.interface_id == interface_id)
    if unresolved_only:
        stmt = stmt.where(Incident.resolved_at.is_(None))
    incidents = db.scalars(stmt).all()
    iface_ids = {i.interface_id for i in incidents}
    interfaces = {
        i.id: i
        for i in db.scalars(select(Interface).where(Interface.id.in_(iface_ids))).all()
    }
    from sqlalchemy import func as _func
    out = []
    for inc in incidents:
        itf = interfaces.get(inc.interface_id)
        # 관련 호출 행 전체(JSON 요청/응답/trace 포함)를 끌어오면 오래 열린 incident
        # 하나가 수만 행을 로딩해 목록이 멈춤 → 건수·최근시각만 DB 에서 집계.
        count, last_call_at = db.execute(
            related_log_query(inc, itf)
            .with_only_columns(_func.count(CallLog.id), _func.max(CallLog.called_at))
            .order_by(None)
        ).one()
        out.append(_to_out(db, inc, count=count, last_call_at=last_call_at))
    return out


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentCreate, db: Session = Depends(get_db)) -> IncidentOut:
    obj = Incident(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return _to_out(db, obj)


@router.patch("/{incident_id}", response_model=IncidentOut)
def update_incident(
    incident_id: int, payload: IncidentUpdate, db: Session = Depends(get_db)
) -> IncidentOut:
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    was_unresolved = obj.resolved_at is None
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    # If this PATCH is what closes the incident, sweep the related call_logs.
    if was_unresolved and obj.resolved_at is not None:
        itf = db.get(Interface, obj.interface_id)
        mark_related_handled(db, obj, itf)
    db.commit()
    db.refresh(obj)
    return _to_out(db, obj)


@router.post("/{incident_id}/resolve", response_model=IncidentOut)
def resolve_incident(
    incident_id: int,
    request: Request,
    resolution: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.OPERATOR, UserRole.ADMIN])),
) -> IncidentOut:
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    if obj.resolved_at is None:
        obj.resolved_at = now_kst()
    if resolution:
        obj.resolution = resolution
    itf = db.get(Interface, obj.interface_id)
    # incident 해결 처리 시 관련 call_logs 도 모두 is_reprocessed=true 로 마킹.
    # 안 그러면 운영자가 호출 로그 페이지 가서 ↻ 재처리 다시 누를 수 있어
    # 외부 기관에 중복 호출 발생 (이전에 사용자가 지적한 헛점).
    handled = mark_related_handled(db, obj, itf)
    db.commit()
    db.refresh(obj)
    record_audit(
        db, actor=actor, action="incident.resolve",
        resource_type="incident", resource_id=obj.id,
        after={"handled_logs": handled, "resolution": resolution},
        request=request,
    )
    return _to_out(db, obj)


@router.get("/{incident_id}/related-logs", response_model=list[CallLogOut])
def related_logs(
    incident_id: int,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[CallLogOut]:
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    itf = db.get(Interface, obj.interface_id)
    rows = db.scalars(
        related_log_query(obj, itf).order_by(CallLog.called_at.desc()).limit(limit)
    ).all()
    return [CallLogOut.model_validate(r) for r in rows]


class IncidentRetryRequest(BaseModel):
    mode: Literal["latest", "all"] = "latest"


@router.post("/{incident_id}/retry-related", response_model=BulkRetryResponse)
async def retry_related(
    incident_id: int,
    payload: IncidentRetryRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.OPERATOR, UserRole.ADMIN])),
) -> BulkRetryResponse:
    """incident 에 묶인 call_logs 일괄 재처리.

    mode=latest → 가장 최근 실패만 1건 재실행 (**안전 기본값**).
        같은 업무 호출이 시스템 자동 재시도로 N번 들어왔을 가능성 있음 →
        N번 다 재처리하면 외부 기관에 중복 청구·발송 위험.
    mode=all    → 보관 안 된 모든 실패 call_log 재실행. 각 호출이 서로
        다른 고객의 별개 요청임이 명확할 때만 사용 (UI 에서 confirm 받음).

    이미 is_reprocessed=true 인 행은 항상 스킵 → 무한 재처리 방지.
    """
    obj = db.get(Incident, incident_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    itf = db.get(Interface, obj.interface_id)
    if not itf or not itf.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "interface missing or disabled")

    base = (
        related_log_query(obj, itf)
        .where(CallLog.is_reprocessed.is_(False))
        .where(CallLog.status != CallStatus.SUCCESS)
        .order_by(CallLog.called_at.desc())
    )
    if payload.mode == "latest":
        base = base.limit(1)
    parents = db.scalars(base).all()

    new_ids: list[int] = []
    skipped = 0
    for parent in parents:
        try:
            new_log = await execute_interface(itf, db, parent_log=parent, actor=actor)
            new_ids.append(new_log.id)
        except Exception:  # noqa: BLE001
            skipped += 1
    record_audit(
        db, actor=actor, action="incident.retry_related",
        resource_type="incident", resource_id=obj.id,
        after={"mode": payload.mode, "submitted": len(new_ids), "skipped": skipped},
        request=request,
    )
    return BulkRetryResponse(submitted=len(new_ids), skipped=skipped, new_log_ids=new_ids)
