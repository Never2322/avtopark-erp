# -*- coding: utf-8 -*-
"""Записи водителей: рейсы и заправки с фото накладной и чека, талоны весов (тоннаж) с фото талона.

Общие операции для сотрудников (список, правка, архив, выгрузка) делает общий механизм разделов (app/registry.py, разделы «trips»,
«fuel_fills» и «weigh_tickets»). Здесь то, чего он не умеет:
- страница водителя: записи только по технике, закреплённой за ним сейчас; внести запись вместе с фото одним запросом;
- повторная отправка с телефона (код client_id) не создаёт вторую запись;
- водитель может убрать только свою запись и только в первые сутки; сотрудник — любую через архив раздела;
- фото не стираются: их «убирают» с отметкой кто и когда;
- лента «Записи водителей» для диспетчера: рейсы и заправки вперемешку за выбранные дни (талоны весов в ленту не входят: у них своя
  вкладка «Тоннаж», см. routers/tonnage.py).
"""
import datetime as dt
import json
import urllib.parse
from dataclasses import dataclass

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.db import get_db
from app.deps import Principal, current, entity_filter
from app.enums import ENTRY_DELETE_HOURS, ENTRY_MAX_PHOTOS
from app.models import EntryPhoto, FuelFill, StoredFile, Trip, Vehicle, VehicleSpecs, VehicleType, WeighTicket
from app.registry import RESOURCES, _vehicle_label, rows_out, save
from app.resources import job_today
from app.routers.dossier import read_uploads, store
from app.services import weigh as weigh_svc
from app.services.people import user_names
from app.services.vehicles import assigned_vehicle_ids

router = APIRouter(prefix="/entries", tags=["Записи водителей"])

MY_DAYS = 60              # водитель видит свои записи за столько дней
FEED_LIMIT = 300
FEED_DEFAULT_DAYS = 7


@dataclass(frozen=True)
class Kind:
    key: str
    res: str            # ключ раздела (и начало имён прав)
    model: type
    photo_col: str
    date_col: str
    title: str


KINDS = {
    "trip": Kind("trip", "trips", Trip, "trip_id", "trip_date", "Рейс"),
    "fuel": Kind("fuel", "fuel_fills", FuelFill, "fuel_fill_id", "fill_date", "Заправка"),
    "ticket": Kind("ticket", "weigh_tickets", WeighTicket, "weigh_ticket_id", "ticket_date", "Талон весов"),
}
LISTED = ("trip", "fuel")            # эти виды идут в «Мои записи» и в ленту; талоны весов живут на своей вкладке «Тоннаж»
TICKET_FIELDS = ("vehicle_id", "ticket_date", "note")    # водитель в талоне задаёт только машину и заметку (дата ставится сама); тонны читаются с фото


def kind_of(kind: str) -> Kind:
    k = KINDS.get(kind)
    if k is None:
        raise fail(404, "not_found", "Запись не найдена")
    return k


# ---------------------------------------------------------------- права

def mine(db: Session, p: Principal) -> set[int]:
    """Машины, закреплённые за человеком сейчас (в рамках одного запроса считаются один раз)."""
    if "entry_vehicles" not in p._cache:
        p._cache["entry_vehicles"] = assigned_vehicle_ids(db, p.employee_id)
    return p._cache["entry_vehicles"]


def can_view(db: Session, p: Principal, k: Kind, obj) -> bool:
    return p.can(f"{k.res}.view", obj.legal_entity_id) or (p.can(f"{k.res}.view_own", obj.legal_entity_id) and obj.vehicle_id in mine(db, p))


def own_fresh(db: Session, p: Principal, k: Kind, obj) -> bool:
    """Водитель может трогать только свою запись, пока ей нет суток, пока она не в архиве и пока машина закреплена за ним."""
    return (p.can(f"{k.res}.create_own", obj.legal_entity_id) and obj.created_by_id == p.id and obj.archived_at is None
            and obj.vehicle_id in mine(db, p) and utcnow() - obj.created_at <= dt.timedelta(hours=ENTRY_DELETE_HOURS))


def photo_mode(db: Session, p: Principal, k: Kind, obj) -> str | None:
    if obj.archived_at is not None:
        return None
    if p.can(f"{k.res}.edit", obj.legal_entity_id):
        return "staff"
    return "driver" if own_fresh(db, p, k, obj) else None


def get_obj(db: Session, p: Principal, k: Kind, oid: int):
    obj = db.get(k.model, oid)
    if obj is None or not can_view(db, p, k, obj):
        raise fail(404, "not_found", "Запись не найдена")
    return obj


# ---------------------------------------------------------------- вывод

