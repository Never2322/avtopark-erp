# -*- coding: utf-8 -*-
"""Заработок водителей: тариф на сотруднике, запись «сколько часов работал или ремонтировал», сумма за день и за месяц.

Ставка запоминается в записи, когда её вносят: изменить тариф задним числом нельзя, прошлые дни не пересчитываются.
Сумма в базе не хранится, её считает amounts(): так не бывает «устаревших» сумм после правки или отмены записи.
Часовой тариф: часы × ставка. Дневной: ставка один раз за день и вид; вторая запись того же дня и вида даёт 0.
"""
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import PAY_TARIFF_KINDS, PAY_TARIFF_UNITS, WORKLOG_KINDS, WORKLOG_MAX_HOURS
from app.models import Employee, Tariff, Vehicle, WorkLog
from app.services.vehicles import employee_in_entity

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def pay_tariff(db: Session, emp: Employee, kind: str) -> Tariff | None:
    tid = emp.work_tariff_id if kind == "work" else emp.repair_tariff_id if kind == "repair" else None
    t = db.get(Tariff, tid) if tid else None
    return t if t is not None and t.archived_at is None and t.kind == kind and t.unit in PAY_TARIFF_UNITS else None


def snapshot(db: Session, emp: Employee, kind: str) -> dict:
    """Что записать в строку часов: какой тариф и какая ставка действовали в этот момент (без тарифа — ставка 0)."""
    t = pay_tariff(db, emp, kind)
    if t is None:
        return {"tariff_id": None, "tariff_name": "", "unit": "hour", "rate": Decimal(0)}
    return {"tariff_id": t.id, "tariff_name": t.name, "unit": t.unit, "rate": t.rate}


def rate_text(log) -> str:
    if log.tariff_id is None and not log.rate:
        return "нет тарифа"
    return f"{money(log.rate):,.2f}".replace(",", " ").replace(".", ",").replace(",00", "") + (" ₽/час" if log.unit == "hour" else " ₽/день")


def amounts(db: Session, logs: list[WorkLog]) -> dict[int, Decimal]:
    """Сумма по каждой записи. Убранные в архив записи дают 0."""
    out: dict[int, Decimal] = {}
    day_groups = {(l.employee_id, l.work_date, l.kind) for l in logs if l.unit == "day" and l.archived_at is None}
    first_of: dict[tuple, int] = {}
    if day_groups:
        emp_ids = {g[0] for g in day_groups}
        dates = [g[1] for g in day_groups]
        rows = db.execute(select(WorkLog.employee_id, WorkLog.work_date, WorkLog.kind, func.min(WorkLog.id)).where(
            WorkLog.employee_id.in_(emp_ids), WorkLog.work_date >= min(dates), WorkLog.work_date <= max(dates),
            WorkLog.unit == "day", WorkLog.archived_at.is_(None)).group_by(WorkLog.employee_id, WorkLog.work_date, WorkLog.kind)).all()
        first_of = {(e, d, k): i for e, d, k, i in rows}
    for l in logs:
        if l.archived_at is not None:
            out[l.id] = Decimal(0)
        elif l.unit == "day":
            out[l.id] = money(l.rate) if first_of.get((l.employee_id, l.work_date, l.kind)) == l.id else Decimal(0)
        else:
            out[l.id] = money(Decimal(l.hours) * Decimal(l.rate))
    return out


def month_pay(db: Session, entity_id: int, first: dt.date, last: dt.date, emp_ids: list[int]) -> dict[int, dict[int, dict]]:
    """{сотрудник: {день месяца: {work_h, repair_h, work, repair, total}}} по живым записям юрлица."""
    if not emp_ids:
        return {}
    logs = list(db.scalars(select(WorkLog).where(WorkLog.legal_entity_id == entity_id, WorkLog.employee_id.in_(emp_ids),
                                                 WorkLog.work_date >= first, WorkLog.work_date <= last, WorkLog.archived_at.is_(None))
                           .order_by(WorkLog.id)))
    amt = amounts(db, logs)
    out: dict[int, dict[int, dict]] = {}
    for l in logs:
        cell = out.setdefault(l.employee_id, {}).setdefault(l.work_date.day, {"work_h": Decimal(0), "repair_h": Decimal(0), "work": Decimal(0), "repair": Decimal(0), "total": Decimal(0)})
        cell[f"{l.kind}_h"] += Decimal(l.hours)
        cell[l.kind] += amt[l.id]
        cell["total"] += amt[l.id]
    return out


def totals(cells: dict[int, dict]) -> dict:
    t = {"work_h": Decimal(0), "repair_h": Decimal(0), "work": Decimal(0), "repair": Decimal(0), "total": Decimal(0)}
    for c in cells.values():
        for k in t:
            t[k] += c[k]
    return t


def num(x) -> float:
    return float(Decimal(str(x)))


def check_entry(db: Session, *, emp: Employee, entity_id: int | None, work_date: dt.date | None, kind: str | None, hours, vehicle_id: int | None,
                description: str, today: dt.date, backdate_days: int | None = None, exclude_id: int | None = None) -> dict[str, str]:
    """Общие проверки записи часов: поле → сообщение. Пустой словарь — всё в порядке."""
    e: dict[str, str] = {}
    if kind not in WORKLOG_KINDS:
        e["kind"] = "Выберите: работа или ремонт"
    if work_date is None:
        e["work_date"] = "Укажите дату"
    elif work_date > today:
        e["work_date"] = "Часы за будущие дни не вносят"
    elif backdate_days is not None and work_date < today - dt.timedelta(days=backdate_days):
        e["work_date"] = f"Самому можно отметить не раньше чем за {backdate_days} дн. Позвоните диспетчеру: он внесёт старый день"
    try:
        h = Decimal(str(hours).replace(",", "."))
    except Exception:
        h = None
    if h is not None and not h.is_finite():
        h = None
    if h is None or h <= 0:
        e["hours"] = "Укажите часы, например 8 или 2,5"
    elif h > WORKLOG_MAX_HOURS:
        e["hours"] = f"В сутках не больше {WORKLOG_MAX_HOURS} часов"
    elif h * 10 != (h * 10).to_integral_value():
        e["hours"] = "Часы с точностью до десятых: 2,5 или 7,3"
    if kind == "repair" and len((description or "").strip()) < 3:
        e["description"] = "Напишите, что делали на ремонте"
    if entity_id is None:
        e.setdefault("legal_entity_id", "Выберите юридическое лицо")
    else:
        if work_date is not None and not employee_in_entity(db, emp, entity_id, work_date):
            e["employee_id"] = "В этот день сотрудник не работает в выбранном юрлице"
        if vehicle_id:
            v = db.get(Vehicle, vehicle_id)
            if v is None or v.archived_at is not None:
                e["vehicle_id"] = "Такой техники нет"
            elif v.legal_entity_id != entity_id:
                e["vehicle_id"] = "Эта техника из другого юрлица"
    if "hours" not in e and "work_date" not in e and h is not None:
        q = select(func.coalesce(func.sum(WorkLog.hours), 0)).where(WorkLog.employee_id == emp.id, WorkLog.work_date == work_date, WorkLog.archived_at.is_(None))
        if exclude_id:
            q = q.where(WorkLog.id != exclude_id)
        had = Decimal(db.scalar(q) or 0)
        if had + h > WORKLOG_MAX_HOURS:
            e["hours"] = f"За этот день уже внесено {had:g} ч: всего в сутках не больше {WORKLOG_MAX_HOURS}"
    return e


def entity_for(db: Session, emp: Employee, work_date: dt.date, vehicle_id: int | None) -> tuple[int | None, str]:
    """Юрлицо записи: у техники, а без техники единственное место работы сотрудника в этот день. (id, пояснение ошибки)."""
    from app.models import EmploymentAssignment
    from sqlalchemy import or_
    if vehicle_id:
        v = db.get(Vehicle, vehicle_id)
        return (v.legal_entity_id, "") if v is not None and v.archived_at is None else (None, "Такой техники нет")
    ids = sorted(set(db.scalars(select(EmploymentAssignment.legal_entity_id).where(
        EmploymentAssignment.employee_id == emp.id,
        or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= work_date),
        or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= work_date)))))
    if len(ids) == 1:
        return ids[0], ""
    if not ids:
        return None, "В этот день вы нигде не числитесь: обратитесь к диспетчеру"
    return None, "Выберите технику: вы числитесь в нескольких юрлицах"


def recalc_without_tariff(db: Session, emp: Employee, user_id: int) -> int:
    """У записей без тарифа появляется ставка, когда сотруднику назначили тариф. Записи с тарифом не трогаем."""
    changed = 0
    for l in db.scalars(select(WorkLog).where(WorkLog.employee_id == emp.id, WorkLog.tariff_id.is_(None), WorkLog.archived_at.is_(None))):
        snap = snapshot(db, emp, l.kind)
        if snap["tariff_id"] is not None:
            for k, v in snap.items():
                setattr(l, k, v)
            l.updated_by_id = user_id
            changed += 1
    return changed
