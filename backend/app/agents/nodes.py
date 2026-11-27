"""그래프 노드 — 앞단 가드 · 안내 에이전트 · 기능 에이전트 4개 · 되묻기 · 마무리.

기능 에이전트는 "정해진 도구 호출 → LLM 1회" 노드입니다 (ReAct 루프 아님: 제공사 체인에
tool-calling 을 못 하는 HF 가 있고, 작은 모델의 도구 선택이 불안정하기 때문). 도구는 agents.yaml 에
적힌 것만 쓸 수 있고, 도구·LLM 호출 수는 max_steps 를 넘지 못합니다.

응답 문구·판정은 전환 전 RagService 의 _handle_stats / _handle_config / _handle_case_lookup 과 같습니다.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.agents.config import agent_tools, max_steps
from app.agents.llm import ainvoke_llm
from app.agents.retriever import NO_MATCH_THRESHOLD, analysis_note
from app.agents.router import decide
from app.agents.state import AgentState
from app.agents.tools import TOOLS
from app.agents.tracing import LLMCallTracker, StepRecord
from app.core import database
from app.services.ai import cache as ai_cache
from app.services.ai.llm import LLMError, current_model, current_provider, is_configured
from app.services.ai.rag import find_recent_failure, make_response, replay_failure, template_answer


class AgentStepLimit(RuntimeError):
    """에이전트가 max_steps 를 넘김."""


class StepBudget:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.limit = max_steps(agent_id)
        self.used = 0

    def spend(self, what: str) -> None:
        self.used += 1
        if self.used > self.limit:
            raise AgentStepLimit(f"{self.agent_id}: max_steps={self.limit} 초과 ({what})")


async def call_tool(state: AgentState, parent: StepRecord, budget: StepBudget, name: str, **args: Any):
    """agents.yaml 에 허용된 도구만 호출. 결과: (content, artifact)."""
    if name not in agent_tools(budget.agent_id):
        raise PermissionError(f"{budget.agent_id} 는 도구 {name} 을 쓸 수 없습니다 (agents.yaml)")
    budget.spend(name)
    rec = state["tracer"].start("tool", name, agent_id=budget.agent_id, parent=parent, input=args)
    try:
        msg = await TOOLS[name].ainvoke({"args": args, "name": name, "type": "tool_call", "id": str(uuid.uuid4())})
    except Exception as e:
        rec.finish(error=f"{type(e).__name__}: {e}")
        raise
    rec.finish(output=msg.content)
    return msg.content, msg.artifact


async def call_llm(state: AgentState, parent: StepRecord, budget: StepBudget, prompt: str, system: str):
    budget.spend("llm")
    tracker = LLMCallTracker(state["tracer"], agent_id=budget.agent_id, parent=parent)
    return await ainvoke_llm(budget.agent_id, prompt, system=system, tracker=tracker)


def _db():
    return database.SessionLocal()


# ---------- 앞단 가드 (기존 코드 그대로) ----------------------------------------------

async def guard_node(state: AgentState) -> dict:
    from app.services.ai.intent import classify_intent

    tr = state["tracer"]
    question = state["question"]
    q_stripped = (question or "").strip()

    # 0) 빈 질문 / 너무 짧은 질문 — LLM 호출 전에 차단
    step = tr.start("guard", "short_question_guard", input=question)
    if len(q_stripped) < 5:
        res = make_response(
            mode="fallback", provider="fallback", model=None,
            answer="질문이 너무 짧아 분석을 시작할 수 없습니다.",
            cases=[], intent="general", outcome="empty_question",
            analysis_note=analysis_note(
                "empty_question",
                "질문이 비어있거나 너무 짧음",
                f"입력된 질문 길이가 {len(q_stripped)}자 입니다. 최소 5자 이상 필요.",
                "어떤 인터페이스의 / 어떤 증상이 / 언제부터 발생했는지를 한 문장으로 적어주세요. "
                "예) '보험개발원 API 가 오후 3시부터 401 에러로 막힘'",
            ),
        )
        step.finish(output="차단: 5자 미만")
        return {"guard": "short_question", "response": res, "route": "guard", "qhash": "<too_short>",
                "intent": "general", "hit_cache": False}
    step.finish(output="통과")

    rule = classify_intent(question)
    provider, model = current_provider(), current_model()
    # 캐시 키는 전환 전처럼 "규칙으로 정한 intent" 로 만듦 → 자주 묻는 질문 묶음·캐시가 그대로 유지됨
    qhash = ai_cache.question_hash(question, provider=provider, model=model, top_k=state["top_k"], intent=rule)
    base = {"rule_intent": rule, "intent": rule, "qhash": qhash, "provider": provider, "model": model}

    # 0.5) 같은 질문의 최근 24h 환경 결함 실패 → 저장된 안내 재사용
    step = tr.start("guard", "repeat_failure_guard", input=f"qhash={qhash[:12]}")

    def _find():
        with _db() as db:
            prev = find_recent_failure(db, qhash)
            return (prev.outcome, prev.asked_at) if prev else None

    prev = await asyncio.to_thread(_find)
    if prev is not None:
        step.finish(output=f"재사용: 이전 실패 {prev[0]}")
        return {**base, "guard": "repeat_failure", "response": replay_failure(*prev), "route": "guard",
                "intent": "general", "hit_cache": False}
    step.finish(output="통과")

    # 1) 캐시 조회
    step = tr.start("guard", "answer_cache", input=f"qhash={qhash[:12]}")
    cached = await ai_cache.get_cached(qhash)
    if cached is not None:
        cached["cached"] = True
        cached.setdefault("analysis_note", None)
        cached.setdefault("intent", rule)
        step.finish(output="캐시 적중")
        return {**base, "guard": "cache", "response": cached, "route": "cache", "hit_cache": True}
    step.finish(output="캐시 없음")
    return {**base, "guard": None, "hit_cache": False}


def after_guard(state: AgentState) -> str:
    return "finalize" if state.get("guard") else "router"


# ---------- 안내 에이전트 ----------------------------------------------------------------

async def router_node(state: AgentState) -> dict:
    tr = state["tracer"]
    step = tr.start("router", "router", agent_id="router", input=state["question"])
    d = await decide(state["question"], incident_id=state.get("incident_id"), tracer=tr, parent=step)
    step.finish(output=f"{d.intent} → {d.agent_id} ({d.method}{', 애매함' if d.ambiguous else ''})")
    out: dict[str, Any] = {"intent": d.intent, "route": d.agent_id, "route_method": d.method, "router_step": step}
    if "rule_intent" not in state:
        out["rule_intent"] = d.rule_intent
    return out


def after_router(state: AgentState) -> str:
    return state["route"]


# ---------- 기능 에이전트 ----------------------------------------------------------------

def _agent_step(state: AgentState, agent_id: str) -> tuple[StepRecord, StepBudget]:
    step = state["tracer"].start("agent", agent_id, agent_id=agent_id, parent=state.get("router_step"),
                                 input=state["question"])
    return step, StepBudget(agent_id)


def _llm_failed(llm_res: LLMError, answer: str, attempts: list, **kw) -> dict:
    return make_response(mode="fallback", provider="fallback", model=None, answer=answer, outcome="llm_failed",
                         llm_error=llm_res.to_dict(), llm_attempts=attempts, **kw)


async def stats_agent(state: AgentState) -> dict:
    """통계 분석 에이전트 — 최근 운영 통계 표만 근거."""
    step, budget = _agent_step(state, "stats_agent")
    question, intent = state["question"], state["intent"]
    stats_md, _ = await call_tool(state, step, budget, "get_ops_stats", days=7, top_n=10)

    if not is_configured():
        res = make_response(
            mode="fallback", provider="fallback", model=None,
            answer=("[운영 통계 분석 — Fallback 모드]\n\n"
                    "LLM 미설정 상태라 통계 데이터를 그대로 표시합니다.\n\n" + stats_md),
            cases=[], intent=intent,
        )
        step.finish(output="LLM 미설정 → 통계 표 그대로")
        return {"response": res, "cacheable": False}

    system = (
        "당신은 보험사 인터페이스 운영 분석가입니다. 아래 제공된 운영 통계 표만 "
        "근거로 한국어로 답하세요. 표에 없는 일반론(인증서 만료/OOM 같은 추측)은 "
        "절대 추가하지 마세요. 데이터에 답이 없으면 '제공된 통계로는 답할 수 없음' 이라고 명시하세요."
    )
    prompt = (
        f"[현재 운영 통계 데이터]\n{stats_md}\n\n"
        f"[운영자 질문]\n{question}\n\n"
        "위 통계 데이터에서 사실 기반으로 답변해주세요:\n"
        "1) 답변 (구체 인터페이스명·수치 인용)\n"
        "2) 함께 보면 좋을 데이터 (있다면)\n"
        "3) 권장 후속 행동 (예: incidents 페이지 확인, mute 검토 등)"
    )
    llm_res, attempts = await call_llm(state, step, budget, prompt, system)
    if isinstance(llm_res, LLMError):
        res = _llm_failed(llm_res, "[운영 통계 분석 — LLM 호출 실패, fallback]\n\n" + stats_md, attempts,
                          cases=[], intent=intent)
        step.finish(output=res["answer"], error=llm_res.kind)
        return {"response": res, "cacheable": False}
    res = make_response(mode="llm", provider=llm_res.provider, model=llm_res.model, answer=llm_res.content,
                        cases=[], intent=intent, llm_attempts=attempts)
    step.finish(output=res["answer"])
    return {"response": res, "cacheable": True}


async def config_agent(state: AgentState) -> dict:
    """설정 점검 에이전트 — 인터페이스 메타 표만 근거."""
    step, budget = _agent_step(state, "config_agent")
    question, intent = state["question"], state["intent"]
    config_md, _ = await call_tool(state, step, budget, "get_interface_config", top_n=30)

    if not is_configured():
        res = make_response(mode="fallback", provider="fallback", model=None,
                            answer=("[설정 정보 조회 — Fallback 모드]\n\n" + config_md), cases=[], intent=intent)
        step.finish(output="LLM 미설정 → 설정 표 그대로")
        return {"response": res, "cacheable": False}

    system = (
        "당신은 보험사 인터페이스 시스템 관리자입니다. 아래 인터페이스 메타 표만 "
        "근거로 한국어로 답하세요. 표에 없는 정보는 '등록된 정보 내에 없음' 이라고 명시하세요."
    )
    prompt = (
        f"[등록된 인터페이스 메타]\n{config_md}\n\n[질문]\n{question}\n\n"
        "표에서 사실 기반으로 답변해주세요. 필요한 추가 작업(수정/등록)이 있으면 마지막에 안내."
    )
    llm_res, attempts = await call_llm(state, step, budget, prompt, system)
    if isinstance(llm_res, LLMError):
        res = _llm_failed(llm_res, "[설정 정보 조회 — LLM 호출 실패, fallback]\n\n" + config_md, attempts,
                          cases=[], intent=intent)
        step.finish(output=res["answer"], error=llm_res.kind)
        return {"response": res, "cacheable": False}
    res = make_response(mode="llm", provider=llm_res.provider, model=llm_res.model, answer=llm_res.content,
                        cases=[], intent=intent, llm_attempts=attempts)
    step.finish(output=res["answer"])
    return {"response": res, "cacheable": True}


async def case_agent(state: AgentState) -> dict:
    """장애 사례 검색 에이전트 — 해결된 과거 장애만 근거. no_match 면 LLM 을 부르지 않음 (환각 차단)."""
    step, budget = _agent_step(state, "case_agent")
    question, intent = state["question"], state["intent"]
    _, art = await call_tool(state, step, budget, "search_similar_cases", question=question, top_k=state["top_k"])
    cases, note = art["cases"], art["note"]

    if note and note["kind"] in ("no_history", "scikit_missing"):
        res = make_response(mode="fallback", provider="fallback", model=None,
                            answer=f"분석을 시작할 수 없습니다 — {note['title']}",
                            cases=[], intent=intent, outcome=note["kind"], analysis_note=note)
        step.finish(output=f"끝: {note['kind']}")
        return {"response": res, "cacheable": False}

    if note and note["kind"] == "no_match":
        res = make_response(
            mode="fallback", provider="fallback", model=None,
            answer=(
                "관련된 과거 장애 사례를 찾지 못해 LLM 분석을 진행하지 않았습니다.\n"
                "키워드를 더 구체적으로 (인터페이스명·기관명·HTTP 상태) 다시 질문해 주세요.\n"
                "운영 현황을 보고 싶다면 '지금 장애가 잦은 인터페이스' 같이 통계 질문으로 다시 시도해 보세요."
            ),
            cases=cases, intent=intent, outcome="no_match", analysis_note=note,
        )
        step.finish(output="끝: 찾지 못했습니다 (no_match, LLM 호출 안 함)")
        return {"response": res, "cacheable": False}

    if not is_configured():
        res = make_response(mode="fallback", provider="fallback", model=None, answer=template_answer(cases),
                            cases=cases, intent=intent, analysis_note=note)
        step.finish(output="LLM 미설정 → 템플릿 답")
        return {"response": res, "cacheable": False}

    context = "\n\n---\n\n".join(f"[incident #{c['incident_id']}]\n{c['content']}" for c in cases)
    system = (
        "당신은 보험사 인터페이스 운영 어시스턴트입니다. 아래 제공된 과거 사례만 "
        "근거로 한국어로 답하세요. 사례에 없는 일반론은 추가하지 마세요. "
        "특정 과거 사례를 인용할 땐 반드시 markdown 링크 형식 "
        "`[#42](/incidents?focus=42)` 처럼 작성하세요 (운영자가 클릭해 상세 이동)."
    )
    prompt = (
        f"[과거 장애 사례 Top-{len(cases)}]\n{context}\n\n"
        f"[신규 장애 질문]\n{question}\n\n"
        "위 사례를 참고해 다음 형식으로 답변:\n"
        "1) 원인 가설 (Top-1) — 근거 사례 [#ID](/incidents?focus=ID) 인용\n"
        "2) 추가 점검 항목\n3) 권장 조치 절차"
    )
    llm_res, attempts = await call_llm(state, step, budget, prompt, system)
    if isinstance(llm_res, LLMError):
        res = _llm_failed(llm_res, template_answer(cases), attempts, cases=cases, intent=intent, analysis_note=note)
        step.finish(output=res["answer"], error=llm_res.kind)
        return {"response": res, "cacheable": False}
    res = make_response(mode="llm", provider=llm_res.provider, model=llm_res.model, answer=llm_res.content,
                        cases=cases, intent=intent, llm_attempts=attempts, analysis_note=note)
    step.finish(output=res["answer"])
    return {"response": res, "cacheable": True}


# ---------- 장애 원인 분석 (새로 만듦) ---------------------------------------------------

RCA_SYSTEM = (
    "당신은 보험사 대외 인터페이스 장애 원인 분석가입니다. 아래 [근거]에 있는 수치와 과거 사례만 근거로 "
    "한국어로 답하세요. 근거에 없는 추측(일반론)은 쓰지 말고, 근거가 부족하면 '근거 부족'이라고 밝히세요. "
    "재처리·음소거·장애 해결 처리 같은 실행은 하지 말고, 운영자가 기존 화면에서 할 일로 제안만 하세요. "
    "과거 사례를 인용할 땐 `[#42](/incidents?focus=42)` 형식으로 쓰세요."
)


def _rca_hints(detail: dict, logs: dict, retry: dict, sla: dict, anom: dict, cases: list[dict]) -> list[str]:
    """LLM 없을 때 보여 줄 정해진 점검 힌트 (판단은 운영자)."""
    hints: list[str] = []
    by_http = logs.get("by_http") or {}
    by_status = logs.get("by_status") or {}
    itf = detail.get("interface") or {}
    auth = sum(v for k, v in by_http.items() if k in ("401", "403"))
    srv = sum(v for k, v in by_http.items() if k.startswith("5"))
    if auth:
        hints.append(f"인증 오류(HTTP 401/403) {auth}건 → 인증 키·토큰 만료/교체 여부 확인"
                     + (f" (시크릿 만료 예정 {itf.get('secret_expires_at')})" if itf.get("secret_expires_at") else ""))
    if srv:
        hints.append(f"상대 기관 서버 오류(HTTP 5xx) {srv}건 → 기관 공지·상태 확인")
    if by_status.get("TIMEOUT"):
        hints.append(f"타임아웃 {by_status['TIMEOUT']}건 → timeout 설정({itf.get('timeout_seconds')}s)과 회선·상대 응답 지연 확인")
    thr = itf.get("response_ms_threshold")
    if thr and logs.get("avg_ms", 0) > thr:
        hints.append(f"관련 호출 평균 {logs['avg_ms']}ms 가 응답 임계 {thr}ms 초과 → 응답 지연 구간 확인")
    if retry.get("multi_attempt_calls"):
        hints.append(f"자동 재시도 {retry['multi_attempt_calls']}건 중 복구 {retry['recovered_calls']}건 → "
                     "일시 장애면 재시도 정책으로 흡수되는지 확인")
    if sla and not sla.get("meets_uptime", True):
        hints.append(f"SLA 가용률 {sla.get('uptime_pct')}% 로 목표 {sla.get('target_uptime')}% 미달")
    if anom.get("is_anomaly"):
        hints.append(f"최근 1시간 호출 패턴이 평소와 다름 (이상 점수 {anom.get('score')})")
    if cases:
        c = cases[0]
        hints.append(f"비슷한 과거 사례 [#{c['incident_id']}](/incidents?focus={c['incident_id']}) 의 원인·조치 참고 "
                     f"(유사도 {c['score']:.2f})")
    return hints or ["수집한 근거에서 두드러진 신호를 찾지 못함 → 운영자 확인 필요"]


async def rca_agent(state: AgentState) -> dict:
    """장애 원인 분석 에이전트 — 근거를 모아 가설 1~3개와 점검 순서를 제안 (실행은 하지 않음)."""
    step, budget = _agent_step(state, "rca_agent")
    incident_id = state.get("incident_id")
    if incident_id is None:
        content, art = await call_tool(state, step, budget, "find_rca_target", question=state["question"])
        incident_id = art.get("incident_id")
        if incident_id is None:
            step.finish(output=f"대상 없음 → 되묻기 ({content})")
            return {"route": "clarify", "error": content}

    detail_c, detail = await call_tool(state, step, budget, "get_incident_detail", incident_id=incident_id)
    if not detail:
        step.finish(output=f"장애 #{incident_id} 없음 → 되묻기")
        return {"route": "clarify", "error": f"장애 #{incident_id} 를 찾지 못했습니다"}
    itf_id = detail["interface"]["id"]
    logs_c, logs = await call_tool(state, step, budget, "get_recent_call_logs", incident_id=incident_id)
    retry_c, retry = await call_tool(state, step, budget, "get_retry_stats", interface_id=itf_id)
    sla_c, sla = await call_tool(state, step, budget, "get_sla_status", interface_id=itf_id)
    anom_c, anom = await call_tool(state, step, budget, "get_anomaly_score", interface_id=itf_id)
    _, case_art = await call_tool(state, step, budget, "search_similar_cases", question=detail["summary"], top_k=3)
    cases = [c for c in case_art["cases"] if c["score"] >= NO_MATCH_THRESHOLD and c["incident_id"] != incident_id]

    has_evidence = bool(logs.get("total")) or bool((sla or {}).get("total_calls")) or bool((retry or {}).get("total_calls"))
    if not has_evidence:
        res = make_response(
            mode="fallback", provider="fallback", model=None,
            answer=(f"근거 부족 — 운영자 확인 필요\n\n장애 #{incident_id} 에 대해 최근 호출 로그·재시도·SLA 데이터가 "
                    "없어 원인을 추정하지 않았습니다. 인터페이스 실행 이력과 상대 기관 공지를 직접 확인하세요."),
            cases=cases, intent="root_cause", outcome="no_evidence",
            analysis_note=analysis_note("no_evidence", "원인 분석 근거 부족",
                                        "관련 호출 로그·재시도·SLA 데이터가 모두 비어 있습니다.",
                                        "호출이 쌓인 뒤 다시 분석하거나 운영자가 직접 확인하세요."),
        )
        step.finish(output="근거 없음 → LLM 호출 안 함 (handoff)")
        return {"response": res, "cacheable": False}

    evidence = "\n\n".join([
        f"[장애]\n{detail_c}", f"[관련 호출 로그]\n{logs_c}", f"[자동 재시도]\n{retry_c}",
        f"[SLA]\n{sla_c}", f"[이상 탐지]\n{anom_c}",
        "[비슷한 과거 사례]\n" + ("\n\n".join(f"[incident #{c['incident_id']}] (유사도 {c['score']:.2f})\n{c['content']}"
                                          for c in cases) or "(없음)"),
    ])
    hints = _rca_hints(detail, logs, retry or {}, sla or {}, anom or {}, cases)
    fallback_answer = "\n".join([
        "[장애 원인 분석 — Fallback 모드 · LLM 미설정, 수집한 근거 요약]",
        "",
        f"대상: 장애 #{incident_id} {detail['type']} ({detail['severity']}) — {detail['summary']}",
        "",
        "근거",
        f"- 관련 호출: {logs_c.splitlines()[0] if logs_c else '-'}",
        f"- 재시도: {retry_c}",
        f"- SLA: {sla_c}",
        f"- 이상 탐지: {anom_c}",
        "",
        "확인할 점 (판단·실행은 운영자가 기존 화면에서)",
        *[f"  · {h}" for h in hints],
    ])

    if not is_configured():
        res = make_response(mode="fallback", provider="fallback", model=None, answer=fallback_answer,
                            cases=cases, intent="root_cause")
        step.finish(output="LLM 미설정 → 근거 요약 + 점검 힌트")
        return {"response": res, "cacheable": False}

    prompt = (
        f"[근거]\n{evidence}\n\n[요청]\n장애 #{incident_id} 의 원인을 분석해 주세요.\n"
        f"{'' if state.get('entry') == 'incident' else '운영자 질문: ' + state['question']}\n\n"
        "다음 형식으로 답변:\n"
        "1) 원인 가설 1~3개 — 각 가설마다 근거 수치(호출 상태·HTTP 코드·재시도·SLA·이상 점수)를 인용\n"
        "2) 확인 순서 (가장 가능성 높은 것부터)\n"
        "3) 권장 조치 — 운영자가 기존 화면에서 실행할 일 (재처리·음소거 등은 제안만)\n"
        "4) 참고한 과거 사례 (있으면 링크)"
    )
    llm_res, attempts = await call_llm(state, step, budget, prompt, RCA_SYSTEM)
    if isinstance(llm_res, LLMError):
        res = _llm_failed(llm_res, fallback_answer, attempts, cases=cases, intent="root_cause")
        step.finish(output="LLM 실패 → 근거 요약", error=llm_res.kind)
        return {"response": res, "cacheable": False}
    res = make_response(mode="llm", provider=llm_res.provider, model=llm_res.model, answer=llm_res.content,
                        cases=cases, intent="root_cause", llm_attempts=attempts)
    step.finish(output=res["answer"])
    # 실시간 데이터라 장애 화면 진입은 캐시하지 않음
    return {"response": res, "cacheable": state.get("entry") != "incident"}


def after_rca(state: AgentState) -> str:
    return "clarify" if state.get("route") == "clarify" else "finalize"


async def clarify_node(state: AgentState) -> dict:
    """되묻기 — 대상 인터페이스·장애를 정하지 못함 (지어내지 않음)."""
    step = state["tracer"].start("agent", "clarify", agent_id="clarify", input=state.get("error"))
    res = make_response(
        mode="fallback", provider="fallback", model=None,
        answer=("어느 인터페이스(또는 장애)의 원인을 분석할지 찾지 못했습니다.\n"
                f"({state.get('error') or '대상 불명'})\n\n"
                "인터페이스 이름이나 기관명을 넣어 다시 질문하거나, 장애 화면에서 '원인 분석' 버튼을 눌러 주세요.\n"
                "예) '보험개발원-실손중복청구확인 지금 5xx 원인 분석해줘'"),
        cases=[], intent=state.get("intent", "root_cause"), outcome="need_target",
        analysis_note=analysis_note("need_target", "분석 대상을 정하지 못함",
                                    state.get("error") or "질문에서 인터페이스를 찾지 못했습니다.",
                                    "인터페이스 이름·기관명을 포함해 다시 질문하세요."),
    )
    step.finish(output="되묻기")
    return {"response": res, "route": "clarify", "cacheable": False}


# ---------- 마무리 (정해진 코드) ---------------------------------------------------------

_STATUS_BY_OUTCOME = {
    "success": "ok",
    "llm_failed": "error",
    "no_match": "handoff", "no_history": "handoff", "scikit_missing": "handoff",
    "no_evidence": "handoff", "need_target": "handoff",
    "empty_question": "refused",
}


def run_status(response: dict) -> str:
    if response.get("repeated_failure"):
        return "refused"
    return _STATUS_BY_OUTCOME.get(response.get("outcome") or "success", "ok")


async def finalize_node(state: AgentState) -> dict:
    tr = state["tracer"]
    res = dict(state["response"])
    step = tr.start("finalize", "finalize", input=f"route={state.get('route')}")

    if state.get("cacheable"):
        await ai_cache.set_cached(state["qhash"], res)

    res["route"] = state.get("route")
    step.finish(output=f"{run_status(res)} / {res.get('outcome')}")
    return {"response": res}
