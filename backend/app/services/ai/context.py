"""Intent 별 LLM 컨텍스트 빌더 (Phase B.8.8).

stats_query / config_query 등 메타 질문일 때, 과거 incident 검색 대신
실제 DB 의 현재 운영 데이터를 markdown 표로 직렬화해 LLM 프롬프트에 주입.
이 컨텍스트만 가지고 LLM 이 답하므로 환각(hallucination) 방지.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.time import now_kst
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus


def build_stats_context(db: Session, *, days: int = 7, top_n: int = 10) -> str:
    """최근 N일 인터페이스별 호출/실패/실패율 + 미해결 incident + 음소거 현황.

    LLM 에 "이 데이터만 보고 답해" 라는 컨텍스트로 사용. 사용자가 운영 통계
    질문 ("지금 가장 잦은 인터페이스") 했을 때 정확한 답이 가능하도록.
    """
    since = now_kst() - timedelta(days=days)

    # 인터페이스별 호출/실패 집계
    failure_expr = case((CallLog.status != CallStatus.SUCCESS, 1), else_=0)
    rows = db.execute(
        select(
            Interface.id,
            Interface.name,
            Interface.organization,
            Interface.protocol,
            Interface.deleted_at,
            Interface.muted_until,
            func.count(CallLog.id).label("total"),
            func.coalesce(func.sum(failure_expr), 0).label("failures"),
        )
        .join(CallLog, CallLog.interface_id == Interface.id)
        .where(CallLog.called_at >= since)
        .group_by(Interface.id)
        .order_by(func.coalesce(func.sum(failure_expr), 0).desc())
        .limit(top_n)
    ).all()

    lines: list[str] = [
        f"## 최근 {days}일 인터페이스 운영 통계 (KST 기준, {now_kst().isoformat()} 시점)",
        "",
        "### 실패 건수 Top 10 인터페이스",
        "| 순위 | 인터페이스 | 기관 | 프로토콜 | 호출수 | 실패수 | 실패율 | 비고 |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    if not rows:
        lines.append("| - | (집계 데이터 없음) | - | - | 0 | 0 | 0% | |")
    for i, r in enumerate(rows, 1):
        rate = (r.failures / r.total * 100) if r.total else 0.0
        notes = []
        if r.deleted_at:
            notes.append("📦 보관됨")
        if r.muted_until and r.muted_until > now_kst():
            notes.append("🔕 음소거중")
        lines.append(
            f"| {i} | {r.name} | {r.organization or '-'} | "
            f"{r.protocol.value if hasattr(r.protocol, 'value') else r.protocol} | "
            f"{r.total} | {r.failures} | {rate:.1f}% | {' '.join(notes) or '-'} |"
        )

    # 미해결 incident 인터페이스별 카운트
    open_incidents = db.execute(
        select(
            Interface.name,
            func.count(Incident.id).label("cnt"),
        )
        .join(Incident, Incident.interface_id == Interface.id)
        .where(Incident.resolved_at.is_(None))
        .group_by(Interface.id)
        .order_by(func.count(Incident.id).desc())
        .limit(10)
    ).all()
    lines += [
        "",
        "### 현재 미해결 장애 (인터페이스별 카운트)",
    ]
    if open_incidents:
        lines.append("| 인터페이스 | 미해결 건수 |")
        lines.append("|---|---:|")
        for r in open_incidents:
            lines.append(f"| {r.name} | {r.cnt} |")
    else:
        lines.append("- 미해결 장애 없음 ✅")

    # 음소거 중인 인터페이스
    muted = db.scalars(
        select(Interface)
        .where(Interface.muted_until.is_not(None))
        .where(Interface.muted_until > now_kst())
    ).all()
    if muted:
        lines += [
            "",
            "### 현재 알림 음소거 중 (toast/Slack/Email skip)",
        ]
        for itf in muted:
            lines.append(f"- {itf.name} ({itf.muted_until.isoformat()} 까지)")

    return "\n".join(lines)


def build_config_context(db: Session, *, top_n: int = 30) -> str:
    """등록된 인터페이스 메타 정보 (config_query 답변용)."""
    itfs = db.scalars(
        select(Interface)
        .where(Interface.deleted_at.is_(None))
        .order_by(Interface.id.desc())
        .limit(top_n)
    ).all()
    lines: list[str] = [
        f"## 등록된 인터페이스 ({len(itfs)}건, 보관 제외)",
        "",
        "| ID | 이름 | 기관 | 프로토콜 | 메서드 | 엔드포인트 | 스케줄 | 활성 | 시크릿 |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for i in itfs:
        proto = i.protocol.value if hasattr(i.protocol, "value") else i.protocol
        lines.append(
            f"| {i.id} | {i.name} | {i.organization or '-'} | {proto} | "
            f"{i.method or '-'} | {i.endpoint[:60]}{'…' if len(i.endpoint) > 60 else ''} | "
            f"{i.schedule_cron or '수동'} | {'✅' if i.enabled else '⏸'} | "
            f"{'🔑' if i.auth_secret else '-'} |"
        )
    return "\n".join(lines)