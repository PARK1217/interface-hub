"""시간대 유틸리티 — 애플리케이션 전역 표준 시각은 KST (Asia/Seoul).

보험사 도메인 결정: UTC 저장 + 화면만 KST 변환 패턴 대신 **KST 전역 통일**
선택. 이유:
  - 운영자/감사관이 psql 직접 조회해도 KST 로 보임
  - 로그 timestamp 가 KST 라 야간 cron 디버깅 시 머리속 시차 변환 불필요
  - 외부 기관·금감원 보고 자료 모두 KST 기준
  - 단일 국가 서비스라 multi-region timezone 충돌 걱정 없음

PG TIMESTAMPTZ 의 내부 저장은 PG 강제로 항상 UTC 지만, 세션 timezone =
Asia/Seoul 로 설정되어 입출력은 KST 로 자동 변환됨 (database.py 참조).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """KST 기준 timezone-aware 현재 시각. ``datetime.now(timezone.utc)`` 대신 사용."""
    return datetime.now(KST)