def live_photos(db: Session, k: Kind, ids: list[int]) -> dict[int, list[EntryPhoto]]:
    out: dict[int, list[EntryPhoto]] = {}
    if ids:
        col = getattr(EntryPhoto, k.photo_col)
        for ph in db.scalars(select(EntryPhoto).where(col.in_(ids), EntryPhoto.removed_at.is_(None)).order_by(EntryPhoto.id)):
            out.setdefault(getattr(ph, k.photo_col), []).append(ph)
    return out


def items_out(db: Session, p: Principal, k: Kind, objs: list) -> list[dict]:
    if not objs:
        return []
    rows = rows_out(db, RESOURCES[k.res], objs, p)
    photos = live_photos(db, k, [o.id for o in objs])
    names = user_names(db, {o.created_by_id for o in objs})
    out = []
    for o, row in zip(objs, rows):
        pics = photos.get(o.id, [])
        mode = photo_mode(db, p, k, o)
        row.update({
            "kind": k.key, "kind_label": k.title, "date": row[k.date_col], "created_by": names.get(o.created_by_id, ""),
            "mine": o.created_by_id == p.id,
            "pics": [{"id": x.id, "name": x.file.original_name, "has_thumb": x.file.thumb is not None} for x in pics],
            "can": {"photo": mode is not None and len(pics) < ENTRY_MAX_PHOTOS, "remove_photo": mode is not None,
                    "delete": o.archived_at is None and own_fresh(db, p, k, o)},
        })
        out.append(row)
    return out


def item_out(db: Session, p: Principal, k: Kind, obj) -> dict:
    return items_out(db, p, k, [obj])[0]


def sort_key(it: dict):
    return (it["date"], it["created_at"], it["id"])


# ---------------------------------------------------------------- страница водителя

@router.get("/mine")
def my_entries(p: Principal = Depends(current), db: Session = Depends(get_db)):
    ids = mine(db, p)
    vehicles = []
    if ids:
        specs = {s.vehicle_id: s for s in db.scalars(select(VehicleSpecs).where(VehicleSpecs.vehicle_id.in_(ids)))}
        for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(ids), Vehicle.archived_at.is_(None)).order_by(Vehicle.plate)):
            s = specs.get(v.id)
            vehicles.append({"id": v.id, "label": _vehicle_label(v), "legal_entity_id": v.legal_entity_id,
                             "tank_liters": float(s.tank_liters) if s and s.tank_liters else None,
                             "fuel_norm": float(s.fuel_norm) if s and s.fuel_norm else None,
                             "meter": "engine_hours" if db.get(VehicleType, v.type_id).primary_reading == "engine_hours" else "km"})
    since = job_today() - dt.timedelta(days=MY_DAYS)
    items: list[dict] = []
    can = {}
    for k in (KINDS[x] for x in LISTED):
        view = p.scope(f"{k.res}.view_own") | p.scope(f"{k.res}.view")
        can[k.key] = bool(vehicles) and p.can(f"{k.res}.create_own") and p.employee_id is not None
        if not (ids and view):
            continue
        q = (select(k.model).where(k.model.vehicle_id.in_(ids), k.model.archived_at.is_(None), getattr(k.model, k.date_col) >= since,
                                   view.cond(k.model.legal_entity_id))
             .order_by(getattr(k.model, k.date_col).desc(), k.model.id.desc()).limit(100))
        items += items_out(db, p, k, list(db.scalars(q)))
    items.sort(key=sort_key, reverse=True)
    return {"vehicles": vehicles, "items": items, "can": can, "today": job_today().isoformat(),
            "max_photos": ENTRY_MAX_PHOTOS, "delete_hours": ENTRY_DELETE_HOURS}


def find_duplicate(db: Session, p: Principal, k: Kind, client_id: str):
    if not client_id:
        return None
    return db.scalar(select(k.model).where(k.model.created_by_id == p.id, k.model.client_id == client_id))


def attach_photos(db: Session, p: Principal, k: Kind, obj, prepared) -> int:
    stored = store(db, p, prepared)
    for sf in stored:
        db.add(EntryPhoto(**{k.photo_col: obj.id}, file_id=sf.id, added_by_id=p.id))
    audit.log(db, p, "entries.photo.add", k.res, obj.id, obj.legal_entity_id, count=len(stored))
    db.commit()
    return len(stored)


