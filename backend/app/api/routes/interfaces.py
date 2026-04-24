from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_role
from app.core.security import decrypt_secret, encrypt_secret
from app.core.time import now_kst
from app.models import Interface, User, UserRole
from app.models.interface import InterfaceCategory, InterfaceDirection, ProtocolType
from app.schemas.interface import (
    InterfaceCreate,
    InterfaceOut,
    InterfaceUpdate,
    RevealSecretRequest,
    RevealSecretResponse,
)
from app.services.audit import record_audit

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
    category: InterfaceCategory | None = None,
    direction: InterfaceDirection | None = None,
    include_deleted: bool = False,
    only_deleted: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),  # 모든 역할 가능 — 인증만 확인
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
    if category is not None:
        stmt = stmt.where(Interface.category == category)
    if direction is not None:
        stmt = stmt.where(Interface.direction == direction)
    return [_to_out(i) for i in db.scalars(stmt.order_by(Interface.id.desc())).all()]


@router.post("", response_model=InterfaceOut, status_code=status.HTTP_201_CREATED)
def create_interface(
    payload: InterfaceCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> InterfaceOut:
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
    record_audit(
        db, actor=actor, action="interface.create",
        resource_type="interface", resource_id=obj.id,
        after={k: v for k, v in data.items() if k != "auth_secret"},
        request=request,
    )
    return _to_out(obj)


@router.get("/{interface_id}", response_model=InterfaceOut)
def get_interface(interface_id: int, db: Session = Depends(get_db)) -> InterfaceOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    return _to_out(obj)


@router.patch("/{interface_id}", response_model=InterfaceOut)
def update_interface(
    interface_id: int,
    payload: InterfaceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> InterfaceOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "deleted interface — restore first")
    data = payload.model_dump(exclude_unset=True)
    # 시크릿 변경 여부만 별도 추적 (값은 절대 감사 로그에 안 남김)
    secret = data.pop("auth_secret", None)
    secret_reason = data.pop("secret_change_reason", None)
    # 시크릿 변경 시 사유 필수 — 보안 감사 추적 가능하도록.
    # 빈 문자열 ("") 로 시크릿을 지우는 케이스도 동일 정책 적용.
    if secret is not None and not (secret_reason and secret_reason.strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "시크릿을 변경하려면 변경 사유 (secret_change_reason) 가 필요합니다.",
        )
    before_snapshot = {k: getattr(obj, k) for k in data if k != "auth_secret"}
    for k, v in data.items():
        setattr(obj, k, v)
    if secret is not None:
        obj.auth_secret = encrypt_secret(secret) if secret else None
    db.commit()
    db.refresh(obj)
    if data:  # 시크릿 외에 변경 필드가 있을 때만 일반 update 감사
        record_audit(
            db, actor=actor, action="interface.update",
            resource_type="interface", resource_id=obj.id,
            before=before_snapshot, after={k: v for k, v in data.items()},
            request=request,
        )
    if secret is not None:
        # 사유는 before_value 에 보존. 값(secret)은 절대 안 남김.
        record_audit(
            db, actor=actor, action="interface.secret_changed",
            resource_type="interface", resource_id=obj.id,
            before={"reason": secret_reason},
            after={"cleared": secret == ""},
            request=request,
        )
    return _to_out(obj)


@router.post("/{interface_id}/reveal-secret", response_model=RevealSecretResponse)
def reveal_secret(
    interface_id: int,
    payload: RevealSecretRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> RevealSecretResponse:
    """저장된 시크릿 평문 1회 조회. ADMIN 만 가능, 사유 필수, 모든 조회 audit.

    개인정보보호법·내부 보안 통제 대응 — "왜 시크릿을 봤느냐" 질문에
    누가·언제·왜 답변 가능해야 함.
    """
    if not (payload.reason and payload.reason.strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "시크릿 조회 사유는 필수입니다.",
        )
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if not obj.auth_secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "이 인터페이스에 저장된 시크릿이 없습니다.")
    plain = decrypt_secret(obj.auth_secret)
    record_audit(
        db, actor=actor, action="interface.secret_view",
        resource_type="interface", resource_id=obj.id,
        before={"reason": payload.reason}, request=request,
    )
    return RevealSecretResponse(
        interface_id=obj.id,
        interface_name=obj.name,
        auth_type=obj.auth_type,
        secret=plain,
    )


@router.delete("/{interface_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_interface(
    interface_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> Response:
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
        record_audit(
            db, actor=actor, action="interface.archive",
            resource_type="interface", resource_id=obj.id,
            after={"name": obj.name}, request=request,
        )
        try:
            from app.services.scheduler import sync_jobs
            sync_jobs()
        except Exception:  # noqa: BLE001
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{interface_id}/mute", response_model=InterfaceOut)
def mute_interface(
    interface_id: int,
    minutes: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.OPERATOR, UserRole.ADMIN])),
) -> InterfaceOut:
    """알림 음소거 —. 정기 점검 등 의도적 알림 폭주 방지용.

    - minutes 분 후 자동 해제 (muted_until = now + minutes)
    - incident 자체는 정상 생성, 대시보드 카운트도 갱신. toast/slack/email 만 skip.
    - OPERATOR/ADMIN 가능. 음소거 시작/해제는 모두 감사 로그.
    """
    from datetime import timedelta

    if minutes < 1 or minutes > 24 * 60:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "음소거 시간은 1분~24시간 사이여야 합니다.")
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    until = now_kst() + timedelta(minutes=minutes)
    obj.muted_until = until
    db.commit()
    db.refresh(obj)
    record_audit(
        db, actor=actor, action="interface.mute",
        resource_type="interface", resource_id=obj.id,
        after={"name": obj.name, "minutes": minutes, "until": until.isoformat()},
        request=request,
    )
    return _to_out(obj)


@router.post("/{interface_id}/unmute", response_model=InterfaceOut)
def unmute_interface(
    interface_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.OPERATOR, UserRole.ADMIN])),
) -> InterfaceOut:
    """음소거 즉시 해제. 멱등 — 음소거 안 된 상태에서 호출해도 OK."""
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    was_muted = obj.muted_until is not None
    obj.muted_until = None
    db.commit()
    db.refresh(obj)
    if was_muted:
        record_audit(
            db, actor=actor, action="interface.unmute",
            resource_type="interface", resource_id=obj.id,
            after={"name": obj.name},
            request=request,
        )
    return _to_out(obj)


@router.post("/{interface_id}/restore", response_model=InterfaceOut)
def restore_interface(
    interface_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> InterfaceOut:
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
    record_audit(
        db, actor=actor, action="interface.restore",
        resource_type="interface", resource_id=obj.id,
        request=request,
    )
    return _to_out(obj)
