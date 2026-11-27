"""과거 장애 사례 검색 — 기존 TF-IDF(char_wb 2~4gram) 검색을 LangChain BaseRetriever 로 감싼 것.

로직은 전환 전 rag.py `_retrieve` / `score_questions` 와 같습니다 (임베딩·FAISS 미사용:
키 없이 오프라인으로 동작하고 결정적이라 지금 데모·운영 조건에 맞음).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident
from app.models.incident import IncidentType

log = logging.getLogger("noahub.agents.retriever")

# 유사도가 이 값 미만이면 "관련 사례 없음" 으로 간주 (TF-IDF char-ngram 기준 경험치)
NO_MATCH_THRESHOLD = 0.05


class SklearnMissing(RuntimeError):
    """scikit-learn 미설치."""


def analysis_note(kind: str, title: str, detail: str, suggestion: str) -> dict:
    """검색/분석 단계의 사용자 안내 (LLM 호출과 무관한 사유)."""
    return {"kind": kind, "title": title, "detail": detail, "suggestion": suggestion}


def _doc_text(inc: Incident) -> str:
    return f"{inc.summary}\n원인:{inc.root_cause or ''}\n조치:{inc.resolution or ''}"


def _resolved(db: Session) -> list[Incident]:
    return list(db.scalars(select(Incident).where(Incident.resolved_at.is_not(None))).all())


def _vectorize(texts: list[str]):
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError as e:
        raise SklearnMissing() from e
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    return vec.fit_transform(texts), cosine_similarity


class IncidentCaseRetriever(BaseRetriever):
    """해결된 incident Top-K. Document.metadata = {incident_id, type, score}."""

    session: Any
    top_k: int = 3

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        incidents = _resolved(self.session)
        if not incidents:
            return []
        docs = [_doc_text(r) for r in incidents]
        matrix, cosine = _vectorize([*docs, query])
        sims = cosine(matrix[-1], matrix[:-1])[0]
        order = sims.argsort()[::-1][: self.top_k]
        out: list[Document] = []
        for i in order:
            inc = incidents[i]
            out.append(Document(page_content=docs[i], metadata={
                "incident_id": inc.id,
                "type": inc.type.value if isinstance(inc.type, IncidentType) else str(inc.type),
                "score": float(sims[i]),
            }))
        return out


def search_cases(db: Session, question: str, top_k: int) -> tuple[list[dict], dict | None]:
    """반환: (cases, analysis_note). 전환 전 RagService._retrieve 와 같은 안내 문구."""
    if not _resolved(db):
        return [], analysis_note(
            "no_history",
            "분석할 과거 사례가 없습니다",
            "DB 의 incidents 테이블에 resolved (해결 완료) 상태인 사례가 한 건도 없습니다.",
            "장애가 발생해 운영자가 '해결로 표시' 처리한 사례가 1건이라도 쌓이면 자동 활성화됩니다. 데모는 docker compose exec backend python -m app.scripts.seed_demo 로 시드 가능.",
        )
    try:
        docs = IncidentCaseRetriever(session=db, top_k=top_k).invoke(question)
    except SklearnMissing:
        log.warning("scikit-learn 미설치 — 빈 결과 반환")
        return [], analysis_note(
            "scikit_missing",
            "검색 엔진 미설치",
            "백엔드 컨테이너에 scikit-learn 이 설치되어 있지 않아 TF-IDF 검색을 수행할 수 없습니다.",
            "backend/requirements.txt 의 scikit-learn 의존성을 확인하고 컨테이너 이미지를 다시 빌드하세요.",
        )
    cases = [{"incident_id": d.metadata["incident_id"], "type": d.metadata["type"],
              "content": d.page_content, "score": d.metadata["score"]} for d in docs]
    note = None
    if cases and cases[0]["score"] < NO_MATCH_THRESHOLD:
        note = analysis_note(
            "no_match",
            "유사한 과거 사례를 찾지 못함",
            f"Top-1 유사도 {cases[0]['score']:.2f} 가 임계({NO_MATCH_THRESHOLD}) 미만입니다. "
            "질문 키워드가 과거 incident 어느 것과도 충분히 겹치지 않습니다.",
            "인터페이스명·기관명·HTTP 상태코드 같은 구체 키워드를 포함해 다시 질문하거나, "
            "'예시 프롬프트' 칩을 클릭해 보세요.",
        )
    return cases, note


def top1_scores(db: Session, questions: list[str]) -> list[float]:
    """여러 후보 질문의 Top-1 유사도 (/suggestions 사전 필터용). 한 번만 fit."""
    if not questions:
        return []
    incidents = _resolved(db)
    if not incidents:
        return [0.0] * len(questions)
    docs = [_doc_text(r) for r in incidents]
    try:
        matrix, cosine = _vectorize([*docs, *questions])
    except SklearnMissing:
        return [0.0] * len(questions)
    n = len(docs)
    scores: list[float] = []
    for i in range(len(questions)):
        sim = cosine(matrix[n + i], matrix[:n])[0]
        scores.append(float(sim.max()) if len(sim) else 0.0)
    return scores
