"""Isolation Forest based anomaly score for an interface's recent call pattern."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import now_kst
from app.models import CallLog
from app.models.call_log import CallStatus

log = logging.getLogger("noahub.ai.anomaly")
MIN_TRAINING = 30  # need at least N points before scoring is meaningful


def _features(rows: list[CallLog]) -> list[list[float]]:
    return [[float(r.duration_ms), 0.0 if r.status == CallStatus.SUCCESS else 1.0] for r in rows]


def score_anomalies(db: Session, interface_id: int) -> tuple[float, bool]:
    """Train on the last 24h, score the last 1h. Returns (score, is_anomaly).

    A negative score from sklearn's IsolationForest indicates an anomaly. We
    return raw score so callers can rank multiple interfaces.
    """
    now = now_kst()
    train_since = now - timedelta(hours=24)
    score_since = now - timedelta(hours=1)

    train_rows = db.scalars(
        select(CallLog)
        .where(CallLog.interface_id == interface_id)
        .where(CallLog.called_at >= train_since)
    ).all()
    if len(train_rows) < MIN_TRAINING:
        return 0.0, False

    score_rows = [r for r in train_rows if r.called_at >= score_since]
    if not score_rows:
        return 0.0, False

    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        log.warning("scikit-learn not available; skipping anomaly scoring")
        return 0.0, False

    model = IsolationForest(contamination=0.1, random_state=42)
    model.fit(_features(train_rows))
    raw = model.decision_function(_features(score_rows))
    avg = float(raw.mean())
    return avg, avg < 0