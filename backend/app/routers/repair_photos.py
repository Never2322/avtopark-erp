# -*- coding: utf-8 -*-
"""Фото ремонта: техника отфотографирована после ремонта, записано, что отремонтировали, директор нажимает «Подтвердить».

Здесь же вся история: любую запись можно открыть и посмотреть фото в любое время.

Правила:
  - добавляет и меняет тот, у кого есть repair_photos.create / edit (механик, директор);
  - подтверждает тот, у кого есть repair_photos.confirm (директор). Подтверждённая запись не меняется (ни текст, ни фото),
    пока подтверждение не снято: так результат нельзя «подправить» после того, как директор его принял;
  - запись и фото не стираются: запись убирают в архив с причиной, фото убирают с отметкой, кто и почему;
  - права смотрятся по юрлицу техники.
"""
import datetime as dt
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, org_today, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current, require
from app.enums import REPAIR_PHOTO_MAX, REPAIR_WHAT_MAX, REQUEST_STATUSES
from app.models import LegalEntity, RepairResult, RepairResultPhoto, ServiceRequest, StoredFile, User, Vehicle
from app.routers.dossier import read_uploads, store
from app.routers.requests_ import visible as request_visible
from app.services import notify

router = APIRouter(prefix="/repair-photos", tags=["Фото ремонта"])

PREVIEW = 4                 # сколько маленьких фото показывать в строке списка
OPTION_REQUESTS = 30        # сколько обращений техники предлагать в форме


class UpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: str | None = Field(default=None, max_length=REPAIR_WHAT_MAX * 2)
    repair_date: dt.date | None = None
    request_id: int | None = None
    clear_request: bool = False


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=300)


# ---------------------------------------------------------------- вспомогательное

def vlabel(v: Vehicle | None) -> str:
    return f"{v.plate} · {(v.make + ' ' + v.model).strip()}".rstrip(" ·") if v else ""


def names(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.display_name or u.login) for u in db.scalars(select(User).where(User.id.in_(ids)))}


REFUSALS = {"repair_photos.edit": "Менять фото ремонта вам нельзя", "repair_photos.confirm": "Подтверждать ремонт может директор",
            "repair_photos.void": "Убирать записи в архив может директор"}


def get_result(db: Session, p: Principal, rid: int, perm: str = "repair_photos.view") -> RepairResult:
    """Запись видна тому, у кого есть право смотреть. Если смотреть можно, а нужного действия нет, это отказ с понятным текстом."""
    r = db.get(RepairResult, rid)
    if not r or not p.can("repair_photos.view", r.legal_entity_id):
        raise fail(404, "not_found", "Запись не найдена")
    if perm != "repair_photos.view" and not p.can(perm, r.legal_entity_id):
        raise fail(403, "forbidden", REFUSALS.get(perm, "Нет доступа к этому действию"))
    return r


def live_photos(db: Session, ids: list[int]) -> dict[int, list[RepairResultPhoto]]:
    out: dict[int, list[RepairResultPhoto]] = {}
    if not ids:
        return out
    q = select(RepairResultPhoto).where(RepairResultPhoto.result_id.in_(ids), RepairResultPhoto.removed_at.is_(None)).order_by(RepairResultPhoto.id)
    for ph in db.scalars(q):
        out.setdefault(ph.result_id, []).append(ph)
    return out


def locked_reason(r: RepairResult) -> str | None:
    """Почему запись сейчас менять нельзя (или None, если можно)."""
    if r.archived_at is not None:
        return "Запись в архиве: сначала верните её из архива"
    if r.confirmed_at is not None:
        return "Ремонт подтверждён, менять его нельзя. Чтобы исправить, директор снимает подтверждение"
    return None


def check_editable(r: RepairResult) -> None:
    why = locked_reason(r)
    if why:
        raise fail(409, "locked", why)


