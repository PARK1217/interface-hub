"""질문 의도 분류 (Phase B.8.7).

기존 RAG 는 모든 질문을 "과거 incident 텍스트 검색" 으로만 처리했음.
그런데 실제 운영자 질문은 크게 4종:

1. **stats_query** (운영 통계) — "지금 장애가 잦은 인터페이스가 뭐야?", "최근 실패율 Top 5는?"
   → 과거 사례 검색 무의미. 현재 call_logs / incidents 통계 데이터가 답.

2. **case_lookup** (사례 조회) — "신용정보원이 401 다발인데 어떻게 처리?", "5xx 떴어 원인은?"
   → 기존 RAG 동작 유지 (resolved incident 매칭).

3. **config_query** (설정 조회) — "KIDI 인터페이스 cron 어떻게 설정?", "엔드포인트가 뭐야?"
   → interfaces 테이블의 메타가 답.

4. **general** — 위 어디에도 안 맞음. 그냥 LLM 에 위임.

분류 방식: 한국어 키워드 룰 (가벼움 + 결정적). 키워드 가중치 점수 합산해 가장
높은 카테고리 선택. 동률이면 stats > config > case > general 우선.
"""

from __future__ import annotations

from typing import Literal

Intent = Literal["stats_query", "case_lookup", "config_query", "general"]

# 카테고리별 키워드 (소문자 매칭). 1점씩 가산.
# "지금/현재/최근" 등 시간성 단어 + "인터페이스/장애" 같이 나오면 stats 강력 시그널.
_KEYWORDS: dict[Intent, list[str]] = {
    "stats_query": [
        # 시간 / 빈도 부사
        "지금", "현재", "요즘", "최근", "오늘", "어제", "이번주", "이번달",
        # 비교 / 순위
        "가장", "제일", "많은", "잦은", "빈번", "top", "랭킹", "순위", "분포",
        # 통계 / 추이
        "통계", "비율", "추이", "추세", "평균", "합계", "건수", "횟수",
        # 메타 질문 패턴
        "어떤 인터페이스", "어느 인터페이스", "무슨 인터페이스",
        "어떤 장애", "어느 장애", "무슨 장애",
    ],
    "case_lookup": [
        # 처리 의도
        "어떻게", "어떻해", "어떡해", "원인", "왜", "이유", "처리", "해결", "조치",
        "복구", "방법", "대응", "고치", "수습",
        # 에러 키워드 (대부분 case 의도)
        "에러", "오류", "실패", "장애", "다발", "막혔", "안돼", "안되",
        "5xx", "4xx", "401", "403", "404", "422", "429", "500", "502", "503", "504",
        "timeout", "타임아웃", "ssl", "tls", "인증서", "certificate",
    ],
    "config_query": [
        "설정", "스케줄", "cron", "크론", "등록", "추가", "수정", "변경",
        "엔드포인트", "url", "endpoint", "메서드", "헤더", "프로토콜",
        "auth", "secret", "시크릿", "api 키", "api키",
        "어떻게 등록", "어떻게 추가", "어떻게 설정",
    ],
}


def classify_intent(question: str) -> Intent:
    """질문 → intent. 키워드 점수 가장 높은 카테고리.

    동률 / 무매치 시 우선순위:
        stats_query > config_query > case_lookup > general
    (운영 데이터 우선 노출이 환각보다 안전)
    """
    if not question or not question.strip():
        return "general"

    q = question.lower()
    scores: dict[Intent, int] = {k: 0 for k in _KEYWORDS}
    for intent, keywords in _KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in q:
                scores[intent] += 1

    max_score = max(scores.values())
    if max_score == 0:
        return "general"

    # 동률 시 우선순위 적용
    priority: list[Intent] = ["stats_query", "config_query", "case_lookup"]
    for intent in priority:
        if scores[intent] == max_score:
            return intent
    return "general"


def intent_label(intent: Intent) -> str:
    """프론트 표시용 한국어 라벨."""
    return {
        "stats_query": "운영 통계 분석",
        "case_lookup": "과거 사례 검색",
        "config_query": "설정 정보 조회",
        "general": "일반 질의",
    }.get(intent, intent)