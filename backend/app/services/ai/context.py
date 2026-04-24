"""Intent 별 LLM 컨텍스트 빌더.

stats_query / config_query 등 메타 질문일 때, 과거 incident 검색 대신
실제 DB 의 현재 운영 데이터를 markdown 표로 직렬화해 LLM 프롬프트에 주입.
이 컨텍스트만 가지고 LLM 이 답하므로 환각(hallucination) 방지.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.time import now_kst
from app.models import CallLog, Incident, Interface
from app.models.call_log import CallStatus


def build_stats_context(db: Session, *, days: int = 7, top_n: int = 10) -> str:
    """최근 N일 인터페이스별 호출/실패/실패율 + 시간대 분포 + 추이 + 미해결 incident.

    기존 7일 합계만 보던 것을 **추이(어제/오늘 비교)** + **시간대별
    분포(가장 실패 잦은 시간대)** 까지 확장. LLM 이 "오후 3시쯤 KIDI 가 자주 막힘"
    같은 패턴 답변 가능.

    LLM 에 "이 데이터만 보고 답해" 라는 컨텍스트로 사용. 표 외 일반론 추가 금지
    (system prompt 에 명시).
    """
    now = now_kst()
    since = now - timedelta(days=days)

    # 인터페이스별 호출/실패 집계
    failure_expr = case((CallLog.status != CallStatus.SUCCESS, 1), else_=0)
    rows = db.execute(
        select(
            Interface.id,
            Interface.name,
            Interface.organization,
            Interface.protocol,
            Interface.deleted_at,
            Interface.muted_until,
            func.count(CallLog.id).label("total"),
            func.coalesce(func.sum(failure_expr), 0).label("failures"),
            func.coalesce(func.avg(CallLog.duration_ms), 0).label("avg_ms"),
        )
        .join(CallLog, CallLog.interface_id == Interface.id)
        .where(CallLog.called_at >= since)
        .group_by(Interface.id)
        .order_by(func.coalesce(func.sum(failure_expr), 0).desc())
        .limit(top_n)
    ).all()

    lines: list[str] = [
        f"## 최근 {days}일 인터페이스 운영 통계 (KST 기준, {now.isoformat()} 시점)",
        "",
        "### 실패 건수 Top 10 인터페이스",
        "| 순위 | 인터페이스 | 기관 | 프로토콜 | 호출수 | 실패수 | 실패율 | 평균응답 | 비고 |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    if not rows:
        lines.append("| - | (집계 데이터 없음) | - | - | 0 | 0 | 0% | - | |")
    for i, r in enumerate(rows, 1):
        rate = (r.failures / r.total * 100) if r.total else 0.0
        notes = []
        if r.deleted_at:
            notes.append("📦 보관됨")
        if r.muted_until and r.muted_until > now:
            notes.append("🔕 음소거중")
        lines.append(
            f"| {i} | {r.name} | {r.organization or '-'} | "
            f"{r.protocol.value if hasattr(r.protocol, 'value') else r.protocol} | "
            f"{r.total} | {r.failures} | {rate:.1f}% | {int(r.avg_ms)}ms | {' '.join(notes) or '-'} |"
        )

    # 어제 vs 오늘 호출/실패 비교 (추이)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    trend_rows = db.execute(
        select(
            (CallLog.called_at >= today_start).label("is_today"),
            func.count(CallLog.id).label("total"),
            func.coalesce(func.sum(failure_expr), 0).label("failures"),
        )
        .where(CallLog.called_at >= yesterday_start)
        .group_by("is_today")
    ).all()
    today_total = today_failures = 0
    yest_total = yest_failures = 0
    for r in trend_rows:
        if r.is_today:
            today_total, today_failures = r.total, r.failures
        else:
            yest_total, yest_failures = r.total, r.failures
    lines += ["", "### 어제 vs 오늘 추이 (전체 인터페이스 합산)"]
    if today_total or yest_total:
        yest_rate = (yest_failures / yest_total * 100) if yest_total else 0
        today_rate = (today_failures / today_total * 100) if today_total else 0
        delta = today_rate - yest_rate
        delta_label = (
            f"🔺 +{delta:.1f}%p (악화)" if delta > 1
            else f"🟢 {delta:.1f}%p (개선)" if delta < -1
            else "➖ 변화 미미"
        )
        lines += [
            "| 구간 | 호출수 | 실패수 | 실패율 |",
            "|---|---:|---:|---:|",
            f"| 어제 (전일) | {yest_total} | {yest_failures} | {yest_rate:.1f}% |",
            f"| 오늘 (지금까지) | {today_total} | {today_failures} | {today_rate:.1f}% |",
            f"| **추세** | | | **{delta_label}** |",
        ]
    else:
        lines.append("- 비교할 데이터 없음")

    # 시간대별 실패 분포 (어느 시간이 가장 위험한지)
    # date_part('hour', ...) 는 PG 함수. SQLite 데모는 미지원이므로 try/except.
    try:
        hour_rows = db.execute(
            select(
                func.date_part("hour", CallLog.called_at).label("hour"),
                func.count(CallLog.id).label("total"),
                func.coalesce(func.sum(failure_expr), 0).label("failures"),
            )
            .where(CallLog.called_at >= since)
            .group_by("hour")
            .order_by(func.coalesce(func.sum(failure_expr), 0).desc())
            .limit(5)
        ).all()
        if hour_rows:
            lines += ["", "### 실패 잦은 시간대 Top 5 (KST hour, 최근 7일)"]
            lines += ["| 시간대 | 호출수 | 실패수 | 실패율 |", "|---|---:|---:|---:|"]
            for h in hour_rows:
                hr = int(h.hour)
                rate = (h.failures / h.total * 100) if h.total else 0
                lines.append(
                    f"| {hr:02d}:00~{(hr + 1) % 24:02d}:00 | {h.total} | {h.failures} | {rate:.1f}% |"
                )
    except Exception:  # noqa: BLE001
        pass

    # 요일 × 시간대 히트맵 (LLM 이 패턴 답변 가능하도록)
    # 월요일 새벽 / 금요일 오후 등 "정기적으로 위험한" 시점 노출. 24×7 = 168 셀
    # 전부 표로 토하면 LLM 컨텍스트 폭증 → 4시간 묶음 (00-04, 04-08, ..., 20-24) 6 슬롯 ×
    # 7요일 = 42 셀로 압축. 각 셀의 호출/실패율, 실패 0% 면 "·" 로 노출 (압축 + 가독성).
    try:
        # PG: dow=0 일요일~6 토요일, hour 0-23. 4시간 슬롯으로 그룹.
        dow_block_rows = db.execute(
            select(
                func.date_part("dow", CallLog.called_at).label("dow"),
                (func.date_part("hour", CallLog.called_at) // 4).label("block"),
                func.count(CallLog.id).label("total"),
                func.coalesce(func.sum(failure_expr), 0).label("failures"),
            )
            .where(CallLog.called_at >= since)
            .group_by("dow", "block")
        ).all()
        if dow_block_rows:
            # (dow, block) → (total, failures)
            cell_map: dict[tuple[int, int], tuple[int, int]] = {}
            for r in dow_block_rows:
                cell_map[(int(r.dow), int(r.block))] = (int(r.total), int(r.failures))

            dow_label = ["일", "월", "화", "수", "목", "금", "토"]
            block_label = [
                "00-04", "04-08", "08-12", "12-16", "16-20", "20-24",
            ]
            lines += [
                "",
                f"### 요일 × 시간대 실패율 히트맵 (최근 {days}일, KST)",
                "각 셀: 실패율% (호출수). 실패 0건은 `·` 로 표시. **두자리수 % 가 패턴 위험 시점**.",
                "",
                "| 요일 \\ 시간 | " + " | ".join(block_label) + " |",
                "|---|" + ("---:|" * len(block_label)),
            ]
            # 월요일부터 표시 (사람 친화적 순서) — dow=1 부터 6, 그 다음 0(일)
            ordered_dow = [1, 2, 3, 4, 5, 6, 0]
            worst: tuple[float, str] = (0.0, "")
            for d in ordered_dow:
                cells: list[str] = []
                for b in range(6):
                    total, failures = cell_map.get((d, b), (0, 0))
                    if total == 0:
                        cells.append("·")
                    elif failures == 0:
                        cells.append(f"0% ({total})")
                    else:
                        rate = failures / total * 100
                        cells.append(f"{rate:.0f}% ({total})")
                        if rate > worst[0] and total >= 5:  # 표본 5건 이상만 패턴으로 인정
                            worst = (rate, f"{dow_label[d]}요일 {block_label[b]}시")
                lines.append(f"| **{dow_label[d]}요일** | " + " | ".join(cells) + " |")

            if worst[1]:
                lines.append("")
                lines.append(
                    f"**🔥 가장 위험한 시점:** {worst[1]} (실패율 {worst[0]:.0f}%) — "
                    "정기 점검·외부 기관 야간 작업 시간과 겹치는지 확인 권장."
                )
    except Exception:  # noqa: BLE001
        pass

    # 카테고리별 (내부 핵심 / 외부 제휴 / 외부 규제기관) 분리 집계
    # 운영자가 "지금 외부 기관 쪽이 더 문제야 내부 모듈 쪽이 더 문제야?" 같은 질문을
    # 했을 때 AI 가 사실 기반으로 답변할 수 있도록.
    cat_rows = db.execute(
        select(
            Interface.category,
            func.count(CallLog.id).label("total"),
            func.coalesce(func.sum(failure_expr), 0).label("failures"),
        )
        .join(CallLog, CallLog.interface_id == Interface.id)
        .where(CallLog.called_at >= since)
        .group_by(Interface.category)
    ).all()
    if cat_rows:
        cat_label = {
            "INTERNAL_CORE": "내부 핵심 시스템",
            "EXTERNAL_PARTNER": "외부 제휴사",
            "EXTERNAL_REGULATOR": "외부 규제기관",
        }
        lines += ["", f"### 분류별 호출/실패율 (최근 {days}일)"]
        lines += ["| 분류 | 호출수 | 실패수 | 실패율 |", "|---|---:|---:|---:|"]
        for r in cat_rows:
            key = r.category.value if hasattr(r.category, "value") else r.category
            rate = (r.failures / r.total * 100) if r.total else 0.0
            lines.append(
                f"| {cat_label.get(key, key)} | {r.total} | {r.failures} | {rate:.1f}% |"
            )

    # 미해결 incident 인터페이스별 카운트
    open_incidents = db.execute(
        select(
            Interface.name,
            func.count(Incident.id).label("cnt"),
        )
        .join(Incident, Incident.interface_id == Interface.id)
        .where(Incident.resolved_at.is_(None))
        .group_by(Interface.id)
        .order_by(func.count(Incident.id).desc())
        .limit(10)
    ).all()
    lines += [
        "",
        "### 현재 미해결 장애 (인터페이스별 카운트)",
    ]
    if open_incidents:
        lines.append("| 인터페이스 | 미해결 건수 |")
        lines.append("|---|---:|")
        for r in open_incidents:
            lines.append(f"| {r.name} | {r.cnt} |")
    else:
        lines.append("- 미해결 장애 없음 ✅")

    # 음소거 중인 인터페이스
    muted = db.scalars(
        select(Interface)
        .where(Interface.muted_until.is_not(None))
        .where(Interface.muted_until > now_kst())
    ).all()
    if muted:
        lines += [
            "",
            "### 현재 알림 음소거 중 (toast/Slack/Email skip)",
        ]
        for itf in muted:
            lines.append(f"- {itf.name} ({itf.muted_until.isoformat()} 까지)")

    return "\n".join(lines)


def build_config_context(db: Session, *, top_n: int = 30) -> str:
    """등록된 인터페이스 메타 정보 (config_query 답변용)."""
    itfs = db.scalars(
        select(Interface)
        .where(Interface.deleted_at.is_(None))
        .order_by(Interface.id.desc())
        .limit(top_n)
    ).all()
    lines: list[str] = [
        f"## 등록된 인터페이스 ({len(itfs)}건, 보관 제외)",
        "",
        "| ID | 이름 | 기관 | 프로토콜 | 메서드 | 엔드포인트 | 스케줄 | 활성 | 시크릿 |",
        "|---:|---|---|---|---|---|---|---|---|",
    ]
    for i in itfs:
        proto = i.protocol.value if hasattr(i.protocol, "value") else i.protocol
        lines.append(
            f"| {i.id} | {i.name} | {i.organization or '-'} | {proto} | "
            f"{i.method or '-'} | {i.endpoint[:60]}{'…' if len(i.endpoint) > 60 else ''} | "
            f"{i.schedule_cron or '수동'} | {'✅' if i.enabled else '⏸'} | "
            f"{'🔑' if i.auth_secret else '-'} |"
        )
    return "\n".join(lines)