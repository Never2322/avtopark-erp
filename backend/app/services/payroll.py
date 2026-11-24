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

from app.enums import PAY_TARIFF_KINDS, PAY_TARIFF_UNITS, TON_UNITS, WORK_TARIFF_UNITS, WORKLOG_KINDS, WORKLOG_MAX_HOURS
from app.models import Employee, Tariff, Vehicle, WeighTicket, WorkLog
from app.services.vehicles import employee_in_entity

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def pay_tariff(db: Session, emp: Employee, kind: str) -> Tariff | None:
    tid = emp.work_tariff_id if kind == "work" else emp.repair_tariff_id if kind == "repair" else None
    t = db.get(Tariff, tid) if tid else None
    units = WORK_TARIFF_UNITS if kind == "work" else PAY_TARIFF_UNITS
    return t if t is not None and t.archived_at is None and t.kind == kind and t.unit in units else None


def ton_tariff(db: Session, emp: Employee) -> Tariff | None:
    """Тариф «за тонну» у сотрудника (мусоровозы): работа оплачивается по тоннажу из талонов весов, а не по часам.
    Простой («ton»): тонны × ставка. С порогом («ton_tier»): тонн за день меньше порога — все тонны дня по первой ставке, иначе по второй."""
    t = pay_tariff(db, emp, "work")
    return t if t is not None and t.unit in TON_UNITS else None


def day_rate(t: Tariff, day_tons) -> Decimal:
    """Ставка за тонну при таком тоннаже водителя за день (для простого тарифа одна и та же)."""
    if t.unit == "ton_tier" and t.tier_tons is not None and t.rate_high is not None and Decimal(day_tons) >= Decimal(t.tier_tons):
        return Decimal(t.rate_high)
    return Decimal(t.rate)


def day_tons(db: Session, pairs) -> dict[tuple[int, dt.date], Decimal]:
    """Тонны водителя за день по всем живым талонам с весом (во всех юрлицах): от них зависит ставка тарифа с порогом."""
    pairs = {(d, day) for d, day in pairs if d is not None}
    if not pairs:
        return {}
    drivers = {d for d, _ in pairs}
    days = [day for _, day in pairs]
    rows = db.execute(select(WeighTicket.driver_id, WeighTicket.ticket_date, func.sum(WeighTicket.tons)).where(
        WeighTicket.driver_id.in_(drivers), WeighTicket.ticket_date >= min(days), WeighTicket.ticket_date <= max(days),
        WeighTicket.archived_at.is_(None), WeighTicket.tons.is_not(None)).group_by(WeighTicket.driver_id, WeighTicket.ticket_date)).all()
    return {(d, day): Decimal(n or 0) for d, day, n in rows}


def ton_rows(db: Session, tickets) -> list[dict]:
    """Сумма по каждому талону весов: тонны × ставка «за тонну» водителя талона (у тарифа с порогом ставка зависит от тонн водителя
    за весь день). Талоны без тоннажа, убранные и у водителей без такого тарифа пропускаются. Ставка берётся из тарифа сотрудника на
    момент расчёта (в талоне она не запоминается)."""
    cache: dict[int, Tariff | None] = {}
    live = [t for t in tickets if t.driver_id is not None and t.tons is not None and t.archived_at is None]
    for t in live:
        if t.driver_id not in cache:
            emp = db.get(Employee, t.driver_id)
            cache[t.driver_id] = ton_tariff(db, emp) if emp is not None else None
    tiered = [t for t in live if cache[t.driver_id] is not None and cache[t.driver_id].unit == "ton_tier"]
    per_day = day_tons(db, [(t.driver_id, t.ticket_date) for t in tiered])
    out = []
    for t in live:
        tar = cache[t.driver_id]
        if tar is not None:
            dtons = per_day.get((t.driver_id, t.ticket_date), Decimal(t.tons))
            rate = day_rate(tar, dtons)
            out.append({"ticket": t, "tariff": tar, "tons": Decimal(t.tons), "rate": rate, "day_tons": dtons,
                        "amount": money(Decimal(t.tons) * rate)})
    return out