def row_out(p: Principal, r: RepairResult, photos: list[RepairResultPhoto], who: dict[int, str], req: ServiceRequest | None) -> dict:
    unlocked = locked_reason(r) is None
    return {
        "id": r.id, "legal_entity_id": r.legal_entity_id, "vehicle_id": r.vehicle_id, "vehicle": vlabel(r.vehicle), "plate": r.vehicle.plate,
        "repair_date": r.repair_date.isoformat(), "what": r.what,
        "request_id": r.request_id, "request_number": req.number if req else None, "request_title": req.title if req else None,
        "photo_count": len(photos), "preview": [ph.id for ph in photos[:PREVIEW]],
        "confirmed": r.confirmed_at is not None, "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        "confirmed_by": who.get(r.confirmed_by_id), "created_at": r.created_at.isoformat(), "created_by": who.get(r.created_by_id),
        "archived": r.archived_at is not None, "archive_reason": r.archive_reason if r.archived_at else "",
        "can_edit": unlocked and p.can("repair_photos.edit", r.legal_entity_id),
        "can_confirm": r.confirmed_at is None and r.archived_at is None and p.can("repair_photos.confirm", r.legal_entity_id),
    }


def items_out(db: Session, p: Principal, rows: list[RepairResult]) -> list[dict]:
    photos = live_photos(db, [r.id for r in rows])
    who = names(db, [x for r in rows for x in (r.created_by_id, r.confirmed_by_id)])
    reqs = {q.id: q for q in db.scalars(select(ServiceRequest).where(ServiceRequest.id.in_([r.request_id for r in rows if r.request_id])))} if rows else {}
    return [row_out(p, r, photos.get(r.id, []), who, reqs.get(r.request_id)) for r in rows]


def detail_out(db: Session, p: Principal, r: RepairResult) -> dict:
    out = items_out(db, p, [r])[0]
    live = live_photos(db, [r.id]).get(r.id, [])
    gone = list(db.scalars(select(RepairResultPhoto).where(RepairResultPhoto.result_id == r.id, RepairResultPhoto.removed_at.is_not(None))
                           .order_by(RepairResultPhoto.removed_at)))
    who = names(db, [x for ph in live + gone for x in (ph.added_by_id, ph.removed_by_id)] + [r.archived_by_id, r.updated_by_id])
    files = {f.id: f for f in db.scalars(select(StoredFile).where(StoredFile.id.in_([ph.file_id for ph in live])))} if live else {}
    ent = db.get(LegalEntity, r.legal_entity_id)
    req = db.get(ServiceRequest, r.request_id) if r.request_id else None
    can_remove = out["can_edit"] and len(live) > 1
    out.update({
        "legal_entity": (ent.short_name or ent.name) if ent else "",
        "photos": [{"id": ph.id, "name": files[ph.file_id].original_name if ph.file_id in files else "", "added_at": ph.added_at.isoformat(),
                    "added_by": who.get(ph.added_by_id), "can_remove": can_remove} for ph in live],
        "removed": [{"id": ph.id, "removed_at": ph.removed_at.isoformat(), "removed_by": who.get(ph.removed_by_id), "reason": ph.remove_reason}
                    for ph in gone],
        "photos_left": max(REPAIR_PHOTO_MAX - len(live), 0),
        "request_status": REQUEST_STATUSES.get(req.status) if req else None,
        "archived_by": who.get(r.archived_by_id),
        "can_unconfirm": r.confirmed_at is not None and r.archived_at is None and p.can("repair_photos.confirm", r.legal_entity_id),
        "can_void": p.can("repair_photos.void", r.legal_entity_id),
        "can_add_photos": out["can_edit"] and len(live) < REPAIR_PHOTO_MAX,
    })
    return out


def parse_date(value: str | None) -> dt.date:
    if not (value or "").strip():
        return org_today()
    try:
        d = dt.date.fromisoformat(value.strip())
    except ValueError:
        raise fail(422, "bad_date", "Введите дату ремонта", "repair_date") from None
    check_date(d)
    return d


def check_date(d: dt.date) -> None:
    if d > org_today():
        raise fail(422, "bad_date", "Дата ремонта не может быть в будущем", "repair_date")
    if d.year < 2000:
        raise fail(422, "bad_date", "Проверьте дату ремонта", "repair_date")


