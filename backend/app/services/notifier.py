"""Slack + email + in-app alerts. 모든 채널은 best-effort (실패는 로그만)."""

from __future__ import annotations

import logging
from email.message import EmailMessage

import httpx

from app.core.config import get_settings
from app.core.time import now_kst
from app.core.websocket import ws_manager
from app.models import Incident, Interface

log = logging.getLogger("noahub.notifier")

# 알림 채널 정식 명. 인터페이스의 alert_channels JSON 에서 사용.
CH_IN_APP = "in_app"   # 프론트 toast 팝업
CH_SLACK = "slack"     # Slack webhook
CH_EMAIL = "email"     # SMTP 이메일
ALL_CHANNELS = (CH_IN_APP, CH_SLACK, CH_EMAIL)


def _format_message(itf: Interface, incident: Incident) -> str:
    return (
        f"[NOA Hub] {incident.severity.upper()} — {itf.name}\n"
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
    """Phase B.7 — muted_until 이 미래면 음소거 중."""
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
    """Incident 발생 시 알림 발송 — Phase B.7 룰 적용.

    채널별 발송 여부:
    - WS 'incident' broadcast: **항상** 발송. 대시보드 카운트/뱃지/라이브 피드는
      음소거와 무관하게 갱신되어야 함 ("기록은 남고 알람만 끔" 의 핵심).
    - in_app toast / slack / email: muted_until 이 미래면 모두 skip.
      또는 alert_channels 에 빠져있으면 해당 채널 skip.

    payload.should_alert: 프론트가 toast 띄울지 결정 — 음소거 중이거나
    in_app 채널이 비활성이면 false. 카운트 갱신은 항상.
    """
    text = _format_message(itf, incident)
    log.warning(text)

    muted = _is_muted(itf)
    chans = _enabled_channels(itf)
    should_alert_in_app = (CH_IN_APP in chans) and not muted
    should_send_slack = (CH_SLACK in chans) and not muted
    should_send_email = (CH_EMAIL in chans) and not muted

    if should_send_slack:
        await _post_slack(text)
    else:
        log.info("slack skipped — muted=%s channels=%s", muted, chans)

    if should_send_email:
        await _send_email(f"[NOA Hub] {itf.name} — {incident.type.value}", text)
    else:
        log.info("email skipped — muted=%s channels=%s", muted, chans)

    # WS broadcast 는 항상 — 다만 should_alert 플래그로 toast 노출 여부 전달
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
            # Phase B.7
            "should_alert": should_alert_in_app,
            "muted": muted,
            "muted_until": itf.muted_until.isoformat() if itf.muted_until else None,
        },
    )