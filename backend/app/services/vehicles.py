# -*- coding: utf-8 -*-
"""Статусы техники и закрепление водителей."""
import datetime as dt

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.enums import STATUS_NEEDS_REASON, STATUS_TERMINAL, VEHICLE_STATUSES
from app.models import Employee, EmploymentAssignment, Vehicle, VehicleAssignment, VehicleStatusHistory


def change_status(db: Session, who, vehicle: Vehicle, status: str, reason: str = "", request_id: int | None = None) -> bool:
    """Меняет статус и пишет историю. Возвращает False, если статус уже такой (запись не дублируется)."""
    if status not in VEHICLE_STATUSES:
        raise fail(422, "bad_status", "Неизвестный статус", "status")
    if vehicle.archived_at:
        raise fail(409, "vehicle_archived", "Машина в архиве: статус не меняется")
    if vehicle.status in STATUS_TERMINAL:
        raise fail(409, "vehicle_written_off", "Машина списана: статус больше не меняется")
    if vehicle.status == status:
        return False
    reason = (reason or "").strip()
    if status in STATUS_NEEDS_REASON and not reason:
        raise fail(422, "reason_required", f"Для статуса «{VEHICLE_STATUSES[status]}» укажите причину", "reason")
    old = vehicle.status
    vehicle.status, vehicle.status_changed_at = status, utcnow()
    db.add(VehicleStatusHistory(vehicle_id=vehicle.id, from_status=old, status=status, reason=reason[:300],
                                changed_by_id=who.user.id if who else None, request_id=request_id))
    audit.log(db, who, "vehicle.status", "vehicle", vehicle.id, vehicle.legal_entity_id,
              from_status=old, to_status=status, reason=reason, request_id=request_id)
    return True


def active_assignment_filter(on: dt.date | None = None):
    on = on or dt.date.today()
    return and_(VehicleAssignment.started_on <= on, or_(VehicleAssignment.ended_on.is_(None), VehicleAssignment.ended_on >= on))


def assigned_vehicle_ids(db: Session, employee_id: int | None) -> set[int]:
    """Машины, закреплённые сейчас за сотрудником: водитель видит только их."""
    if not employee_id:
        return set()
    return set(db.scalars(select(VehicleAssignment.vehicle_id).where(
        VehicleAssignment.employee_id == employee_id, active_assignment_filter())))


def employee_in_entity(db: Session, employee: Employee, legal_entity_id: int, on: dt.date | None = None) -> bool:
    on = on or dt.date.today()
    return db.scalar(select(EmploymentAssignment.id).where(
        EmploymentAssignment.employee_id == employee.id, EmploymentAssignment.legal_entity_id == legal_entity_id,
        or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= on),
        or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= on)).limit(1)) is not None


def current_driver_id(db: Session, vehicle_id: int, on: dt.date | None = None) -> int | None:
    """Кто закреплён за машиной сейчас; если закреплено несколько — тот, кого закрепили последним."""
    return db.scalar(select(VehicleAssignment.employee_id).where(
        VehicleAssignment.vehicle_id == vehicle_id, active_assignment_filter(on))
        .order_by(VehicleAssignment.started_on.desc(), VehicleAssignment.id.desc()).limit(1))
