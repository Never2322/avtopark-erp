# -*- coding: utf-8 -*-
"""Напоминания и календарь: сроки из разных разделов собираются в один список.

Один и тот же сбор (`collect`) кормит и календарь (все даты периода), и страницу «Напоминания» (только то, что наступило
или скоро наступит). Каждый источник проверяет право на свой раздел и юрлицо: человек видит в напоминаниях и календаре
только то, что видит в самих разделах. Ничего здесь не пишется в базу.
"""
import calendar
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import Principal, Scope
from app.enums import (BILL_UNPAID_DAYS, DOC_EXPIRING_DAYS, DOC_KINDS_NO_EXPIRY, EMPLOYEE_DOC_KINDS, EMPLOYEE_EVENT_KINDS,
                       EMPLOYEE_SENSITIVE_KINDS, PPE_SOON_DAYS, REMINDER_LEAD_DAYS, RENTAL_DIRECTIONS, VEHICLE_DOC_KINDS)
from app.models import (CalendarEvent, DossierDoc, Employee, EmployeeEvent, EmploymentAssignment, Fine, FuelCard, PpeIssue, PpeNorm,
                        Rental, RepairBill, Vehicle)

FINE_SOON_DAYS = 7          # как в разделе «Штрафы»: «скоро срок»
CARD_SOON_DAYS = 30         # как в разделе «Топливные карты»: «скоро кончится»

SOURCES = {
    "employee_doc": "Документы сотрудников",
    "vehicle_doc": "Документы техники",
    "employee_event": "События сотрудников",
    "ppe": "СИЗ",
    "fine": "Штрафы",
    "rental": "Аренда",
    "fuel_card": "Топливные карты",
    "bill": "Счета на ремонт",
    "calendar": "Свои события",
    "birthday": "Дни рождения",
}


def org_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().org_timezone)


def local_today() -> dt.date:
    return dt.datetime.now(org_tz()).date()


def local_day(moment: dt.datetime) -> dt.date:
    return moment.astimezone(org_tz()).date()


def day_start(d: dt.date) -> dt.datetime:
    """Начало суток по часам организации (в UTC для сравнения с базой)."""
    return dt.datetime.combine(d, dt.time.min, tzinfo=org_tz()).astimezone(dt.timezone.utc)


def left_text(left: int) -> str:
    if left < 0:
        return f"просрочено на {-left} дн."
    return "срок сегодня" if left == 0 else f"{left} дн."


def level_of(day: dt.date, today: dt.date, soon_days: int) -> str:
    """due — срок наступил или прошёл; soon — наступит в ближайшие дни; ok — ещё далеко."""
    left = (day - today).days
    return "due" if left <= 0 else "soon" if left <= soon_days else "ok"


def tone_of(level: str) -> str:
    return {"due": "error", "soon": "warning"}.get(level, "success")


def years_text(n: int) -> str:
    return f"{n} {'год' if n % 10 == 1 and n % 100 != 11 else 'года' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'лет'}"


def vehicle_label(v: Vehicle) -> str:
    return f"{v.plate} · {(v.make + ' ' + v.model).strip()}".rstrip(" ·")


def person(e: Employee) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x)


@dataclass
class Item:
    source: str
    day: dt.date
    title: str
    subject: str = ""
    url: str = ""
    entity_id: int | None = None
    level: str | None = None        # задан, если уровень считается не только по дате (свои события с напоминанием)
    tone: str | None = None         # цвет в календаре, если не по сроку (день рождения)
    time: str = ""
    ref_id: int | None = None
    done: bool = False              # свои события: отмечено выполненным
    end: str = ""                   # свои события: когда заканчивается (по часам организации)
    all_day: bool = False


@dataclass
class Ctx:
    p: Principal
    db: Session
    today: dt.date
    entity_id: int | None
    lo: dt.date | None              # границы периода для календаря; для напоминаний lo не задан
    hi: dt.date | None
    reminders: bool                 # True — собираем напоминания (только наступившее и близкое), False — календарь

    def scope(self, perm: str) -> Scope:
        return self.p.scope(perm).narrow(self.entity_id)

    def window(self, soon_days: int) -> tuple[dt.date | None, dt.date | None]:
        return (None, self.today + dt.timedelta(days=soon_days)) if self.reminders else (self.lo, self.hi)


def _between(col, lo, hi):
    conds = [col.is_not(None)]
    if lo is not None:
        conds.append(col >= lo)
    if hi is not None:
        conds.append(col <= hi)
    return and_(*conds)


def _emp_cond(scope: Scope):
    """Сотрудник виден, если работает в доступном юрлице (всем юрлицам — без условия)."""
    if scope.everything:
        return None
    return exists().where(EmploymentAssignment.employee_id == Employee.id, EmploymentAssignment.legal_entity_id.in_(scope.ids))


