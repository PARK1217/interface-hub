"""인증 키 만료 모니터링 검증.

검증 항목:
  1. interfaces.auth_secret_expires_at / auth_secret_warning_sent_at 컬럼 존재
  2. IncidentType.SECRET_EXPIRY_WARNING enum 값 존재
  3. 시드에 만료 임박/이미 만료된 인터페이스 존재
  4. check_expiring_secrets() 가 D-7 이내 인터페이스에 incident 생성
  5. 같은 날 두 번째 호출은 dedup (생성 안 됨)
  6. severity 분류: D-3 이내 → critical / D-7 이내 → warning
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models import Incident, Interface
from app.models.incident import IncidentType
from app.services.secret_expiry import check_expiring_secrets


def main() -> int:
    failures: list[str] = []

    # 1) 컬럼 존재
    with engine.connect() as conn:
        cols = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='interfaces' "
            "AND column_name IN ('auth_secret_expires_at', 'auth_secret_warning_sent_at')"
        )).fetchall()
        names = {c[0] for c in cols}
        if "auth_secret_expires_at" not in names:
            failures.append("interfaces.auth_secret_expires_at 컬럼 누락")
        if "auth_secret_warning_sent_at" not in names:
            failures.append("interfaces.auth_secret_warning_sent_at 컬럼 누락")

    # 2) enum 값
    if "SECRET_EXPIRY_WARNING" not in {t.value for t in IncidentType}:
        failures.append("IncidentType.SECRET_EXPIRY_WARNING enum 값 누락")

    # 3) 시드에 만료 임박 인터페이스 존재
    with SessionLocal() as db:
        with_expiry = db.scalars(
            select(Interface)
            .where(Interface.auth_secret_expires_at.is_not(None))
            .where(Interface.deleted_at.is_(None))
        ).all()
        print(f"  [info] 만료일 설정된 인터페이스: {len(with_expiry)}개")
        for itf in with_expiry:
            print(f"    * {itf.name}: {itf.auth_secret_expires_at.date()} 만료")
        if not with_expiry:
            failures.append("시드에 만료일 설정된 인터페이스 0개 — seed_demo 재실행 필요")

        # warning_sent 초기화 (반복 검증을 위해)
        for itf in with_expiry:
            itf.auth_secret_warning_sent_at = None
        db.commit()

        # 검증 전 기존 SECRET_EXPIRY_WARNING incident 정리
        db.execute(text(
            "DELETE FROM incidents WHERE type = 'SECRET_EXPIRY_WARNING'"
        ))
        db.commit()

        # 4) 1차 호출 — D-7 이내인 것만 incident 생성
        n1 = check_expiring_secrets(db)
        print(f"  [info] 1차 호출 새 incident: {n1}건")
        # SEED_SECRET_EXPIRY_DAYS 중 D-7 이내: -2 (만료) / 2 (D-2) / 5 (D-5) = 3건. D-30 은 제외.
        if n1 < 3:
            failures.append(f"D-7 이내 인터페이스 3건 기대, got {n1}")

        # 5) dedup — 같은 날 두 번째 호출은 0건
        n2 = check_expiring_secrets(db)
        print(f"  [info] 2차 호출 새 incident (dedup 기대): {n2}건")
        if n2 != 0:
            failures.append(f"dedup 실패 — 2차 호출에서도 {n2}건 생성됨")

        # 6) severity 분류
        rows = db.scalars(
            select(Incident).where(Incident.type == IncidentType.SECRET_EXPIRY_WARNING)
        ).all()
        crit = sum(1 for r in rows if r.severity == "critical")
        warn = sum(1 for r in rows if r.severity == "warning")
        print(f"  [info] severity 분포: critical={crit} warning={warn}")
        # D-2 (긴급), -2 (만료) → critical 2건. D-5 → warning 1건.
        if crit < 2:
            failures.append(f"critical 2건 이상 기대, got {crit}")
        if warn < 1:
            failures.append(f"warning 1건 이상 기대, got {warn}")

    if failures:
        print("[FAIL] 인증 키 만료 모니터링 검증 실패:")
        for f in failures:
            print("  -", f)
        return 1
    print("[OK] 인증 키 만료 모니터링 — 모든 시나리오 통과")
    print("  * 컬럼·enum / 시드 데이터 / D-7 검출 / dedup / severity 분류")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
