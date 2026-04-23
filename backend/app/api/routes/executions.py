from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import Interface
from app.schemas.call_log import CallLogOut
from app.services.executor import execute_interface

router = APIRouter(prefix="/interfaces", tags=["executions"])


@router.post("/{interface_id}/execute", response_model=CallLogOut)
async def execute_now(
    interface_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> CallLogOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "interface is deleted — restore first")
    log = await execute_interface(obj, db, triggered_by="manual")
    return CallLogOut.model_validate(log)