def tariff_text(t: Tariff | None) -> str:
    """Ставка тарифа словами: «600 ₽/т» или «400 ₽/т до 12 т в день, 450 ₽/т от 12 т»."""
    if t is None:
        return ""
    if t.unit == "ton_tier":
        tier = f"{Decimal(t.tier_tons or 0).normalize():f}".replace(".", ",")
        return f"{rate_str(t.rate, 'ton')} до {tier} т в день, {rate_str(t.rate_high or 0, 'ton')} от {tier} т"
    return rate_str(t.rate, t.unit)


def ton_pending(db: Session, tickets) -> int:
    """Сколько талонов водителей с тарифом «за тонну» ещё без тоннажа: они не оплачены, пока вес не внесён."""
    cache: dict[int, bool] = {}
    n = 0
    for t in tickets:
        if t.tons is not None or t.driver_id is None or t.archived_at is not None:
            continue
        if t.driver_id not in cache:
            emp = db.get(Employee, t.driver_id)
            cache[t.driver_id] = emp is not None and ton_tariff(db, emp) is not None
        n += 1 if cache[t.driver_id] else 0
    return n


NO_TONS = {"by_tons": False, "tons": Decimal(0), "rate": Decimal(0), "amount": Decimal(0), "pending": 0, "tariff_name": "", "rate_text": ""}


def ton_pay(db: Session, emp: Employee, first: dt.date, last: dt.date, cond=None) -> dict:
    """Заработок за тонны за период: тонны, ставка, сумма и сколько талонов ещё ждут веса. by_tons=False, если тариф не «за тонну»."""
    tar = ton_tariff(db, emp)
    if tar is None:
        return dict(NO_TONS)
    stmt = select(WeighTicket).where(WeighTicket.driver_id == emp.id, WeighTicket.ticket_date >= first, WeighTicket.ticket_date <= last,
                                     WeighTicket.archived_at.is_(None))
    if cond is not None:
        stmt = stmt.where(cond(WeighTicket.legal_entity_id))
    tickets = list(db.scalars(stmt))
    rows = ton_rows(db, tickets)
    return {"by_tons": True, "tons": sum((r["tons"] for r in rows), Decimal(0)), "rate": Decimal(tar.rate), "tariff_name": tar.name, "rate_text": tariff_text(tar),
            "amount": sum((r["amount"] for r in rows), Decimal(0)), "pending": sum(1 for t in tickets if t.tons is None)}


def ton_fields(tp: dict) -> dict:
    """Поля тоннажа для ответа сайту (общие для главной водителя, «Моих часов» и карточки сотрудника)."""
    return {"by_tons": tp["by_tons"], "tons": num(tp["tons"]), "tons_rate": num(tp["rate"]), "tons_amount": num(tp["amount"]),
            "tons_pending": tp["pending"], "tons_tariff": tp["tariff_name"], "tons_rate_text": tp.get("rate_text", "")}


def snapshot(db: Session, emp: Employee, kind: str) -> dict:
    """Что записать в строку часов: какой тариф и какая ставка действовали в этот момент (без тарифа — ставка 0).
    Тариф берётся из карточки сотрудника (по виду «работа» или «ремонт»)."""
    t = pay_tariff(db, emp, kind)
    if t is None:
        return {"tariff_id": None, "tariff_name": "", "unit": "hour", "rate": Decimal(0), "tariff_manual": False}
    if t.unit in TON_UNITS:                             # часы записываются, а платят за тонны из талонов весов: ставка часа 0
        return {"tariff_id": t.id, "tariff_name": t.name, "unit": "hour", "rate": Decimal(0), "tariff_manual": False}
    return {"tariff_id": t.id, "tariff_name": t.name, "unit": t.unit, "rate": t.rate, "tariff_manual": False}


def snapshot_of(t: Tariff) -> dict:
    """То же, но по тарифу, который выбрал человек при внесении часов."""
    return {"tariff_id": t.id, "tariff_name": t.name, "unit": t.unit, "rate": t.rate, "tariff_manual": True}


