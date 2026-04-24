from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiQueryLog(Base):
    """AI 분석 어시스턴트 질의 로그.

    매 /ai/ask 호출마다 1행 기록. 용도:
    1. 자주 묻는 질문 통계 (popular questions chip 노출)
    2. 본인 대화 히스토리 (사용자별 최근 N건 조회)
    3. 캐시 히트율 / LLM 실패 분포 모니터링

    질문 본문은 그대로 저장 — 보험사 운영 도메인 컨텍스트에 한정되며 PII 가능성
    낮음. 응답 본문은 길어서 프리뷰만 (response_excerpt). 전체 응답은 캐시에서.
    """

    __tablename__ = "ai_query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 질문자 — 익명 호출은 NULL (현재는 인증 필수라 NULL 거의 없음)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(80), default=None)

    question: Mapped[str] = mapped_column(Text)
    # 정규화 후 SHA256 — 캐시 키 + popular questions 그룹화 키
    question_hash: Mapped[str] = mapped_column(String(64), index=True)

    # 응답 메타
    mode: Mapped[str] = mapped_column(String(20))  # "llm" | "fallback"
    provider: Mapped[str | None] = mapped_column(String(40), default=None)
    model: Mapped[str | None] = mapped_column(String(80), default=None)
    # LLM 실패 시 사유 분류 (none/http_error/timeout/network/parse_error/not_configured)
    llm_error_kind: Mapped[str | None] = mapped_column(String(40), default=None, index=True)
    # 캐시 히트 여부
    hit_cache: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    # 분석 성공/실패 유형 (popular/history/suggestions 필터 + 중복 차단용)
    # "success" | "empty_question" | "no_history" | "no_match" | "scikit_missing" | "llm_failed"
    outcome: Mapped[str] = mapped_column(String(30), default="success", nullable=False, index=True)

    # 유사 사례 Top-1 점수 (있으면) — 검색 품질 모니터링용
    similarity_top: Mapped[float | None] = mapped_column(default=None)
    # 응답 프리뷰 (300자) — 히스토리 패널 표시용
    response_excerpt: Mapped[str | None] = mapped_column(String(600), default=None)

    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )