# -*- coding: utf-8 -*-
"""Уведомления на сайте (колокольчик): кому и о чём сообщать.

Правила:
- уведомление получает только тот, кто может открыть то, о чём оно (по праву и юрлицу или как автор и исполнитель);
- сам человек уведомления о своих действиях не получает;
- создание уведомления не должно ломать само действие: ошибка записи уходит в журнал сервера, обращение или задание сохраняются;
- напоминания по срокам (документы, штрафы, аренда, СИЗ, счета) превращаются в уведомления фоновым обходом; ключ
  не даёт сообщить об одном и том же дважды, а если набралось сразу много, приходит одно общее.
"""
import asyncio
import datetime as dt
import functools
import hashlib
import logging
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select, text
from sqlalchemy.orm import Session

from app.common import utcnow
from app.config import get_settings
from app.db import engine, session_factory
from app.deps import Principal, load_grants
from app.enums import (NOTIFICATION_DIGEST_FROM, NOTIFICATION_KEEP_DAYS, NOTIFICATION_KEYED_KEEP_DAYS, NOTIFICATION_KINDS,
                       REQUEST_STATUSES)
from app.models import (Employee, Notification, Permission, RolePermission, ServiceRequest, User, UserRole, VehicleAssignment)
from app.services import channels
from app.services.vehicles import active_assignment_filter

log = logging.getLogger("erp.notify")
SWEEP_LOCK_KEY = 7301
SWEEP_EVERY_SECONDS = 300


def short(value: str | None, limit: int = 140) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


# ---------------------------------------------------------------- кому

