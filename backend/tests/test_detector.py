from datetime import datetime, timezone

from app.models import CallLog, Interface
from app.models.call_log import CallStatus


def _mk_call(status=CallStatus.SUCCESS, duration_ms=100):
    return CallLog(
        interface_id=1,
        status=status,
        http_status=200 if status == CallStatus.SUCCESS else 500,
        duration_ms=duration_ms,
        called_at=datetime.now(timezone.utc),
        triggered_by="manual",
    )


def test_classify_status():
    from app.services.executor import _classify

    import httpx

    assert _classify(200, None) == CallStatus.SUCCESS
    assert _classify(401, None) == CallStatus.AUTH_ERROR
    assert _classify(422, None) == CallStatus.FORMAT_ERROR
    assert _classify(503, None) == CallStatus.SERVER_ERROR
    assert _classify(None, httpx.TimeoutException("t")) == CallStatus.TIMEOUT
    assert _classify(None, RuntimeError("boom")) == CallStatus.FAILURE
