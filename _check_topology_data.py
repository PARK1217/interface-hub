"""토폴로지 페이지 데이터 정합성 검증 (DB 직접).
토폴로지 페이지가 사용하는 grouping 의 정합성:
  1. 모든 인터페이스에 category 가 NOT NULL
  2. 모든 인터페이스에 direction 이 NOT NULL
  3. 카테고리 3종 모두 1개 이상 존재 (시드 후)
  4. INBOUND 인터페이스가 1개 이상 (방향 시각화 의미 있음)
  5. organization 미설정 비율 < 50% (그룹핑 의미 있음)
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from collections import Counter

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Interface, InterfaceCategory, InterfaceDirection


def main() -> int:
    failures: list[str] = []

    with SessionLocal() as db:
        rows = db.scalars(
            select(Interface).where(Interface.deleted_at.is_(None))
        ).all()

    if not rows:
        print("[FAIL] 인터페이스 0개 — 시드 실행 필요")
        return 1

    # 1) category NOT NULL
    no_cat = [r.name for r in rows if r.category is None]
    if no_cat:
        failures.append(f"category=NULL 인 인터페이스: {no_cat[:3]}")

    # 2) direction NOT NULL
    no_dir = [r.name for r in rows if r.direction is None]
    if no_dir:
        failures.append(f"direction=NULL 인 인터페이스: {no_dir[:3]}")

    # 3) 카테고리 3종 모두 존재
    by_cat = Counter(r.category.value for r in rows)
    print(f"  [info] 카테고리 분포: {dict(by_cat)}")
    for c in (InterfaceCategory.INTERNAL_CORE, InterfaceCategory.EXTERNAL_PARTNER, InterfaceCategory.EXTERNAL_REGULATOR):
        if by_cat.get(c.value, 0) == 0:
            failures.append(f"카테고리 {c.value} 인터페이스 0개")

    # 4) INBOUND 1개 이상
    by_dir = Counter(r.direction.value for r in rows)
    print(f"  [info] 방향 분포: {dict(by_dir)}")
    if by_dir.get(InterfaceDirection.INBOUND.value, 0) == 0:
        failures.append("INBOUND 인터페이스 0개 — 토폴로지 INBOUND 시각화 안 됨")

    # 5) organization 미설정 비율
    no_org = sum(1 for r in rows if not r.organization)
    org_ratio = no_org / len(rows)
    print(f"  [info] organization 미설정: {no_org}/{len(rows)} ({org_ratio:.0%})")
    if org_ratio > 0.5:
        failures.append(f"organization 미설정 비율 {org_ratio:.0%} 너무 높음 — 그룹핑 의미 적음")

    # 6) 토폴로지 시각화 의미 있는지 — INTERNAL_CORE 와 EXTERNAL 양쪽 모두 존재
    internal_count = by_cat.get(InterfaceCategory.INTERNAL_CORE.value, 0)
    external_count = (
        by_cat.get(InterfaceCategory.EXTERNAL_PARTNER.value, 0)
        + by_cat.get(InterfaceCategory.EXTERNAL_REGULATOR.value, 0)
    )
    print(f"  [info] 사내↔외부 비율: 사내 {internal_count} / 외부 {external_count}")
    if internal_count == 0 or external_count == 0:
        failures.append("사내·외부 한쪽이 비어있음 — 토폴로지 비대칭")

    if failures:
        print("[FAIL] 토폴로지 데이터 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1
    print("[OK] 토폴로지 데이터 — 모든 정합성 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