# ---------------------------------------------------------------- источники

def _employee_docs(c: Ctx) -> list[Item]:
    if not c.p.scope("employee_files.view"):
        return []
    lo, hi = c.window(DOC_EXPIRING_DAYS)
    out = []
    for sensitive in (False, True):
        scope = c.scope("employee_files.view_passport" if sensitive else "employee_files.view")
        if not scope:
            continue
        q = (select(DossierDoc, Employee).join(Employee, Employee.id == DossierDoc.employee_id)
             .where(DossierDoc.archived_at.is_(None), Employee.archived_at.is_(None), _between(DossierDoc.valid_until, lo, hi),
                    DossierDoc.kind.notin_(tuple(DOC_KINDS_NO_EXPIRY)),
                    DossierDoc.kind.in_(tuple(EMPLOYEE_SENSITIVE_KINDS)) if sensitive else DossierDoc.kind.notin_(tuple(EMPLOYEE_SENSITIVE_KINDS))))
        cond = _emp_cond(scope)
        if cond is not None:
            q = q.where(cond)
        for d, e in c.db.execute(q.order_by(DossierDoc.valid_until, DossierDoc.id)):
            name = (d.title or "Другой документ") if d.kind == "other" else EMPLOYEE_DOC_KINDS.get(d.kind, d.kind)
            out.append(Item("employee_doc", d.valid_until, f"Кончается срок: {name}", person(e), f"/dossier/employees/{e.id}", None, ref_id=d.id))
    return out


def _vehicle_docs(c: Ctx) -> list[Item]:
    scope = c.scope("vehicle_files.view")
    if not scope:
        return []
    lo, hi = c.window(DOC_EXPIRING_DAYS)
    q = (select(DossierDoc, Vehicle).join(Vehicle, Vehicle.id == DossierDoc.vehicle_id)
         .where(DossierDoc.archived_at.is_(None), Vehicle.archived_at.is_(None), scope.cond(Vehicle.legal_entity_id),
                _between(DossierDoc.valid_until, lo, hi), DossierDoc.kind.notin_(tuple(DOC_KINDS_NO_EXPIRY)))
         .order_by(DossierDoc.valid_until, DossierDoc.id))
    out = []
    for d, v in c.db.execute(q):
        name = (d.title or "Другой документ") if d.kind == "other" else VEHICLE_DOC_KINDS.get(d.kind, d.kind)
        out.append(Item("vehicle_doc", d.valid_until, f"Кончается срок: {name}", vehicle_label(v), f"/dossier/vehicles/{v.id}", v.legal_entity_id, ref_id=d.id))
    return out


def _employee_events(c: Ctx) -> list[Item]:
    scope = c.scope("employee_events.view")
    if not scope:
        return []
    lo, hi = c.window(REMINDER_LEAD_DAYS)
    q = (select(EmployeeEvent, Employee).join(Employee, Employee.id == EmployeeEvent.employee_id)
         .where(EmployeeEvent.archived_at.is_(None), EmployeeEvent.controlled.is_(True), Employee.archived_at.is_(None),
                scope.cond(EmployeeEvent.legal_entity_id), _between(EmployeeEvent.due_date, lo, hi))
         .order_by(EmployeeEvent.due_date, EmployeeEvent.id))
    out = []
    for ev, e in c.db.execute(q):
        kind = EMPLOYEE_EVENT_KINDS.get(ev.kind, ev.kind)
        title = f"{kind}: следующий срок" + (f" ({ev.description})" if ev.description and ev.description.strip().lower() != kind.lower() else "")
        out.append(Item("employee_event", ev.due_date, title, person(e), "/employee-events", ev.legal_entity_id, ref_id=ev.id))
    return out


def _ppe(c: Ctx) -> list[Item]:
    scope = c.scope("ppe.view")
    if not scope:
        return []
    lo, hi = c.window(PPE_SOON_DAYS)
    q = (select(PpeIssue, PpeNorm, Employee).join(PpeNorm, PpeNorm.id == PpeIssue.ppe_id).join(Employee, Employee.id == PpeIssue.employee_id)
         .where(PpeIssue.archived_at.is_(None), PpeIssue.returned_date.is_(None), Employee.archived_at.is_(None),
                scope.cond(PpeIssue.legal_entity_id), _between(PpeIssue.due_date, lo, hi))
         .order_by(PpeIssue.due_date, PpeIssue.id))
    return [Item("ppe", i.due_date, f"Замена СИЗ: {n.name}", person(e), "/ppe", i.legal_entity_id, ref_id=i.id) for i, n, e in c.db.execute(q)]


