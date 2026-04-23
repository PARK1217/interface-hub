"""Slack + email alerts. Both channels are best-effort; failures are logged, not raised."""

from __future__ import annotations

import logging
from email.message import EmailMessage

import httpx

from app.core.config import get_settings
from app.core.websocket import ws_manager
from app.models import Incident, Interface

log = logging.getLogger("noahub.notifier")


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


async def dispatch_alert(itf: Interface, incident: Incident) -> None:
    text = _format_message(itf, incident)
    log.warning(text)
    await _post_slack(text)
    await _send_email(f"[NOA Hub] {itf.name} — {incident.type.value}", text)
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
        },
    )
