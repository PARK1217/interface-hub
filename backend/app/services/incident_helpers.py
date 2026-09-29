"""incident ↔ call_log 연결 공통 헬퍼.

incidents 라우트와 detector 의 자동 해결 경로 양쪽에서 동일 로직으로
"이 incident 에 어떤 call_logs 가 묶이는지" 판단하도록 한 곳에 모음.
양쪽 룰이 어긋나면 운영자가 incident 다이얼로그에서 본 N건과 detector 가
처리하는 N건이 달라지는 골치아픈 버그가 생김.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import now_kst
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus
from app.models.incident import IncidentType


# detector._STATUS_TO_INCIDENT 의 역매핑 — incident 유형별로 어떤
# CallStatus 가 "관련" 으로 묶이는지.
_INCIDENT_TO_STATUSES: dict[IncidentType, list[CallStatus]] = {
    IncidentType.TIMEOUT: [CallStatus.TIMEOUT],
    IncidentType.AUTH_ERROR: [CallStatus.AUTH_ERROR],
    IncidentType.FORMAT_ERROR: [CallStatus.FORMAT_ERROR],
    IncidentType.SERVER_ERROR: [CallStatus.SERVER_ERROR],
    IncidentType.UNKNOWN: [CallStatus.FAILURE],
    IncidentType.SLOW_RESPONSE: [],  # 특수: status 가 아니라 duration_ms 로 필터
    IncidentType.HIGH_FAILURE_RATE: [s for s in CallStatus if s != CallStatus.SUCCESS],
}


def related_log_query(incident: Incident, interface: Interface | None):
    """이 incident 에 묶인 call_logs 를 고르는 WHERE 절 빌더."""
    end = incident.resolved_at or now_kst()
    base = (
        select(CallLog)
        .where(CallLog.interface_id == incident.interface_id)
        .where(CallLog.called_at >= incident.detected_at)
        .where(CallLog.called_at <= end)
    )
    if incident.type == IncidentType.SLOW_RESPONSE:
        threshold = (
            (interface.response_ms_threshold if interface else None)
            or get_settings().default_response_ms_threshold
        )
        base = base.where(CallLog.duration_ms > threshold)
    else:
        statuses = _INCIDENT_TO_STATUSES.get(incident.type, [])
        if statuses:
            base = base.where(CallLog.status.in_(statuses))
    return base


def mark_related_handled(
    db: Session, incident: Incident, interface: Interface | None
) -> int:
    """이 incident 에 묶인 미처리 call_logs 를 모두 ``is_reprocessed=True`` 로 마킹.

    incident 가 닫힐 때마다 호출 (수동 해결 / 자동 해결 둘 다). 운영자가
    incident 닫은 뒤에도 호출 로그 페이지 가서 ↻ 재처리 다시 눌러서 외부
    기관에 중복 호출 가는 헛점 방지. 마킹된 행은 UI 에서 ↻ 버튼이
    사라짐 (canRetry → false).

    반환: 마킹된 행 수.
    """
    # id 만 조회 — 오래 열린 incident 는 관련 행이 수만 건이라 전체 행 로딩 시 느려짐
    log_ids = list(db.scalars(
        related_log_query(incident, interface)
        .where(CallLog.is_reprocessed.is_(False))
        .with_only_columns(CallLog.id)
    ).all())
    if not log_ids:
        return 0
    db.execute(update(CallLog).where(CallLog.id.in_(log_ids)).values(is_reprocessed=True))
    return len(log_ids)