def clean_what(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise fail(422, "required", "Напишите, что отремонтировали", "what")
    if len(text) > REPAIR_WHAT_MAX:
        raise fail(422, "too_long", f"Не больше {REPAIR_WHAT_MAX} знаков", "what")
    return text


def pick_request(db: Session, p: Principal, request_id: int, vehicle_id: int) -> ServiceRequest:
    req = db.get(ServiceRequest, request_id)
    if not req or not request_visible(p, req):
        raise fail(422, "bad_request", "Обращение не найдено", "request_id")
    if req.vehicle_id != vehicle_id:
        raise fail(422, "bad_request", "Это обращение относится к другой технике", "request_id")
    return req


def attach(db: Session, p: Principal, r: RepairResult, files: list[UploadFile]) -> int:
    prepared = read_uploads(files, images_only=True, required=True)
    have = len(live_photos(db, [r.id]).get(r.id, []))
    if have + len(prepared) > REPAIR_PHOTO_MAX:
        raise fail(422, "too_many_photos", f"К одному ремонту можно приложить не больше {REPAIR_PHOTO_MAX} фото", "files")
    for sf in store(db, p, prepared):
        db.add(RepairResultPhoto(result_id=r.id, file_id=sf.id, added_by_id=p.id))
    db.flush()
    return len(prepared)


# ---------------------------------------------------------------- выбор в форме

@router.get("/options")
def options(vehicle_id: int | None = None, legal_entity_id: int | None = None, p: Principal = Depends(require("repair_photos.create")),
            db: Session = Depends(get_db)):
    """Какую технику можно выбрать и какие обращения у выбранной техники (чтобы привязать ремонт к обращению)."""
    scope = p.scope("repair_photos.create")
    if legal_entity_id is not None:
        scope = scope.narrow(legal_entity_id)
    ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
    vehicles = [{"id": v.id, "label": vlabel(v), "legal_entity_id": v.legal_entity_id, "legal_entity": ents.get(v.legal_entity_id, "")}
                for v in db.scalars(select(Vehicle).where(scope.cond(Vehicle.legal_entity_id), Vehicle.archived_at.is_(None)).order_by(Vehicle.plate).limit(500))]
    requests: list[dict] = []
    v = db.get(Vehicle, vehicle_id) if vehicle_id else None
    if v and scope.allows(v.legal_entity_id):
        q = (select(ServiceRequest).where(ServiceRequest.vehicle_id == v.id).order_by(ServiceRequest.id.desc()).limit(OPTION_REQUESTS))
        requests = [{"id": r.id, "number": r.number, "title": r.title, "status": REQUEST_STATUSES.get(r.status, r.status)}
                    for r in db.scalars(q) if request_visible(p, r)]
    return {"vehicles": vehicles, "requests": requests, "today": org_today().isoformat(), "max_photos": REPAIR_PHOTO_MAX, "what_max": REPAIR_WHAT_MAX}


# ---------------------------------------------------------------- список (история)

@router.get("")
def list_results(q: str = "", vehicle_id: int | None = None, date_from: dt.date | None = None, date_to: dt.date | None = None, state: str = "",
                 archived: bool = False, legal_entity_id: int | None = None, page: int = 1, page_size: int = 24,
                 p: Principal = Depends(require("repair_photos.view")), db: Session = Depends(get_db)):
    """История фото ремонта: свежие сверху. state: waiting (ждут подтверждения) или confirmed."""
    scope = p.scope("repair_photos.view")
    if legal_entity_id is not None:
        if not scope.allows(legal_entity_id):
            raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
        scope = scope.narrow(legal_entity_id)
    base = [scope.cond(RepairResult.legal_entity_id), RepairResult.archived_at.is_not(None) if archived else RepairResult.archived_at.is_(None)]
    if vehicle_id:
        base.append(RepairResult.vehicle_id == vehicle_id)
    if date_from:
        base.append(RepairResult.repair_date >= date_from)
    if date_to:
        base.append(RepairResult.repair_date <= date_to)
    if q.strip():
        t = like(q.strip())
        base.append(or_(RepairResult.what.ilike(t), RepairResult.vehicle_id.in_(
            select(Vehicle.id).where(or_(Vehicle.plate.ilike(t), Vehicle.make.ilike(t), Vehicle.model.ilike(t))))))
    counts = dict(db.execute(select(RepairResult.confirmed_at.is_not(None), func.count()).where(*base).group_by(RepairResult.confirmed_at.is_not(None))).all())
    query = select(RepairResult).where(*base)
    if state == "waiting":
        query = query.where(RepairResult.confirmed_at.is_(None))
    elif state == "confirmed":
        query = query.where(RepairResult.confirmed_at.is_not(None))
    query = query.order_by(RepairResult.repair_date.desc(), RepairResult.id.desc())
    rows, total = page_of(db, query, page, page_size)
    items = items_out(db, p, [r[0] for r in rows])
    return {"items": items, "total": total, "page": max(page, 1), "page_size": min(max(page_size, 1), 200),
            "waiting": int(counts.get(False, 0)), "confirmed": int(counts.get(True, 0)),
            "can_create": bool(p.scope("repair_photos.create")), "can_confirm": bool(p.scope("repair_photos.confirm")),
            "can_void": bool(p.scope("repair_photos.void"))}


@router.get("/photos/{pid}")
def photo_file(pid: int, thumb: bool = False, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ph = db.get(RepairResultPhoto, pid)
    r = db.get(RepairResult, ph.result_id) if ph else None
    if not ph or not r or ph.removed_at is not None or not p.can("repair_photos.view", r.legal_entity_id):
        raise fail(404, "not_found", "Фото не найдено")
    sf = db.get(StoredFile, ph.file_id)
    data, ctype = (sf.thumb, "image/jpeg") if thumb and sf.thumb else (sf.data, sf.content_type)
    name = urllib.parse.quote(sf.original_name or "photo")
    return Response(content=data, media_type=ctype, headers={"Content-Disposition": f"inline; filename*=UTF-8''{name}", "Cache-Control": "private, no-store"})


@router.get("/{rid}")
def get_one(rid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    return {"item": detail_out(db, p, get_result(db, p, rid))}


# ---------------------------------------------------------------- добавление и правка

@router.post("")
def create(vehicle_id: str = Form(""), what: str = Form(""), repair_date: str = Form(""), request_id: str = Form(""),
           files: list[UploadFile] = File(default=[]), p: Principal = Depends(require("repair_photos.create")), db: Session = Depends(get_db)):
    try:
        vid = int(vehicle_id)
    except ValueError:
        raise fail(422, "required", "Выберите технику", "vehicle_id") from None
    v = db.get(Vehicle, vid)
    if not v or v.archived_at is not None or not p.can("repair_photos.create", v.legal_entity_id):
        raise fail(422, "bad_vehicle", "Выберите технику из списка", "vehicle_id")
    text = clean_what(what)
    day = parse_date(repair_date)
    req = None
    if request_id.strip():
        try:
            req = pick_request(db, p, int(request_id), v.id)
        except ValueError:
            raise fail(422, "bad_request", "Обращение не найдено", "request_id") from None
    prepared = read_uploads(files, images_only=True, required=True)         # до записи в базу: плохой файл не оставит пустую запись
    if len(prepared) > REPAIR_PHOTO_MAX:
        raise fail(422, "too_many_photos", f"К одному ремонту можно приложить не больше {REPAIR_PHOTO_MAX} фото", "files")
    r = RepairResult(legal_entity_id=v.legal_entity_id, vehicle_id=v.id, request_id=req.id if req else None, repair_date=day, what=text,
                     created_by_id=p.id, updated_by_id=p.id)
    db.add(r)
    db.flush()
    for sf in store(db, p, prepared):
        db.add(RepairResultPhoto(result_id=r.id, file_id=sf.id, added_by_id=p.id))
    audit.log(db, p, "repair_photos.create", "repair_result", r.id, r.legal_entity_id, vehicle=v.plate, photos=len(prepared),
              request_id=req.id if req else None)
    notify.on_repair_added(db, r, v.plate, p.id)
    db.commit()
    return {"item": detail_out(db, p, r)}


@router.patch("/{rid}")
def update(rid: int, body: UpdateIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_result(db, p, rid, "repair_photos.edit")
    check_editable(r)
    changes = {}
    if body.what is not None:
        text = clean_what(body.what)
        if text != r.what:
            changes["what"] = True
            r.what = text
    if body.repair_date is not None and body.repair_date != r.repair_date:
        check_date(body.repair_date)
        r.repair_date = body.repair_date
        changes["repair_date"] = True
    if body.clear_request and r.request_id is not None:
        r.request_id = None
        changes["request"] = True
    elif body.request_id is not None and body.request_id != r.request_id:
        r.request_id = pick_request(db, p, body.request_id, r.vehicle_id).id
        changes["request"] = True
    if changes:
        r.updated_by_id = p.id
        audit.log(db, p, "repair_photos.update", "repair_result", r.id, r.legal_entity_id, fields=sorted(changes))
        db.commit()
    return {"item": detail_out(db, p, r)}


@router.post("/{rid}/photos")
def add_photos(rid: int, files: list[UploadFile] = File(default=[]), p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_result(db, p, rid, "repair_photos.edit")
    check_editable(r)
    n = attach(db, p, r, files)
    r.updated_by_id = p.id
    audit.log(db, p, "repair_photos.photo.add", "repair_result", r.id, r.legal_entity_id, count=n)
    db.commit()
    return {"item": detail_out(db, p, r)}


@router.post("/photos/{pid}/remove")
def remove_photo(pid: int, body: ReasonIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ph = db.get(RepairResultPhoto, pid)
    r = db.get(RepairResult, ph.result_id) if ph else None
    if not ph or not r or ph.removed_at is not None or not p.can("repair_photos.view", r.legal_entity_id):
        raise fail(404, "not_found", "Фото не найдено")
    if not p.can("repair_photos.edit", r.legal_entity_id):
        raise fail(403, "forbidden", REFUSALS["repair_photos.edit"])
    check_editable(r)
    if len(live_photos(db, [r.id]).get(r.id, [])) <= 1:
        raise fail(409, "last_photo", "Это единственное фото. Сначала добавьте новое, потом уберите это")
    ph.removed_at, ph.removed_by_id, ph.remove_reason = utcnow(), p.id, body.reason.strip()[:300] or "Убрано"
    r.updated_by_id = p.id
    audit.log(db, p, "repair_photos.photo.remove", "repair_result", r.id, r.legal_entity_id, photo_id=ph.id, reason=ph.remove_reason)
    db.commit()
    return {"item": detail_out(db, p, r)}


# ---------------------------------------------------------------- подтверждение и архив

@router.post("/{rid}/confirm")
def confirm(rid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_result(db, p, rid, "repair_photos.confirm")
    if r.archived_at is not None:
        raise fail(409, "locked", "Запись в архиве: сначала верните её из архива")
    if r.confirmed_at is not None:
        raise fail(409, "already_confirmed", "Этот ремонт уже подтверждён")
    if not live_photos(db, [r.id]).get(r.id):
        raise fail(409, "no_photo", "Без фото ремонт не подтвердить")
    r.confirmed_at, r.confirmed_by_id, r.updated_by_id = utcnow(), p.id, p.id
    audit.log(db, p, "repair_photos.confirm", "repair_result", r.id, r.legal_entity_id, vehicle=r.vehicle.plate)
    notify.on_repair_confirmed(db, r, r.vehicle.plate, p.id)
    db.commit()
    return {"item": detail_out(db, p, r)}


@router.post("/{rid}/unconfirm")
def unconfirm(rid: int, body: ReasonIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_result(db, p, rid, "repair_photos.confirm")
    if r.confirmed_at is None:
        raise fail(409, "not_confirmed", "Этот ремонт ещё не подтверждён")
    if r.archived_at is not None:
        raise fail(409, "locked", "Запись в архиве: сначала верните её из архива")
    was = {"confirmed_at": r.confirmed_at.isoformat(), "confirmed_by_id": r.confirmed_by_id}
    r.confirmed_at, r.confirmed_by_id, r.updated_by_id = None, None, p.id
    audit.log(db, p, "repair_photos.unconfirm", "repair_result", r.id, r.legal_entity_id, reason=body.reason.strip()[:300], **was)
    db.commit()
    return {"item": detail_out(db, p, r)}


@router.post("/{rid}/void")
def void(rid: int, body: ReasonIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_result(db, p, rid, "repair_photos.void")
    if r.archived_at is not None:
        raise fail(409, "archived", "Запись уже в архиве")
    reason = body.reason.strip()
    if not reason:
        raise fail(422, "reason_required", "Напишите причину", "reason")
    r.archived_at, r.archived_by_id, r.archive_reason, r.updated_by_id = utcnow(), p.id, reason[:300], p.id
    audit.log(db, p, "repair_photos.void", "repair_result", r.id, r.legal_entity_id, reason=reason[:300], confirmed=r.confirmed_at is not None)
    db.commit()
    return {"item": detail_out(db, p, r)}


@router.post("/{rid}/restore")
def restore(rid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_result(db, p, rid, "repair_photos.void")
    if r.archived_at is None:
        raise fail(409, "not_archived", "Запись не в архиве")
    r.archived_at, r.archived_by_id, r.archive_reason, r.updated_by_id = None, None, "", p.id
    audit.log(db, p, "repair_photos.restore", "repair_result", r.id, r.legal_entity_id)
    db.commit()
    return {"item": detail_out(db, p, r)}
