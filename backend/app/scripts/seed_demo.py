"""Seed realistic Korean insurance company demo data.

Usage (inside backend container):
    docker compose exec backend python -m app.scripts.seed_demo

Effect:
- Wipes existing interfaces / call_logs / incidents / sla_targets.
- Inserts ~10 realistic external-agency interfaces a Korean insurer would use.
- Generates ~7 days of synthetic call_logs with believable distributions.
- Inserts a handful of resolved incidents (so the RAG index has signal).
- Configures SLA targets per interface.
"""

from __future__ import annotations

import math
import random
from datetime import timedelta

from sqlalchemy import delete

from app.core.database import SessionLocal, init_db
from app.core.time import now_kst
from app.models import CallLog, Incident, Interface, SlaTarget
from app.models.call_log import CallStatus
from app.models.incident import IncidentType
from app.models.interface import AuthType, ProtocolType

random.seed(42)
NOW = now_kst()
WINDOW_DAYS = 7


# Each entry: realistic Korean external counterpart with traffic/error profile.
SEED_INTERFACES: list[dict] = [
    {
        "name": "보험개발원-실손중복청구확인",
        "organization": "보험개발원 (KIDI)",
        "description": "실손보험 중복 청구 여부 조회",
        "protocol": ProtocolType.REST,
        "endpoint": "https://api.kidi.or.kr/v1/duplicate-check",
        "method": "POST",
        "schedule_cron": None,
        "auth_type": AuthType.API_KEY,
        "rps": 12.0, "fail_rate": 0.015, "latency_ms": (180, 80),
        "sla": (99.5, 800),
    },
    {
        "name": "신용정보원-CB조회",
        "organization": "한국신용정보원 (KCIS)",
        "description": "보험 가입 시 신용정보 조회",
        "protocol": ProtocolType.SOAP,
        "endpoint": "https://api.kcredit.or.kr/cb/inquiry",
        "method": "POST",
        "schedule_cron": None,
        "auth_type": AuthType.OAUTH2,
        "rps": 8.0, "fail_rate": 0.02, "latency_ms": (350, 120),
        "sla": (99.0, 1500),
    },
    {
        "name": "건강보험심사평가원-요양기관조회",
        "organization": "건강보험심사평가원 (HIRA)",
        "description": "병원·약국 코드 마스터 조회",
        "protocol": ProtocolType.REST,
        "endpoint": "https://api.hira.or.kr/openapi/medi-info",
        "method": "GET",
        "schedule_cron": "0 3 * * *",   # 매일 03:00 마스터 동기화
        "auth_type": AuthType.API_KEY,
        "rps": 0.05, "fail_rate": 0.0, "latency_ms": (4500, 1200),  # 야간 배치성
        "sla": (98.0, 8000),
    },
    {
        "name": "도로교통공단-운전면허확인",
        "organization": "도로교통공단 (KoROAD)",
        "description": "자동차보험 가입 전 면허 유효성 확인",
        "protocol": ProtocolType.REST,
        "endpoint": "https://api.koroad.or.kr/license/verify",
        "method": "POST",
        "schedule_cron": None,
        "auth_type": AuthType.BEARER,
        "rps": 5.0, "fail_rate": 0.03, "latency_ms": (220, 90),
        "sla": (99.0, 1000),
    },
    {
        "name": "국세청-사업자상태조회",
        "organization": "국세청 (NTS)",
        "description": "법인보험 청약 시 사업자등록 상태 확인",
        "protocol": ProtocolType.REST,
        "endpoint": "https://api.nts.go.kr/biz-status",
        "method": "POST",
        "schedule_cron": None,
        "auth_type": AuthType.API_KEY,
        "rps": 1.5, "fail_rate": 0.04, "latency_ms": (450, 200),
        "sla": (98.5, 1500),
    },
    {
        "name": "마이데이터허브-자산스크래핑",
        "organization": "한국신용정보원 마이데이터",
        "description": "고객 동의 기반 마이데이터 자산 정보 조회",
        "protocol": ProtocolType.REST,
        "endpoint": "https://api.mydata-hub.kr/v1/assets",
        "method": "GET",
        "schedule_cron": None,
        "auth_type": AuthType.OAUTH2,
        "rps": 22.0, "fail_rate": 0.06, "latency_ms": (650, 280),  # 외부 종속성 많음
        "sla": (97.0, 2500),
    },
    {
        "name": "금감원-전산사고보고",
        "organization": "금융감독원 (FSS)",
        "description": "장애 발생 시 전산사고 보고 제출",
        "protocol": ProtocolType.REST,
        "endpoint": "https://itcr.fss.or.kr/api/incident-report",
        "method": "POST",
        "schedule_cron": None,
        "auth_type": AuthType.OAUTH2,
        "rps": 0.02, "fail_rate": 0.01, "latency_ms": (1200, 400),
        "sla": (99.9, 3000),
    },
    {
        "name": "카카오알림톡-청구알림발송",
        "organization": "카카오 비즈메시지",
        "description": "보험금 지급 / 만기 안내 알림톡 발송",
        "protocol": ProtocolType.REST,
        "endpoint": "https://kakao-bizmsg.example.com/v2/send",
        "method": "POST",
        "schedule_cron": "*/10 * * * *",
        "auth_type": AuthType.BEARER,
        "rps": 35.0, "fail_rate": 0.025, "latency_ms": (140, 60),
        "sla": (99.0, 600),
    },
    {
        "name": "토스페이먼츠-자동이체",
        "organization": "토스페이먼츠 (PG)",
        "description": "월납 보험료 자동이체 결제 요청",
        "protocol": ProtocolType.REST,
        "endpoint": "https://api.tosspayments.com/v1/billing/charge",
        "method": "POST",
        "schedule_cron": "0 9 1 * *",   # 매월 1일 09:00 정기 출금
        "auth_type": AuthType.BASIC,
        "rps": 4.0, "fail_rate": 0.018, "latency_ms": (320, 110),
        "sla": (99.5, 1200),
    },
    {
        "name": "보험개발원-차량시세-MQ",
        "organization": "보험개발원 (KIDI)",
        "description": "차량 시세 산출 결과 비동기 수신",
        "protocol": ProtocolType.MQ,
        "endpoint": "tcp://kidi-mq.example.com:1414/QM.KIDI/CARMARKET.OUT",
        "method": "GET",
        "schedule_cron": None,
        "auth_type": AuthType.NONE,
        "rps": 1.2, "fail_rate": 0.01, "latency_ms": (90, 30),
        "sla": (99.0, 500),
    },
    {
        "name": "보험개발원-일일보험료정산-Batch",
        "organization": "보험개발원 (KIDI)",
        "description": "전일 보험계약 보험료 일괄 정산 파일 (SFTP 업로드)",
        "protocol": ProtocolType.BATCH,
        "endpoint": "sftp://batch.kidi.or.kr/incoming/PREMIUM_SETTLEMENT/",
        "method": "POST",
        "schedule_cron": "0 2 * * *",   # 매일 02:00 KST
        "auth_type": AuthType.BASIC,
        "rps": 0.0007, "fail_rate": 0.05, "latency_ms": (90000, 25000),
        "sla": (99.0, 180000),
        "request_template": {
            "job_type": "DAILY_PREMIUM_SETTLEMENT",
            "input_dir": "/sftp/in",
            "result_dir": "/sftp/out/kidi",
            "remote_host": "batch.kidi.or.kr",
            "filename_pattern": "PREMIUM_*.csv",
        },
    },
    {
        "name": "금융결제원-자동이체결과수신-Batch",
        "organization": "금융결제원 (KFTC)",
        "description": "자동이체 출금 결과 파일 수신 및 적재",
        "protocol": ProtocolType.BATCH,
        "endpoint": "sftp://batch.kftc.or.kr/outgoing/AUTO_DEBIT_RESULT/",
        "method": "GET",
        "schedule_cron": "30 9 * * *",  # 매일 09:30 KST
        "auth_type": AuthType.API_KEY,
        "rps": 0.0007, "fail_rate": 0.02, "latency_ms": (45000, 12000),
        "sla": (99.5, 90000),
        "request_template": {
            "job_type": "AUTO_DEBIT_RESULT_INGEST",
            "input_dir": "/sftp/inbound",
            "result_dir": "/sftp/processed",
            "remote_host": "batch.kftc.or.kr",
            "filename_pattern": "ADR_*.csv",
        },
    },
]


