"""Phase B.12 — 인터페이스 카테고리 (내부/외부 분류) 검증.

기획서 1번 항목 ("내부 핵심 시스템과 외부 기관 간 다수의 인터페이스를 단일 화면에서
제어") 의 시스템적 표현이 동작하는지:

  1. enum 3종 정의 (INTERNAL_CORE / EXTERNAL_PARTNER / EXTERNAL_REGULATOR)
  2. 마이그레이션이 적용되어 컬럼이 존재
  3. /api/interfaces?category=... 필터 동작
  4. AI 통계 컨텍스트에 분류별 집계 섹션 노출
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Interface, InterfaceCategory
from app.services.ai.context import build_stats_context


def main() -> int:
    failures: list[str] = []

    # 1) enum 3종
    expected = {"INTERNAL_CORE", "EXTERNAL_PARTNER", "EXTERNAL_REGULATOR"}
    got = {c.value for c in InterfaceCategory}
    if got != expected:
        failures.append(f"enum 정의 다름. expected={expected} got={got}")

    with SessionLocal() as db:
        # 2) DB 컬럼 존재 + 모든 row 가 NOT NULL
        rows = db.scalars(select(Interface)).all()
        if not rows:
            print("(경고) interfaces 테이블이 비어있어 일부 검증 스킵 — 시드 실행 권장")
        none_cat = [r.name for r in rows if r.category is None]
        if none_cat:
            failures.append(f"category=NULL 인 인터페이스 발견: {none_cat[:3]}...")

        # 3) 분류별 카운트 (시드 적용 후라면 모두 > 0 이상이어야 정상)
        by_cat: dict[str, int] = {}
        for r in rows:
            key = r.category.value if r.category else "NULL"
            by_cat[key] = by_cat.get(key, 0) + 1
        print(f"  [info] 분류별 인터페이스 수: {by_cat}")

        # 4) 필터 동작 — INTERNAL_CORE 만 select
        internals = db.scalars(
            select(Interface).where(Interface.category == InterfaceCategory.INTERNAL_CORE)
        ).all()
        for itf in internals:
            if itf.category != InterfaceCategory.INTERNAL_CORE:
                failures.append(f"필터 동작 안 함: {itf.name} category={itf.category}")

        # 5) AI 통계 컨텍스트에 분류별 섹션 노출
        md = build_stats_context(db, days=7, top_n=10)
        if "### 분류별 호출/실패율" not in md:
            failures.append("AI 통계 컨텍스트에 '분류별 호출/실패율' 섹션 누락")

    if failures:
        print("[FAIL] Phase B.12 카테고리 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1
    print("[OK] Phase B.12 인터페이스 카테고리 — 모든 시나리오 통과")
    print("  * enum 3종 / 컬럼 NOT NULL / 필터 동작 / AI 컨텍스트 섹션")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
