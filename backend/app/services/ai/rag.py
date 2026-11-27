"""AI 분석 어시스턴트 — 응답 형식·가드·질의 로그 헬퍼 + RagService(그래프 진입점).

에이전트 전환 후 흐름은 app/agents/graph.py (LangGraph) 가 맡습니다:
  앞단 가드(짧은 질문 · 24시간 실패 재사용 · Redis 캐시) → 안내 에이전트 → 기능 에이전트 → 마무리
이 모듈에는 그 흐름이 함께 쓰는 정해진 코드(응답 dict, 템플릿 답, 실패 재사용, ai_query_logs 기록)를 둡니다.
문구·판정 기준은 전환 전과 같습니다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agents.retriever import NO_MATCH_THRESHOLD, analysis_note, top1_scores
from app.core.time import now_kst
from app.models import AiQueryLog, User
from app.services.ai.intent import Intent
from app.services.ai.llm import current_model, current_provider, is_configured

log = logging.getLogger("noahub.ai.rag")

__all__ = ["NO_MATCH_THRESHOLD", "RagService", "llm_status", "score_questions"]

_NO_MATCH_THRESHOLD = NO_MATCH_THRESHOLD  # 내부 호환

# 같은 질문의 분석 실패 이력이 이 시간 내에 있으면 재호출하지 않고
# 저장된 안내를 그대로 반환. 하루 지나면 인프라 상태가 바뀌었을 수 있으니 다시 허용.
_FAILURE_REUSE_HOURS = 24

# 분석 실패 유형 — 두 그룹:
# (1) 사용자 입력 결함: 같은 질문은 영원히 같은 결과 → DB 기록도 안 함, 매번 즉시 안내
#     (need_target: RCA 대상 인터페이스를 질문에서 찾지 못해 되물은 경우 — 전환 후 추가)
_USER_INPUT_FAILURES = {"empty_question", "no_match", "need_target"}
# (2) 환경 결함: 운영자가 고치면 자동 해제 → DB 기록 + 24h 재사용 가드
_ENVIRONMENT_FAILURES = {"no_history", "scikit_missing", "llm_failed"}
_FAILED_OUTCOMES = _ENVIRONMENT_FAILURES

_analysis_note = analysis_note  # 내부 호환


def _excerpt(text: str, n: int = 500) -> str:
    if not text:
        return ""
    s = text.strip()
    return s if len(s) <= n else s[:n] + "…"


def make_response(
    *,
    mode: str,
    provider: str | None,
    model: str | None,
    answer: str,
    cases: list[dict],
    intent: Intent = "general",
    outcome: str = "success",
    llm_error: dict | None = None,
    llm_attempts: list[dict] | None = None,
    analysis_note: dict | None = None,
    cached: bool = False,
    repeated_failure: bool = False,
) -> dict:
    return {
        "mode": mode,
        "provider": provider,
        "model": model,
        "answer": answer,
        "similar_cases": cases,
        "intent": intent,
        "outcome": outcome,
        "llm_error": llm_error,
        "llm_attempts": llm_attempts or [],
        "analysis_note": analysis_note,
        "cached": cached,
        "repeated_failure": repeated_failure,
    }


def template_answer(cases: list[dict]) -> str:
    """LLM 없을 때 과거 사례 템플릿 답."""
    if not cases:
        return "분석할 과거 장애 이력이 아직 없습니다. 장애 발생 후 다시 시도하세요."
    top = cases[0]
    lines = [
        "[Fallback 모드 — LLM 미설정, TF-IDF 키워드 유사도 매칭]",
        "",
        f"가장 유사한 과거 사례 (유사도 {top['score']:.2f}):",
        top["content"],
        "",
        "권장 조치:",
        "  · 위 사례의 root cause / 조치 절차가 현재 상황과 일치하는지 먼저 확인",
        "  · 인터페이스별 임계값 / 인증서 만료 / 상대방 측 공지를 동시에 점검",
        "  · 동일 root cause로 판명되면 위 사례의 resolution 그대로 적용 가능",
    ]
    if len(cases) > 1:
        lines.append("")
        lines.append(f"추가 후보: {len(cases) - 1}건 더 있음 (아래 카드 참조)")
    return "\n".join(lines)


def find_recent_failure(db: Session, question_hash: str) -> AiQueryLog | None:
    """같은 question_hash 의 최근 24h **환경 결함** 실패 로그."""
    since = now_kst() - timedelta(hours=_FAILURE_REUSE_HOURS)
    return db.scalar(
        select(AiQueryLog)
        .where(AiQueryLog.question_hash == question_hash)
        .where(AiQueryLog.outcome.in_(list(_FAILED_OUTCOMES)))
        .where(AiQueryLog.asked_at >= since)
        .order_by(desc(AiQueryLog.asked_at))
        .limit(1)
    )


def replay_failure(prev_outcome: str, prev_asked_at) -> dict:
    """이전 실패 로그 → 사용자에게 돌려줄 응답 dict. LLM/DB 검색 전부 생략."""
    note_map = {
        "no_history": analysis_note(
            "no_history",
            "최근 분석 실패한 질문",
            "같은 질문이 24시간 안에 '분석할 과거 사례 없음' 으로 처리된 이력이 있습니다.",
            "장애가 한 건도 resolved 된 적이 없는 상태입니다. 시드 데이터 또는 실제 장애 처리 후 재시도.",
        ),
        "scikit_missing": analysis_note(
            "scikit_missing",
            "최근 분석 실패한 질문",
            "같은 질문이 24시간 안에 '검색 엔진 미설치' 로 처리된 이력이 있습니다.",
            "backend 컨테이너에 scikit-learn 설치 후 재기동 필요.",
        ),
        "llm_failed": analysis_note(
            "no_match",  # UI 아이콘 재사용
            "최근 LLM 호출 실패한 질문",
            "같은 질문이 24시간 안에 LLM 호출 실패로 처리된 이력이 있습니다.",
            "AI_PROVIDER 키 / 네트워크를 점검한 뒤 다시 시도하거나, 질문을 수정하세요.",
        ),
    }
    note = note_map.get(prev_outcome, analysis_note(
        prev_outcome, "최근 분석 실패한 질문",
        f"같은 질문이 24시간 안에 '{prev_outcome}' 사유로 실패했습니다.",
        "질문을 다르게 표현하거나 24시간 후 다시 시도하세요.",
    ))
    return make_response(
        mode="fallback", provider="fallback", model=None,
        answer=(
            f"이 질문은 24시간 안에 이미 분석 불가로 판정되었습니다.\n"
            f"(이전 사유: {prev_outcome} · {prev_asked_at.isoformat() if prev_asked_at else '-'})\n\n"
            f"같은 질문을 다시 보내도 동일한 결과가 나오므로 LLM/DB 호출을 건너뛰었습니다.\n"
            f"다르게 표현해 재시도하세요."
        ),
        cases=[],
        intent="general", outcome=prev_outcome,
        analysis_note=note,
        repeated_failure=True,
    )


def log_query(
    db: Session,
    *,
    question: str,
    question_hash: str,
    result: dict,
    actor_user_id: int | None,
    actor_username: str | None,
    hit_cache: bool,
    run_id: uuid.UUID | None = None,
) -> None:
    """ai_query_logs 1행. 사용자 입력 결함 (empty_question / no_match / need_target) 은 저장 안 함."""
    outcome = result.get("outcome") or "success"
    if outcome in _USER_INPUT_FAILURES:
        return
    try:
        cases = result.get("similar_cases") or []
        err = result.get("llm_error") or {}
        db.add(AiQueryLog(
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            question=question,
            question_hash=question_hash,
            mode=result.get("mode") or "fallback",
            provider=result.get("provider"),
            model=result.get("model"),
            llm_error_kind=err.get("kind") if err else None,
            hit_cache=hit_cache,
            similarity_top=cases[0]["score"] if cases else None,
            response_excerpt=_excerpt(result.get("answer", ""), 500),
            outcome=outcome,
            run_id=run_id,
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("ai_query_log 기록 실패 — 본 응답은 정상")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


class RagService:
    """/ai/ask 진입점. 실제 흐름은 LangGraph 그래프 (app/agents/graph.py)."""

    def __init__(self, db: Session | None = None) -> None:
        # 요청 세션은 인증에만 쓰고, 그래프 안의 DB 작업은 작업마다 새 세션을 씀 (async 안 sync 세션 정리)
        self.db = db

    async def ask(self, question: str, top_k: int = 3, *, actor: User | None = None) -> dict:
        from app.agents.graph import run_ask

        return await run_ask(
            question, top_k=top_k,
            actor_user_id=actor.id if actor else None,
            actor_username=actor.username if actor else None,
        )

    async def analyze_incident(self, incident_id: int, *, actor: User | None = None) -> dict:
        from app.agents.graph import run_rca

        return await run_rca(
            incident_id,
            actor_user_id=actor.id if actor else None,
            actor_username=actor.username if actor else None,
        )


def llm_status() -> dict:
    return {
        "configured": is_configured(),
        "provider": current_provider(),
        "model": current_model(),
    }


def score_questions(db: Session, questions: list[str]) -> list[float]:
    """여러 후보 질문의 Top-1 유사도를 일괄 계산 (/suggestions 사전 필터). 검색 로직은 retriever 와 같음."""
    return top1_scores(db, questions)