# Resolved historical incidents — for both UI history and RAG index seeding.
SEED_INCIDENTS: list[dict] = [
    {
        "interface_name": "보험개발원-실손중복청구확인",
        "type": IncidentType.SERVER_ERROR,
        "severity": "critical",
        "summary": "KIDI 측 5xx 다발, 30분간 청구 처리 지연",
        "root_cause": "KIDI 인증서버 OOM 으로 토큰 발급이 막혀 다운스트림 5xx 가 전파됨",
        "resolution": "KIDI 운영팀 통보 후 인증서버 재기동, 사내 재시도 큐로 5분 내 자동 재처리 완료",
        "days_ago": 18,
    },
    {
        "interface_name": "신용정보원-CB조회",
        "type": IncidentType.AUTH_ERROR,
        "severity": "critical",
        "summary": "OAuth 토큰 만료로 전체 신용조회 401",
        "root_cause": "OAuth refresh 토큰의 90일 만료를 갱신 스케줄러가 인지하지 못해 일괄 401 발생",
        "resolution": "수동으로 refresh_token 재발급, 토큰 만료 14일 전 알림 잡 추가",
        "days_ago": 42,
    },
    {
        "interface_name": "마이데이터허브-자산스크래핑",
        "type": IncidentType.SLOW_RESPONSE,
        "severity": "warning",
        "summary": "오후 2~4시 응답시간 3배 증가",
        "root_cause": "마이데이터 허브가 우리쪽 동시요청 50개 한도를 초과하여 큐잉됨",
        "resolution": "사내 호출 어댑터에 토큰 버킷(40 RPS) 적용, 평균 응답 280ms 로 회복",
        "days_ago": 9,
    },
    {
        "interface_name": "도로교통공단-운전면허확인",
        "type": IncidentType.TIMEOUT,
        "severity": "warning",
        "summary": "KoROAD SSL 인증서 만료로 핸드셰이크 실패",
        "root_cause": "상대방 와일드카드 인증서 만료 후 갱신 지연, 우리쪽은 root CA pin 으로 timeout 처리",
        "resolution": "KoROAD 측 인증서 재배포 확인 후 자동 회복, 만료 모니터링 봇에 도메인 추가",
        "days_ago": 31,
    },
    {
        "interface_name": "카카오알림톡-청구알림발송",
        "type": IncidentType.FORMAT_ERROR,
        "severity": "warning",
        "summary": "알림톡 템플릿 변수 길이 초과로 422 다발",
        "root_cause": "고객명 최대 10자 제한이 카카오 정책 변경으로 8자로 축소됨",
        "resolution": "발송 전 trim() 어댑터 추가, 카카오 비즈메시지 변경 공지 메일링 가입",
        "days_ago": 5,
    },
    {
        "interface_name": "토스페이먼츠-자동이체",
        "type": IncidentType.HIGH_FAILURE_RATE,
        "severity": "critical",
        "summary": "월정기 출금일 잔액부족 외 비율 12% 도달",
        "root_cause": "Toss 측 일시적 DB lag 으로 빌링키 검증이 지연되어 false negative 발생",
        "resolution": "Toss 운영팀과 핫라인, 30분 후 자동 회복. 실패건은 D+1 자동 재시도 잡으로 보완",
        "days_ago": 2,
    },
]