def _fines(c: Ctx) -> list[Item]:
    scope = c.scope("fines.view")
    if not scope:
        return []
    base = [Fine.archived_at.is_(None), Fine.paid_at.is_(None), scope.cond(Fine.legal_entity_id)]
    out = []
    if c.reminders:
        # одна дата на штраф: со скидкой, пока она действует, иначе общий срок (как в разделе «Штрафы»)
        q = select(Fine, Vehicle).join(Vehicle, Vehicle.id == Fine.vehicle_id).where(*base).order_by(Fine.id)
        for f, v in c.db.execute(q):
            discount = f.discount_until is not None and f.discount_until >= c.today
            day = f.discount_until if discount else f.due_date
            if day is None or day > c.today + dt.timedelta(days=FINE_SOON_DAYS):
                continue
            out.append(Item("fine", day, "Оплатить штраф со скидкой" if discount else "Оплатить штраф", f"{vehicle_label(v)}, {f.resolution_number}".rstrip(", "),
                            "/fines", f.legal_entity_id, ref_id=f.id))
        return out
    for col, title in ((Fine.discount_until, "Скидка на штраф кончается"), (Fine.due_date, "Оплатить штраф")):
        q = (select(Fine, Vehicle).join(Vehicle, Vehicle.id == Fine.vehicle_id).where(*base, _between(col, c.lo, c.hi)).order_by(col, Fine.id))
        for f, v in c.db.execute(q):
            out.append(Item("fine", getattr(f, col.key), title, f"{vehicle_label(v)}, {f.resolution_number}".rstrip(", "), "/fines", f.legal_entity_id, ref_id=f.id))
    return out


def _rentals(c: Ctx) -> list[Item]:
    scope = c.scope("rentals.view")
    if not scope:
        return []
    lo, hi = c.window(REMINDER_LEAD_DAYS)
    q = (select(Rental, Vehicle).join(Vehicle, Vehicle.id == Rental.vehicle_id)
         .where(Rental.archived_at.is_(None), Rental.finished.is_(False), scope.cond(Rental.legal_entity_id), _between(Rental.end_date, lo, hi))
         .order_by(Rental.end_date, Rental.id))
    return [Item("rental", r.end_date, f"Кончается аренда ({RENTAL_DIRECTIONS.get(r.direction, '').lower()})", vehicle_label(v), "/rentals", r.legal_entity_id, ref_id=r.id)
            for r, v in c.db.execute(q)]


def _fuel_cards(c: Ctx) -> list[Item]:
    scope = c.scope("fuel_cards.view")
    if not scope:
        return []
    lo, hi = c.window(CARD_SOON_DAYS)
    q = (select(FuelCard).where(FuelCard.archived_at.is_(None), scope.cond(FuelCard.legal_entity_id), _between(FuelCard.valid_until, lo, hi))
         .order_by(FuelCard.valid_until, FuelCard.id))
    return [Item("fuel_card", f.valid_until, "Кончается срок топливной карты", f"{f.number}{', ' + f.provider if f.provider else ''}", "/fuel-cards", f.legal_entity_id, ref_id=f.id)
            for f in c.db.scalars(q)]


def _bills(c: Ctx) -> list[Item]:
    """Только в напоминаниях: счёт не оплачен уже неделю. В календаре у счёта нет даты, на которую его ждут."""
    scope = c.scope("bills.view")
    if not scope or not c.reminders:
        return []
    q = (select(RepairBill).where(RepairBill.archived_at.is_(None), RepairBill.status == "unpaid", scope.cond(RepairBill.legal_entity_id),
                                  RepairBill.bill_date <= c.today - dt.timedelta(days=BILL_UNPAID_DAYS)).order_by(RepairBill.bill_date, RepairBill.id))
    return [Item("bill", b.bill_date + dt.timedelta(days=BILL_UNPAID_DAYS), "Счёт на ремонт не оплачен", f"№ {b.number}{', ' + b.supplier_name if b.supplier_name else ''}",
                 f"/bills/{b.id}", b.legal_entity_id, ref_id=b.id) for b in c.db.scalars(q)]


