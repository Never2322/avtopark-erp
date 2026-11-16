# -*- coding: utf-8 -*-
"""Технические задания: доска дня для диспетчера, задания водителя, отметки «приехал» и «закончил», фото до и после.

Общие операции (список, создание, правка, архив, выгрузка) делает общий механизм разделов (app/registry.py, раздел «jobs»).
Здесь то, чего он не умеет: отметки, состояния, повтор на следующий день и фото.

Правила:
- водитель видит и отмечает только задания по технике, закреплённой за ним сейчас;
- чужое юрлицо и чужая техника выглядят как «не найдено»;
- фото не стираются: их «убирают» с отметкой кто и когда, сам файл остаётся в базе;
- к отменённому заданию и к заданию в архиве фото не добавляются; водитель не добавляет фото «до работы» после того, как задание выполнен,
  и не убирает фото из выполненного или отменённого задания;
- каждое действие пишется в журнал.
"""
import datetime as dt
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.db import get_db
from app.deps import Principal, current, entity_filter
from app.enums import JOB_MAX_PHOTOS_PER_STAGE, JOB_STAGES, JOB_STATES
from app.models import Job, JobPhoto, StoredFile, Vehicle
from app.registry import _vehicle_label, org_tz
from app.resources import job_check_date, job_check_vehicle, job_state_badge, job_today
from app.routers.dossier import read_uploads, store
from app.services import notify
from app.services.people import user_names
from app.services.vehicles import assigned_vehicle_ids

router = APIRouter(prefix="/jobs", tags=["Технические задания"])

DRIVER_BACK_DAYS = 7          # незаконченные задания водитель видит за эту неделю назад
OPEN_STATES = ("planned", "on_site")


# ---------------------------------------------------------------- права

def mine(db: Session, p: Principal) -> set[int]:
    """Машины, закреплённые за человеком сейчас (в рамках одного запроса считаются один раз)."""
    if "job_vehicles" not in p._cache:
        p._cache["job_vehicles"] = assigned_vehicle_ids(db, p.employee_id)
    return p._cache["job_vehicles"]


def can_view(db: Session, p: Principal, job: Job) -> bool:
    return p.can("jobs.view", job.legal_entity_id) or (p.can("jobs.view_own", job.legal_entity_id) and job.vehicle_id in mine(db, p))


def act_mode(db: Session, p: Principal, job: Job) -> str | None:
    """«staff» — сотрудник с правом менять задания; «driver» — водитель своей техники; None — отмечать нельзя."""
    if p.can("jobs.edit", job.legal_entity_id):
        return "staff"
    if p.can("jobs.act_own", job.legal_entity_id) and job.vehicle_id in mine(db, p):
        return "driver"
    return None


def get_job(db: Session, p: Principal, jid: int) -> Job:
    job = db.get(Job, jid)
    if job is None or not can_view(db, p, job):
        raise fail(404, "not_found", "Задание не найдено")
    return job


def need_act(db: Session, p: Principal, job: Job) -> str:
    mode = act_mode(db, p, job)
    if mode is None:
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    if job.archived_at:
        raise fail(409, "archived", "Задание в архиве: сначала верните его из архива")
    return mode


# ---------------------------------------------------------------- вывод

def photo_out(ph: JobPhoto, names: dict[int, str]) -> dict:
    sf = ph.file
    return {"id": ph.id, "stage": ph.stage, "file_id": ph.file_id, "name": sf.original_name, "width": sf.width, "height": sf.height,
            "size": sf.size_bytes, "has_thumb": sf.thumb is not None, "added_at": ph.added_at.isoformat(),
            "added_by": names.get(ph.added_by_id, "")}


