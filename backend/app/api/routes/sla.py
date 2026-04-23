from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.core.time import KST, now_kst
from app.models import CallLog, Interface, SlaTarget, User, UserRole
from app.services.audit import record_audit
from app.models.call_log import CallStatus
from app.schemas.sla_target import SlaReportRow, SlaTargetCreate, SlaTargetOut, SlaTargetUpdate

router = APIRouter(prefix="/sla", tags=["sla"])


def _interfaces_with_data_in_period(db: Session, since: datetime) -> list[Interface]:
    """기간 내 호출 로그가 있던 **모든** 인터페이스 (보관된 것 포함) 반환.

    감사·SLA 보고는 retroactive immutable 원칙 (한 번 보고한 자료는 변경
    되지 않음). 오늘 인터페이스를 보관 처리해도 지난달 SLA 보고서에는
    그대로 남아 있어야 함 — 안 그러면 금감원 보고 일관성 깨지고 "왜
    숫자가 어제랑 다르냐" 추궁 받음.

    Active 만 보고 싶으면 routes/interfaces.py 의 list (deleted_at 필터)
    사용. 이 헬퍼는 "기간 내 데이터가 존재했던 모든 인터페이스" 가 의도.
    """
    ids = set(
        db.scalars(
            select(CallLog.interface_id).where(CallLog.called_at >= since).distinct()
        ).all()
    )
    if not ids:
        return list(
            db.scalars(select(Interface).where(Interface.deleted_at.is_(None))).all()
        )
    return list(db.scalars(select(Interface).where(Interface.id.in_(ids)).order_by(Interface.id)).all())


# ---------- targets CRUD ----------------------------------------------------
@router.get("/targets", response_model=list[SlaTargetOut])
def list_targets(db: Session = Depends(get_db)) -> list[SlaTargetOut]:
    return [SlaTargetOut.model_validate(t) for t in db.scalars(select(SlaTarget)).all()]


@router.post("/targets", response_model=SlaTargetOut, status_code=status.HTTP_201_CREATED)
def upsert_target(
    payload: SlaTargetCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> SlaTargetOut:
    existing = db.scalar(select(SlaTarget).where(SlaTarget.interface_id == payload.interface_id))
    if existing:
        for k, v in payload.model_dump(exclude={"interface_id"}).items():
            setattr(existing, k, v)
        obj = existing
    else:
        obj = SlaTarget(**payload.model_dump())
        db.add(obj)
    db.commit()
    db.refresh(obj)
    return SlaTargetOut.model_validate(obj)


@router.patch("/targets/{target_id}", response_model=SlaTargetOut)
def update_target(
    target_id: int,
    payload: SlaTargetUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_role([UserRole.ADMIN])),
) -> SlaTargetOut:
    obj = db.get(SlaTarget, target_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sla target not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return SlaTargetOut.model_validate(obj)


# ---------- single-period report (existing) ---------------------------------
def _compute_row(itf: Interface, logs: list[CallLog], target: SlaTarget | None) -> dict:
    target_uptime = target.uptime_target if target else 99.0
    target_resp = target.response_ms_target if target else 2000
    total = len(logs)
    success = sum(1 for log in logs if log.status == CallStatus.SUCCESS)
    uptime = (success / total * 100.0) if total else 100.0
    avg = (sum(log.duration_ms for log in logs) / total) if total else 0.0
    return {
        "uptime_pct": round(uptime, 3),
        "avg_response_ms": round(avg, 1),
        "target_uptime": target_uptime,
        "target_response_ms": target_resp,
        "meets_uptime": uptime >= target_uptime,
        "meets_response": avg <= target_resp,
        "total_calls": total,
    }


@router.get("/report", response_model=list[SlaReportRow])
def report(days: int = 30, db: Session = Depends(get_db)) -> list[SlaReportRow]:
    since = now_kst() - timedelta(days=days)
    interfaces = _interfaces_with_data_in_period(db, since)
    targets = {
        t.interface_id: t for t in db.scalars(select(SlaTarget)).all()
    }
    out: list[SlaReportRow] = []
    for itf in interfaces:
        logs = db.scalars(
            select(CallLog)
            .where(CallLog.interface_id == itf.id)
            .where(CallLog.called_at >= since)
        ).all()
        row = _compute_row(itf, logs, targets.get(itf.id))
        out.append(
            SlaReportRow(
                interface_id=itf.id,
                interface_name=itf.name,
                deleted_at=itf.deleted_at,
                **{k: v for k, v in row.items() if k != "total_calls"},
            )
        )
    return out


# ---------- monthly / quarterly trend ---------------------------------------
class TrendPoint(BaseModel):
    period: str  # "2026-04" or "2026-Q2"
    interface_id: int
    interface_name: str
    uptime_pct: float
    avg_response_ms: float
    target_uptime: float
    target_response_ms: int
    meets_uptime: bool
    meets_response: bool
    total_calls: int
    deleted_at: datetime | None = None


def _period_key(dt: datetime, bucket: Literal["month", "quarter"]) -> str:
    if bucket == "month":
        return f"{dt.year:04d}-{dt.month:02d}"
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year:04d}-Q{q}"