def pickable(db: Session) -> list[Tariff]:
    """Тарифы, из которых офис выбирает оплату за запись часов: любые действующие, считаемые за час или за сутки.
    Сначала «Оплата работы» и «Оплата ремонта», потом остальные (например, тарифы для заказчика)."""
    order = {"work": 0, "repair": 1}
    rows = db.scalars(select(Tariff).where(Tariff.archived_at.is_(None), Tariff.unit.in_(PAY_TARIFF_UNITS)))
    return sorted(rows, key=lambda t: (order.get(t.kind, 2), (t.name or "").lower()))


def pick_group(kind: str) -> str:
    return {"work": "Оплата работы", "repair": "Оплата ремонта"}.get(kind, "Другие тарифы")


def rate_str(rate, unit: str) -> str:
    return f"{money(rate):,.2f}".replace(",", " ").replace(".", ",").replace(",00", "") + {"hour": " ₽/час", "ton": " ₽/т"}.get(unit, " ₽/день")


def rate_text(log) -> str:
    if log.tariff_id is None and not log.rate:
        return "нет тарифа"
    if log.tariff_id is not None and not log.rate and log.unit == "hour":
        return "за тонны"                                # платят по тоннажу, часы без суммы
    return rate_str(log.rate, log.unit)


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


def month_totals(db: Session, employee_id: int, first: dt.date, last: dt.date, cond=None, scope=None) -> dict:
    """Заработано за период: часы и суммы по работе и ремонту и общий итог (убранные в архив записи не считаются).
    cond: ограничение по юрлицам, если смотрит сотрудник офиса, которому разрешены не все. «Нет тарифа» — сколько записей без ставки."""
    stmt = select(WorkLog).where(WorkLog.employee_id == employee_id, WorkLog.work_date >= first, WorkLog.work_date <= last,
                                 WorkLog.archived_at.is_(None))
    if cond is not None:
        stmt = stmt.where(cond)
    logs = list(db.scalars(stmt))
    amt = amounts(db, logs)
    tot = {"work_h": Decimal(0), "repair_h": Decimal(0), "work": Decimal(0), "repair": Decimal(0)}
    for l in logs:
        tot[f"{l.kind}_h"] += Decimal(l.hours)
        tot[l.kind] += amt[l.id]
    emp = db.get(Employee, employee_id)
    tp = ton_pay(db, emp, first, last, scope.cond if scope is not None else None) if emp is not None else dict(NO_TONS)
    tot["work"] += tp["amount"]                         # работа за тонны входит в сумму за работу
    out = {k: num(v) for k, v in tot.items()}
    out["total"] = num(tot["work"] + tot["repair"])
    out["no_rate"] = sum(1 for l in logs if l.tariff_id is None)
    out["entries"] = len(logs)
    out.update(ton_fields(tp))
    return out


def _day_amounts(logs: list) -> dict[int, Decimal]:
    """Суммы записей одного человека за один день, если все записи дня перед глазами (для предварительного расчёта)."""
    first: dict[str, int] = {}
    for l in sorted(logs, key=lambda x: x.id):
        if l.unit == "day":
            first.setdefault(l.kind, l.id)
    return {l.id: (money(l.rate) if first.get(l.kind) == l.id else Decimal(0)) if l.unit == "day" else money(Decimal(l.hours) * Decimal(l.rate)) for l in logs}


class _Draft:
    """Запись, которой ещё нет в базе: нужна только для предварительного расчёта суммы."""
    def __init__(self, id_, kind, unit, rate, hours):
        self.id, self.kind, self.unit, self.rate, self.hours = id_, kind, unit, rate, hours