def jobs_out(db: Session, p: Principal, jobs: list[Job]) -> list[dict]:
    if not jobs:
        return []
    ids = [j.id for j in jobs]
    photos: dict[int, list[JobPhoto]] = {}
    for ph in db.scalars(select(JobPhoto).where(JobPhoto.job_id.in_(ids), JobPhoto.removed_at.is_(None)).order_by(JobPhoto.id)):
        photos.setdefault(ph.job_id, []).append(ph)
    names = user_names(db, {x for j in jobs for x in (j.arrived_by_id, j.finished_by_id)} |
                       {ph.added_by_id for lst in photos.values() for ph in lst})
    vehicles = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_({j.vehicle_id for j in jobs})))}
    today, tz = job_today(), org_tz()
    out = []
    for j in jobs:
        mode = act_mode(db, p, j)
        live = j.archived_at is None
        open_ = j.state in OPEN_STATES
        can_act = bool(mode) and live
        has = photos.get(j.id, [])
        before = [photo_out(x, names) for x in has if x.stage == "before"]
        after = [photo_out(x, names) for x in has if x.stage == "after"]
        v = vehicles.get(j.vehicle_id)
        out.append({
            "id": j.id, "job_date": j.job_date.isoformat(), "time_from": j.time_from, "vehicle_id": j.vehicle_id,
            "vehicle_label": _vehicle_label(v) if v else "", "plate": v.plate if v else "", "customer": j.customer, "address": j.address,
            "task": j.task, "state": j.state, "state_label": JOB_STATES[j.state], "status": job_state_badge(j, today),
            "late": open_ and j.job_date < today, "legal_entity_id": j.legal_entity_id,
            "arrived_at": j.arrived_at.astimezone(tz).isoformat() if j.arrived_at else None, "arrived_by": names.get(j.arrived_by_id, ""),
            "finished_at": j.finished_at.astimezone(tz).isoformat() if j.finished_at else None, "finished_by": names.get(j.finished_by_id, ""),
            "result_note": j.result_note, "archived": not live, "archive_reason": j.archive_reason,
            "photos": {"before": before, "after": after},
            "can": {
                "arrive": can_act and j.state == "planned", "finish": can_act and open_,
                "photo_before": can_act and j.state != "cancelled" and not (mode == "driver" and j.state == "done") and len(before) < JOB_MAX_PHOTOS_PER_STAGE,
                "photo_after": can_act and j.state != "cancelled" and len(after) < JOB_MAX_PHOTOS_PER_STAGE,
                "remove_photo": can_act and not (mode == "driver" and not open_),
                "edit": live and p.can("jobs.edit", j.legal_entity_id), "state": live and p.can("jobs.edit", j.legal_entity_id),
                "copy": live and p.can("jobs.create", j.legal_entity_id), "void": p.can("jobs.void", j.legal_entity_id),
            },
        })
    return out


def one_out(db: Session, p: Principal, job: Job) -> dict:
    return jobs_out(db, p, [job])[0]


def order_open_first(q):
    return q.order_by(Job.job_date, (Job.time_from == ""), Job.time_from, Job.id)


# ---------------------------------------------------------------- списки