@router.get("/trend", response_model=list[TrendPoint])
def trend(
    bucket: Literal["month", "quarter"] = "month",
    months: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
) -> list[TrendPoint]:
    since = now_kst() - timedelta(days=months * 31)
    interfaces = _interfaces_with_data_in_period(db, since)
    targets = {t.interface_id: t for t in db.scalars(select(SlaTarget)).all()}

    out: list[TrendPoint] = []
    for itf in interfaces:
        rows = db.scalars(
            select(CallLog)
            .where(CallLog.interface_id == itf.id)
            .where(CallLog.called_at >= since)
        ).all()
        # bucket by period
        buckets: dict[str, list[CallLog]] = {}
        for r in rows:
            buckets.setdefault(_period_key(r.called_at, bucket), []).append(r)
        for period in sorted(buckets):
            row = _compute_row(itf, buckets[period], targets.get(itf.id))
            out.append(
                TrendPoint(
                    period=period,
                    interface_id=itf.id,
                    interface_name=itf.name,
                    deleted_at=itf.deleted_at,
                    **row,
                )
            )
    return out


# ---------- daily calendar (heatmap) ----------------------------------------
class CalendarCell(BaseModel):
    date: str  # "2026-04-22"
    interface_id: int
    interface_name: str
    uptime_pct: float
    avg_response_ms: float
    target_uptime: float
    target_response_ms: int
    meets: bool
    total_calls: int
    deleted_at: datetime | None = None


@router.get("/calendar", response_model=list[CalendarCell])
def calendar(
    days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
) -> list[CalendarCell]:
    since = now_kst() - timedelta(days=days)
    interfaces = _interfaces_with_data_in_period(db, since)
    targets = {t.interface_id: t for t in db.scalars(select(SlaTarget)).all()}

    out: list[CalendarCell] = []
    for itf in interfaces:
        rows = db.scalars(
            select(CallLog)
            .where(CallLog.interface_id == itf.id)
            .where(CallLog.called_at >= since)
        ).all()
        # bucket by date (KST)
        by_day: dict[str, list[CallLog]] = {}
        for r in rows:
            d = r.called_at.astimezone(KST).date().isoformat()
            by_day.setdefault(d, []).append(r)
        for d, logs in sorted(by_day.items()):
            row = _compute_row(itf, logs, targets.get(itf.id))
            out.append(
                CalendarCell(
                    date=d,
                    interface_id=itf.id,
                    interface_name=itf.name,
                    deleted_at=itf.deleted_at,
                    uptime_pct=row["uptime_pct"],
                    avg_response_ms=row["avg_response_ms"],
                    target_uptime=row["target_uptime"],
                    target_response_ms=row["target_response_ms"],
                    meets=row["meets_uptime"] and row["meets_response"],
                    total_calls=row["total_calls"],
                )
            )
    return out


# ---------- Excel export ----------------------------------------------------
@router.get("/export.xlsx")
def export_xlsx(days: int = 30, db: Session = Depends(get_db)) -> StreamingResponse:
    """Generate an Excel workbook with two sheets: 요약 / 일별 상세."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    summary = report(days=days, db=db)
    cal_cells = calendar(days=days, db=db)

    wb = Workbook()
    # ---- Sheet 1: summary
    ws = wb.active
    assert ws is not None
    ws.title = "요약"
    ws.append([
        "인터페이스 ID", "이름", "가동률(%)", "목표(%)", "가동 충족",
        "평균 응답(ms)", "목표(ms)", "응답 충족",
    ])
    header_fill = PatternFill("solid", fgColor="1F3A93")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for r in summary:
        ws.append([
            r.interface_id, r.interface_name,
            r.uptime_pct, r.target_uptime, "O" if r.meets_uptime else "X",
            r.avg_response_ms, r.target_response_ms, "O" if r.meets_response else "X",
        ])
    for col_letter, width in zip("ABCDEFGH", [12, 38, 12, 10, 10, 14, 12, 10]):
        ws.column_dimensions[col_letter].width = width

    # ---- Sheet 2: daily breakdown
    ws2 = wb.create_sheet("일별 상세")
    ws2.append([
        "날짜", "인터페이스", "호출 수", "가동률(%)",
        "평균 응답(ms)", "목표 충족",
    ])
    for cell in ws2[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    for c in cal_cells:
        ws2.append([
            c.date, c.interface_name, c.total_calls,
            c.uptime_pct, c.avg_response_ms, "O" if c.meets else "X",
        ])
    for col_letter, width in zip("ABCDEF", [14, 38, 10, 12, 14, 10]):
        ws2.column_dimensions[col_letter].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    today = date.today().isoformat()
    fname = f"sla-report-{today}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