def preview_entry(db: Session, *, emp: Employee, work_date: dt.date, kind: str, hours, tariff_id: int | None, exclude_id: int | None, allows) -> dict:
    """Сколько выйдет по записи часов и за весь день у человека, если её сохранить. Ничего не пишет.
    allows(legal_entity_id) — какие юрлица может видеть тот, кто спрашивает: в итог дня попадают только они."""
    t, source = None, "none"
    if tariff_id:
        t = db.get(Tariff, tariff_id)
        if t is None or t.archived_at is not None or t.unit not in PAY_TARIFF_UNITS:
            return {"ok": False, "message": "Выберите тариф из списка"}
        source = "chosen"
    else:
        t = pay_tariff(db, emp, kind) if kind in WORKLOG_KINDS else None
        source = "card" if t is not None else "none"
    try:
        h = Decimal(str(hours).replace(",", ".")) if str(hours or "").strip() else None
        h = h if h is not None and h.is_finite() and 0 < h <= WORKLOG_MAX_HOURS else None
    except Exception:
        h = None
    day = [l for l in db.scalars(select(WorkLog).where(WorkLog.employee_id == emp.id, WorkLog.work_date == work_date, WorkLog.archived_at.is_(None))
                                 .order_by(WorkLog.id)) if l.id != exclude_id and allows(l.legal_entity_id)]
    before = _day_amounts(day)
    by_ton = t is not None and t.unit in TON_UNITS       # платят за тонны: часы без суммы
    out = {"ok": True, "source": source, "tariff_id": t.id if t else None, "tariff_name": t.name if t else "",
           "unit": "hour" if by_ton or not t else t.unit, "rate": 0.0 if by_ton or not t else num(t.rate),
           "rate_text": "нет тарифа" if not t else "за тонны" if by_ton else rate_str(t.rate, t.unit),
           "hours": num(h) if h is not None else None, "day_hours_other": num(sum((Decimal(l.hours) for l in day), Decimal(0))),
           "day_other": num(sum(before.values(), Decimal(0))), "amount": None, "day_total": None}
    if h is not None:
        draft = _Draft(exclude_id or 10**12, kind, "hour" if by_ton or not t else t.unit, Decimal(0) if by_ton or not t else t.rate, h)
        after = _day_amounts(day + [draft])
        out["amount"] = num(after[draft.id])
        out["day_total"] = num(sum(after.values(), Decimal(0)))
    return out


def month_pay(db: Session, entity_id: int, first: dt.date, last: dt.date, emp_ids: list[int]) -> dict[int, dict[int, dict]]:
    """{сотрудник: {день месяца: {work_h, repair_h, work, repair, total[, tons]}}} по живым записям юрлица и талонам весов (tons — у дней с талонами)."""
    if not emp_ids:
        return {}
    logs = list(db.scalars(select(WorkLog).where(WorkLog.legal_entity_id == entity_id, WorkLog.employee_id.in_(emp_ids),
                                                 WorkLog.work_date >= first, WorkLog.work_date <= last, WorkLog.archived_at.is_(None))
                           .order_by(WorkLog.id)))
    amt = amounts(db, logs)
    out: dict[int, dict[int, dict]] = {}
    for l in logs:
        cell = _cell(out, l.employee_id, l.work_date.day)
        cell[f"{l.kind}_h"] += Decimal(l.hours)
        cell[l.kind] += amt[l.id]
        cell["total"] += amt[l.id]
    tickets = db.scalars(select(WeighTicket).where(WeighTicket.legal_entity_id == entity_id, WeighTicket.driver_id.in_(emp_ids), WeighTicket.archived_at.is_(None),
                                                    WeighTicket.ticket_date >= first, WeighTicket.ticket_date <= last, WeighTicket.tons.is_not(None)))
    for r in ton_rows(db, tickets):                     # водителям с тарифом «за тонну» работа считается по талонам весов
        cell = _cell(out, r["ticket"].driver_id, r["ticket"].ticket_date.day)
        cell["work"] += r["amount"]
        cell["total"] += r["amount"]
        cell["tons"] = cell.get("tons", Decimal(0)) + r["tons"]          # ключ «tons» только у дней с талонами
    return out


def _cell(out: dict, emp_id: int, day: int) -> dict:
    return out.setdefault(emp_id, {}).setdefault(day, {"work_h": Decimal(0), "repair_h": Decimal(0), "work": Decimal(0), "repair": Decimal(0), "total": Decimal(0)})


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
        e["work_date"] = f"Можно внести не раньше чем за {backdate_days} дн. Старый день внесёт директор или диспетчер"
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
        return None, "В этот день сотрудник нигде не числится: сначала оформите его в карточке сотрудника"
    return None, "Выберите технику: сотрудник числится в нескольких юрлицах"


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