@router.get("/day")
def day_board(date: dt.date | None = None, legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Доска дня для сотрудника: все задания на дату и незакрытые задания за прошлые дни."""
    scope = entity_filter(p, "jobs.view", legal_entity_id)
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    today = job_today()
    day = date or today
    rows = db.scalars(order_open_first(select(Job).where(scope.cond(Job.legal_entity_id), Job.job_date == day, Job.archived_at.is_(None)))).all()
    counts = {"total": len(rows), **{k: 0 for k in JOB_STATES}}
    for j in rows:
        counts[j.state] += 1
    stale_q = select(Job).where(scope.cond(Job.legal_entity_id), Job.job_date < today, Job.state.in_(OPEN_STATES), Job.archived_at.is_(None))
    stale_total = db.scalar(select(func.count()).select_from(stale_q.subquery())) or 0
    stale = db.scalars(order_open_first(stale_q).limit(30)).all()
    return {"date": day.isoformat(), "today": today.isoformat(), "items": jobs_out(db, p, list(rows)), "counts": counts,
            "stale": jobs_out(db, p, list(stale)), "stale_total": stale_total, "can_create": p.can("jobs.create")}


@router.get("/mine")
def my_jobs(p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Задания водителя по его технике: сегодня, завтра и незаконченные за последнюю неделю."""
    scope = p.scope("jobs.view_own")
    ids = mine(db, p)
    today = job_today()
    if not scope or not ids:
        return {"today": today.isoformat(), "items": [], "has_vehicle": bool(ids)}
    window = (Job.job_date >= today) & (Job.job_date <= today + dt.timedelta(days=1))
    stale = (Job.job_date < today) & (Job.job_date >= today - dt.timedelta(days=DRIVER_BACK_DAYS)) & Job.state.in_(OPEN_STATES)
    q = select(Job).where(scope.cond(Job.legal_entity_id), Job.vehicle_id.in_(ids), Job.archived_at.is_(None), window | stale)
    rows = db.scalars(order_open_first(q)).all()
    return {"today": today.isoformat(), "items": jobs_out(db, p, list(rows)), "has_vehicle": True}


# ---------------------------------------------------------------- отметки

class MarkIn(BaseModel):
    at: dt.datetime | None = None          # когда водитель нажал кнопку: телефон без связи присылает своё время


class FinishIn(MarkIn):
    note: str = Field("", max_length=1000)


CLIENT_TIME_BACK = dt.timedelta(days=3)       # отметка из очереди без связи может опоздать, но не больше чем на трое суток
CLIENT_TIME_AHEAD = dt.timedelta(minutes=5)   # часы телефона могут чуть спешить


def mark_time(at: dt.datetime | None, now: dt.datetime, floor: dt.datetime | None = None) -> tuple[dt.datetime, bool]:
    """Время отметки и признак «взято со слов телефона». Невероятное время (будущее, слишком давнее) заменяется временем сервера."""
    if at is None or at.tzinfo is None:
        return now, False
    at = at.astimezone(dt.timezone.utc)
    if at > now + CLIENT_TIME_AHEAD or at < now - CLIENT_TIME_BACK:
        return now, False
    at = min(at, now)
    if floor is not None and at < floor:
        at = floor
    return at, abs((now - at).total_seconds()) > 60


class StateIn(BaseModel):
    state: str


@router.post("/{jid}/arrive")
def arrive(jid: int, body: MarkIn | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    job = get_job(db, p, jid)
    need_act(db, p, job)
    if job.state == "cancelled":
        raise fail(409, "cancelled", "Задание отменено")
    if job.state == "done":
        raise fail(409, "already_done", "Задание уже выполнено")
    changed = job.state == "planned"
    if changed:
        when, late = mark_time(body.at if body else None, utcnow())
        job.state, job.arrived_at, job.arrived_by_id, job.updated_by_id = "on_site", when, p.id, p.id
        audit.log(db, p, "jobs.arrive", "jobs", job.id, job.legal_entity_id, vehicle_id=job.vehicle_id, address=job.address,
                  **({"reported_at": when.isoformat()} if late else {}))
        notify.on_job_progress(db, job, "Водитель приехал", p.id)
        db.commit()
    return {"changed": changed, "job": one_out(db, p, job)}


@router.post("/{jid}/finish")
def finish(jid: int, body: FinishIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    job = get_job(db, p, jid)
    need_act(db, p, job)
    if job.state == "cancelled":
        raise fail(409, "cancelled", "Задание отменено")
    changed = job.state in OPEN_STATES
    if changed:
        note = body.note.strip()
        now, late = mark_time(body.at, utcnow(), floor=job.arrived_at)       # «закончил» не раньше «приехал»
        job.state, job.finished_at, job.finished_by_id, job.updated_by_id = "done", now, p.id, p.id
        if note:
            job.result_note = note
        if job.arrived_at is None:      # «приехал» не нажали: считаем, что был на месте
            job.arrived_at, job.arrived_by_id = now, p.id
        audit.log(db, p, "jobs.finish", "jobs", job.id, job.legal_entity_id, vehicle_id=job.vehicle_id, note=note,
                  **({"reported_at": now.isoformat()} if late else {}))
        notify.on_job_progress(db, job, "Работа закончена", p.id)
        db.commit()
    return {"changed": changed, "job": one_out(db, p, job)}


@router.post("/{jid}/state")
def set_state(jid: int, body: StateIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Сотрудник правит состояние вручную: отменить, вернуть в работу, отметить за водителя."""
    job = get_job(db, p, jid)
    if not p.can("jobs.edit", job.legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    if job.archived_at:
        raise fail(409, "archived", "Задание в архиве: сначала верните его из архива")
    if body.state not in JOB_STATES:
        raise fail(422, "bad_state", "Выберите состояние из списка", "state")
    old = job.state
    if body.state == old:
        return {"changed": False, "job": one_out(db, p, job)}
    now = utcnow()
    if body.state == "planned":
        job.arrived_at = job.arrived_by_id = job.finished_at = job.finished_by_id = None
    elif body.state == "on_site":
        if job.arrived_at is None:
            job.arrived_at, job.arrived_by_id = now, p.id
        job.finished_at = job.finished_by_id = None
    elif body.state == "done":
        job.finished_at, job.finished_by_id = now, p.id
    job.state, job.updated_by_id = body.state, p.id
    audit.log(db, p, "jobs.state", "jobs", job.id, job.legal_entity_id, changes={"state": [old, body.state]})
    if body.state == "cancelled" or old == "cancelled":
        notify.on_job_cancelled(db, job, body.state, p.id)
    db.commit()
    return {"changed": True, "job": one_out(db, p, job)}


@router.post("/{jid}/copy")
def copy_next_day(jid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Такое же задание на следующий день: для объектов, куда ездят несколько дней подряд."""
    job = get_job(db, p, jid)
    if not p.can("jobs.create", job.legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    if job.archived_at:
        raise fail(409, "archived", "Задание в архиве: сначала верните его из архива")
    errors: dict = {}
    day = job.job_date + dt.timedelta(days=1)
    job_check_date(day, errors, "job_date")
    job_check_vehicle(db, job.vehicle_id, errors, "vehicle_id")
    vehicle = db.get(Vehicle, job.vehicle_id)
    if vehicle is not None and vehicle.archived_at:
        errors["vehicle_id"] = "Техника в архиве"
    if errors:
        raise fail(422, "cannot_copy", next(iter(errors.values())), next(iter(errors)))
    new = Job(legal_entity_id=job.legal_entity_id, vehicle_id=job.vehicle_id, job_date=day, time_from=job.time_from,
              customer=job.customer, address=job.address, task=job.task, created_by_id=p.id, updated_by_id=p.id)
    db.add(new)
    db.flush()
    audit.log(db, p, "jobs.copy", "jobs", new.id, new.legal_entity_id, copied_from=job.id, job_date=day.isoformat())
    db.commit()
    return {"job": one_out(db, p, new)}


# ---------------------------------------------------------------- фото

def live_photos(db: Session, job_id: int, stage: str) -> int:
    return db.scalar(select(func.count()).select_from(JobPhoto).where(
        JobPhoto.job_id == job_id, JobPhoto.stage == stage, JobPhoto.removed_at.is_(None))) or 0


@router.post("/{jid}/photos")
def add_photos(jid: int, stage: str = Form(...), files: list[UploadFile] = File(default=[]), p: Principal = Depends(current),
               db: Session = Depends(get_db)):
    job = get_job(db, p, jid)
    mode = need_act(db, p, job)
    if stage not in JOB_STAGES:
        raise fail(422, "bad_stage", "Укажите, когда сделаны фото: до работы или после", "stage")
    if job.state == "cancelled" or (mode == "driver" and job.state == "done" and stage == "before"):
        raise fail(409, "photos_closed", "К этому заданию фото уже не добавить")
    prepared = read_uploads(files, images_only=True, required=True)
    # Тот же файл повторно (телефон без связи не узнал, что первая отправка дошла): второй раз не добавляется
    have = set(db.scalars(select(StoredFile.sha256).join(JobPhoto, JobPhoto.file_id == StoredFile.id).where(
        JobPhoto.job_id == job.id, JobPhoto.stage == stage, JobPhoto.removed_at.is_(None))))
    fresh: list = []
    for f in prepared:
        if f.sha256 not in have:
            have.add(f.sha256)
            fresh.append(f)
    skipped = len(prepared) - len(fresh)
    if not fresh:
        return {"job": one_out(db, p, job), "added": 0, "skipped": skipped}
    prepared = fresh
    if live_photos(db, job.id, stage) + len(prepared) > JOB_MAX_PHOTOS_PER_STAGE:
        raise fail(422, "too_many_photos", f"Не больше {JOB_MAX_PHOTOS_PER_STAGE} фото «{JOB_STAGES[stage].lower()}»", "files")
    stored = store(db, p, prepared)
    for sf in stored:
        db.add(JobPhoto(job_id=job.id, file_id=sf.id, stage=stage, added_by_id=p.id))
    job.updated_by_id = p.id
    audit.log(db, p, "jobs.photo.add", "jobs", job.id, job.legal_entity_id, stage=stage, count=len(stored))
    db.commit()
    return {"job": one_out(db, p, job), "added": len(stored), "skipped": skipped}


class RemoveIn(BaseModel):
    reason: str = Field("", max_length=300)


def get_photo(db: Session, p: Principal, pid: int) -> tuple[JobPhoto, Job]:
    ph = db.get(JobPhoto, pid)
    job = db.get(Job, ph.job_id) if ph else None
    if ph is None or job is None or not can_view(db, p, job):
        raise fail(404, "not_found", "Фото не найдено")
    return ph, job


@router.get("/photos/{pid}")
def photo_file(pid: int, thumb: bool = False, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ph, _job = get_photo(db, p, pid)
    if ph.removed_at is not None:
        raise fail(404, "not_found", "Фото не найдено")
    sf = db.get(StoredFile, ph.file_id)
    data, ctype = (sf.thumb, "image/jpeg") if thumb and sf.thumb else (sf.data, sf.content_type)
    name = urllib.parse.quote(sf.original_name or "photo")
    return Response(content=data, media_type=ctype,
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{name}", "Cache-Control": "private, no-store"})


@router.post("/photos/{pid}/remove")
def remove_photo(pid: int, body: RemoveIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ph, job = get_photo(db, p, pid)
    if ph.removed_at is not None:
        raise fail(404, "not_found", "Фото не найдено")
    mode = need_act(db, p, job)
    if mode == "driver" and job.state not in OPEN_STATES:
        raise fail(403, "forbidden", "Из выполненного задания фото убрать нельзя: обратитесь к диспетчеру")
    ph.removed_at, ph.removed_by_id, ph.remove_reason = utcnow(), p.id, body.reason.strip() or "Убрано"
    job.updated_by_id = p.id
    audit.log(db, p, "jobs.photo.remove", "jobs", job.id, job.legal_entity_id, photo_id=ph.id, stage=ph.stage, reason=ph.remove_reason)
    db.commit()
    return {"job": one_out(db, p, job)}
