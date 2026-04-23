from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import encrypt_secret
from app.core.time import now_kst
from app.models import Interface
from app.models.interface import ProtocolType
from app.schemas.interface import InterfaceCreate, InterfaceOut, InterfaceUpdate

router = APIRouter(prefix="/interfaces", tags=["interfaces"])


@router.get("/cron-preview")
def cron_preview(expression: str, count: int = 3) -> dict:
    """Validate a cron expression and return the next N fire times in KST.

    Used by the registration dialog so operators can confirm what their
    chosen schedule actually means before saving.
    """
    from croniter import croniter

    from app.core.time import KST, now_kst

    expr = (expression or "").strip()
    if not expr:
        return {"valid": False, "error": "empty"}
    if not croniter.is_valid(expr):
        return {"valid": False, "error": "invalid cron expression"}
    it = croniter(expr, now_kst())
    next_runs = []
    for _ in range(max(1, min(count, 10))):
        nxt = it.get_next(ret_type=type(now_kst()))
        # croniter gives naive in some configs — re-localize to KST
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=KST)
        next_runs.append(nxt.isoformat())
    return {"valid": True, "next_runs": next_runs}


def _to_out(i: Interface) -> InterfaceOut:
    payload = InterfaceOut.model_validate(i)
    payload.has_secret = bool(i.auth_secret)
    return payload


@router.get("", response_model=list[InterfaceOut])
def list_interfaces(
    enabled: bool | None = None,
    organization: str | None = None,
    protocol: ProtocolType | None = None,
    include_deleted: bool = False,
    only_deleted: bool = False,
    db: Session = Depends(get_db),
) -> list[InterfaceOut]:
    stmt = select(Interface)
    if only_deleted:
        stmt = stmt.where(Interface.deleted_at.is_not(None))
    elif not include_deleted:
        stmt = stmt.where(Interface.deleted_at.is_(None))
    if enabled is not None:
        stmt = stmt.where(Interface.enabled == enabled)
    if organization:
        stmt = stmt.where(Interface.organization == organization)
    if protocol is not None:
        stmt = stmt.where(Interface.protocol == protocol)
    return [_to_out(i) for i in db.scalars(stmt.order_by(Interface.id.desc())).all()]


@router.post("", response_model=InterfaceOut, status_code=status.HTTP_201_CREATED)
def create_interface(payload: InterfaceCreate, db: Session = Depends(get_db)) -> InterfaceOut:
    data = payload.model_dump(exclude={"auth_secret"})
    obj = Interface(**data)
    if payload.auth_secret:
        obj.auth_secret = encrypt_secret(payload.auth_secret)
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "interface name already exists") from e
    db.refresh(obj)
    return _to_out(obj)


@router.get("/{interface_id}", response_model=InterfaceOut)
def get_interface(interface_id: int, db: Session = Depends(get_db)) -> InterfaceOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    return _to_out(obj)


@router.patch("/{interface_id}", response_model=InterfaceOut)
def update_interface(
    interface_id: int, payload: InterfaceUpdate, db: Session = Depends(get_db)
) -> InterfaceOut:
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "deleted interface — restore first")
    data = payload.model_dump(exclude_unset=True)
    secret = data.pop("auth_secret", None)
    for k, v in data.items():
        setattr(obj, k, v)
    if secret is not None:
        obj.auth_secret = encrypt_secret(secret) if secret else None
    db.commit()
    db.refresh(obj)
    return _to_out(obj)


@router.delete("/{interface_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_interface(interface_id: int, db: Session = Depends(get_db)) -> Response:
    """Soft delete — marks the interface as deleted but **keeps it forever**.

    The row stays in the DB indefinitely so cascade-linked call_logs /
    incidents / SLA targets remain queryable for audit (금감원 전산사고
    보고). There is intentionally NO purge / hard-delete cron — losing the
    Interface row would cascade-delete all historical evidence.

    Side effects: enabled=false (scheduler stops firing it), excluded from
    the default interface list (use ``include_deleted=true`` to see it).
    """
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is None:
        obj.deleted_at = now_kst()
        obj.enabled = False
        db.commit()
        try:
            from app.services.scheduler import sync_jobs
            sync_jobs()
        except Exception:  # noqa: BLE001
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{interface_id}/restore", response_model=InterfaceOut)
def restore_interface(interface_id: int, db: Session = Depends(get_db)) -> InterfaceOut:
    """Undelete a soft-deleted interface. Does NOT auto re-enable execution —
    operator must explicitly toggle ``enabled`` afterward."""
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    if obj.deleted_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not deleted")
    obj.deleted_at = None
    db.commit()
    db.refresh(obj)
    return _to_out(obj)
