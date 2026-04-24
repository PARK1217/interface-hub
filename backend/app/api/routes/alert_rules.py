"""전역 알림 룰 (Phase B.10) — 단일 행 GET/PUT.

설계: 멀티 룰 (severity 별, 인터페이스 그룹별 등) 까지 가면 복잡도 폭발.
대신 운영자 1명이 관리하는 NOA Hub 데모 컨텍스트에 맞춰 **글로벌 룰 1행 + per-interface
오버라이드(Phase B.7)** 로 단순화. 향후 그룹 라우팅 필요해지면 여기 확장.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_role
from app.models import AlertRule, User, UserRole
from app.services.audit import record_audit

router = APIRouter(prefix="/alert-rules", tags=["alert-rules"])


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    info_channels: list[str]
    warning_channels: list[str]
    critical_channels: list[str]
    quiet_hours_enabled: bool
    quiet_hours_start: int
    quiet_hours_end: int
    quiet_hours_skip_critical: bool
    weekend_silence: bool


class AlertRuleUpdate(BaseModel):
    info_channels: list[str] | None = None
    warning_channels: list[str] | None = None
    critical_channels: list[str] | None = None
    quiet_hours_enabled: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_skip_critical: bool | None = None
    weekend_silence: bool | None = None


_VALID_CHANNELS = {"in_app", "slack", "email"}


def _get_or_create(db: Session) -> AlertRule:
    """단일 행 보장 — 마이그레이션이 INSERT 했지만, dev DB 누락 케이스 방어."""
    rule = db.scalar(select(AlertRule).where(AlertRule.id == 1))
    if rule is None:
        rule = AlertRule(id=1)
        db.add(rule)
        db.commit()
        db.refresh(rule)
    return rule


@router.get("", response_model=AlertRuleOut)
def get_alert_rules(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AlertRuleOut:
    return AlertRuleOut.model_validate(_get_or_create(db))


@router.put("", response_model=AlertRuleOut)
def update_alert_rules(
    payload: AlertRuleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> AlertRuleOut:
    """ADMIN 만 수정 가능. 모든 변경은 audit_logs 에 기록."""
    rule = _get_or_create(db)
    data = payload.model_dump(exclude_unset=True)

    # 채널 화이트리스트 검증 — 프론트가 잘못 보내도 알 수 없는 채널 명 저장 X
    for k in ("info_channels", "warning_channels", "critical_channels"):
        if k in data and data[k] is not None:
            invalid = [c for c in data[k] if c not in _VALID_CHANNELS]
            if invalid:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"unknown channel(s): {invalid}. allowed: {sorted(_VALID_CHANNELS)}",
                )

    before = {k: getattr(rule, k) for k in data}
    for k, v in data.items():
        setattr(rule, k, v)
    rule.updated_by = actor.id
    db.commit()
    db.refresh(rule)
    record_audit(
        db, actor=actor, action="alert_rules.update",
        resource_type="alert_rules", resource_id=rule.id,
        before=before, after=data, request=request,
    )
    return AlertRuleOut.model_validate(rule)
