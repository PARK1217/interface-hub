"""에이전트 도구 — 기존 함수를 LangChain @tool 로 감싼 것 (로직은 옮기지 않음).

규칙
- 모든 도구는 **읽기 전용**: readonly_session() 이 ORM 쓰기(before_flush)와 SELECT 가 아닌 SQL 을 막습니다.
- 재처리·실행·해결 처리·음소거·시크릿 조회·알림 발송 같은 함수는 어떤 도구에도 넣지 않습니다
  (config/agents.yaml forbidden_tools, tests/test_agent_tools_guard.py 가 검사).
- 도구 결과: content(LLM 에 넣는 글) + artifact(코드가 쓰는 구조화 값). 요청·응답 본문·시크릿은 넣지 않습니다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import desc, event, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.time import now_kst
from app.models import CallLog, Incident, Interface, SlaTarget
from app.services.ai.anomaly import score_anomalies
from app.services.ai.context import build_config_context, build_stats_context
from app.services.incident_helpers import related_log_query

# 테스트에서 sqlite 세션으로 바꿔 끼움
SESSION_FACTORY = SessionLocal


class ReadOnlyViolation(PermissionError):
    """에이전트 도구가 DB 에 쓰려고 함."""


@contextmanager
def readonly_session() -> Iterator[Session]:
    db = SESSION_FACTORY()

    def _block_flush(session, flush_context, instances):  # noqa: ARG001
        if session.new or session.dirty or session.deleted:
            raise ReadOnlyViolation("에이전트 도구는 읽기 전용입니다 (ORM 쓰기 차단)")

    def _block_write_sql(state):
        if state.is_select:
            return
        sql = str(state.statement).lstrip().lower()
        if not (sql.startswith("select") or sql.startswith("with")):
            raise ReadOnlyViolation("에이전트 도구는 읽기 전용입니다 (SELECT 외 SQL 차단)")

    event.listen(db, "before_flush", _block_flush)
    event.listen(db, "do_orm_execute", _block_write_sql)
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _enum(v) -> str | None:
    return v.value if hasattr(v, "value") else (str(v) if v is not None else None)


# ---------- 통계·설정 ---------------------------------------------------------------

@tool(response_format="content_and_artifact")
def get_ops_stats(days: int = 7, top_n: int = 10) -> tuple[str, str]:
    """최근 N일 인터페이스별 호출·실패·실패율·시간대 분포·추이·미해결 장애를 markdown 표로 돌려줍니다.
    "지금 장애가 잦은 인터페이스", "실패율 Top N" 같은 운영 통계 질문에 씁니다."""
    with readonly_session() as db:
        md = build_stats_context(db, days=days, top_n=top_n)
    return md, md


@tool(response_format="content_and_artifact")
def get_interface_config(top_n: int = 30) -> tuple[str, str]:
    """등록된 인터페이스 메타(이름·기관·프로토콜·메서드·엔드포인트·스케줄·활성·시크릿 유무)를 markdown 표로
    돌려줍니다. 시크릿 값은 들어 있지 않습니다. 설정·스케줄·엔드포인트 질문에 씁니다."""
    with readonly_session() as db:
        md = build_config_context(db, top_n=top_n)
    return md, md


# ---------- 과거 사례 ---------------------------------------------------------------

@tool(response_format="content_and_artifact")
def search_similar_cases(question: str, top_k: int = 3) -> tuple[str, dict]:
    """해결된 과거 장애 중 질문과 비슷한 Top-K (TF-IDF). 장애 원인·조치 질문, 원인 분석의 참고 사례에 씁니다.
    artifact = {"cases": [...], "note": no_history/no_match/scikit_missing 안내 또는 None}."""
    from app.agents.retriever import search_cases

    with readonly_session() as db:
        cases, note = search_cases(db, question, top_k)
    content = "\n\n---\n\n".join(f"[incident #{c['incident_id']}]\n{c['content']}" for c in cases)
    return content or "(과거 사례 없음)", {"cases": cases, "note": note}


# ---------- 원인 분석(RCA) ----------------------------------------------------------

def _interface_tokens(itf: Interface) -> set[str]:
    raw = [itf.name, *itf.name.split("-"), itf.organization or ""]
    raw += re.findall(r"\(([^)]+)\)", itf.organization or "")
    raw += [re.sub(r"\s*\(.*\)", "", itf.organization or "")]
    return {t.strip().lower() for t in raw if t and len(t.strip()) >= 2}


@tool(response_format="content_and_artifact")
def find_rca_target(question: str) -> tuple[str, dict]:
    """질문에 나온 인터페이스 이름·기관명으로 원인 분석 대상(가장 최근 열린 장애)을 찾습니다.
    artifact = {"incident_id": int|None, "interface_ids": [...]}. 못 찾으면 incident_id=None (되묻기)."""
    q = (question or "").lower()
    with readonly_session() as db:
        itfs = db.scalars(select(Interface).where(Interface.deleted_at.is_(None))).all()
        scored = [(sum(1 for t in _interface_tokens(i) if t in q), i) for i in itfs]
        best = max((s for s, _ in scored), default=0)
        matched = [i for s, i in scored if s == best and s > 0]
        if not matched:
            return "질문에서 인터페이스를 찾지 못함", {"incident_id": None, "interface_ids": []}
        ids = [i.id for i in matched]
        names = ", ".join(i.name for i in matched)
        inc = db.scalar(
            select(Incident).where(Incident.interface_id.in_(ids)).where(Incident.resolved_at.is_(None))
            .order_by(desc(Incident.detected_at)).limit(1)
        )
        found = (inc.id, inc.interface_id) if inc is not None else None
    if found is None:
        return f"대상 인터페이스({names})에 열린 장애가 없음", {"incident_id": None, "interface_ids": ids}
    return f"대상 장애 #{found[0]} (interface_id={found[1]})", {"incident_id": found[0], "interface_ids": ids}


@tool(response_format="content_and_artifact")
def get_incident_detail(incident_id: int) -> tuple[str, dict]:
    """장애 1건의 유형·심각도·요약·감지 시각과 해당 인터페이스 설정(임계치·재시도·타임아웃)을 돌려줍니다.
    시크릿 값은 넣지 않습니다. 원인 분석의 첫 단계에 씁니다."""
    with readonly_session() as db:
        inc = db.get(Incident, incident_id)
        if inc is None:
            return f"incident #{incident_id} 없음", {}
        itf = db.get(Interface, inc.interface_id)
        d = {
            "incident_id": inc.id, "type": _enum(inc.type), "severity": inc.severity, "summary": inc.summary,
            "detected_at": _iso(inc.detected_at), "resolved_at": _iso(inc.resolved_at),
            "interface": None if itf is None else {
                "id": itf.id, "name": itf.name, "organization": itf.organization, "protocol": _enum(itf.protocol),
                "method": itf.method, "endpoint": (itf.endpoint or "")[:80], "enabled": itf.enabled,
                "schedule_cron": itf.schedule_cron, "timeout_seconds": itf.timeout_seconds,
                "retry_max": itf.retry_max, "response_ms_threshold": itf.response_ms_threshold,
                "failure_rate_threshold": itf.failure_rate_threshold, "muted_until": _iso(itf.muted_until),
                "has_secret": bool(itf.auth_secret), "secret_expires_at": _iso(itf.auth_secret_expires_at),
            },
        }
    i = d["interface"] or {}
    content = (
        f"장애 #{d['incident_id']} · 유형 {d['type']} · 심각도 {d['severity']} · 감지 {d['detected_at']}\n"
        f"요약: {d['summary']}\n"
        f"인터페이스: {i.get('name')} ({i.get('organization')}) {i.get('protocol')} {i.get('method')} "
        f"timeout={i.get('timeout_seconds')}s retry_max={i.get('retry_max')} "
        f"응답임계={i.get('response_ms_threshold')}ms 실패율임계={i.get('failure_rate_threshold')} "
        f"시크릿만료={i.get('secret_expires_at')}"
    )
    return content, d


@tool(response_format="content_and_artifact")
def get_recent_call_logs(incident_id: int, limit: int = 30) -> tuple[str, dict]:
    """장애에 묶인 호출 로그(incident_helpers.related_log_query 와 같은 기준) 최근 N건을 요약합니다.
    상태·HTTP 코드·소요 ms·오류 유형·시각만 넣고 요청·응답 본문은 넣지 않습니다."""
    with readonly_session() as db:
        inc = db.get(Incident, incident_id)
        if inc is None:
            return "장애 없음", {"logs": [], "total": 0}
        itf = db.get(Interface, inc.interface_id)
        rows = db.scalars(related_log_query(inc, itf).order_by(desc(CallLog.called_at)).limit(limit)).all()
        # 장애에 묶인 로그가 없으면 감지 전후 1시간 전체 로그로 맥락 제공
        if not rows:
            rows = db.scalars(
                select(CallLog).where(CallLog.interface_id == inc.interface_id)
                .where(CallLog.called_at >= inc.detected_at - timedelta(hours=1))
                .order_by(desc(CallLog.called_at)).limit(limit)
            ).all()
        logs = [{"called_at": _iso(r.called_at), "status": _enum(r.status), "http_status": r.http_status,
                 "duration_ms": r.duration_ms, "error_type": r.error_type, "attempt_count": r.attempt_count}
                for r in rows]
    by_status: dict[str, int] = {}
    for g in logs:
        by_status[g["status"]] = by_status.get(g["status"], 0) + 1
    by_http: dict[str, int] = {}
    for g in logs:
        if g["http_status"]:
            by_http[str(g["http_status"])] = by_http.get(str(g["http_status"]), 0) + 1
    avg_ms = round(sum(g["duration_ms"] for g in logs) / len(logs)) if logs else 0
    content = (
        f"관련 호출 {len(logs)}건 · 상태별 {by_status} · HTTP별 {by_http} · 평균 {avg_ms}ms\n"
        + "\n".join(f"- {g['called_at']} {g['status']} HTTP {g['http_status']} {g['duration_ms']}ms "
                    f"{g['error_type'] or ''} 시도{g['attempt_count']}" for g in logs[:10])
    )
    return content, {"logs": logs, "total": len(logs), "by_status": by_status, "by_http": by_http, "avg_ms": avg_ms}


@tool(response_format="content_and_artifact")
def get_retry_stats(interface_id: int, days: int = 7) -> tuple[str, dict]:
    """인터페이스의 최근 N일 자동 재시도 효과(재시도 발생·복구·재시도 후 실패·평균 시도)를 돌려줍니다.
    /performance/retry-effects 와 같은 집계를 씁니다."""
    from app.api.routes.performance import retry_effects

    with readonly_session() as db:
        rows = retry_effects(days=days, only_with_policy=False, db=db)
    row = next((r for r in rows if r.interface_id == interface_id), None)
    if row is None:
        return "재시도 통계 없음", {}
    d = row.model_dump(mode="json")
    content = (f"최근 {days}일 재시도: 정책 retry_max={d['retry_max']} · 전체 {d['total_calls']}건 · "
               f"재시도 발생 {d['multi_attempt_calls']}건 · 복구 {d['recovered_calls']}건 · "
               f"재시도 후 실패 {d['failed_after_retry']}건 · 평균 시도 {d['avg_attempts']:.2f}")
    return content, d


@tool(response_format="content_and_artifact")
def get_sla_status(interface_id: int, days: int = 30) -> tuple[str, dict]:
    """인터페이스의 최근 N일 SLA(가용률·평균 응답·목표 충족 여부)를 /sla/report 와 같은 계산으로 돌려줍니다."""
    from app.api.routes.sla import _compute_row

    since = now_kst() - timedelta(days=days)
    with readonly_session() as db:
        itf = db.get(Interface, interface_id)
        if itf is None:
            return "인터페이스 없음", {}
        logs = db.scalars(select(CallLog).where(CallLog.interface_id == interface_id)
                          .where(CallLog.called_at >= since)).all()
        target = db.scalar(select(SlaTarget).where(SlaTarget.interface_id == interface_id))
        d = _compute_row(itf, list(logs), target)
    content = (f"최근 {days}일 SLA: 가용률 {d['uptime_pct']}% (목표 {d['target_uptime']}%, "
               f"{'충족' if d['meets_uptime'] else '미달'}) · 평균 응답 {d['avg_response_ms']}ms "
               f"(목표 {d['target_response_ms']}ms, {'충족' if d['meets_response'] else '미달'}) · 호출 {d['total_calls']}건")
    return content, d


@tool(response_format="content_and_artifact")
def get_anomaly_score(interface_id: int) -> tuple[str, dict]:
    """IsolationForest 이상 점수 (최근 24시간 학습, 최근 1시간 평가). 음수면 이상. 데이터가 적으면 0."""
    with readonly_session() as db:
        score, is_anom = score_anomalies(db, interface_id)
    d = {"score": round(score, 4), "is_anomaly": is_anom}
    return f"이상 점수 {d['score']} ({'이상' if is_anom else '정상 범위'})", d


TOOLS: dict[str, BaseTool] = {t.name: t for t in (
    get_ops_stats, get_interface_config, search_similar_cases, find_rca_target, get_incident_detail,
    get_recent_call_logs, get_retry_stats, get_sla_status, get_anomaly_score,
)}


def tools_for(agent_id: str) -> list[BaseTool]:
    """agents.yaml 의 tools 목록 → 도구 객체. 목록에 없는 이름이면 KeyError (설정 오류를 바로 드러냄)."""
    from app.agents.config import agent_tools

    return [TOOLS[name] for name in agent_tools(agent_id)]


def artifact_of(result: Any) -> Any:
    return getattr(result, "artifact", None)
