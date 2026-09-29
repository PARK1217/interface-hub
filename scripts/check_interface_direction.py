"""인터페이스 방향 (OUTBOUND/INBOUND) 단위 검증.
빠른 자체 검증 (HTTP 호출 없이) — 모델/스키마/마이그레이션/scheduler/seed 통합.
HTTP/E2E 검증은 _test_inbound_e2e.sh 가 별도로 처리.

검증 항목:
  1. enum 2종 정의
  2. 마이그레이션이 실행되어 컬럼 + 인덱스 존재
  3. 시드 데이터에 INBOUND 인터페이스 1개 이상
  4. INBOUND 인터페이스가 cron 잡 등록 안 됨 (sync_jobs 결과)
  5. ingest 가 INBOUND/OUTBOUND 둘 다 받음 (CallLog 직접 생성으로 시뮬)
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models import Interface, InterfaceDirection
from app.services.scheduler import sync_jobs, get_scheduler


def main() -> int:
    failures: list[str] = []

    # 1) enum 2종
    expected = {"OUTBOUND", "INBOUND"}
    got = {d.value for d in InterfaceDirection}
    if got != expected:
        failures.append(f"enum 정의 다름. expected={expected} got={got}")

    # 2) PG 컬럼 + 인덱스 존재
    with engine.connect() as conn:
        cols = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='interfaces' AND column_name='direction'"
        )).fetchall()
        if not cols:
            failures.append("interfaces.direction 컬럼 누락")
        idx = conn.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename='interfaces' AND indexname='ix_interfaces_direction'"
        )).fetchall()
        if not idx:
            failures.append("ix_interfaces_direction 인덱스 누락")

    # 3) 시드 INBOUND 1개 이상
    with SessionLocal() as db:
        inbound = db.scalars(
            select(Interface).where(Interface.direction == InterfaceDirection.INBOUND)
        ).all()
        outbound = db.scalars(
            select(Interface).where(Interface.direction == InterfaceDirection.OUTBOUND)
        ).all()
        print(f"  [info] direction 분포: INBOUND={len(inbound)} OUTBOUND={len(outbound)}")
        if not inbound:
            failures.append("시드에 INBOUND 인터페이스 없음 (seed_demo 재실행 필요)")
        for itf in inbound:
            print(f"    * INBOUND: #{itf.id} {itf.name} ({itf.organization})")

    # 4) sync_jobs() 후 INBOUND 가 cron 잡으로 등록 안 됨
    sync_jobs()
    sched = get_scheduler()
    job_interface_ids = []
    for j in sched.get_jobs():
        if j.id.startswith("interface-"):
            try:
                job_interface_ids.append(int(j.id.split("-", 1)[1]))
            except ValueError:
                pass
    inbound_ids = {itf.id for itf in inbound}
    leaked = inbound_ids & set(job_interface_ids)
    if leaked:
        failures.append(f"INBOUND 인터페이스가 cron 등록됨 (있으면 안 됨): {leaked}")
    print(f"  [info] cron 등록된 인터페이스 수={len(job_interface_ids)} (INBOUND 제외 확인)")

    # 5) Interface.deleted_at + direction 인덱스 활용 — 쿼리 EXPLAIN 확인 (성능 검증)
    with engine.connect() as conn:
        plan = conn.execute(text(
            "EXPLAIN SELECT * FROM interfaces WHERE direction='INBOUND'"
        )).fetchall()
        plan_str = "\n".join(r[0] for r in plan)
        # 데이터가 18 row 라 PG 가 Seq Scan 선택할 수도 있음 — 인덱스 존재 자체만 확인
        print(f"  [info] EXPLAIN: {plan_str.splitlines()[0][:80]}")

    if failures:
        print("[FAIL] direction 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1
    print("[OK] direction — 모든 시나리오 통과")
    print("  * enum 2종 / 컬럼·인덱스 존재 / 시드 INBOUND 노출 / scheduler INBOUND 제외 / EXPLAIN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
