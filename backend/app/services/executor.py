"""Interface execution — REST/SOAP/FTP/MQ adapters.

Phase 1 ships REST end-to-end and stubs SOAP via httpx; FTP/MQ adapters raise
NotImplementedError but are wired up so Phase 2/3 work plugs in cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.core.websocket import ws_manager
from app.models import CallLog, Interface
from app.models.call_log import CallStatus
from app.models.interface import AuthType, ProtocolType

log = logging.getLogger("noahub.executor")
DEFAULT_TIMEOUT = 10.0  # seconds


def _classify(http_status: int | None, exc: Exception | None) -> CallStatus:
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException | asyncio.TimeoutError):
            return CallStatus.TIMEOUT
        return CallStatus.FAILURE
    if http_status is None:
        return CallStatus.FAILURE
    if 200 <= http_status < 400:
        return CallStatus.SUCCESS
    if http_status in (401, 403):
        return CallStatus.AUTH_ERROR
    if http_status == 422:
        return CallStatus.FORMAT_ERROR
    if 500 <= http_status < 600:
        return CallStatus.SERVER_ERROR
    return CallStatus.FAILURE


def _build_auth_headers(itf: Interface) -> dict[str, str]:
    headers: dict[str, str] = dict(itf.headers or {})
    if itf.auth_type == AuthType.NONE or not itf.auth_secret:
        return headers
    secret = decrypt_secret(itf.auth_secret)
    if itf.auth_type == AuthType.API_KEY:
        headers.setdefault("X-API-Key", secret)
    elif itf.auth_type in (AuthType.BEARER, AuthType.OAUTH2):
        headers.setdefault("Authorization", f"Bearer {secret}")
    elif itf.auth_type == AuthType.BASIC:
        # secret stored as "user:password"
        import base64

        token = base64.b64encode(secret.encode()).decode()
        headers.setdefault("Authorization", f"Basic {token}")
    return headers


async def _exec_rest(itf: Interface) -> tuple[int | None, dict | None, Exception | None]:
    headers = _build_auth_headers(itf)
    method = (itf.method or "GET").upper()
    body = itf.request_template or None
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            req_kwargs: dict[str, Any] = {"headers": headers}
            if body is not None and method in ("POST", "PUT", "PATCH"):
                req_kwargs["json"] = body
            resp = await client.request(method, itf.endpoint, **req_kwargs)
            try:
                body_payload: Any = resp.json()
            except ValueError:
                body_payload = {"text": resp.text[:2000]}
            payload = {
                "headers": dict(resp.headers),
                "body": body_payload,
            }
            return resp.status_code, payload, None
    except Exception as e:  # noqa: BLE001
        return None, None, e


async def _exec_soap(itf: Interface) -> tuple[int | None, dict | None, Exception | None]:
    # Lightweight SOAP: post the template body as XML text. Real SOAP clients
    # should use zeep with WSDL parsing — left as Phase 2 enhancement.
    headers = _build_auth_headers(itf)
    headers.setdefault("Content-Type", "text/xml; charset=utf-8")
    body = (itf.request_template or {}).get("xml", "")
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(itf.endpoint, headers=headers, content=body.encode("utf-8"))
            return resp.status_code, {"text": resp.text[:2000]}, None
    except Exception as e:  # noqa: BLE001
        return None, None, e


async def execute_interface(
    itf: Interface,
    db: Session,
    *,
    triggered_by: str = "manual",
    parent_log: CallLog | None = None,
) -> CallLog:
    """Execute an interface once, persist a CallLog row, broadcast & detect.

    When ``parent_log`` is provided, the new row is linked as a retry of that
    log: ``parent_log_id`` set, ``retry_count`` incremented from the parent,
    ``triggered_by='reprocess'``, and the parent is flagged ``is_reprocessed``.
    """
    started = time.perf_counter()
    http_status: int | None = None
    response: dict | None = None
    exc: Exception | None = None

    if itf.protocol == ProtocolType.REST:
        http_status, response, exc = await _exec_rest(itf)
    elif itf.protocol == ProtocolType.SOAP:
        http_status, response, exc = await _exec_soap(itf)
    elif itf.protocol in (ProtocolType.FTP, ProtocolType.MQ):
        exc = NotImplementedError(f"protocol {itf.protocol} not yet implemented")
    else:
        exc = ValueError(f"unknown protocol: {itf.protocol}")

    duration_ms = int((time.perf_counter() - started) * 1000)
    status = _classify(http_status, exc)

    err_type: str | None = None
    err_trace: str | None = None
    if exc is not None:
        err_type = type(exc).__name__
        # truncate trace to keep logs row reasonably small (~4KB)
        err_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]

    log_row = CallLog(
        interface_id=itf.id,
        request={
            "method": itf.method,
            "endpoint": itf.endpoint,
            "body": itf.request_template,
        },
        response=response,
        status=status,
        http_status=http_status,
        duration_ms=duration_ms,
        error_message=str(exc) if exc else None,
        error_type=err_type,
        error_trace=err_trace,
        triggered_by="reprocess" if parent_log is not None else triggered_by,
        parent_log_id=parent_log.id if parent_log is not None else None,
        retry_count=(parent_log.retry_count + 1) if parent_log is not None else 0,
    )
    db.add(log_row)
    if parent_log is not None and not parent_log.is_reprocessed:
        parent_log.is_reprocessed = True
    db.commit()
    db.refresh(log_row)

    # Phase 2 hook: detect & notify
    try:
        from app.services.detector import evaluate_after_call

        evaluate_after_call(db, itf, log_row)
    except Exception:  # noqa: BLE001
        log.exception("detector failed for interface=%s", itf.id)

    # broadcast to live monitoring clients
    asyncio.create_task(  # noqa: RUF006 — fire-and-forget
        ws_manager.broadcast(
            "call_log",
            {
                "id": log_row.id,
                "interface_id": itf.id,
                "interface_name": itf.name,
                "status": log_row.status.value,
                "http_status": log_row.http_status,
                "duration_ms": log_row.duration_ms,
                "called_at": log_row.called_at.isoformat() if log_row.called_at else None,
            },
        )
    )

    return log_row
