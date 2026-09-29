"""AI 통계 컨텍스트의 요일×시간 히트맵 검증.
build_stats_context 가:
  1. 요일×시간대 히트맵 섹션 포함
  2. 7요일 × 6 슬롯 (4시간 단위) = 42 셀 표
  3. 표본 5건 이상인 셀 중 가장 실패율 높은 시점을 "가장 위험한 시점" 으로 강조
  4. 비어있는 셀은 "·" 로 압축
"""
from __future__ import annotations

import os
import re

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.core.database import SessionLocal
from app.services.ai.context import build_stats_context


def main() -> int:
    failures: list[str] = []

    with SessionLocal() as db:
        md = build_stats_context(db, days=7, top_n=10)

    # 1) 섹션 헤더 존재
    if "### 요일 × 시간대 실패율 히트맵" not in md:
        failures.append("히트맵 섹션 헤더 누락")

    # 2) 시간 슬롯 6개 헤더
    expected_slots = ["00-04", "04-08", "08-12", "12-16", "16-20", "20-24"]
    for s in expected_slots:
        if s not in md:
            failures.append(f"시간 슬롯 '{s}' 누락")

    # 3) 7요일 모두 등장 — 각 요일은 한 번씩 굵게 (`**X요일**`)
    for d in ["월", "화", "수", "목", "금", "토", "일"]:
        if f"**{d}요일**" not in md:
            failures.append(f"{d}요일 행 누락")

    # 4) 가장 위험한 시점 라벨 (데이터 있을 때만)
    has_worst = "🔥 가장 위험한 시점" in md
    print(f"  [info] 가장 위험한 시점 라벨: {'노출됨' if has_worst else '데이터 부족으로 미노출'}")

    # 5) 셀 형식: "%% (호출수)" 또는 "·"
    cell_pattern = re.compile(r"\d+% \(\d+\)|·|0% \(\d+\)")
    heatmap_section = md.split("### 요일 × 시간대 실패율 히트맵")[1].split("###")[0] if "### 요일 × 시간대 실패율 히트맵" in md else ""
    cells_found = cell_pattern.findall(heatmap_section)
    print(f"  [info] 히트맵 셀 패턴 매치 수: {len(cells_found)} (42 가까운 값 기대)")
    if len(cells_found) < 30:
        failures.append(f"히트맵 셀 너무 적음: {len(cells_found)}")

    if failures:
        print("[FAIL] 히트맵 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1

    print("[OK] 요일×시간 히트맵 — 모든 검증 통과")
    print("\n--- 히트맵 미리보기 ---")
    if heatmap_section:
        print(heatmap_section[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
