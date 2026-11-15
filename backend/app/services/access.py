# -*- coding: utf-8 -*-
"""Кто какую технику видит. Водитель видит только закреплённую за ним, остальные по правам и юрлицам."""
from sqlalchemy import and_, false, or_
from sqlalchemy.orm import Session

from app.common import fail
from app.deps import Principal
from app.models import Vehicle
from app.services.vehicles import assigned_vehicle_ids


def view_condition(p: Principal, db: Session):
    """Условие для запросов по технике. Нет права на просмотр вообще — 403."""
    full, own = p.scope("vehicles.view"), p.scope("vehicles.view_own")
    parts = []
    if full:
        parts.append(full.cond(Vehicle.legal_entity_id))
    if own:
        parts.append(and_(own.cond(Vehicle.legal_entity_id), Vehicle.id.in_(assigned_vehicle_ids(db, p.employee_id) or {-1})))
    if not parts:
        raise fail(403, "forbidden", "Нет доступа к технике")
    return or_(*parts)


def can_view(p: Principal, db: Session, v: Vehicle) -> bool:
    if p.can("vehicles.view", v.legal_entity_id):
        return True
    return p.can("vehicles.view_own", v.legal_entity_id) and v.id in assigned_vehicle_ids(db, p.employee_id)


def lookup_condition(p: Principal, db: Session):
    """Какие машины можно выбрать в списке при создании обращения или путевого листа."""
    full, own = p.scope("vehicles.view"), p.scope("vehicles.view_own")
    parts = []
    if full:
        parts.append(full.cond(Vehicle.legal_entity_id))
    if own:
        parts.append(and_(own.cond(Vehicle.legal_entity_id), Vehicle.id.in_(assigned_vehicle_ids(db, p.employee_id) or {-1})))
    if not full and not own:
        # у сотрудника без права смотреть технику есть только право «создать обращение»: машину выбрать он может
        extra = p.scope("requests.create")
        if extra:
            parts.append(extra.cond(Vehicle.legal_entity_id))
    if not parts:
        raise fail(403, "forbidden", "Нет доступа к технике")
    return or_(*parts)


def can_pick(p: Principal, db: Session, v: Vehicle) -> bool:
    """Можно ли выбрать эту машину в обращении или путевом листе."""
    if can_view(p, db, v):
        return True
    return not p.scope("vehicles.view") and not p.scope("vehicles.view_own") and p.can("requests.create", v.legal_entity_id)


def reading_allowed(p: Principal, db: Session, v: Vehicle) -> bool:
    if p.can("readings.create", v.legal_entity_id):
        return True
    return p.can("readings.create_own", v.legal_entity_id) and v.id in assigned_vehicle_ids(db, p.employee_id)