def users_with(db: Session, perm: str, entity_id: int | None) -> set[int]:
    """Активные пользователи, у которых есть право в этом юрлице (или везде)."""
    q = (select(UserRole.user_id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
         .join(Permission, Permission.id == RolePermission.permission_id).join(User, User.id == UserRole.user_id)
         .where(Permission.code == perm, User.is_active.is_(True),
                or_(UserRole.legal_entity_id.is_(None), UserRole.legal_entity_id == entity_id)))
    return set(db.scalars(q))


def users_with_any(db: Session, perms: tuple[str, ...], entity_id: int | None) -> set[int]:
    out: set[int] = set()
    for perm in perms:
        out |= users_with(db, perm, entity_id)
    return out


def drivers_of(db: Session, vehicle_id: int) -> set[int]:
    """Пользователи-водители, закреплённые за машиной сейчас."""
    q = (select(Employee.user_id).join(VehicleAssignment, VehicleAssignment.employee_id == Employee.id)
         .join(User, User.id == Employee.user_id)
         .where(VehicleAssignment.vehicle_id == vehicle_id, active_assignment_filter(), Employee.user_id.is_not(None),
                Employee.archived_at.is_(None), User.is_active.is_(True)))
    return set(db.scalars(q))


# ---------------------------------------------------------------- запись

def push(db: Session, user_ids, kind: str, title: str, text_: str = "", url: str = "", entity_id: int | None = None,
         actor_id: int | None = None, key: str | None = None, read: bool = False) -> int:
    """Создаёт уведомления. Возвращает, сколько создано. Ошибка не прерывает вызывающее действие."""
    ids = sorted({i for i in user_ids if i and i != actor_id})
    kind = kind if kind in NOTIFICATION_KINDS else "info"
    made = 0
    created: list[int] = []
    for uid in ids:
        try:
            with db.begin_nested():
                if key and db.scalar(select(Notification.id).where(Notification.user_id == uid, Notification.key == key)):
                    continue
                db.add(Notification(user_id=uid, kind=kind, title=short(title, 160), text=short(text_, 500), url=url[:200],
                                    legal_entity_id=entity_id, key=key, read_at=utcnow() if read else None))
                db.flush()
                made += 1
                created.append(uid)
        except Exception:                                   # noqa: BLE001
            log.exception("Не удалось создать уведомление «%s» для пользователя %s", title, uid)
    if created and not read:                                # «прочитанное сразу» — это части общего напоминания: наружу уходит только общее
        try:
            with db.begin_nested():
                channels.enqueue(db, created, short(title, 160), short(text_, 500), url[:200])
        except Exception:                                   # noqa: BLE001
            log.exception("Не удалось поставить в очередь отправку «%s» в Telegram и почту", title)
    return made


def safe(fn):
    """Событие программы: ошибка подбора получателей или записи не должна ломать само действие (обращение, задание, покупку)."""
    @functools.wraps(fn)
    def wrapper(db: Session, *args, **kw):
        try:
            with db.begin_nested():
                return fn(db, *args, **kw)
        except Exception:                                   # noqa: BLE001
            log.exception("Не удалось создать уведомления: %s", fn.__name__)
            return None
    return wrapper


def _request_people(r: ServiceRequest) -> set[int]:
    return {x for x in (r.reporter_id, r.assignee_id) if x}


def _plate(r: ServiceRequest) -> str:
    return r.vehicle.plate if r.vehicle else ""


# ---------------------------------------------------------------- события программы

@safe
def on_request_new(db: Session, r: ServiceRequest, actor_id: int | None) -> None:
    ids = users_with_any(db, ("requests.edit", "requests.approve"), r.legal_entity_id)
    urgent = "Срочное обращение" if r.priority == "urgent" else "Новое обращение"
    push(db, ids, "request", f"{urgent} № {r.number}: {_plate(r)}", short(r.title), f"/requests/{r.id}", r.legal_entity_id, actor_id)


@safe
def on_request_comment(db: Session, r: ServiceRequest, text_: str, actor_id: int | None) -> None:
    ids = _request_people(r)
    if actor_id is not None and actor_id == r.reporter_id:            # написал автор обращения: сообщить тем, кто ведёт обращения
        ids |= users_with_any(db, ("requests.edit", "requests.approve"), r.legal_entity_id)
    push(db, ids, "reply", f"Сообщение в обращении № {r.number} ({_plate(r)})", short(text_), f"/requests/{r.id}", r.legal_entity_id, actor_id)


@safe
def on_request_status(db: Session, r: ServiceRequest, new: str, comment: str, actor_id: int | None) -> None:
    ids = _request_people(r)
    if new == "approval":                                             # ремонт ждёт согласования: сообщить тем, кто согласует
        push(db, users_with(db, "requests.approve", r.legal_entity_id), "request", f"Ремонт ждёт согласования: обращение № {r.number} ({_plate(r)})",
             short(r.title), f"/requests/{r.id}", r.legal_entity_id, actor_id)
    push(db, ids, "request", f"Обращение № {r.number}: {REQUEST_STATUSES.get(new, new).lower()}", short(comment), f"/requests/{r.id}",
         r.legal_entity_id, actor_id)


@safe
def on_request_assigned(db: Session, r: ServiceRequest, user_id: int, actor_id: int | None) -> None:
    push(db, [user_id], "request", f"Вам назначено обращение № {r.number} ({_plate(r)})", short(r.title), f"/requests/{r.id}", r.legal_entity_id, actor_id)


@safe
def on_parts_bought(db: Session, r: ServiceRequest, names: list[str], actor_id: int | None) -> None:
    push(db, _request_people(r), "parts", f"Куплено для обращения № {r.number}", short("; ".join(names), 300), f"/requests/{r.id}",
         r.legal_entity_id, actor_id)


@safe
def on_parts_from_stock(db: Session, r: ServiceRequest, name: str, actor_id: int | None) -> None:
    push(db, _request_people(r), "parts", f"Деталь со склада для обращения № {r.number}", short(name, 300), f"/requests/{r.id}",
         r.legal_entity_id, actor_id)


@safe
def on_stock_low(db: Session, item, left, actor_id: int | None) -> None:
    """Товар заканчивается: тем, кто может менять товары юрлица. На один товар не чаще раза в день."""
    day = dt.datetime.now(ZoneInfo(get_settings().org_timezone)).strftime("%Y%m%d")
    q = format(Decimal(str(left)).normalize(), "f").replace(".", ",")
    m = format(Decimal(str(item.min_qty)).normalize(), "f").replace(".", ",")
    push(db, users_with(db, "stock_items.edit", item.legal_entity_id), "stock", f"Заканчивается: {item.name}",
         f"Осталось {q} {item.unit}, минимум {m}", "/stock?low=1", item.legal_entity_id, actor_id, key=f"low:{item.id}:{day}")


@safe
def on_job_saved(db: Session, job, creating: bool, actor_id: int | None) -> None:
    """Задание назначено или изменено: водителям этой машины."""
    where = f"{job.job_date.strftime('%d.%m.%Y')}{', ' + job.time_from if job.time_from else ''}"
    title = ("Новое задание: " if creating else "Задание изменено: ") + where
    push(db, drivers_of(db, job.vehicle_id), "job", title, short(f"{job.address}. {job.task}".strip(". "), 300), "/jobs/mine", job.legal_entity_id, actor_id)


@safe
def on_job_cancelled(db: Session, job, state: str, actor_id: int | None) -> None:
    title = ("Задание отменено: " if state == "cancelled" else "Задание снова в работе: ") + job.job_date.strftime("%d.%m.%Y")
    push(db, drivers_of(db, job.vehicle_id), "job", title, short(job.address, 300), "/jobs/mine", job.legal_entity_id, actor_id)


@safe
def on_job_progress(db: Session, job, what: str, actor_id: int | None) -> None:
    """Водитель приехал или закончил: тем, кто ведёт задания."""
    plate = job.vehicle.plate if getattr(job, "vehicle", None) else ""
    text_ = short(f"{job.address}. {job.result_note}".strip(". "), 300)
    push(db, users_with(db, "jobs.edit", job.legal_entity_id), "job", f"{what}: {plate}".strip(": "), text_, "/jobs", job.legal_entity_id, actor_id)


@safe
def on_worklog_kind(db: Session, emp: Employee, work_date: dt.date, kind: str, before, after, entity_id: int | None, actor_id: int | None) -> None:
    """Офис перевёл часы водителя за день в другой тип: водителю сообщаем, что и как изменилась сумма за день."""
    if emp.user_id is None:
        return
    money = lambda x: f"{Decimal(str(x)):,.2f}".replace(",", " ").replace(".", ",")          # noqa: E731
    title = f"Часы за {work_date.strftime('%d.%m.%Y')} переведены: {'ремонт' if kind == 'repair' else 'работа'}"
    push(db, {emp.user_id}, "info", title, f"Сумма за день: {money(before)} ₽ → {money(after)} ₽. Изменили в офисе.", "/my-time", entity_id, actor_id)


# ---------------------------------------------------------------- напоминания по срокам

def _key(r: dict) -> str:
    stamp = hashlib.md5(f"{r['title']}|{r['subject']}".encode("utf-8")).hexdigest()[:8]
    return f"rem:{r['source']}:{r['ref_id'] or 0}:{r['day']}:{r['level']}:{stamp}"


def sweep_user(db: Session, user: User) -> int:
    """Новые напоминания пользователя → уведомления. Возвращает, сколько создано (общее считается за одно)."""
    from app.services import reminders as rem
    grants = load_grants(db, user.id)
    if "reminders.view" not in grants:
        return 0
    emp = db.scalar(select(Employee.id).where(Employee.user_id == user.id, Employee.archived_at.is_(None)))
    p = Principal(user, grants, None, "", emp)                          # сессия обходу не нужна
    keyed = [(_key(r), r) for r in rem.reminders(db, p)]
    if not keyed:
        return 0
    have = set(db.scalars(select(Notification.key).where(Notification.user_id == user.id, Notification.key.in_([k for k, _ in keyed]))))
    fresh = [(k, r) for k, r in keyed if k not in have]
    if not fresh:
        return 0
    many = len(fresh) >= NOTIFICATION_DIGEST_FROM
    for k, r in fresh:
        sub = " · ".join(x for x in (r["subject"], r["left_text"]) if x)
        push(db, [user.id], "reminder", r["title"], sub, r["url"] or "/reminders", r["entity_id"], key=k, read=many)
    if many:
        due = sum(1 for _, r in fresh if r["level"] == "due")
        first = "; ".join(short(r["title"], 60) for _, r in fresh[:3])
        push(db, [user.id], "reminder", f"Напоминания: новых {len(fresh)}", f"Наступило: {due}, скоро: {len(fresh) - due}. {first} и другие",
             "/reminders", None, key=f"remd:{utcnow():%Y%m%d%H%M}")
    return len(fresh)


def cleanup(db: Session) -> int:
    now = utcnow()
    n = db.execute(delete(Notification).where(Notification.key.is_(None), Notification.read_at.is_not(None),
                                              Notification.created_at < now - dt.timedelta(days=NOTIFICATION_KEEP_DAYS))).rowcount
    n += db.execute(delete(Notification).where(Notification.key.is_not(None),
                                               Notification.created_at < now - dt.timedelta(days=NOTIFICATION_KEYED_KEEP_DAYS))).rowcount
    return n


def sweep(db: Session) -> dict:
    users = list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.id)))
    made = 0
    for u in users:
        try:
            made += sweep_user(db, u)
            db.commit()
        except Exception:                                               # noqa: BLE001
            db.rollback()
            log.exception("Обход напоминаний: не удалось для пользователя %s", u.id)
    removed = cleanup(db)
    db.commit()
    return {"users": len(users), "created": made, "removed": removed}


def run_sweep() -> None:
    """Один обход. Блокировка в базе не даёт двум экземплярам сервера делать его одновременно."""
    with engine().connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SWEEP_LOCK_KEY}).scalar():
            return
        try:
            with session_factory()() as db:
                sweep(db)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SWEEP_LOCK_KEY})
            lock.commit()


async def scheduler() -> None:
    await asyncio.sleep(15)                                             # дать серверу подняться
    while True:
        try:
            await asyncio.to_thread(run_sweep)
        except Exception:                                               # noqa: BLE001
            log.exception("Обход напоминаний не выполнен")
        await asyncio.sleep(SWEEP_EVERY_SECONDS)