def _own_events(c: Ctx) -> list[Item]:
    scope = c.scope("calendar_events.view")
    if not scope:
        return []
    q = select(CalendarEvent).where(CalendarEvent.archived_at.is_(None), scope.cond(CalendarEvent.legal_entity_id))
    if c.reminders:
        q = q.where(CalendarEvent.done.is_(False), CalendarEvent.remind_min.is_not(None),
                    CalendarEvent.start_at < day_start(c.today + dt.timedelta(days=REMINDER_LEAD_DAYS + 1)))
    else:
        q = q.where(CalendarEvent.start_at >= day_start(c.lo), CalendarEvent.start_at < day_start(c.hi + dt.timedelta(days=1)))
    now = dt.datetime.now(dt.timezone.utc)
    names = {}
    out = []
    for ev in c.db.scalars(q.order_by(CalendarEvent.start_at, CalendarEvent.id)):
        local = ev.start_at.astimezone(org_tz())
        subject = ""
        if ev.vehicle_id:
            v = names.get(("v", ev.vehicle_id)) or names.setdefault(("v", ev.vehicle_id), c.db.get(Vehicle, ev.vehicle_id))
            subject = vehicle_label(v) if v else ""
        if ev.employee_id:
            e = names.get(("e", ev.employee_id)) or names.setdefault(("e", ev.employee_id), c.db.get(Employee, ev.employee_id))
            subject = (subject + ", " if subject else "") + (person(e) if e else "")
        level = None
        if c.reminders:
            level = "due" if now >= ev.start_at - dt.timedelta(minutes=ev.remind_min or 0) else "soon"
        out.append(Item("calendar", local.date(), ev.title, subject, "/calendar", ev.legal_entity_id, level=level, tone=ev.color,
                        time="" if ev.all_day else local.strftime("%H:%M"), ref_id=ev.id, done=ev.done, all_day=ev.all_day,
                        end=ev.end_at.astimezone(org_tz()).isoformat(timespec="minutes") if ev.end_at else ""))
    return out


def _birthdays(c: Ctx) -> list[Item]:
    """Только в календаре. День рождения — личные данные: видят те, у кого есть право на личные данные сотрудников."""
    scope = c.scope("employees.view_sensitive")
    if not scope or c.reminders:
        return []
    q = select(Employee).where(Employee.archived_at.is_(None), Employee.birth_date.is_not(None))
    cond = _emp_cond(scope)
    if cond is not None:
        q = q.where(cond)
    out = []
    for e in c.db.scalars(q):
        b = e.birth_date
        for year in range(c.lo.year, c.hi.year + 1):
            day = dt.date(year, b.month, b.day) if not (b.month == 2 and b.day == 29 and not calendar.isleap(year)) else dt.date(year, 2, 28)
            if c.lo <= day <= c.hi and year > b.year:
                out.append(Item("birthday", day, f"День рождения, {years_text(year - b.year)}", person(e), "/employees", None, tone="violet", ref_id=e.id))
    return out


COLLECTORS = (_employee_docs, _vehicle_docs, _employee_events, _ppe, _fines, _rentals, _fuel_cards, _bills, _own_events, _birthdays)


def collect(db: Session, p: Principal, *, entity_id: int | None = None, lo: dt.date | None = None, hi: dt.date | None = None,
            reminders: bool = False, today: dt.date | None = None) -> list[Item]:
    ctx = Ctx(p, db, today or local_today(), entity_id, lo, hi, reminders)
    items: list[Item] = []
    for fn in COLLECTORS:
        items.extend(fn(ctx))
    return items


# ---------------------------------------------------------------- вид для сайта

SOON_BY_SOURCE = {"employee_doc": DOC_EXPIRING_DAYS, "vehicle_doc": DOC_EXPIRING_DAYS, "ppe": PPE_SOON_DAYS, "fine": FINE_SOON_DAYS,
                  "fuel_card": CARD_SOON_DAYS}


def reminder_out(i: Item, today: dt.date) -> dict:
    soon = SOON_BY_SOURCE.get(i.source, REMINDER_LEAD_DAYS)
    left = (i.day - today).days
    level = i.level or level_of(i.day, today, soon)
    return {"source": i.source, "source_label": SOURCES[i.source], "level": level, "tone": tone_of(level), "day": i.day.isoformat(), "left": left,
            "left_text": left_text(left), "title": i.title, "subject": i.subject, "url": i.url, "entity_id": i.entity_id, "ref_id": i.ref_id}


def reminders(db: Session, p: Principal, entity_id: int | None = None) -> list[dict]:
    today = local_today()
    rows = [reminder_out(i, today) for i in collect(db, p, entity_id=entity_id, reminders=True, today=today)]
    rows = [r for r in rows if r["level"] in ("due", "soon")]
    rows.sort(key=lambda r: (0 if r["level"] == "due" else 1, r["day"], r["source"], r["title"]))
    return rows


def calendar_out(i: Item, today: dt.date) -> dict:
    auto = i.source != "calendar"
    tone = i.tone or ("red" if i.day <= today else "amber")
    return {"source": i.source, "source_label": SOURCES[i.source], "auto": auto, "day": i.day.isoformat(), "time": i.time, "title": i.title,
            "subject": i.subject, "url": i.url, "color": tone, "entity_id": i.entity_id, "ref_id": i.ref_id,
            "done": i.done, "all_day": i.all_day, "end": i.end}
