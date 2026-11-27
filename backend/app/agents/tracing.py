"""실행 기록 — agent_runs(요청 1건) / agent_steps(라우터·에이전트·도구·LLM 단계).

- Tracer: 요청 1건의 기록. 단계는 메모리에 모았다가 요청이 끝날 때 한 트랜잭션으로 저장합니다
  (DB 쓰기는 asyncio.to_thread + 전용 SessionLocal — 이벤트 루프를 막지 않음, 요청당 DB 왕복 최소화).
- LLMCallTracker: LangChain 콜백. on_llm_end 의 usage_metadata(응답 값 그대로)로 토큰을 적고
  config/llm_prices.yaml 단가 × USD_KRW_RATE 로 비용(원)을 계산합니다. 단가·환율이 없으면 NULL.
- redact(): 요약을 200자로 자르고 이메일·전화번호·키·토큰을 가립니다. 원문 전체는 저장하지 않습니다.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

import yaml
from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy.orm import Session

from app.agents.config import resolve_path
from app.core.config import get_settings
from app.core.time import now_kst

log = logging.getLogger("noahub.agents.tracing")

SUMMARY_MAX = 200

# ---------- 가리기 -----------------------------------------------------------------

_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+/=]+"), "Bearer [가림]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S+"), r"\1=[가림]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "[키 가림]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[이메일 가림]"),
    (re.compile(r"\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b"), "[전화번호 가림]"),
    (re.compile(r"\b\d{2,3}-\d{3,4}-\d{4}\b"), "[전화번호 가림]"),
    (re.compile(r"\b\d{6}-?[1-4]\d{6}\b"), "[주민번호 가림]"),
    (re.compile(r"\b[A-Za-z0-9+/=_\-]{40,}\b"), "[긴 토큰 가림]"),
]


def redact(text: Any, n: int = SUMMARY_MAX) -> str | None:
    if text is None:
        return None
    s = str(text).strip().replace("\r", "")
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s if len(s) <= n else s[:n] + "…"


def user_ref(user_id: int | None) -> str | None:
    """가린 사용자 값. 서버 비밀값으로 HMAC — 순번 id 를 단순 해시하면 1..N 을 대입해 되돌릴 수 있음."""
    if user_id is None:
        return None
    key = (get_settings().secret_key or "").encode()
    return "u_" + hmac.new(key, f"noahub-user-{user_id}".encode(), hashlib.sha256).hexdigest()[:10]


# ---------- 단가·비용 ---------------------------------------------------------------

@lru_cache
def _price_table() -> dict[tuple[str, str], tuple[float | None, float | None]]:
    try:
        with resolve_path(get_settings().llm_price_path).open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        log.warning("llm_prices.yaml 을 읽지 못함 — 비용은 NULL")
        return {}
    out: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for m in data.get("models") or []:
        if m.get("provider") and m.get("model"):
            out[(m["provider"], m["model"])] = (m.get("input_per_1m_usd"), m.get("output_per_1m_usd"))
    return out


def price_for(provider: str | None, model: str | None) -> tuple[float | None, float | None]:
    if not provider or not model:
        return None, None
    return _price_table().get((provider, model), (None, None))


def cost_krw(provider: str | None, model: str | None,
             input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
    """토큰 × 단가(100만 토큰당 USD) × 환율. 하나라도 모르면 None (지어내지 않음)."""
    rate = get_settings().usd_krw_rate
    inp, outp = price_for(provider, model)
    if rate is None or inp is None or outp is None or input_tokens is None or output_tokens is None:
        return None
    usd = (input_tokens * inp + output_tokens * outp) / 1_000_000
    return Decimal(str(round(usd * rate, 4)))


# ---------- 기록 ---------------------------------------------------------------------

@dataclass
class StepRecord:
    step_id: uuid.UUID
    seq: int
    kind: str                     # router | agent | tool | llm | guard | finalize
    name: str
    agent_id: str | None = None
    parent_step_id: uuid.UUID | None = None
    model: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_krw: Decimal | None = None
    latency_ms: int | None = None
    started_at: datetime = field(default_factory=now_kst)
    error: str | None = None
    _t0: float = field(default_factory=time.perf_counter, repr=False)

    def finish(self, *, output: Any = None, error: Any = None) -> StepRecord:
        self.latency_ms = int((time.perf_counter() - self._t0) * 1000)
        if output is not None:
            self.output_summary = redact(output)
        if error is not None:
            self.error = redact(error, 300)
        return self


class Tracer:
    """요청 1건의 실행 기록."""

    def __init__(self, *, entry: str, user_id: int | None, request: str, enabled: bool | None = None) -> None:
        self.enabled = get_settings().agent_trace_enabled if enabled is None else enabled
        self.run_id = uuid.uuid4()
        self.entry = entry
        self.user_ref = user_ref(user_id)
        self.request_summary = redact(request)
        self.steps: list[StepRecord] = []
        self.persisted = False  # agent_runs 행이 실제로 저장됐는지 (ai_query_logs.run_id 연결 판단)
        self._started_at = now_kst()

    # --- 단계 ---
    def start(self, kind: str, name: str, *, agent_id: str | None = None,
              parent: StepRecord | uuid.UUID | None = None, input: Any = None, model: str | None = None) -> StepRecord:
        parent_id = parent.step_id if isinstance(parent, StepRecord) else parent
        rec = StepRecord(step_id=uuid.uuid4(), seq=len(self.steps) + 1, kind=kind, name=name, agent_id=agent_id,
                         parent_step_id=parent_id, model=model, input_summary=redact(input))
        self.steps.append(rec)
        return rec

    def totals(self) -> tuple[int, int, Decimal]:
        llm = [s for s in self.steps if s.kind == "llm"]
        tin = sum(s.input_tokens or 0 for s in llm)
        tout = sum(s.output_tokens or 0 for s in llm)
        cost = sum((s.cost_krw for s in llm if s.cost_krw is not None), Decimal("0"))
        return tin, tout, cost

    # --- 저장 (요청이 끝날 때 한 번에) ---
    async def close(self, *, route: str | None, status: str, outcome: str | None, error: str | None = None,
                    also: Callable[[Session, uuid.UUID | None], None] | None = None) -> bool:
        """agent_runs 1행 + agent_steps 를 한 트랜잭션으로 저장. also(db, run_id) 는 같은 연결에서 이어서 실행
        (ai_query_logs 기록 — 실행 기록이 저장됐으면 run_id 로 연결). 저장 성공 여부를 돌려줌."""
        return await asyncio.to_thread(self._close_sync, route, status, outcome, error, also)

    def _close_sync(self, route, status, outcome, error, also) -> bool:
        from app.core import database
        from app.models import AgentRun, AgentStep

        tin, tout, cost = self.totals()
        with database.SessionLocal() as db:
            if self.enabled:
                try:
                    db.add(AgentRun(
                        run_id=self.run_id, started_at=self._started_at, finished_at=now_kst(), entry=self.entry,
                        user_ref=self.user_ref, request_summary=self.request_summary, route=route, status=status,
                        outcome=outcome, error=redact(error, 500), total_input_tokens=tin, total_output_tokens=tout,
                        total_cost_krw=cost,
                    ))
                    db.flush()
                    # seq 순서(= 생성 순서)로 넣으므로 부모 단계가 항상 먼저 들어감
                    db.add_all([AgentStep(
                        step_id=x.step_id, run_id=self.run_id, parent_step_id=x.parent_step_id, seq=x.seq,
                        kind=x.kind, name=x.name, agent_id=x.agent_id, model=x.model,
                        input_summary=x.input_summary, output_summary=x.output_summary,
                        input_tokens=x.input_tokens, output_tokens=x.output_tokens, cost_krw=x.cost_krw,
                        latency_ms=x.latency_ms, started_at=x.started_at, error=x.error,
                    ) for x in sorted(self.steps, key=lambda y: y.seq)])
                    db.commit()
                    self.persisted = True
                except Exception:  # noqa: BLE001 — 기록 실패해도 답은 정상으로 나감
                    log.exception("agent_runs/agent_steps 기록 실패 — 본 응답은 정상")
                    db.rollback()
            if also is not None:
                also(db, self.run_id if self.persisted else None)
        return self.persisted


# ---------- LangChain 콜백 ------------------------------------------------------------

@dataclass
class LLMCall:
    provider: str | None
    model: str | None             # 응답이 알려 준 모델 (예: gpt-4o-mini-2024-07-18)
    requested_model: str | None = None   # 설정에서 요청한 모델 (예: gpt-4o-mini) — 단가 조회·응답 표시용
    ok: bool = False
    text: str = ""
    error: BaseException | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMCallTracker(BaseCallbackHandler):
    """채팅 모델 호출마다 (제공사, 모델, 성공/실패, 토큰) 을 모으고 agent_steps(kind=llm) 를 남깁니다."""

    run_inline = True     # 순서 보장 (IO 없음)
    raise_error = False

    def __init__(self, tracer: Tracer | None, *, agent_id: str | None, parent: StepRecord | None = None) -> None:
        self.tracer = tracer
        self.agent_id = agent_id
        self.parent = parent
        self.calls: list[LLMCall] = []
        self._open: dict[uuid.UUID, tuple[LLMCall, StepRecord | None]] = {}

    def result_for(self, provider: str) -> LLMCall | None:
        found = [c for c in self.calls if c.provider == provider]
        return found[-1] if found else None

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[Any]], *, run_id: uuid.UUID,
                            metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        md = metadata or {}
        provider = md.get("agent_provider") or md.get("ls_provider")
        model = md.get("agent_model") or md.get("ls_model_name")
        call = LLMCall(provider=provider, model=model, requested_model=model)
        self.calls.append(call)
        step = None
        if self.tracer is not None:
            last_user = messages[0][-1].content if messages and messages[0] else None
            step = self.tracer.start("llm", f"{provider}:{model}", agent_id=self.agent_id, parent=self.parent,
                                     input=last_user, model=model)
        self._open[run_id] = (call, step)

    def on_llm_end(self, response: Any, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        from app.agents.llm import message_text
        from app.services.ai.llm import EmptyResponseError

        call, step = self._open.pop(run_id, (None, None))
        if call is None:
            return
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        msg = getattr(gen, "message", None)
        usage = getattr(msg, "usage_metadata", None) or {}
        call.input_tokens = usage.get("input_tokens")
        call.output_tokens = usage.get("output_tokens")
        meta = getattr(msg, "response_metadata", None) or {}
        call.model = meta.get("model_name") or meta.get("model") or call.model
        call.text = message_text(msg) if msg is not None else ""
        call.ok = bool(call.text)
        if not call.ok:
            call.error = EmptyResponseError("empty response")
        if step is not None:
            step.model = call.model
            step.input_tokens = call.input_tokens
            step.output_tokens = call.output_tokens
            # 응답 모델 이름(스냅샷)으로 먼저 찾고, 단가표에 없으면 요청한 모델 이름으로
            step.cost_krw = (cost_krw(call.provider, call.model, call.input_tokens, call.output_tokens)
                             if price_for(call.provider, call.model) != (None, None)
                             else cost_krw(call.provider, call.requested_model, call.input_tokens, call.output_tokens))
            step.finish(output=call.text or None, error=None if call.ok else "빈 응답")

    def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        call, step = self._open.pop(run_id, (None, None))
        if call is None:
            return
        call.ok = False
        call.error = error
        if step is not None:
            status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
            step.finish(error=f"{type(error).__name__}" + (f" (HTTP {status})" if status else ""))