def _classify_log(
    success: bool, latency: int, profile: dict
) -> tuple[CallStatus, int | None, str | None, str | None, str | None]:
    """Map a synthetic outcome → (status, http_status, error_msg, error_type, error_trace)."""
    if success:
        return CallStatus.SUCCESS, 200, None, None, None
    kind = random.choices(["TIMEOUT", "AUTH", "FORMAT", "SERVER"], weights=[1, 1, 1, 4], k=1)[0]
    if kind == "TIMEOUT":
        return (
            CallStatus.TIMEOUT,
            None,
            "Read timed out after 10s",
            "ReadTimeout",
            (
                'Traceback (most recent call last):\n'
                '  File "/app/app/services/executor.py", line 78, in _exec_rest\n'
                '    resp = await client.request(method, itf.endpoint, **req_kwargs)\n'
                '  File "/usr/local/lib/python3.12/site-packages/httpx/_client.py", line 1576, in request\n'
                '    return await self.send(request, auth=auth, follow_redirects=follow_redirects)\n'
                '  File "/usr/local/lib/python3.12/site-packages/httpx/_client.py", line 1665, in send\n'
                '    response = await self._send_handling_auth(...)\n'
                '  File "/usr/local/lib/python3.12/site-packages/httpcore/_async/connection.py", line 99, in handle_async_request\n'
                '    raise exc\n'
                'httpx.ReadTimeout: timed out\n'
            ),
        )
    if kind == "AUTH":
        return (
            CallStatus.AUTH_ERROR,
            401,
            "401 Unauthorized — access token expired or invalid",
            "HTTPStatusError",
            (
                'Traceback (most recent call last):\n'
                '  File "/app/app/services/executor.py", line 82, in _exec_rest\n'
                '    resp.raise_for_status()\n'
                '  File "/usr/local/lib/python3.12/site-packages/httpx/_models.py", line 763, in raise_for_status\n'
                '    raise HTTPStatusError(message, request=request, response=self)\n'
                'httpx.HTTPStatusError: Client error \'401 Unauthorized\' for url \'%s\'\n'
                'For more info: https://httpwg.org/specs/rfc9110.html#status.401\n'
            )
            % profile.get("endpoint_short", "https://api.example.com"),
        )
    if kind == "FORMAT":
        return (
            CallStatus.FORMAT_ERROR,
            422,
            "422 Unprocessable Entity — request schema mismatch",
            "ValidationError",
            (
                'Traceback (most recent call last):\n'
                '  File "/app/app/services/executor.py", line 96, in execute_interface\n'
                '    payload = schema.validate(req_body)\n'
                '  File "/app/app/services/schema_validator.py", line 41, in validate\n'
                '    raise ValidationError(errors)\n'
                'app.errors.ValidationError: [{"path":"$.amount","msg":"must be > 0"}]\n'
            ),
        )
    code = random.choice([500, 502, 503])
    return (
        CallStatus.SERVER_ERROR,
        code,
        f"{code} {('Internal Server Error', 'Bad Gateway', 'Service Unavailable')[[500,502,503].index(code)]} from upstream",
        "HTTPStatusError",
        (
            f'Traceback (most recent call last):\n'
            f'  File "/app/app/services/executor.py", line 82, in _exec_rest\n'
            f'    resp.raise_for_status()\n'
            f'  File "/usr/local/lib/python3.12/site-packages/httpx/_models.py", line 763, in raise_for_status\n'
            f'    raise HTTPStatusError(message, request=request, response=self)\n'
            f'httpx.HTTPStatusError: Server error \'{code}\' for url\n'
        ),
    )


