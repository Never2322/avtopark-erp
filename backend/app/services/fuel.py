# -*- coding: utf-8 -*-
"""Расход топлива по заправкам.

Считается «от полного бака до полного бака»: между двумя заправками «до полного» с известным пробегом берутся все литры,
залитые после первой (включая вторую), и делятся на пройденные километры. Частичные заправки между ними входят в литры.
Если у полной заправки нет пробега или он не вырос, отсчёт начинается заново: расход по такому отрезку не показываем.
Норма и объём бака берутся с карточки техники; если их нет, расход считается, но с нормой не сравнивается.
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import num
from app.enums import FUEL_OVERRUN_PCT
from app.models import FuelFill, VehicleSpecs


def segments(db: Session, vehicle_id: int) -> dict[int, dict]:
    """{id заправки: {"km", "liters", "per100"}} для заправок, которыми закрывается отрезок."""
    fills = db.scalars(select(FuelFill).where(FuelFill.vehicle_id == vehicle_id, FuelFill.archived_at.is_(None))
                       .order_by(FuelFill.fill_date, FuelFill.id)).all()
    out: dict[int, dict] = {}
    anchor: int | None = None
    acc = Decimal(0)
    for f in fills:
        if anchor is not None:
            acc += f.liters
        if not f.full_tank:
            continue
        if f.odometer is None:
            anchor, acc = None, Decimal(0)
            continue
        if anchor is not None and f.odometer > anchor:
            km = f.odometer - anchor
            out[f.id] = {"km": km, "liters": acc, "per100": (acc / km * 100).quantize(Decimal("0.1"))}
        anchor, acc = f.odometer, Decimal(0)
    return out


def hour_segments(db: Session, vehicle_id: int) -> dict[int, dict]:
    """То же для техники с моточасами: {id заправки: {"hours", "liters", "per_hour"}} — литры с прошлой заправки, делённые на моточасы."""
    fills = db.scalars(select(FuelFill).where(FuelFill.vehicle_id == vehicle_id, FuelFill.archived_at.is_(None), FuelFill.engine_hours.is_not(None))
                       .order_by(FuelFill.fill_date, FuelFill.id)).all()
    out: dict[int, dict] = {}
    prev = None
    for f in fills:
        if prev is not None and f.engine_hours > prev:
            h = f.engine_hours - prev
            out[f.id] = {"hours": h, "liters": f.liters, "per_hour": (f.liters / h).quantize(Decimal("0.1"))}
        prev = f.engine_hours
    return out


def norms(db: Session, vehicle_ids) -> dict[int, tuple[Decimal | None, Decimal | None]]:
    """{id техники: (объём бака, норма расхода)}"""
    ids = set(vehicle_ids)
    if not ids:
        return {}
    return {s.vehicle_id: (s.tank_liters, s.fuel_norm) for s in db.scalars(select(VehicleSpecs).where(VehicleSpecs.vehicle_id.in_(ids)))}


def over_norm(per100: Decimal | None, norm: Decimal | None) -> int | None:
    """На сколько процентов расход выше нормы, если выше порога; иначе None."""
    if per100 is None or not norm:
        return None
    pct = int((per100 / norm - 1) * 100)
    return pct if pct >= FUEL_OVERRUN_PCT else None


def remarks(fill: FuelFill, tank: Decimal | None, norm: Decimal | None, seg: dict | None) -> list[str]:
    """Что выглядит подозрительно в заправке: показывается сотруднику и водителю, запись при этом сохраняется."""
    out = []
    if tank and fill.liters > tank:
        out.append(f"Залито {num(fill.liters)} л, а бак вмещает {num(tank)} л: проверьте литры")
    if seg:
        pct = over_norm(seg["per100"], norm)
        if pct is not None:
            out.append(f"Расход {num(seg['per100'])} л на 100 км выше нормы {num(norm)} на {pct}%: проверьте литры и пробег")
    return out
