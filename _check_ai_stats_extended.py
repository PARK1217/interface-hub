"""AI 통계 컨텍스트 확장 검증.
build_stats_context 가:
  1. 어제/오늘 추이 섹션 포함
  2. 시간대별 실패 분포 Top 5 섹션 포함 (PG 한정)
  3. 평균 응답시간 컬럼 추가
  4. 데이터 없을 때도 섹션 헤더 출력 (LLM 이 "데이터 없음" 안내 가능)
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.core.database import SessionLocal
from app.services.ai.context import build_stats_context, build_config_context


def main() -> int:
    failures: list[str] = []

    with SessionLocal() as db:
        md = build_stats_context(db, days=7, top_n=10)

    required_sections = [
        "## 최근 7일 인터페이스 운영 통계",
        "### 실패 건수 Top 10 인터페이스",
        "### 어제 vs 오늘 추이",
        "### 현재 미해결 장애",
    ]
    for s in required_sections:
        if s not in md:
            failures.append(f"섹션 누락: '{s}'")

    # 평균응답 컬럼 추가됐는지
    if "평균응답" not in md:
        failures.append("평균응답 컬럼 누락 (Top 10 표)")

    # 시간대 섹션은 PG 한정 — 데이터 있으면 노출되지만 없을 수 있음
    # 따라서 검증은 "있으면 시간대 형식 OK" 까지만
    if "시간대" in md and ":00~" not in md:
        failures.append("시간대 헤더는 있는데 hour 포맷 누락")

    # build_config_context 도 정상 동작
    with SessionLocal() as db:
        cmd = build_config_context(db, top_n=30)
    if "## 등록된 인터페이스" not in cmd:
        failures.append("config_context 섹션 누락")

    if failures:
        print("[FAIL] 통계 컨텍스트 확장 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1
    print("[OK] 통계 컨텍스트 확장 — 모든 섹션 노출")
    print("  * Top 10 + 평균응답 / 어제 vs 오늘 추이 / (PG) 시간대 분포 / 미해결 장애")
    print("\n--- 생성된 markdown 미리보기 (처음 800자) ---")
    print(md[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