# ---------- realistic payload generators -------------------------------------
# Each function returns (request_body, success_response_body).

def _hash_pii(prefix: str = "") -> str:
    """Synthetic SHA-like hash for masked PII fields."""
    return prefix + "".join(random.choices("0123456789abcdef", k=16))


def _claim_no() -> str:
    return f"CLM{NOW.strftime('%Y')}{random.randint(100000, 999999)}"


def _trace_id() -> str:
    return f"trace-{random.randint(10**11, 10**12 - 1):x}"


def _payloads_for(name: str) -> tuple[dict | None, dict | None]:
    if "실손중복청구" in name:
        req = {
            "resident_no_hash": _hash_pii("rrn-"),
            "claim_no": _claim_no(),
            "treatment_date": "2026-04-15",
            "hospital_code": f"H{random.randint(10000, 99999)}",
            "amount_won": random.choice([45000, 120000, 230000, 580000]),
        }
        resp = {
            "is_duplicate": random.random() < 0.08,
            "similar_claims": [],
            "checked_at": NOW.isoformat(),
            "trace_id": _trace_id(),
        }
        return req, resp
    if "CB조회" in name:
        req = {"rrn_hash": _hash_pii("rrn-"), "query_type": "PRE_CONTRACT", "request_id": _trace_id()}
        resp = {
            "credit_score": random.randint(620, 950),
            "grade": random.choice(["A1", "A2", "B1", "B2", "C1"]),
            "last_updated": "2026-04-22",
        }
        return req, resp
    if "요양기관조회" in name:
        req = {"as_of_date": NOW.strftime("%Y-%m-%d"), "page": 1, "size": 1000}
        resp = {"count": 98421, "page": 1, "next_cursor": "eyJvZmZzZXQiOjEwMDB9"}
        return req, resp
    if "운전면허확인" in name:
        req = {
            "name": "홍길동",
            "license_no": f"{random.randint(11, 28)}-{random.randint(10, 99):02d}-{random.randint(100000, 999999)}-{random.randint(10, 99)}",
            "rrn_front": "900101",
        }
        resp = {"valid": True, "type": "1종보통", "expires_at": "2030-09-30"}
        return req, resp
    if "사업자상태조회" in name:
        req = {"business_no": f"{random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10000, 99999)}"}
        resp = {"status": "01", "status_name": "계속사업자", "tax_type": "일반과세자"}
        return req, resp
    if "마이데이터" in name:
        req = {"consent_id": _hash_pii("cnst-"), "scope": ["bank.account", "card.tx"], "from": "2026-01-01"}
        resp = {
            "accounts": random.randint(2, 7),
            "cards": random.randint(1, 4),
            "total_assets_won": random.randint(5_000_000, 320_000_000),
            "fetched_at": NOW.isoformat(),
        }
        return req, resp
    if "전산사고보고" in name:
        req = {
            "incident_id": f"INC{NOW.strftime('%Y%m')}{random.randint(100, 999)}",
            "severity": random.choice(["WARNING", "CRITICAL"]),
            "occurred_at": NOW.isoformat(),
            "summary": "외부 인터페이스 5xx 다발",
        }
        resp = {"received": True, "report_no": f"FSS-{random.randint(10000, 99999)}"}
        return req, resp
    if "알림톡" in name:
        req = {
            "template_code": "INSURE_PAYMENT_DONE_v3",
            "to": _hash_pii("phone-"),
            "vars": {"name": "홍**", "amount": "1,250,000"},
        }
        resp = {"message_id": _trace_id(), "status": "ACCEPTED"}
        return req, resp
    if "토스" in name or "자동이체" in name:
        req = {
            "billing_key": _hash_pii("bk-"),
            "amount": random.choice([45000, 89000, 120000, 230000]),
            "order_id": f"ORD{random.randint(10**9, 10**10 - 1)}",
        }
        resp = {"approved_at": NOW.isoformat(), "card_company": random.choice(["KB", "신한", "삼성", "현대"])}
        return req, resp
    if "차량시세" in name:
        req = {"queue": "CARMARKET.OUT", "consumer_group": "noahub-cg-01"}
        resp = {
            "model": random.choice(["현대 그랜저 IG", "기아 K5", "BMW 520i", "벤츠 E300"]),
            "year": random.randint(2018, 2025),
            "estimated_won": random.randint(15_000_000, 75_000_000),
        }
        return req, resp
    if "일일보험료정산" in name:
        records = random.randint(80000, 150000)
        req = {
            "job_type": "DAILY_PREMIUM_SETTLEMENT",
            "input_dir": "/sftp/in",
            "result_dir": "/sftp/out/kidi",
            "as_of_date": (NOW - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        resp = {
            "job_type": "DAILY_PREMIUM_SETTLEMENT",
            "records_processed": records,
            "errors": random.randint(0, records // 500),
            "result_file": f"/sftp/out/kidi/premium_{(NOW - timedelta(days=1)).strftime('%Y%m%d')}.csv",
            "total_won": random.randint(2_000_000_000, 6_500_000_000),
            "input_files_picked": random.randint(2, 4),
        }
        return req, resp
    if "자동이체결과수신" in name:
        records = random.randint(20000, 65000)
        req = {
            "job_type": "AUTO_DEBIT_RESULT_INGEST",
            "remote_dir": "/outgoing/AUTO_DEBIT_RESULT",
            "filename_pattern": "ADR_*.csv",
        }
        resp = {
            "job_type": "AUTO_DEBIT_RESULT_INGEST",
            "files_picked": random.randint(1, 3),
            "records_processed": records,
            "success_records": int(records * 0.973),
            "failure_records": int(records * 0.027),
            "ingested_at": NOW.isoformat(),
        }
        return req, resp
    return None, None


def _failure_response(profile_status: CallStatus) -> dict | None:
    if profile_status == CallStatus.AUTH_ERROR:
        return {
            "headers": {
                "www-authenticate": 'Bearer realm="api", error="invalid_token", error_description="The access token expired"',
                "content-type": "application/json",
            },
            "body": {"error": "INVALID_TOKEN", "message": "access token expired"},
        }
    if profile_status == CallStatus.FORMAT_ERROR:
        return {
            "headers": {"content-type": "application/json"},
            "body": {
                "error": "VALIDATION_FAILED",
                "fields": [{"path": "$.amount", "msg": "must be > 0"}],
            },
        }
    if profile_status == CallStatus.SERVER_ERROR:
        return {
            "headers": {"content-type": "application/json", "x-trace-id": _trace_id()},
            "body": {"error": "UPSTREAM_5XX", "trace_id": _trace_id()},
        }
    return None


def _generate_logs(itf: Interface, profile: dict, db) -> int:
    """Generate one row per simulated call across the WINDOW_DAYS window."""
    start = NOW - timedelta(days=WINDOW_DAYS)
    duration_s = WINDOW_DAYS * 24 * 3600
    expected_total = max(1, int(profile["rps"] * duration_s / 60.0))  # rps is per-minute, soft scale
    if expected_total > 1500:  # cap to keep seed runtime reasonable
        expected_total = 1500

    rows = []
    mean, std = profile["latency_ms"]
    base_fail = profile["fail_rate"]
    req_template, resp_template = _payloads_for(itf.name)

    # 30% chance an interface has a "bad window" in the last 24h
    bad_start = NOW - timedelta(hours=random.randint(2, 22)) if random.random() < 0.3 else None
    bad_end = bad_start + timedelta(hours=random.randint(1, 4)) if bad_start else None

    for _ in range(expected_total):
        # uniform-ish timestamp across the window
        ts = start + timedelta(seconds=random.uniform(0, duration_s))
        # diurnal: weight more business-hour traffic (KST 9-18 ≈ UTC 0-9)
        if random.random() < 0.4:  # 40% reroll into business hours
            day_offset = random.randint(0, WINDOW_DAYS - 1)
            ts = (start + timedelta(days=day_offset)).replace(
                hour=random.randint(0, 9), minute=random.randint(0, 59),
                second=random.randint(0, 59), microsecond=0,
            )
        in_bad = bad_start is not None and bad_start <= ts <= bad_end

        fail_rate = base_fail * (5 if in_bad else 1)
        latency_mean = mean * (2.5 if in_bad else 1)
        latency = max(5, int(random.lognormvariate(math.log(max(latency_mean, 1)), 0.4)))
        success = random.random() > min(fail_rate, 0.6)
        status, http_status, err, err_type, err_trace = _classify_log(success, latency, profile)

        # snapshot a fresh body per row so request varies (different claim_no etc.)
        req_body, succ_body = _payloads_for(itf.name)
        rows.append(
            CallLog(
                interface_id=itf.id,
                request={
                    "method": itf.method,
                    "endpoint": itf.endpoint,
                    "headers": {"X-Request-ID": _trace_id(), "Content-Type": "application/json"},
                    "body": req_body,
                },
                response=succ_body if success else _failure_response(status),
                status=status,
                http_status=http_status,
                duration_ms=latency,
                error_message=err,
                error_type=err_type,
                error_trace=err_trace,
                triggered_by="schedule" if itf.schedule_cron else "manual",
                called_at=ts,
            )
        )
    db.add_all(rows)
    return len(rows)


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        # wipe — demo dataset is replaced wholesale on each run
        db.execute(delete(CallLog))
        db.execute(delete(Incident))
        db.execute(delete(SlaTarget))
        db.execute(delete(Interface))
        db.commit()

        name_to_itf: dict[str, Interface] = {}
        for spec in SEED_INTERFACES:
            itf = Interface(
                name=spec["name"],
                organization=spec["organization"],
                description=spec["description"],
                protocol=spec["protocol"],
                endpoint=spec["endpoint"],
                method=spec["method"],
                schedule_cron=spec["schedule_cron"],
                auth_type=spec["auth_type"],
                request_template=spec.get("request_template"),
                enabled=True,
            )
            db.add(itf)
            db.flush()
            name_to_itf[spec["name"]] = itf

            uptime, resp = spec["sla"]
            db.add(SlaTarget(interface_id=itf.id, uptime_target=uptime, response_ms_target=resp))

        db.commit()
        print(f"✓ {len(name_to_itf)} interfaces + SLA targets")

        total_logs = 0
        for spec in SEED_INTERFACES:
            itf = name_to_itf[spec["name"]]
            total_logs += _generate_logs(itf, spec, db)
        db.commit()
        print(f"✓ {total_logs} call logs ({WINDOW_DAYS}일치)")

        for inc in SEED_INCIDENTS:
            itf = name_to_itf[inc["interface_name"]]
            detected = NOW - timedelta(days=inc["days_ago"], hours=random.randint(0, 23))
            resolved = detected + timedelta(minutes=random.randint(20, 240))
            db.add(
                Incident(
                    interface_id=itf.id,
                    type=inc["type"],
                    severity=inc["severity"],
                    summary=inc["summary"],
                    root_cause=inc["root_cause"],
                    resolution=inc["resolution"],
                    detected_at=detected,
                    resolved_at=resolved,
                )
            )
        db.commit()
        print(f"✓ {len(SEED_INCIDENTS)} resolved incidents (RAG seed)")

        print("\n시드 완료. http://localhost:5173 에서 새로고침해서 확인하세요.")
    finally:
        db.close()


if __name__ == "__main__":
    main()