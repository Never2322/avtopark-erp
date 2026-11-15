# -*- coding: utf-8 -*-
"""Показания пробега и моточасов.

Правила (из критериев готовности): показание нельзя незаметно переписать; значения не уменьшаются; странные скачки
уходят на проверку; исправление — это новая запись, старая остаётся в журнале.
"""
import datetime as dt
from decimal import Decimal

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, aliased

from app import audit
from app.common import fail, num, utcnow
from app.config import get_settings
from app.enums import EFFECTIVE_STATES, READING_KINDS
from app.models import Vehicle, VehicleReading


def effective(vehicle_id: int, kind: str | None = None):
    """Запрос «действующих» показаний: не заменённых исправлением и не отклонённых, не ждущих проверки."""
    newer = aliased(VehicleReading)
    q = select(VehicleReading).where(
        VehicleReading.vehicle_id == vehicle_id,
        VehicleReading.review_state.in_(EFFECTIVE_STATES),
        ~exists().where(newer.corrects_id == VehicleReading.id),
    )
    if kind:
        q = q.where(VehicleReading.kind == kind)
    return q


def neighbours(db: Session, vehicle_id: int, kind: str, at: dt.datetime, skip_id: int | None = None):
    """Ближайшие действующие показания до и после момента at (по времени записи)."""
    base = effective(vehicle_id, kind)
    if skip_id:
        base = base.where(VehicleReading.id != skip_id)
    prev = db.scalar(base.where(VehicleReading.recorded_at <= at).order_by(VehicleReading.recorded_at.desc(), VehicleReading.id.desc()).limit(1))
    nxt = db.scalar(base.where(VehicleReading.recorded_at > at).order_by(VehicleReading.recorded_at, VehicleReading.id).limit(1))
    return prev, nxt


def recompute_current(db: Session, vehicle: Vehicle) -> None:
    """Текущие км и моточасы машины — это последние действующие показания. Пересчитываются, руками не правятся."""
    for kind, attr in (("km", "current_km"), ("engine_hours", "current_hours")):
        last = db.scalar(effective(vehicle.id, kind).order_by(VehicleReading.recorded_at.desc(), VehicleReading.id.desc()).limit(1))
        setattr(vehicle, attr, last.value if last else None)


def _check_order(prev, nxt, value: Decimal, kind: str):
    unit = READING_KINDS[kind].split(", ")[1]
    if prev and value < prev.value:
        raise fail(422, "reading_decreased", f"Показание не может быть меньше предыдущего: было {num(prev.value)} {unit}", "value")
    if nxt and value > nxt.value:
        raise fail(422, "reading_conflict", f"Показание больше более позднего ({num(nxt.value)} {unit}): проверьте дату", "value")


def add_reading(db: Session, who, vehicle: Vehicle, kind: str, value: Decimal, *, recorded_at: dt.datetime | None = None,
                source: str = "manual", note: str = "", waybill_id: int | None = None, recompute: bool = True) -> VehicleReading:
    """recompute=False нужен загрузке из файла: текущие показания машины пересчитываются один раз в конце, а не на каждой строке."""
    if kind not in READING_KINDS:
        raise fail(422, "bad_kind", "Неизвестный вид показания", "kind")
    if vehicle.archived_at:
        raise fail(409, "vehicle_archived", "Машина в архиве: показания не вводятся")
    now = utcnow()
    at = recorded_at or now
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    if at > now + dt.timedelta(minutes=5):
        raise fail(422, "reading_future", "Дата показания не может быть в будущем", "recorded_at")
    prev, nxt = neighbours(db, vehicle.id, kind, at)
    _check_order(prev, nxt, value, kind)
    jump_limit = get_settings().reading_jump_km if kind == "km" else get_settings().reading_jump_hours
    anomaly = bool(prev and value - prev.value > jump_limit)
    reading = VehicleReading(
        vehicle_id=vehicle.id, kind=kind, value=value, recorded_at=at, source=source,
        author_id=who.user.id if who else None, note=note[:300], waybill_id=waybill_id,
        is_anomaly=anomaly, review_state="pending" if anomaly else "ok")
    db.add(reading)
    db.flush()
    if recompute:
        recompute_current(db, vehicle)
    audit.log(db, who, "reading.create", "vehicle", vehicle.id, vehicle.legal_entity_id, reading_id=reading.id,
              kind=kind, value=str(value), source=source, needs_review=anomaly)
    return reading


def correct_reading(db: Session, who, reading: VehicleReading, value: Decimal, reason: str) -> VehicleReading:
    vehicle = db.get(Vehicle, reading.vehicle_id)
    if not reason.strip():
        raise fail(422, "reason_required", "Укажите причину исправления", "reason")
    still_effective = db.scalar(effective(vehicle.id, reading.kind).where(VehicleReading.id == reading.id))
    if not still_effective:
        raise fail(409, "reading_not_current", "Исправить можно только действующее показание")
    prev, nxt = neighbours(db, vehicle.id, reading.kind, reading.recorded_at, skip_id=reading.id)
    _check_order(prev, nxt, value, reading.kind)
    fixed = VehicleReading(
        vehicle_id=vehicle.id, kind=reading.kind, value=value, recorded_at=reading.recorded_at, source=reading.source,
        author_id=who.user.id, note=("Исправление: " + reason.strip())[:300], waybill_id=reading.waybill_id,
        corrects_id=reading.id, review_state="ok")
    db.add(fixed)
    db.flush()
    recompute_current(db, vehicle)
    audit.log(db, who, "reading.correct", "vehicle", vehicle.id, vehicle.legal_entity_id, reading_id=fixed.id,
              corrects=reading.id, before=str(reading.value), after=str(value), reason=reason.strip())
    return fixed


def review_reading(db: Session, who, reading: VehicleReading, decision: str, comment: str) -> VehicleReading:
    if reading.review_state != "pending":
        raise fail(409, "not_pending", "Это показание уже проверено")
    if decision not in ("confirm", "reject"):
        raise fail(422, "bad_decision", "Выберите «подтвердить» или «отклонить»", "decision")
    if decision == "reject" and not comment.strip():
        raise fail(422, "reason_required", "Укажите, почему показание отклонено", "comment")
    vehicle = db.get(Vehicle, reading.vehicle_id)
    if decision == "confirm":
        # подтверждаем, только если порядок значений не нарушается (пока показание ждало, могли ввести другие)
        prev, nxt = neighbours(db, vehicle.id, reading.kind, reading.recorded_at, skip_id=reading.id)
        _check_order(prev, nxt, reading.value, reading.kind)
    reading.review_state = "confirmed" if decision == "confirm" else "rejected"
    reading.reviewed_by_id = who.user.id
    reading.reviewed_at = utcnow()
    reading.review_comment = comment.strip()[:300]
    db.flush()
    recompute_current(db, vehicle)
    audit.log(db, who, "reading.review", "vehicle", vehicle.id, vehicle.legal_entity_id, reading_id=reading.id,
              decision=decision, comment=comment.strip())
    return reading
