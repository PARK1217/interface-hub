"""안내 에이전트(라우터) — 규칙 먼저, 애매할 때만 LLM.

1. incident_id 가 있으면 → root_cause (장애 화면 "원인 분석")
2. 기존 키워드 점수(services/ai/intent.classify_intent) → stats / config / case / general
3. "애매함" = 점수 0(general) 또는 1·2위 동점 → ROUTER_LLM_FALLBACK=1 이고 LLM 이 설정돼 있으면
   LLM 분류 1회 (stats_query / config_query / case_lookup / root_cause 중 하나만 답하게 함)
4. LLM 이 없거나 실패하거나 엉뚱하게 답하거나 ROUTER_LLM_TIMEOUT_SECONDS 를 넘기면 규칙 결과 사용
   (general → case_agent) — 전환 전과 같은 동작. ROUTER_LLM_FALLBACK 기본값은 0(끔)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from app.agents.config import router_config
from app.agents.llm import ainvoke_llm
from app.agents.tracing import LLMCallTracker, StepRecord, Tracer
from app.core.config import get_settings
from app.services.ai.intent import Intent, classify_intent, intent_scores
from app.services.ai.llm import LLMResponse, is_configured

LLM_CHOICES: tuple[Intent, ...] = ("stats_query", "config_query", "case_lookup", "root_cause")

ROUTER_SYSTEM = (
    "당신은 보험사 인터페이스 운영 도구의 안내 담당입니다. 운영자 질문을 아래 4가지 중 하나로만 분류하세요.\n"
    "- stats_query: 지금/최근 운영 통계(실패율, 순위, 추이, 건수)를 묻는 질문\n"
    "- config_query: 인터페이스 설정(스케줄, 엔드포인트, 인증, 등록 정보)을 묻는 질문\n"
    "- case_lookup: 장애 유형·에러의 일반적인 처리 방법을 과거 사례에서 찾는 질문\n"
    "- root_cause: 특정 인터페이스에 지금/최근 일어난 장애의 원인을 분석해 달라는 질문\n"
    "답은 분류 이름 한 단어만 쓰세요."
)


@dataclass
class RouteDecision:
    rule_intent: Intent         # 키워드 규칙 결과 (캐시 키·intent 필드 호환용)
    intent: Intent              # 최종 intent
    agent_id: str               # 맡을 에이전트
    method: str                 # incident | rule | llm | rule(llm 실패)
    ambiguous: bool


def is_ambiguous(question: str) -> bool:
    scores = sorted(intent_scores(question).values(), reverse=True)
    return scores[0] == 0 or (len(scores) > 1 and scores[0] == scores[1])


def agent_for(intent: Intent) -> str:
    return router_config()["routes"][intent]


def parse_choice(text: str) -> Intent | None:
    found = re.findall(r"(stats_query|config_query|case_lookup|root_cause)", (text or "").lower())
    return found[0] if len(set(found)) == 1 else None  # type: ignore[return-value]


async def decide(question: str, *, incident_id: int | None, tracer: Tracer | None,
                 parent: StepRecord | None) -> RouteDecision:
    if incident_id is not None:
        return RouteDecision("root_cause", "root_cause", agent_for("root_cause"), "incident", False)

    rule = classify_intent(question)
    ambiguous = is_ambiguous(question)
    if not ambiguous or not get_settings().router_llm_fallback or not is_configured():
        return RouteDecision(rule, rule, agent_for(rule), "rule", ambiguous)

    tracker = LLMCallTracker(tracer, agent_id="router", parent=parent)
    try:
        # 제공사당 60초 × 대체 체인이 되지 않도록 라우터 분류 전체에 짧은 제한 시간
        res, _ = await asyncio.wait_for(
            ainvoke_llm("router", f"[질문]\n{question}", system=ROUTER_SYSTEM, tracker=tracker),
            timeout=get_settings().router_llm_timeout_seconds,
        )
    except TimeoutError:
        return RouteDecision(rule, rule, agent_for(rule), "rule(llm 시간 초과)", ambiguous)
    choice = parse_choice(res.content) if isinstance(res, LLMResponse) else None
    if choice is None:
        return RouteDecision(rule, rule, agent_for(rule), "rule(llm 실패)", ambiguous)
    return RouteDecision(rule, choice, agent_for(choice), "llm", ambiguous)
