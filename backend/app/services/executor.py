"""인터페이스 실행 — REST/SOAP/SFTP/Batch/MQ 5종 어댑터 디스패치.

각 어댑터는 (http_status, response, exc) 튜플 반환. execute_interface 가
공통 후처리 (call_log 저장, traceback 캡처, detector 호출, WebSocket broadcast)
담당. 새 프로토콜 추가 시 _exec_xxx 함수 + 디스패치 한 줄만 추가.

설계 메모:
- 같은 call_log 두 번 재처리 방지: parent_log.is_reprocessed=True 마킹
- traceback 풀 스택 캡처: 운영자가 ELK 안 가도 다이얼로그에서 원인 파악 가능
- response 에 헤더+바디 분리 저장: 401 의 www-authenticate 같은 진단 정보 보존
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
import traceback
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
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


async def _exec_sftp(itf: Interface) -> tuple[int | None, dict | None, Exception | None]:
    """Real SFTP via paramiko (sync) wrapped in asyncio.to_thread.

    request_template fields:
      host, port, path, operation (LIST | GET | PUT), filename (for GET/PUT),
      content (for PUT, base64 or text)
    auth_secret format: 'user:password' (decrypted from AES-GCM blob).
    Endpoint can be `sftp://host:port/path` for convenience; explicit fields
    in request_template take precedence.
    """
    cfg = itf.request_template or {}
    parsed_host, parsed_port, parsed_path = _parse_sftp_endpoint(itf.endpoint)
    host = cfg.get("host") or parsed_host or "sftp"
    port = int(cfg.get("port") or parsed_port or 22)
    path = cfg.get("path") or parsed_path or "/upload"
    operation = (cfg.get("operation") or "LIST").upper()

    user, password = "noahub", "noahub_pw"  # sane defaults for the demo container
    if itf.auth_secret:
        try:
            secret = decrypt_secret(itf.auth_secret)
            if ":" in secret:
                user, password = secret.split(":", 1)
        except Exception:  # noqa: BLE001
            pass  # fall back to demo creds

    try:
        result = await asyncio.to_thread(
            _sftp_sync, host, port, user, password, path, operation, cfg
        )
        return 200, {"headers": {"x-sftp-host": f"{host}:{port}"}, "body": result}, None
    except Exception as e:  # noqa: BLE001
        return None, None, e


def _parse_sftp_endpoint(endpoint: str) -> tuple[str | None, int | None, str | None]:
    """Best-effort parse of `sftp://host[:port][/path]`."""
    if not endpoint:
        return None, None, None
    raw = endpoint
    for prefix in ("sftp://", "ftp://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    path = None
    if "/" in raw:
        host_part, path = raw.split("/", 1)
        path = "/" + path
    else:
        host_part = raw
    host: str | None = host_part
    port: int | None = None
    if host and ":" in host:
        host, port_str = host.split(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = None
    return host, port, path


def _sftp_sync(
    host: str, port: int, user: str, password: str, path: str, operation: str, cfg: dict
) -> dict:
    import paramiko  # local import keeps cold start fast for non-SFTP runs

    transport = paramiko.Transport((host, port))
    try:
        transport.connect(username=user, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise RuntimeError("could not open SFTP channel")
        try:
            if operation == "LIST":
                items = sftp.listdir_attr(path)
                return {
                    "operation": "LIST",
                    "path": path,
                    "file_count": len(items),
                    "files": [
                        {"name": it.filename, "size": it.st_size, "mtime": it.st_mtime}
                        for it in items[:50]
                    ],
                }
            if operation == "GET":
                fname = cfg.get("filename") or "premium_20260422.csv"
                remote = f"{path.rstrip('/')}/{fname}"
                with sftp.open(remote, "rb") as fp:
                    data = fp.read(64 * 1024)  # cap demo payload at 64 KB
                preview = data.decode("utf-8", errors="replace")[:1000]
                return {
                    "operation": "GET",
                    "remote_path": remote,
                    "bytes_read": len(data),
                    "preview": preview,
                }
            if operation == "PUT":
                fname = cfg.get("filename") or f"upload_{int(time.time())}.txt"
                content = (cfg.get("content") or f"# generated by NOA Hub @ {time.time()}\n").encode("utf-8")
                remote = f"{path.rstrip('/')}/{fname}"
                with sftp.open(remote, "wb") as fp:
                    fp.write(content)
                return {
                    "operation": "PUT",
                    "remote_path": remote,
                    "bytes_written": len(content),
                }
            raise ValueError(f"unsupported operation: {operation}")
        finally:
            sftp.close()
    finally:
        transport.close()


async def _exec_mq(itf: Interface) -> tuple[int | None, dict | None, Exception | None]:
    """Pop one message from a Redis LIST that we use as a demo MQ queue.

    request_template fields:
      queue          – Redis key, e.g. 'CARMARKET.OUT'
      consumer_group – informational only (Redis LIST has no real groups)
      block_ms       – BLPOP timeout (ms) — 0 = non-blocking
    Endpoint can be `redis://host:port/db/QUEUE` for convenience.
    """
    cfg = itf.request_template or {}
    queue = (cfg.get("queue") or _parse_queue_from_endpoint(itf.endpoint) or "default")
    consumer_group = cfg.get("consumer_group", "noahub")
    block_ms = int(cfg.get("block_ms", 1000))

    try:
        from redis import asyncio as aioredis  # type: ignore

        client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            timeout_s = max(0, block_ms // 1000)
            popped = await client.blpop([queue], timeout=timeout_s)
            depth = await client.llen(queue)
            if not popped:
                return (
                    200,
                    {
                        "headers": {"x-mq-host": "redis", "x-queue": queue, "x-queue-depth": str(depth)},
                        "body": {
                            "queue": queue,
                            "consumer_group": consumer_group,
                            "messages_received": 0,
                            "remaining_in_queue": depth,
                        },
                    },
                    None,
                )
            _, raw = popped
            try:
                msg = _json.loads(raw)
            except Exception:  # noqa: BLE001
                msg = {"raw": raw}
            return (
                200,
                {
                    "headers": {"x-mq-host": "redis", "x-queue": queue, "x-queue-depth": str(depth)},
                    "body": {
                        "queue": queue,
                        "consumer_group": consumer_group,
                        "messages_received": 1,
                        "message": msg,
                        "remaining_in_queue": depth,
                    },
                },
                None,
            )
        finally:
            await client.aclose()
    except Exception as e:  # noqa: BLE001
        return None, None, e


def _parse_queue_from_endpoint(endpoint: str | None) -> str | None:
    """Extract trailing queue name from `redis://host:port/db/QUEUE` style URLs."""
    if not endpoint:
        return None
    raw = endpoint
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    if "/" in raw:
        parts = raw.split("/")
        last = parts[-1].strip()
        if last and last not in ("0", "1", "2"):  # avoid db index
            return last
    return None


async def _exec_batch(itf: Interface) -> tuple[int | None, dict | None, Exception | None]:
    """Simulated batch job — pickup → process → result file.

    Real production would parse `request_template` for SFTP host, file glob,
    business job type, etc. Here we emulate a deterministic-ish run so the UI
    has something to display.
    """
    import random

    cfg = itf.request_template or {}
    job_type = cfg.get("job_type", "GENERIC_BATCH")
    input_dir = cfg.get("input_dir", "/sftp/in")
    result_dir = cfg.get("result_dir", "/sftp/out")

    # simulate processing time (slow ish, reflects real batch latency)
    await asyncio.sleep(random.uniform(0.3, 1.5))

    records = random.randint(800, 12000)
    errors = random.randint(0, max(1, records // 200))
    duration_part = random.randint(800, 4000)
    result_file = f"{result_dir}/{job_type.lower()}_{itf.id}_{int(time.time())}.csv"

    payload = {
        "headers": {"x-job-type": job_type, "x-pickup-dir": input_dir},
        "body": {
            "job_type": job_type,
            "records_processed": records,
            "errors": errors,
            "result_file": result_file,
            "process_ms": duration_part,
            "input_files_picked": random.randint(1, 5),
        },
    }
    # synthetic status: 200 ok, occasionally 500 if too many errors
    if errors > records // 100:
        return 500, payload | {"body": payload["body"] | {"reason": "ERROR_RATE_EXCEEDED"}}, None
    return 200, payload, None


async def execute_interface(
    itf: Interface,
    db: Session,
    *,
    triggered_by: str = "manual",
    parent_log: CallLog | None = None,
    actor: "User | None" = None,  # noqa: F821 — 순환 임포트 회피
) -> CallLog:
    """인터페이스 1회 실행 → CallLog 저장 → broadcast/detect.

    ``parent_log`` 가 주어지면 그 호출의 재처리로 연결:
    parent_log_id 설정, retry_count 증가, triggered_by='reprocess',
    원본은 is_reprocessed=True 로 마킹 (UI 에서 ↻ 버튼 사라져 무한 재처리
    방지).
    """
    started = time.perf_counter()
    http_status: int | None = None
    response: dict | None = None
    exc: Exception | None = None

    if itf.protocol == ProtocolType.REST:
        http_status, response, exc = await _exec_rest(itf)
    elif itf.protocol == ProtocolType.SOAP:
        http_status, response, exc = await _exec_soap(itf)
    elif itf.protocol == ProtocolType.BATCH:
        http_status, response, exc = await _exec_batch(itf)
    elif itf.protocol == ProtocolType.FTP:
        http_status, response, exc = await _exec_sftp(itf)
    elif itf.protocol == ProtocolType.MQ:
        http_status, response, exc = await _exec_mq(itf)
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
        actor_user_id=actor.id if actor is not None else None,
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
