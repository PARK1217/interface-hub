"""Slack + email + in-app alerts. 모든 채널은 best-effort (실패는 로그만)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email.message import EmailMessage

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.time import now_kst
from app.core.websocket import ws_manager
from app.models import AlertRule, Incident, Interface

log = logging.getLogger("noahub.notifier")

# 알림 채널 정식 명. 인터페이스의 alert_channels JSON 에서 사용.
CH_IN_APP = "in_app"   # 프론트 toast 팝업
CH_SLACK = "slack"     # Slack webhook
CH_EMAIL = "email"     # SMTP 이메일
ALL_CHANNELS = (CH_IN_APP, CH_SLACK, CH_EMAIL)


@dataclass
class GlobalRuleDecision:
    """전역 알림 룰 평가 결과.

    - allowed_channels: severity 별 화이트리스트 (인터페이스 화이트리스트와 교집합)
    - quiet_now: 현재 quiet hours / 주말 silence 인지
    - silenced_reason: 사용자에게 노출할 silence 사유 (None=정상 발송)
    """
    allowed_channels: set[str]
    quiet_now: bool
    silenced_reason: str | None


def _load_global_rule() -> AlertRule | None:
    """단일 행 룰 조회. 미설정/오류 시 None — 호출자가 기본 동작(전부 발송)."""
    try:
        with SessionLocal() as db:
            return db.scalar(select(AlertRule).where(AlertRule.id == 1))
    except Exception:  # noqa: BLE001
        log.exception("alert_rules 조회 실패 — 기본 동작 (전부 발송) 으로 계속")
        return None


def _within_quiet_hours(start: int, end: int, now_hour: int) -> bool:
    """now_hour 가 [start, end) 범위 안인지. start>end 면 자정 넘김."""
    if start == end:
        return False
    if start < end:
        return start <= now_hour < end
    return now_hour >= start or now_hour < end


def evaluate_global_rule(severity: str) -> GlobalRuleDecision:
    """전역 룰 평가 — severity (info/warning/critical) 와 현재 시각 기준.

    인터페이스의 alert_channels 와는 별개. 두 개 모두 통과해야 발송.
    인터페이스의 muted_until 도 별도 — dispatch_alert 에서 따로 검사.
    """
    rule = _load_global_rule()
    if rule is None:
        return GlobalRuleDecision(set(ALL_CHANNELS), False, None)

    severity = (severity or "warning").lower()
    if severity == "info":
        allowed = set(rule.info_channels or [])
    elif severity == "critical":
        allowed = set(rule.critical_channels or [])
    else:
        allowed = set(rule.warning_channels or [])

    now = now_kst()
    quiet = False
    reason: str | None = None
    if rule.weekend_silence and now.weekday() >= 5:  # 5=토 6=일
        quiet = True
        reason = "주말 silence (전역 룰)"
    elif rule.quiet_hours_enabled and _within_quiet_hours(
        rule.quiet_hours_start, rule.quiet_hours_end, now.hour
    ):
        quiet = True
        reason = (
            f"근무시간 외 silence ({rule.quiet_hours_start:02d}:00~"
            f"{rule.quiet_hours_end:02d}:00, 전역 룰)"
        )

    # critical 은 quiet hours 무시 옵션 (기본 ON)
    if quiet and severity == "critical" and rule.quiet_hours_skip_critical:
        quiet = False
        reason = None

    return GlobalRuleDecision(allowed, quiet, reason)


def _format_message(itf: Interface, incident: Incident) -> str:
    return (
        f"[Interface Hub] {incident.severity.upper()} — {itf.name}\n"
        f"  · type     : {incident.type.value}\n"
        f"  · summary  : {incident.summary}\n"
        f"  · detected : {incident.detected_at.isoformat() if incident.detected_at else '-'}"
    )


async def _post_slack(message: str) -> None:
    url = get_settings().slack_webhook_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json={"text": message})
    except Exception:  # noqa: BLE001
        log.exception("slack notify failed")


async def _send_email(subject: str, body: str) -> None:
    s = get_settings()
    if not (s.smtp_host and s.alert_email_to):
        return
    try:
        import aiosmtplib

        msg = EmailMessage()
        msg["From"] = s.alert_email_from
        msg["To"] = s.alert_email_to
        msg["Subject"] = subject
        msg.set_content(body)
        await aiosmtplib.send(
            msg,
            hostname=s.smtp_host,
            port=s.smtp_port,
            username=s.smtp_user,
            password=s.smtp_password,
            start_tls=True,
        )
    except Exception:  # noqa: BLE001
        log.exception("email notify failed")


def _is_muted(itf: Interface) -> bool:
    """muted_until 이 미래면 음소거 중."""
    return itf.muted_until is not None and itf.muted_until > now_kst()


def _enabled_channels(itf: Interface) -> set[str]:
    """인터페이스의 alert_channels 화이트리스트 → 정규화된 set.

    null/빈 리스트 시 기본 3종 모두 활성 (알림 룰 미설정 시 기존 동작 유지).
    """
    raw = itf.alert_channels
    if not raw:
        return set(ALL_CHANNELS)
    return {c for c in raw if c in ALL_CHANNELS}


async def dispatch_alert(itf: Interface, incident: Incident) -> None:
    """Incident 발생 시 알림 발송 — 인터페이스 룰 + 전역 룰.

    채널별 발송 여부 (모두 통과해야 발송):
    1. 인터페이스 muted_until 미래 → 전부 skip
    2. 인터페이스 alert_channels 화이트리스트
    3. 전역 룰의 severity 별 채널 화이트리스트
    4. 전역 룰의 quiet hours / 주말 silence (critical 은 skip 옵션)

    WS broadcast 는 **항상** 발송 — 대시보드 카운트/뱃지/라이브 피드는 silence
    와 무관하게 갱신되어야 함 ("기록은 남고 알람만 끔").

    payload.should_alert: 프론트가 toast 띄울지 결정 — silence 중이거나
    in_app 채널이 어디서든 차단되면 false.
    """
    text = _format_message(itf, incident)
    log.warning(text)

    muted = _is_muted(itf)
    itf_chans = _enabled_channels(itf)
    decision = evaluate_global_rule(incident.severity)

    # 두 화이트리스트 교집합 + 인터페이스 음소거 + 전역 quiet
    silenced = muted or decision.quiet_now
    effective = itf_chans & decision.allowed_channels

    should_alert_in_app = (CH_IN_APP in effective) and not silenced
    should_send_slack = (CH_SLACK in effective) and not silenced
    should_send_email = (CH_EMAIL in effective) and not silenced

    if should_send_slack:
        await _post_slack(text)
    else:
        log.info(
            "slack skipped — muted=%s quiet=%s itf=%s global=%s",
            muted, decision.quiet_now, itf_chans, decision.allowed_channels,
        )

    if should_send_email:
        await _send_email(f"[Interface Hub] {itf.name} — {incident.type.value}", text)
    else:
        log.info(
            "email skipped — muted=%s quiet=%s itf=%s global=%s",
            muted, decision.quiet_now, itf_chans, decision.allowed_channels,
        )

    silence_reason: str | None = None
    if muted:
        silence_reason = (
            f"인터페이스 음소거 ({itf.muted_until.isoformat()} 까지)"
            if itf.muted_until else "인터페이스 음소거"
        )
    elif decision.silenced_reason:
        silence_reason = decision.silenced_reason

    # WS broadcast 는 항상 — 프론트 카운트/뱃지/라이브 피드 갱신
    await ws_manager.broadcast(
        "incident",
        {
            "id": incident.id,
            "interface_id": itf.id,
            "interface_name": itf.name,
            "type": incident.type.value,
            "severity": incident.severity,
            "summary": incident.summary,
            "detected_at": incident.detected_at.isoformat() if incident.detected_at else None,
            "should_alert": should_alert_in_app,
            "muted": muted,
            "muted_until": itf.muted_until.isoformat() if itf.muted_until else None,
            # 전역 룰 적용 결과 (UI 안내용)
            "silenced": silenced,
            "silenced_reason": silence_reason,
            "effective_channels": sorted(effective),
        },
    )