@router.post("/mine/{kind}", status_code=201)
def add_mine(kind: str, background: BackgroundTasks, data: str = Form(...), files: list[UploadFile] = File(default=[]),
             p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Водитель вносит рейс, заправку или талон весов. data — поля записи в виде JSON, files — фото накладной, чека или талона.
    У талона водитель задаёт только машину и заметку: тонны программа читает с фото (в фоне, после ответа)."""
    k = kind_of(kind)
    code = f"{k.res}.create_own"
    if not p.can(code):
        raise fail(403, "forbidden", "Нет права вносить такие записи")
    if p.employee_id is None:
        raise fail(409, "no_employee", "К вашей учётной записи не привязан сотрудник: обратитесь к администратору")
    try:
        raw = json.loads(data)
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        raise fail(422, "bad_data", "Не удалось прочитать форму: обновите страницу и повторите")
    cid = str(raw.get("client_id") or "")[:64]
    body = {key: val for key, val in raw.items() if key not in ("driver_id", "legal_entity_id", "client_id")}
    ticket = k.key == "ticket"
    if ticket:
        body = {key: val for key, val in body.items() if key in TICKET_FIELDS}
        body.setdefault("ticket_date", job_today().isoformat())
    prepared = read_uploads(files, images_only=True, required=False)
    if len(prepared) > ENTRY_MAX_PHOTOS:
        raise fail(422, "too_many_photos", f"Не больше {ENTRY_MAX_PHOTOS} фото к одной записи", "files")
    dup = find_duplicate(db, p, k, cid)
    if dup is not None:
        # та же отправка пришла второй раз (телефон не получил ответ): записи не дублируем, но дозагружаем фото, если их ещё нет
        if prepared and not live_photos(db, k, [dup.id]).get(dup.id) and own_fresh(db, p, k, dup):
            attach_photos(db, p, k, dup, prepared)
            if ticket:
                background.add_task(weigh_svc.read_ticket, dup.id)
        return {"item": item_out(db, p, k, dup), "warnings": [], "duplicate": True}
    if ticket and not prepared:
        raise fail(422, "no_file", "Приложите фото талона", "files")
    try:
        vid = int(body.get("vehicle_id"))
    except (TypeError, ValueError):
        raise fail(422, "validation", "Выберите технику", "vehicle_id") from None
    if vid not in mine(db, p):
        raise fail(422, "validation", "Выберите свою технику из списка", "vehicle_id")
    obj, warnings = save(db, RESOURCES[k.res], p, body, None, code=code, extra={"driver_id": p.employee_id, "client_id": cid})
    if prepared:
        attach_photos(db, p, k, obj, prepared)
    if ticket:
        background.add_task(weigh_svc.read_ticket, obj.id)
    return {"item": item_out(db, p, k, obj), "warnings": warnings, "duplicate": False}


@router.post("/mine/{kind}/{oid}/delete")
def delete_mine(kind: str, oid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    k = kind_of(kind)
    obj = get_obj(db, p, k, oid)
    if not own_fresh(db, p, k, obj):
        raise fail(403, "forbidden", f"Убрать можно только свою запись и только в первые {ENTRY_DELETE_HOURS} ч: обратитесь к диспетчеру")
    obj.archived_at, obj.archived_by_id, obj.archive_reason, obj.updated_by_id = utcnow(), p.id, "Убрал водитель", p.id
    audit.log(db, p, "entries.driver_void", k.res, obj.id, obj.legal_entity_id, kind=k.key)
    db.commit()
    note = ""
    if k.key == "fuel" and (obj.odometer is not None or obj.engine_hours is not None):
        note = "Пробег или моточасы из этой заправки остался в показаниях техники: если он неверный, скажите механику, он исправит"
    return {"ok": True, "note": note}


# ---------------------------------------------------------------- фото

@router.post("/{kind}/{oid}/photos")
def add_photos(kind: str, oid: int, background: BackgroundTasks, files: list[UploadFile] = File(default=[]), p: Principal = Depends(current),
               db: Session = Depends(get_db)):
    k = kind_of(kind)
    obj = get_obj(db, p, k, oid)
    if photo_mode(db, p, k, obj) is None:
        raise fail(403, "forbidden", "К этой записи фото уже не добавить")
    prepared = read_uploads(files, images_only=True, required=True)
    have = len(live_photos(db, k, [obj.id]).get(obj.id, []))
    if have + len(prepared) > ENTRY_MAX_PHOTOS:
        raise fail(422, "too_many_photos", f"Не больше {ENTRY_MAX_PHOTOS} фото к одной записи", "files")
    attach_photos(db, p, k, obj, prepared)
    if k.key == "ticket" and obj.tons is None:
        background.add_task(weigh_svc.read_ticket, obj.id)         # вес ещё не определён: новое фото может помочь
    return {"item": item_out(db, p, k, obj)}


def get_photo(db: Session, p: Principal, pid: int) -> tuple[EntryPhoto, Kind, object]:
    ph = db.get(EntryPhoto, pid)
    if ph is not None:
        k = KINDS["trip"] if ph.trip_id else KINDS["ticket"] if ph.weigh_ticket_id else KINDS["fuel"]
        obj = db.get(k.model, getattr(ph, k.photo_col))
        if obj is not None and can_view(db, p, k, obj):
            return ph, k, obj
    raise fail(404, "not_found", "Фото не найдено")


@router.get("/photos/{pid}")
def photo_file(pid: int, thumb: bool = False, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ph, _k, _obj = get_photo(db, p, pid)
    if ph.removed_at is not None:
        raise fail(404, "not_found", "Фото не найдено")
    sf = db.get(StoredFile, ph.file_id)
    data, ctype = (sf.thumb, "image/jpeg") if thumb and sf.thumb else (sf.data, sf.content_type)
    name = urllib.parse.quote(sf.original_name or "photo")
    return Response(content=data, media_type=ctype,
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{name}", "Cache-Control": "private, no-store"})


class RemoveIn(BaseModel):
    reason: str = Field("", max_length=300)


@router.post("/photos/{pid}/remove")
def remove_photo(pid: int, body: RemoveIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ph, k, obj = get_photo(db, p, pid)
    if ph.removed_at is not None:
        raise fail(404, "not_found", "Фото не найдено")
    if photo_mode(db, p, k, obj) is None:
        raise fail(403, "forbidden", "Из этой записи фото уже не убрать: обратитесь к диспетчеру")
    ph.removed_at, ph.removed_by_id, ph.remove_reason = utcnow(), p.id, body.reason.strip() or "Убрано"
    obj.updated_by_id = p.id
    audit.log(db, p, "entries.photo.remove", k.res, obj.id, obj.legal_entity_id, photo_id=ph.id, reason=ph.remove_reason)
    db.commit()
    return {"item": item_out(db, p, k, obj)}


# ---------------------------------------------------------------- лента для сотрудников

@router.get("/feed")
def feed(date_from: str | None = None, date_to: str | None = None, kind: str = "", vehicle_id: int | None = None,
         problems: bool = False, legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Рейсы и заправки вперемешку, свежие сверху. «Только подозрительные» — заправки, у которых есть замечания по литрам или расходу."""
    kinds = [k for k in (KINDS[x] for x in LISTED) if (not kind or kind == k.key) and p.scope(f"{k.res}.view")]
    if not kinds and not p.scope("trips.view") and not p.scope("fuel_fills.view"):
        raise fail(403, "forbidden", "Нет права смотреть записи водителей")
    try:
        d_to = dt.date.fromisoformat(date_to) if date_to else job_today()
        d_from = dt.date.fromisoformat(date_from) if date_from else d_to - dt.timedelta(days=FEED_DEFAULT_DAYS - 1)
    except ValueError:
        raise fail(422, "bad_date", "Введите дату") from None
    items: list[dict] = []
    totals = {"trips": 0, "fills": 0, "liters": 0.0, "amount": 0.0}
    truncated = False
    for k in kinds:
        scope = entity_filter(p, f"{k.res}.view", legal_entity_id)
        date_col = getattr(k.model, k.date_col)
        where = [k.model.archived_at.is_(None), date_col >= d_from, date_col <= d_to, scope.cond(k.model.legal_entity_id)]
        if vehicle_id:
            where.append(k.model.vehicle_id == vehicle_id)
        if k.key == "trip":
            totals["trips"] = db.scalar(select(func.count()).select_from(Trip).where(*where)) or 0
        else:
            n, liters, amount = db.execute(select(func.count(), func.coalesce(func.sum(FuelFill.liters), 0),
                                                  func.coalesce(func.sum(FuelFill.amount), 0)).where(*where)).one()
            totals.update(fills=n, liters=float(liters), amount=float(amount))
        rows = list(db.scalars(select(k.model).where(*where).order_by(date_col.desc(), k.model.id.desc()).limit(FEED_LIMIT)))
        truncated = truncated or len(rows) >= FEED_LIMIT
        items += items_out(db, p, k, rows)
    if problems:
        items = [i for i in items if i.get("check_notes")]
    items.sort(key=sort_key, reverse=True)
    return {"items": items[:FEED_LIMIT], "totals": totals, "truncated": truncated, "date_from": d_from.isoformat(), "date_to": d_to.isoformat()}


@router.get("/{kind}/{oid}")
def one_entry(kind: str, oid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    k = kind_of(kind)
    return {"item": item_out(db, p, k, get_obj(db, p, k, oid))}
