# -*- coding: utf-8 -*-
import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app.common import fail, page_of
from app.db import get_db
from app.deps import Principal, current, require
from app.enums import READING_KINDS, READING_SOURCES
from app.models import User, Vehicle, VehicleReading
from app.routers.vehicles import get_vehicle
from app.services import access, readings as svc

router = APIRouter(tags=["Показания"])


class ReadingIn(BaseModel):
    kind: str
    value: Decimal = Field(ge=0, le=9_999_999, max_digits=12, decimal_places=1)
    recorded_at: dt.datetime | None = None
    note: str = Field(default="", max_length=300)


class CorrectIn(BaseModel):
    value: Decimal = Field(ge=0, le=9_999_999, max_digits=12, decimal_places=1)
    reason: str = Field(min_length=1, max_length=300)


class ReviewIn(BaseModel):
    decision: str
    comment: str = Field(default="", max_length=300)


def out(r: VehicleReading, author: str | None, superseded_by: int | None) -> dict:
    return {
        "id": r.id, "kind": r.kind, "kind_label": READING_KINDS[r.kind], "value": float(r.value),
        "recorded_at": r.recorded_at.isoformat(), "source": r.source, "source_label": READING_SOURCES[r.source],
        "author": author, "note": r.note, "is_anomaly": r.is_anomaly, "review_state": r.review_state,
        "review_comment": r.review_comment, "corrects_id": r.corrects_id, "superseded_by": superseded_by,
        "effective": superseded_by is None and r.review_state in ("ok", "confirmed"), "waybill_id": r.waybill_id,
    }


@router.get("/vehicles/{vid}/readings")
def list_readings(vid: int, kind: str = "", page: int = 1, page_size: int = 50, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Полный журнал, включая исправленные и отклонённые записи: ничего не прячется."""
    v = get_vehicle(db, p, vid)
    newer = aliased(VehicleReading)
    q = (select(VehicleReading, User.display_name, User.login, newer.id)
         .outerjoin(User, User.id == VehicleReading.author_id)
         .outerjoin(newer, newer.corrects_id == VehicleReading.id)
         .where(VehicleReading.vehicle_id == v.id))
    if kind:
        if kind not in READING_KINDS:
            raise fail(422, "bad_kind", "Неизвестный вид показания", "kind")
        q = q.where(VehicleReading.kind == kind)
    q = q.order_by(VehicleReading.recorded_at.desc(), VehicleReading.id.desc())
    rows, total = page_of(db, q, page, page_size)
    return {"items": [out(r, name or login, sup) for r, name, login, sup in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/vehicles/{vid}/readings", status_code=201)
def add_reading(vid: int, body: ReadingIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    if not access.reading_allowed(p, db, v):
        raise fail(403, "forbidden", "Нет права вводить показания этой техники")
    r = svc.add_reading(db, p, v, body.kind, body.value, recorded_at=body.recorded_at, note=body.note.strip())
    db.commit()
    return {**out(r, p.user.display_name or p.login, None), "needs_review": r.is_anomaly,
            "current_km": float(v.current_km) if v.current_km is not None else None,
            "current_hours": float(v.current_hours) if v.current_hours is not None else None}


def reading_or_404(db: Session, p: Principal, rid: int) -> tuple[VehicleReading, Vehicle]:
    r = db.get(VehicleReading, rid)
    v = get_vehicle(db, p, r.vehicle_id) if r else None
    if not r or not v:
        raise fail(404, "not_found", "Показание не найдено")
    return r, v


@router.post("/readings/{rid}/correct", status_code=201)
def correct(rid: int, body: CorrectIn, p: Principal = Depends(require("readings.correct")), db: Session = Depends(get_db)):
    r, v = reading_or_404(db, p, rid)
    if not p.can("readings.correct", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права исправлять показания этого юрлица")
    fixed = svc.correct_reading(db, p, r, body.value, body.reason)
    db.commit()
    return out(fixed, p.user.display_name or p.login, None)


@router.post("/readings/{rid}/review")
def review(rid: int, body: ReviewIn, p: Principal = Depends(require("readings.review")), db: Session = Depends(get_db)):
    r, v = reading_or_404(db, p, rid)
    if not p.can("readings.review", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права проверять показания этого юрлица")
    svc.review_reading(db, p, r, body.decision, body.comment)
    db.commit()
    return out(r, None, None)


@router.get("/readings/pending")
def pending(legal_entity_id: int | None = None, p: Principal = Depends(require("readings.review")), db: Session = Depends(get_db)):
    """Очередь на проверку: показания, у которых скачок больше обычного."""
    from app.deps import entity_filter
    scope = entity_filter(p, "readings.review", legal_entity_id)
    rows = db.execute(select(VehicleReading, Vehicle.plate, Vehicle.make, Vehicle.model, Vehicle.legal_entity_id, User.display_name, User.login)
                      .join(Vehicle, Vehicle.id == VehicleReading.vehicle_id)
                      .outerjoin(User, User.id == VehicleReading.author_id)
                      .where(VehicleReading.review_state == "pending", scope.cond(Vehicle.legal_entity_id))
                      .order_by(VehicleReading.recorded_at)).all()
    items = []
    for r, plate, make, model, eid, name, login in rows:
        prev, _ = svc.neighbours(db, r.vehicle_id, r.kind, r.recorded_at, skip_id=r.id)
        items.append({**out(r, name or login, None), "vehicle_id": r.vehicle_id, "plate": plate, "vehicle": f"{make} {model}".strip(),
                      "previous": float(prev.value) if prev else None})
    return {"items": items}
