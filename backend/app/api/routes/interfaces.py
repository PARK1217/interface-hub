from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import encrypt_secret
from app.models import Interface
from app.models.interface import ProtocolType
from app.schemas.interface import InterfaceCreate, InterfaceOut, InterfaceUpdate
from app.api.deps import get_db

router = APIRouter(prefix="/interfaces", tags=["interfaces"])


def _to_out(i: Interface) -> InterfaceOut:
    payload = InterfaceOut.model_validate(i)
    payload.has_secret = bool(i.auth_secret)
    return payload


@router.get("", response_model=list[InterfaceOut])
def list_interfaces(
    enabled: bool | None = None,
    organization: str | None = None,
    protocol: ProtocolType | None = None,
    db: Session = Depends(get_db),
) -> list[InterfaceOut]:
    stmt = select(Interface)
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
    obj = db.get(Interface, interface_id)
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not found")
    db.delete(obj)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
