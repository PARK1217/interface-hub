"""Timezone utilities — application standard is KST (Asia/Seoul)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """Timezone-aware 'now' in KST. Use this instead of datetime.now(timezone.utc)."""
    return datetime.now(KST)