from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import encrypt_secret
from app.core.time import now_kst
from app.models import Interface
from app.models.interface import ProtocolType
from app.schemas.interface import InterfaceCreate, InterfaceOut, InterfaceUpdate

router = APIRouter(prefix="/interfaces", tags=["interfaces"])


@router.get("/cron-preview")
def cron_preview(expression: str, count: int = 3) -> dict:
    """cron 표현식 검증 + 다음 N회 실행 시각 반환 (KST 기준).

    등록 다이얼로그에서 운영자가 선택한 스케줄이 실제로 언제 도는지 미리
    확인할 수 있도록 사용. 운영자는 cron 문법 직접 입력 안 하지만 (라디오
    버튼 + 드롭다운으로 cron 자동 생성), 그 결과를 신뢰하려면 미리보기
    필요.
    """
    from croniter import croniter

    from app.core.time import KST, now_kst

    expr = (expression or "").strip()
    if not expr:
        return {"valid": False, "error": "empty"}
    if not croniter.is_valid(expr):
        return {"valid": False, "error": "invalid cron expression"}
    it = croniter(expr, now_kst())
    next_runs = []
    for _ in range(max(1, min(count, 10))):
        nxt = it.get_next(ret_type=type(now_kst()))
        # croniter gives naive in some configs — re-localize to KST
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=KST)
        next_runs.append(nxt.isoformat())
    return {"valid": True, "next_runs": next_runs}


def _to_out(i: Interface) -> InterfaceOut:
    payload = InterfaceOut.model_validate(i)
    payload.has_secret = bool(i.auth_secret)
    return payload


@router.get("", response_model=list[InterfaceOut])
def list_interfaces(
    enabled: bool | None = None,
    organization: str | None = None,
    protocol: ProtocolType | None = None,
    include_deleted: bool = False,
    only_deleted: bool = False,
    db: Session = Depends(get_db),
) -> list[InterfaceOut]:
    stmt = select(Interface)
    if only_deleted:
        stmt = stmt.where(Interface.deleted_at.is_not(None))
    elif not include_deleted:
        stmt = stmt.where(Interface.deleted_at.is_(None))
    if enabled is not None:
        stmt = stmt.where(Interface.enabled == enabled)
    if organization:
        stmt = stmt.where(Interface.organization == organization)
    if protocol is not None:
        stmt = stmt.where(Interface.protocol == protocol)
    return [_to_out(i) for i in db.scalars(stmt.order_by(Interface.id.desc())).all()]


@router.post("", response_model=InterfaceOut, status_code=status.HTTP_201_CREATED)
def create_interface(payload: InterfaceCreate, db: Session = Depends(get_db)) -> InterfaceOut:
    data = payload.model_dump(exclude={"auth_secret"})
    obj = Interface(**data)
    if payload.auth_secret:
        obj.auth_secret = encrypt_secret(payload.auth_secret)
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "interface name already exists") from e
    db.refresh(obj)
    return _to_out(obj)


@router.get("/{interface_id}", response_model=InterfaceOut)
def get_interface(interface_id: int, db: Session = Depends(get_db)) -> InterfaceOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    return _to_out(obj)


@router.patch("/{interface_id}", response_model=InterfaceOut)
def update_interface(
    interface_id: int, payload: InterfaceUpdate, db: Session = Depends(get_db)
) -> InterfaceOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "deleted interface — restore first")
    data = payload.model_dump(exclude_unset=True)
    secret = data.pop("auth_secret", None)
    for k, v in data.items():
        setattr(obj, k, v)
    if secret is not None:
        obj.auth_secret = encrypt_secret(secret) if secret else None
    db.commit()
    db.refresh(obj)
    return _to_out(obj)


@router.delete("/{interface_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_interface(interface_id: int, db: Session = Depends(get_db)) -> Response:
    """소프트 삭제 — deleted_at 만 마킹하고 **DB 행은 영구 보존**.

    cascade 로 묶인 call_logs / incidents / SLA targets 가 금감원 전산사고
    보고용 감사 자료라서, Interface 행을 hard delete 하면 그 자료가 전부
    cascade 로 사라짐. 그래서 **N일 보관 후 자동 삭제 cron 같은 건 절대
    추가하지 말 것** (이전 시도 후 사용자가 거부함).

    부수 효과: enabled=false 로 같이 꺼서 스케줄러가 더 안 부름. 기본
    인터페이스 목록에서 제외 (휴지통 토글로만 보임).
    """
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is None:
        obj.deleted_at = now_kst()
        obj.enabled = False
        db.commit()
        try:
            from app.services.scheduler import sync_jobs
            sync_jobs()
        except Exception:  # noqa: BLE001
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{interface_id}/restore", response_model=InterfaceOut)
def restore_interface(interface_id: int, db: Session = Depends(get_db)) -> InterfaceOut:
    """보관 해제 (휴지통 → 활성). enabled 는 자동으로 켜지 않음 — 운영자가
    의도적으로 ``enabled`` 토글을 다시 켜야 cron 이 돌기 시작."""
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not deleted")
    obj.deleted_at = None
    db.commit()
    db.refresh(obj)
    return _to_out(obj)
