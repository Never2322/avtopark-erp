# -*- coding: utf-8 -*-
"""Описание разделов «Справочники» и «Деньги»: какие поля, какие правила. Всё остальное делает app/registry.py."""
import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, case, func, select

from app.common import num
from app.enums import (MAINTENANCE_KINDS, TIRE_SEASONS, VEHICLE_FINANCE_KINDS, VEHICLE_REPAIR_KINDS, CALENDAR_COLORS, CALENDAR_REMIND_MAX, EMPLOYEE_EVENT_KINDS, EMPLOYEE_EVENT_MONEY_KINDS, REMINDER_LEAD_DAYS, PPE_SOON_DAYS, PPE_WEAR_MAX_MONTHS, PART_UNITS, CASH_ACCOUNT_KINDS, CASH_OP_KINDS, COUNTERPARTY_KINDS, DOC_FULL_NAMES, DOC_TYPES, ENTRY_LATE_DAYS, FINE_PAYERS,
                       FUEL_MAX_LITERS, JOB_DATE_WINDOW_DAYS, JOB_STATES, ORDER_STATUSES, PAYMENT_KINDS, RENTAL_DIRECTIONS, TARIFF_UNITS,
                       STOCK_UNITS, VAT_RATES)
from app.models import (ControlEvent, MaintenanceItem, VehicleFinance, VehicleRepair, VehicleTire, CalendarEvent, EmployeeEvent, CashAccount, CashOp, Constant, Counterparty, Document, Employee, EmployeePayment, EntryPhoto, Fine, FuelCard,
                        FuelFill, Job, JobPhoto, MiscExpense, Order, PartCatalog, PpeIssue, PpeNorm, Rental, StockItem, Tariff, Trip, Vehicle, Warehouse)
from app.registry import MAX_MONEY, Col, F, Invalid, R, org_tz, register
from app.parts_seed import norm as part_key
from app.services import control as control_svc
from app.services import fuel as fuel_calc
from app.services import ppe as ppe_calc
from app.services import notify as notify_svc
from app.services import stock as stock_svc
from app.services import reminders as rem
from app.services.people import user_names
from app.services.vehicles import current_driver_id
from app.services.numbering import next_number

EXPIRING_DAYS = 30        # топливная карта: «скоро кончается»
FINE_SOON_DAYS = 7        # штраф: «скоро срок»
ONE_DAY = dt.timedelta(days=1)


def _today() -> dt.date:
    return dt.date.today()


def _not_future(m: dict, name: str, label: str, errors: dict):
    """Даты событий не бывают в будущем (запас в сутки: у людей и сервера разные часовые пояса)."""
    d = m.get(name)
    if d and d > _today() + ONE_DAY:
        errors[name] = f"{label} не может быть в будущем"


def _digits(m: dict, name: str, lengths: tuple[int, ...], label: str, errors: dict):
    v = (m.get(name) or "").strip()
    if v and (not v.isdigit() or len(v) not in lengths):
        errors[name] = f"{label}: {' или '.join(str(n) for n in lengths)} цифр"


# ================================================================ справочники

def _counterparty_prepare(db, p, v, obj, m):
    e: dict = {}
    _digits(m, "inn", (10, 12), "ИНН", e)
    _digits(m, "kpp", (9,), "КПП", e)
    _digits(m, "bik", (9,), "БИК", e)
    _digits(m, "account", (20,), "Расчётный счёт", e)
    _digits(m, "corr_account", (20,), "Корр. счёт", e)
    email = (m.get("email") or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        e["email"] = "Проверьте адрес почты"
    if e:
        raise Invalid(e)


register(R(
    key="counterparties", title="Контрагенты", one="контрагент", model=Counterparty, scope="global",
    fields=[
        F("name", "Название", required=True, max=200, wide=True),
        F("kind", "Кто это", "enum", options=COUNTERPARTY_KINDS, default="customer", filter=True),
        F("inn", "ИНН", max=12), F("kpp", "КПП", max=9, list=False),
        F("phone", "Телефон", max=40), F("email", "Почта", max=120, list=False), F("contact", "Контактное лицо", max=120),
        F("address", "Адрес", max=300, list=False, wide=True),
        F("bank_name", "Банк", max=120, list=False), F("bik", "БИК", max=9, list=False),
        F("account", "Расчётный счёт", max=20, list=False), F("corr_account", "Корр. счёт", max=20, list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    search=("name", "inn", "phone", "contact"), order=("name",), prepare=_counterparty_prepare,
    unique_field="name", unique_text="Контрагент с таким названием уже есть",
))

register(R(
    key="tariffs", title="Тарифы", one="тариф", model=Tariff, scope="global",
    fields=[
        F("name", "Название", required=True, max=160, wide=True, hint="Например: «Самосвал, час» или «Вывоз снега, рейс»"),
        F("unit", "За что платят", "enum", options=TARIFF_UNITS, default="hour", required=True, filter=True),
        F("rate", "Ставка, ₽", "money", required=True, default="0"),
        F("min_amount", "Минимальная сумма, ₽", "money", default="0", hint="Меньше этого за выезд не берём; 0 — без минимума"),
        F("note", "Заметка", "textarea", list=False),
    ],
    search=("name", "note"), order=("name",),
    unique_field="name", unique_text="Тариф с таким названием уже есть",
))

register(R(
    key="constants", title="Константы", one="константа", model=Constant, scope="global",
    fields=[
        F("name", "Название", required=True, max=120, hint="Например: «Цена дизельного топлива, ₽/л»"),
        F("value", "Значение", required=True, max=300),
        F("note", "Пояснение", max=300, wide=True),
    ],
    search=("name", "value", "note"), order=("name",),
    unique_field="name", unique_text="Константа с таким названием уже есть",
))


# ================================================================ топливные карты

def _card_state(valid_until: dt.date | None, today: dt.date) -> dict:
    if valid_until is None:
        return {"text": "Без срока", "tone": "neutral"}
    if valid_until < today:
        return {"text": "Срок вышел", "tone": "error"}
    if valid_until <= today + dt.timedelta(days=EXPIRING_DAYS):
        return {"text": "Скоро кончится", "tone": "warning"}
    return {"text": "Действует", "tone": "success"}


def _card_computed(db, objs):
    today = _today()
    return {o.id: {"state": _card_state(o.valid_until, today)} for o in objs}


def _card_filter(q, state, today):
    soon = today + dt.timedelta(days=EXPIRING_DAYS)
    if state == "expired":
        return q.where(FuelCard.valid_until < today)
    if state == "expiring":
        return q.where(FuelCard.valid_until >= today, FuelCard.valid_until <= soon)
    return q.where((FuelCard.valid_until.is_(None)) | (FuelCard.valid_until > soon))


register(R(
    key="fuel_cards", title="Топливные карты", one="топливная карта", model=FuelCard,
    fields=[
        F("number", "Номер карты", required=True, max=40),
        F("provider", "Поставщик", max=80, suggest=True, hint="Например: Лукойл, Роснефть"),
        F("vehicle_id", "Закреплена за техникой", "ref", ref="vehicle"),
        F("limit_month_l", "Лимит в месяц, л", "decimal", places=1),
        F("valid_until", "Действует до", "date"),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("state", "Состояние", "badge")],
    search=("number", "provider"), search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")),), order=("number",),
    computed=_card_computed, states={"expired": "Срок вышел", "expiring": "Скоро кончится", "ok": "Действуют"}, state_filter=_card_filter,
    unique_field="number", unique_text="Карта с таким номером уже есть",
))


# ================================================================ штрафы

def _fine_deadline(f) -> dt.date | None:
    """Платим до: со скидкой, пока она действует, иначе до общего срока."""
    today = _today()
    if f.discount_until and f.discount_until >= today:
        return f.discount_until
    return f.due_date


def _fine_state(f, today: dt.date) -> dict:
    if f.paid_at:
        return {"text": "Оплачен", "tone": "success"}
    deadline = _fine_deadline(f)
    if deadline is None:
        return {"text": "Не оплачен", "tone": "info"}
    if deadline < today:
        return {"text": "Просрочен", "tone": "error"}
    if deadline == today:
        return {"text": "Последний день", "tone": "warning"}
    if deadline <= today + dt.timedelta(days=FINE_SOON_DAYS):
        return {"text": "Скоро срок", "tone": "warning"}
    return {"text": "Не оплачен", "tone": "info"}


def _fine_computed(db, objs):
    today = _today()
    return {o.id: {"state": _fine_state(o, today), "deadline": _fine_deadline(o)} for o in objs}


def _fine_prepare(db, p, v, obj, m):
    e: dict = {}
    _not_future(m, "fine_date", "Дата нарушения", e)
    _not_future(m, "paid_at", "Дата оплаты", e)
    fd = m.get("fine_date")
    for name, label in (("discount_until", "Срок со скидкой"), ("due_date", "Срок оплаты"), ("paid_at", "Дата оплаты")):
        d = m.get(name)
        if d and fd and d < fd:
            e[name] = f"{label} не может быть раньше даты нарушения"
    if m.get("discount_until") and m.get("due_date") and m["discount_until"] > m["due_date"]:
        e["discount_until"] = "Срок со скидкой не может быть позже общего срока"
    if e:
        raise Invalid(e)


def _fine_filter(q, state, today):
    deadline = case((and_(Fine.discount_until.is_not(None), Fine.discount_until >= today), Fine.discount_until), else_=Fine.due_date)
    if state == "paid":
        return q.where(Fine.paid_at.is_not(None))
    q = q.where(Fine.paid_at.is_(None))
    if state == "overdue":
        return q.where(deadline < today)
    return q


register(R(
    key="fines", title="Штрафы", one="штраф", model=Fine, entity_from="vehicle",
    fields=[
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True),
        F("driver_id", "Водитель", "ref", ref="employee", hint="Кто был за рулём, если известно"),
        F("fine_date", "Дата нарушения", "date", required=True),
        F("resolution_number", "Номер постановления", max=60),
        F("article", "Статья", max=120, list=False),
        F("amount", "Сумма, ₽", "money", required=True, sum=True),
        F("discount_until", "Со скидкой до", "date", list=False),
        F("due_date", "Оплатить до", "date", list=False),
        F("payer", "Кто платит", "enum", options=FINE_PAYERS, default="company", required=True, filter=True),
        F("paid_at", "Оплачен", "date", hint="Дата оплаты; пусто — ещё не оплачен", list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("deadline", "Платить до", "date"), Col("state", "Состояние", "badge")],
    search=("resolution_number", "article", "note"),
    search_refs=(("vehicle_id", Vehicle, ("plate",)), ("driver_id", Employee, ("last_name", "first_name"))),
    date="fine_date", order=("-fine_date",), prepare=_fine_prepare, computed=_fine_computed,
    states={"unpaid": "Не оплачены", "overdue": "Просрочены", "paid": "Оплачены"}, state_filter=_fine_filter,
))


# ================================================================ аренда техники

def _rental_days(r, today: dt.date) -> int:
    """Сутки считаются включительно; у аренды без даты окончания — по сегодняшний день."""
    return max(((r.end_date or today) - r.start_date).days + 1, 0)


def _rental_computed(db, objs):
    today = _today()
    out = {}
    for r in objs:
        days = _rental_days(r, today)
        state = {"text": "Закончена", "tone": "neutral"} if r.finished else (
            {"text": "Ещё не началась", "tone": "info"} if r.start_date > today else {"text": "Идёт", "tone": "success"})
        out[r.id] = {"days": days, "total": float(Decimal(days) * (r.rate or 0)), "state": state}
    return out


def _rental_prepare(db, p, v, obj, m):
    e: dict = {}
    s, en = m.get("start_date"), m.get("end_date")
    if en and s and en < s:
        e["end_date"] = "Дата окончания не может быть раньше начала"
    if m.get("finished") and not en:
        e["end_date"] = "Для оконченной аренды укажите дату окончания"
    if not e:
        # один и тот же автомобиль не сдают (и не берут) дважды на одни и те же дни
        q = select(Rental.id, Rental.start_date, Rental.end_date).where(
            Rental.vehicle_id == m["vehicle_id"], Rental.direction == m["direction"], Rental.archived_at.is_(None))
        if obj is not None:
            q = q.where(Rental.id != obj.id)
        far = dt.date(2100, 1, 1)
        for _id, rs, re_ in db.execute(q):
            if s <= (re_ or far) and rs <= (en or far):
                e["start_date"] = f"Эти дни пересекаются с другой арендой этой техники ({rs.strftime('%d.%m.%Y')} — {re_.strftime('%d.%m.%Y') if re_ else 'без даты окончания'})"
                break
    if e:
        raise Invalid(e)


register(R(
    key="rentals", title="Аренда техники", one="аренда", model=Rental, entity_from="vehicle",
    fields=[
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True),
        F("direction", "Направление", "enum", options=RENTAL_DIRECTIONS, default="out", required=True, filter=True),
        F("counterparty_id", "Контрагент", "ref", ref="counterparty"),
        F("start_date", "С", "date", required=True),
        F("end_date", "По", "date", hint="Пусто — без даты окончания"),
        F("rate", "Цена за сутки, ₽", "money", required=True, default="0"),
        F("finished", "Аренда закончена", "bool", list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("days", "Суток", "number"), Col("total", "Итого, ₽", "money", sum=True), Col("state", "Состояние", "badge")],
    search=("note",), search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")), ("counterparty_id", Counterparty, ("name",))),
    date="start_date", order=("-start_date",), prepare=_rental_prepare, computed=_rental_computed,
    states={"active": "Идут", "finished": "Закончены"},
    state_filter=lambda q, state, today: q.where(Rental.finished.is_(state == "finished")),
))


# ================================================================ прочие расходы и выплаты

def _dated_prepare(*pairs):
    def prepare(db, p, v, obj, m):
        e: dict = {}
        for name, label in pairs:
            _not_future(m, name, label, e)
        if e:
            raise Invalid(e)
    return prepare


register(R(
    key="expenses", title="Расходы", one="расход", model=MiscExpense,
    fields=[
        F("op_date", "Дата", "date", required=True),
        F("category", "Статья расхода", required=True, max=80, suggest=True, hint="Например: аренда стоянки, мойка, связь"),
        F("amount", "Сумма, ₽", "money", required=True, lo=Decimal("0.01"), sum=True),
        F("description", "Что оплатили", max=300, wide=True),
    ],
    search=("category", "description"), date="op_date", order=("-op_date",), prepare=_dated_prepare(("op_date", "Дата")),
))

register(R(
    key="payments", title="Выплаты сотрудникам", one="выплата", model=EmployeePayment,
    fields=[
        F("employee_id", "Сотрудник", "ref", ref="employee", required=True),
        F("pay_date", "Дата", "date", required=True),
        F("kind", "Вид", "enum", options=PAYMENT_KINDS, default="salary", required=True, filter=True),
        F("amount", "Сумма, ₽", "money", required=True, lo=Decimal("0.01"), sum=True),
        F("note", "Заметка", max=300, wide=True),
    ],
    search=("note",), search_refs=(("employee_id", Employee, ("last_name", "first_name")),),
    date="pay_date", order=("-pay_date",), prepare=_dated_prepare(("pay_date", "Дата")),
))


# ================================================================ счета и касса

def _balances(db, ids: list[int]) -> dict[int, Decimal]:
    """Остаток = начальный + приходы − расходы − переводы ушедшие + переводы пришедшие (отменённые операции не считаются)."""
    if not ids:
        return {}
    live = CashOp.archived_at.is_(None)
    opening = {i: o for i, o in db.execute(select(CashAccount.id, CashAccount.opening).where(CashAccount.id.in_(ids)))}
    res = {i: Decimal(opening.get(i) or 0) for i in ids}
    for acc, kind, total in db.execute(select(CashOp.account_id, CashOp.kind, func.sum(CashOp.amount)).where(live, CashOp.account_id.in_(ids)).group_by(CashOp.account_id, CashOp.kind)):
        res[acc] += total if kind == "in" else -total
    for acc, total in db.execute(select(CashOp.to_account_id, func.sum(CashOp.amount)).where(live, CashOp.kind == "transfer", CashOp.to_account_id.in_(ids)).group_by(CashOp.to_account_id)):
        res[acc] += total
    return res


def _account_computed(db, objs):
    bal = _balances(db, [o.id for o in objs])
    return {o.id: {"balance": float(bal.get(o.id, 0))} for o in objs}


register(R(
    key="cash_accounts", title="Счета и кассы", one="счёт", model=CashAccount,
    fields=[
        F("name", "Название", required=True, max=120, hint="Например: «Касса офиса» или «Расчётный счёт в банке»"),
        F("kind", "Вид", "enum", options=CASH_ACCOUNT_KINDS, default="cash", required=True, filter=True),
        F("opening", "Остаток на начало, ₽", "money", default="0", hint="Сколько было, когда начали вести учёт здесь"),
        F("note", "Заметка", max=300, wide=True),
    ],
    cols=[Col("balance", "Остаток сейчас, ₽", "money", sum=True)],
    search=("name", "note"), order=("name",), computed=_account_computed,
    unique_field="name", unique_text="Счёт с таким названием в этом юрлице уже есть",
))

OP_PREFIX = {"in": "ПКО", "out": "РКО", "transfer": "ПЕР"}
OP_COUNTER = {"in": "cash_in", "out": "cash_out", "transfer": "cash_transfer"}


def _cash_prepare(db, p, v, obj, m):
    e: dict = {}
    _not_future(m, "op_date", "Дата", e)
    if m.get("kind") == "transfer" and m.get("to_account_id") == m.get("account_id"):
        e["to_account_id"] = "Выберите другой счёт: деньги переводятся между разными счетами"
    if obj is not None and m.get("op_date") and m["op_date"].year != obj.year:
        e["op_date"] = "Дату нельзя перенести в другой год: отмените операцию и добавьте новую"
    if e:
        raise Invalid(e)
    if obj is None:
        v["year"] = m["op_date"].year
        v["number"] = next_number(db, m["legal_entity_id"], OP_COUNTER[m["kind"]], v["year"])
    if m.get("kind") != "transfer":
        v["to_account_id"] = None


def _cash_computed(db, objs):
    return {o.id: {"doc_no": f"{OP_PREFIX[o.kind]}-{o.number}/{o.year}"} for o in objs}


def _cash_warn(db, op) -> list[str]:
    """После расхода или перевода остаток не должен уйти в минус; если ушёл, пишем об этом, но не запрещаем (бывает и по факту)."""
    if op.kind == "in":
        return []
    left = _balances(db, [op.account_id]).get(op.account_id, Decimal(0))
    if left < 0:
        name = db.get(CashAccount, op.account_id).name
        return [f"Остаток на счёте «{name}» стал отрицательным: {left:,.2f} ₽".replace(",", " ").replace(".", ",")]
    return []


register(R(
    key="cash", title="Касса и банк", one="операция", model=CashOp,
    fields=[
        F("op_date", "Дата", "date", required=True),
        F("kind", "Вид операции", "enum", options=CASH_OP_KINDS, default="in", required=True, locked=True, filter=True),
        F("account_id", "Счёт", "ref", ref="account", required=True, hint="С какого счёта или в какую кассу"),
        F("to_account_id", "На счёт", "ref", ref="account", required=True, show_if=("kind", "transfer")),
        F("amount", "Сумма, ₽", "money", required=True, lo=Decimal("0.01"), sum=False),
        F("category", "Статья", max=80, suggest=True),
        F("counterparty_id", "Контрагент", "ref", ref="counterparty", list=False),
        F("employee_id", "Сотрудник", "ref", ref="employee", list=False),
        F("note", "Основание", max=300, wide=True),
        F("year", "Год", "int", auto=True, list=False), F("number", "Номер", "int", auto=True, list=False),
    ],
    cols=[Col("doc_no", "Документ", "text")],
    search=("category", "note"),
    search_refs=(("counterparty_id", Counterparty, ("name",)), ("employee_id", Employee, ("last_name", "first_name"))),
    date="op_date", order=("-op_date", "-number"), prepare=_cash_prepare, computed=_cash_computed, warn=_cash_warn,
))


# ================================================================ заказы и документы

ORDER_TONES = {"new": "info", "confirmed": "mint", "in_progress": "warning", "done": "success", "cancelled": "neutral"}
CENT = Decimal("0.01")
VOLUME_UNITS = {"km": ("distance_km", "расстояние"), "hour": ("hours", "время в работе"), "ton": ("cargo_kg", "массу груза")}


def _money_round(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _vat_split(base: Decimal, vat_pct: int) -> tuple[Decimal, Decimal]:
    """НДС сверху: сумма без НДС → (НДС, итого)."""
    vat = _money_round(base * vat_pct / 100)
    return vat, base + vat


def _emp_label_safe(e) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x) if e is not None else ""


def _order_no(o) -> str:
    return f"№ {o.number}/{o.year}"


def _active_docs(db, order_id: int) -> list:
    return list(db.scalars(select(Document).where(Document.order_id == order_id, Document.archived_at.is_(None)).order_by(Document.doc_type, Document.number)))


def _order_prepare(db, p, v, obj, m):
    e: dict = {}
    if m.get("depart_at") and m.get("deliver_at") and m["deliver_at"] < m["depart_at"]:
        e["deliver_at"] = "Доставка не может быть раньше выезда"
    if obj is not None and m.get("order_date") and m["order_date"].year != obj.year:
        e["order_date"] = "Дату нельзя перенести в другой год: уберите заказ в архив и добавьте новый"
    if obj is not None and m.get("customer_id") != obj.customer_id and _active_docs(db, obj.id):
        e["customer_id"] = "Нельзя сменить заказчика: по заказу уже есть документы. Сначала уберите их в архив"
    vat = int(m.get("vat_pct") or 0)
    v["vat_pct"] = vat

    # расчёт суммы
    tariff = db.get(Tariff, m["tariff_id"]) if m.get("tariff_id") else None
    qty, rate, amount = m.get("qty"), Decimal(m.get("rate") or 0), Decimal(m.get("amount") or 0)
    if tariff is not None:
        changed = obj is not None and obj.tariff_id != tariff.id
        if not rate or (changed and rate == obj.rate):
            rate = tariff.rate                                    # ставка тарифа; вписанная вручную (скидка) остаётся
        if tariff.unit in VOLUME_UNITS:
            field, word = VOLUME_UNITS[tariff.unit]
            vol = m.get(field)
            if vol is None:
                e[field] = f"Для тарифа «{tariff.name}» заполните {word}"
            else:
                qty = Decimal(vol) / 1000 if tariff.unit == "ton" else Decimal(vol)
        else:
            qty = Decimal(qty) if qty else Decimal(1)
        qty = Decimal(qty).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP) if not e else qty
        if not e:
            amount = max(_money_round(Decimal(qty) * rate), tariff.min_amount)
    elif qty is not None and rate and not amount:
        amount = _money_round(Decimal(qty) * rate)
    if amount > MAX_ORDER:
        e["amount"] = "Слишком большая сумма: проверьте цифры"
    if e:
        raise Invalid(e)
    v["qty"], v["rate"], v["amount"] = (None if qty is None else Decimal(qty).quantize(Decimal("0.001"))), rate, amount
    if obj is None:
        v["year"] = m["order_date"].year
        v["number"] = next_number(db, m["legal_entity_id"], "order", v["year"])


def _order_computed(db, objs):
    ids = [o.id for o in objs]
    docs: dict[int, list[str]] = {}
    if ids:
        for d in db.scalars(select(Document).where(Document.order_id.in_(ids), Document.archived_at.is_(None)).order_by(Document.doc_type, Document.number)):
            docs.setdefault(d.order_id, []).append(f"{DOC_TYPES[d.doc_type]} {d.number}")
    out = {}
    for o in objs:
        vat, total = _vat_split(o.amount, o.vat_pct)
        route = " — ".join(x for x in (o.point_from, o.point_to) if x)
        out[o.id] = {"doc_no": _order_no(o), "route": route, "vat": vat, "total": total,
                     "state": {"text": ORDER_STATUSES[o.status], "tone": ORDER_TONES[o.status]},
                     "docs": ", ".join(docs.get(o.id, []))}
    return out


def _order_before_void(db, obj):
    docs = _active_docs(db, obj.id)
    if docs:
        raise Invalid({"_": "По заказу есть документы (%s): сначала уберите их в архив" % ", ".join(f"{DOC_TYPES[d.doc_type]} {d.number}" for d in docs)})


def _order_filter(q, state, today):
    return q.where(Order.status.in_({"open": ("new", "confirmed", "in_progress"), "done": ("done",), "cancelled": ("cancelled",)}[state]))


MAX_ORDER = MAX_MONEY

register(R(
    key="orders", title="Заказы", one="заказ", model=Order,
    fields=[
        F("order_date", "Дата заказа", "date", required=True),
        F("customer_id", "Заказчик", "ref", ref="customer", required=True),
        F("status", "Статус", "enum", options=ORDER_STATUSES, default="new", required=True, list=False),
        F("point_from", "Откуда", max=200, list=False), F("point_to", "Куда", max=200, list=False),
        F("cargo", "Груз или работа", max=200),
        F("cargo_kg", "Масса груза, кг", "decimal", places=1, list=False),
        F("distance_km", "Расстояние, км", "int", lo=Decimal(0), list=False),
        F("hours", "Время в работе, ч", "decimal", places=2, list=False),
        F("depart_at", "Выезд", "datetime", list=False), F("deliver_at", "Доставка", "datetime", list=False),
        F("vehicle_id", "Техника", "ref", ref="vehicle", list=False),
        F("driver_id", "Водитель", "ref", ref="employee", list=False),
        F("tariff_id", "Тариф", "ref", ref="tariff", list=False, hint="Сумма посчитается сама: расстояние, часы или масса умножаются на ставку (не меньше минимальной суммы тарифа)"),
        F("qty", "Количество", "decimal", places=3, list=False, hint="Нужно только для тарифов «за рейс» и «за сутки»; по умолчанию 1"),
        F("rate", "Ставка, ₽", "money", list=False, hint="Пусто: ставка тарифа. Впишите свою, если договорились о другой цене"),
        F("amount", "Сумма без НДС, ₽", "money", sum=True, hint="С тарифом считается сама. Без тарифа впишите вручную"),
        F("vat_pct", "НДС", "enum", options=VAT_RATES, default="0", required=True, list=False),
        F("note", "Заметка", "textarea", list=False),
        F("year", "Год", "int", auto=True, list=False), F("number", "Номер", "int", auto=True, list=False),
    ],
    cols=[Col("doc_no", "№", "text", first=True), Col("route", "Маршрут", "text"), Col("state", "Статус", "badge"),
          Col("total", "С НДС, ₽", "money", sum=True), Col("docs", "Документы", "text")],
    search=("cargo", "point_from", "point_to", "note"),
    search_refs=(("customer_id", Counterparty, ("name",)), ("vehicle_id", Vehicle, ("plate", "make", "model"))),
    states={"open": "В работе", "done": "Выполнены", "cancelled": "Отменены"}, state_filter=_order_filter,
    date="order_date", order=("-order_date", "-number"), prepare=_order_prepare, computed=_order_computed, before_void=_order_before_void,
))


DOC_PAID = {"text": "Оплачен", "tone": "success"}


def _doc_calc(d) -> tuple[Decimal, Decimal, Decimal]:
    amount = _money_round(Decimal(d.qty) * d.price)
    vat, total = _vat_split(amount, d.vat_pct)
    return amount, vat, total


def _doc_no(d) -> str:
    return f"{DOC_TYPES[d.doc_type]} № {d.number}/{d.year}"


def _doc_prepare(db, p, v, obj, m):
    e: dict = {}
    order = db.get(Order, m["order_id"]) if m.get("order_id") else None
    cp = m.get("counterparty_id")
    if order is not None:
        if obj is None and order.status == "cancelled":
            e["order_id"] = "Заказ отменён: документы по нему не оформляются"
        if not cp:
            cp = v["counterparty_id"] = m["counterparty_id"] = order.customer_id
        elif cp != order.customer_id:
            e["counterparty_id"] = "Контрагент должен совпадать с заказчиком в заказе"
        same = select(Document.number).where(Document.order_id == order.id, Document.doc_type == m["doc_type"], Document.archived_at.is_(None))
        if obj is not None:
            same = same.where(Document.id != obj.id)
        dup = db.scalar(same.limit(1))
        if dup is not None:
            e["order_id"] = f"По этому заказу уже есть {DOC_TYPES[m['doc_type']].lower()} № {dup}: второй такой же не нужен"
    if not cp:
        e["counterparty_id"] = "Выберите заказчика или заказ"
    if obj is not None and m.get("doc_date") and m["doc_date"].year != obj.year:
        e["doc_date"] = "Дату нельзя перенести в другой год: уберите документ в архив и составьте новый"
    _not_future(m, "paid_date", "Дата оплаты", e)
    v["vat_pct"] = int(m.get("vat_pct") or 0)
    if not (m.get("unit") or "").strip():
        v["unit"] = "усл."
    if e:
        raise Invalid(e)
    if _money_round(Decimal(m.get("qty") or 0) * Decimal(m.get("price") or 0)) > MAX_ORDER:
        raise Invalid({"price": "Слишком большая сумма: проверьте цифры"})
    if obj is None:
        v["year"] = m["doc_date"].year
        v["number"] = next_number(db, m["legal_entity_id"], "doc_" + m["doc_type"], v["year"])


def _doc_computed(db, objs):
    out = {}
    for d in objs:
        amount, vat, total = _doc_calc(d)
        out[d.id] = {"doc_no": _doc_no(d), "amount": amount, "vat": vat, "total": total,
                     "state": DOC_PAID if d.paid_date else {"text": "Не оплачен", "tone": "warning"}}
    return out


def _doc_filter(q, state, today):
    return q.where(Document.paid_date.is_not(None) if state == "paid" else Document.paid_date.is_(None))


register(R(
    key="documents", title="Документы", one="документ", model=Document,
    fields=[
        F("doc_type", "Вид документа", "enum", options=DOC_FULL_NAMES, default="invoice", required=True, locked=True, filter=True),
        F("doc_date", "Дата документа", "date", required=True),
        F("order_id", "По заказу", "ref", ref="order", list=False, filter=True, hint="Заказчик и сумма подставятся из заказа. По одному заказу — по одному документу каждого вида"),
        F("counterparty_id", "Заказчик", "ref", ref="customer", hint="Если выбран заказ — берётся из него"),
        F("subject", "Что оказано (предмет)", "textarea", list=False, wide=True),
        F("qty", "Количество", "decimal", places=3, lo=Decimal("0.001"), default="1", required=True, list=False),
        F("unit", "Единица", max=20, default="усл.", list=False),
        F("price", "Цена за единицу, ₽", "money", required=True, default="0", list=False),
        F("vat_pct", "НДС", "enum", options=VAT_RATES, default="0", required=True, list=False),
        F("paid_date", "Оплачен", "date", hint="Дата оплаты. Пусто, пока деньги не пришли"),
        F("note", "Заметка", "textarea", list=False),
        F("year", "Год", "int", auto=True, list=False), F("number", "Номер", "int", auto=True, list=False),
    ],
    cols=[Col("doc_no", "Документ", "text", first=True), Col("amount", "Без НДС, ₽", "money"), Col("total", "Итого, ₽", "money", sum=True), Col("state", "Оплата", "badge")],
    search=("subject", "note"), search_refs=(("counterparty_id", Counterparty, ("name",)),),
    states={"unpaid": "Не оплачены", "paid": "Оплачены"}, state_filter=_doc_filter,
    date="doc_date", order=("-doc_date", "-number"), prepare=_doc_prepare, computed=_doc_computed,
))



# ================================================================ наряды на день

JOB_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
JOB_TONES = {"planned": "info", "on_site": "warning", "done": "success", "cancelled": "neutral"}


def job_today() -> dt.date:
    return dt.datetime.now(org_tz()).date()


def job_state_badge(j, today: dt.date) -> dict:
    """Не закрытый наряд за прошлые дни выделяется красным: его нужно закрыть или отменить."""
    if j.state in ("planned", "on_site") and j.job_date < today:
        return {"text": "Не закрыт", "tone": "error"}
    return {"text": JOB_STATES[j.state], "tone": JOB_TONES[j.state]}


def job_check_date(d: dt.date, errors: dict, name: str = "job_date") -> None:
    if abs((d - job_today()).days) > JOB_DATE_WINDOW_DAYS:
        errors[name] = "Дата слишком далеко от сегодняшней: проверьте год"


def job_check_vehicle(db, vehicle_id: int, errors: dict, name: str = "vehicle_id") -> None:
    v = db.get(Vehicle, vehicle_id)
    if v is not None and v.status == "written_off":
        errors[name] = "Эта техника списана"


def _job_prepare(db, p, v, obj, m):
    e: dict = {}
    for name in ("customer", "address"):
        if name in v:
            v[name] = " ".join((v[name] or "").split())
    if "task" in v:
        v["task"] = (v["task"] or "").strip()
    if "result_note" in v:
        v["result_note"] = (v["result_note"] or "").strip()
    if not m.get("address") and "address" in v:
        e["address"] = "Напишите адрес или название объекта"
    t = (m.get("time_from") or "").strip()
    if t and not JOB_TIME_RE.match(t):
        e["time_from"] = "Время в формате ЧЧ:ММ, например 08:30"
    if "time_from" in v:
        v["time_from"] = t
    if obj is None or "job_date" in v and v["job_date"] != obj.job_date:
        job_check_date(m["job_date"], e)
    if obj is None or "vehicle_id" in v and v["vehicle_id"] != obj.vehicle_id:
        job_check_vehicle(db, m["vehicle_id"], e)
    if e:
        raise Invalid(e)


def _job_computed(db, objs):
    today = job_today()
    ids = [o.id for o in objs]
    counts: dict[int, dict[str, int]] = {}
    if ids:
        for jid, stage, n in db.execute(select(JobPhoto.job_id, JobPhoto.stage, func.count()).where(
                JobPhoto.job_id.in_(ids), JobPhoto.removed_at.is_(None)).group_by(JobPhoto.job_id, JobPhoto.stage)):
            counts.setdefault(jid, {})[stage] = n
    names = user_names(db, {x for o in objs for x in (o.arrived_by_id, o.finished_by_id)})

    def stamp(at, by):
        if at is None:
            return ""
        return at.astimezone(org_tz()).strftime("%d.%m %H:%M") + (f" · {names.get(by, '')}" if by else "")

    out = {}
    for o in objs:
        c = counts.get(o.id, {})
        out[o.id] = {"status": job_state_badge(o, today), "arrived": stamp(o.arrived_at, o.arrived_by_id),
                     "finished": stamp(o.finished_at, o.finished_by_id),
                     "photos": f"до {c.get('before', 0)} · после {c.get('after', 0)}" if c else ""}
    return out


def _job_filter(q, state, today):
    if state == "open":
        return q.where(Job.state.in_(("planned", "on_site")))
    return q.where(Job.state == state)


register(R(
    key="jobs", title="Наряды на день", one="наряд", model=Job, entity_from="vehicle",
    fields=[
        F("job_date", "Дата", "date", required=True),
        F("time_from", "Время", max=5, hint="Во сколько быть на объекте, например 08:30. Можно не указывать"),
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True),
        F("customer", "Заказчик", max=120, suggest=True, hint="Можно написать своими словами; подсказки берутся из уже введённых"),
        F("address", "Адрес или объект", max=200, required=True),
        F("task", "Что сделать", "textarea", max=1000),
        F("result_note", "Итог работы", "textarea", max=1000, list=False, hint="Водитель пишет, когда отмечает «закончил»"),
        F("state", "Состояние", "enum", options=JOB_STATES, default="planned", auto=True, list=False),
    ],
    cols=[Col("status", "Состояние", "badge"), Col("arrived", "Приехал", "text"), Col("finished", "Закончил", "text"),
          Col("photos", "Фото", "text")],
    search=("customer", "address", "task", "result_note"),
    search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")),),
    states={"open": "Не закончены", "done": "Выполнены", "cancelled": "Отменены"}, state_filter=_job_filter,
    date="job_date", order=("-job_date", "time_from", "id"), prepare=_job_prepare, computed=_job_computed,
    after_save=lambda db, p, job, creating: notify_svc.on_job_saved(db, job, creating, p.id),
))



# ================================================================ записи водителей: рейсы и заправки

MAX_TRIP_KM = 5000          # за один рейс больше — скорее всего опечатка
MAX_CARGO_KG = 200_000


def entry_check_common(db, p, v, obj, m, date_name: str, perm_full: str, e: dict) -> None:
    """Общие правила для рейса и заправки: дата, техника, водитель по умолчанию."""
    if obj is None or date_name in v and v[date_name] != getattr(obj, date_name):
        d = m[date_name]
        today = job_today()
        if d > today + ONE_DAY:
            e[date_name] = "Дата не может быть в будущем"
        else:
            # сотрудник с полным правом может вносить давние записи, водитель — только за последний месяц
            limit = JOB_DATE_WINDOW_DAYS if p.can(perm_full, m.get("legal_entity_id")) else ENTRY_LATE_DAYS
            if (today - d).days > limit:
                e[date_name] = "Слишком давняя дата: проверьте число и год" if limit == JOB_DATE_WINDOW_DAYS else \
                    f"Водитель вносит записи не старше {ENTRY_LATE_DAYS} дней: попросите диспетчера"
    if obj is None or "vehicle_id" in v and v["vehicle_id"] != obj.vehicle_id:
        job_check_vehicle(db, m["vehicle_id"], e)
    if obj is None and not m.get("driver_id") and m.get("vehicle_id"):
        v["driver_id"] = current_driver_id(db, m["vehicle_id"])


def _trip_prepare(db, p, v, obj, m):
    e: dict = {}
    for name in ("route", "customer", "cargo", "doc_number"):
        if name in v:
            v[name] = " ".join((v[name] or "").split())
    if "note" in v:
        v["note"] = (v["note"] or "").strip()
    entry_check_common(db, p, v, obj, m, "trip_date", "trips.create", e)
    if m.get("km") is not None and m["km"] > MAX_TRIP_KM:
        e["km"] = f"Больше {MAX_TRIP_KM} км за рейс: проверьте цифры"
    if m.get("cargo_kg") is not None and m["cargo_kg"] > MAX_CARGO_KG:
        e["cargo_kg"] = "Слишком большой вес: проверьте цифры"
    if e:
        raise Invalid(e)


def photo_counts(db, objs, column) -> dict[int, int]:
    ids = [o.id for o in objs]
    if not ids:
        return {}
    return dict(db.execute(select(column, func.count()).where(column.in_(ids), EntryPhoto.removed_at.is_(None)).group_by(column)).all())


def _trip_computed(db, objs):
    photos = photo_counts(db, objs, EntryPhoto.trip_id)
    return {o.id: {"photos": photos.get(o.id, 0) or ""} for o in objs}


register(R(
    key="trips", title="Рейсы", one="рейс", model=Trip, entity_from="vehicle",
    fields=[
        F("trip_date", "Дата", "date", required=True),
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True),
        F("driver_id", "Водитель", "ref", ref="employee", hint="Если пусто, подставится водитель, закреплённый за машиной"),
        F("route", "Маршрут", max=200, required=True, wide=True, hint="Откуда и куда, например «Склад — карьер Северный»"),
        F("customer", "Заказчик", max=120, suggest=True),
        F("cargo", "Груз", max=200, suggest=True),
        F("cargo_kg", "Вес груза, кг", "decimal", places=1, lo=Decimal(0), list=False),
        F("doc_number", "Номер накладной", max=60),
        F("km", "Пройдено, км", "int", lo=0),
        F("note", "Заметка", "textarea", max=500, list=False),
        F("client_id", "Код отправки", max=64, auto=True, list=False),
    ],
    cols=[Col("photos", "Фото", "text")],
    search=("route", "customer", "cargo", "doc_number", "note"),
    search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")), ("driver_id", Employee, ("last_name", "first_name"))),
    date="trip_date", order=("-trip_date", "-id"), prepare=_trip_prepare, computed=_trip_computed,
))


def _fill_prepare(db, p, v, obj, m):
    e: dict = {}
    if "station" in v:
        v["station"] = " ".join((v["station"] or "").split())
    if "note" in v:
        v["note"] = (v["note"] or "").strip()
    if "amount" in v and v["amount"] is None:
        v["amount"] = Decimal(0)
    entry_check_common(db, p, v, obj, m, "fill_date", "fuel_fills.create", e)
    if m.get("liters") is not None and m["liters"] > FUEL_MAX_LITERS:
        e["liters"] = f"Больше {FUEL_MAX_LITERS} л за раз: проверьте цифры"
    if m.get("odometer") is not None and m["odometer"] > 9_999_999:
        e["odometer"] = "Слишком большой пробег: проверьте цифры"
    if e:
        raise Invalid(e)


def _fill_computed(db, objs):
    photos = photo_counts(db, objs, EntryPhoto.fuel_fill_id)
    vehicles = {o.vehicle_id for o in objs}
    specs = fuel_calc.norms(db, vehicles)
    segs: dict[int, dict] = {}
    for vid in vehicles:
        segs.update(fuel_calc.segments(db, vid))
    out = {}
    for o in objs:
        tank, norm = specs.get(o.vehicle_id, (None, None))
        seg = segs.get(o.id)
        notes = fuel_calc.remarks(o, tank, norm, seg)
        check = {"text": "Проверить", "tone": "error"} if notes else None
        out[o.id] = {
            "per_l": (o.amount / o.liters).quantize(Decimal("0.01")) if o.amount and o.liters else None,
            "consumption": f"{seg['per100']:.1f}".replace(".", ",") if seg else "", "norm": norm,
            "check": check or ({"text": "В норме", "tone": "success"} if seg and norm else {"text": "—", "tone": "neutral"}),
            "check_notes": notes, "photos": photos.get(o.id, 0) or "",
        }
    return out


def _fill_after_save(db, p, obj, creating):
    """Пробег с заправки попадает в показания техники (источник «Заправка»). Не получилось — запись целиком не сохраняется."""
    if not creating or obj.odometer is None:
        return None
    from fastapi import HTTPException
    from app.services.readings import add_reading
    vehicle = db.get(Vehicle, obj.vehicle_id)
    today = job_today()
    at = None if obj.fill_date >= today else dt.datetime.combine(obj.fill_date, dt.time(12, 0), tzinfo=org_tz())
    try:
        reading = add_reading(db, p, vehicle, "km", Decimal(obj.odometer), recorded_at=at, source="fuel", note=f"Заправка {num(obj.liters)} л")
    except HTTPException as ex:
        d = ex.detail
        raise HTTPException(status_code=ex.status_code, detail={**d, "field": "odometer"}) from None
    if reading.review_state == "pending":
        return ["Пробег намного больше прошлого показания: он отправлен механику на проверку"]
    return None


def _fill_warn(db, obj):
    tank, norm = fuel_calc.norms(db, [obj.vehicle_id]).get(obj.vehicle_id, (None, None))
    return fuel_calc.remarks(obj, tank, norm, fuel_calc.segments(db, obj.vehicle_id).get(obj.id))


register(R(
    key="fuel_fills", title="Заправки", one="заправка", model=FuelFill, entity_from="vehicle",
    fields=[
        F("fill_date", "Дата", "date", required=True),
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True),
        F("driver_id", "Водитель", "ref", ref="employee", hint="Если пусто, подставится водитель, закреплённый за машиной"),
        F("liters", "Литров", "decimal", places=2, lo=Decimal("0.01"), required=True),
        F("amount", "Сумма по чеку, ₽", "money", default="0", sum=True),
        F("odometer", "Пробег по спидометру, км", "int", lo=0, hint="По нему считается расход. Можно не указывать"),
        F("full_tank", "Залит полный бак", "bool", default="1", hint="Отметьте, если заправились «до пробки»: тогда считается расход на 100 км"),
        F("station", "Заправка (АЗС)", max=80, suggest=True),
        F("note", "Заметка", "textarea", max=300, list=False),
        F("client_id", "Код отправки", max=64, auto=True, list=False),
    ],
    cols=[Col("per_l", "Цена за литр, ₽", "money"), Col("consumption", "Расход, л/100 км", "text"), Col("check", "Проверка", "badge"),
          Col("photos", "Фото", "text")],
    search=("station", "note"),
    search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")), ("driver_id", Employee, ("last_name", "first_name"))),
    date="fill_date", order=("-fill_date", "-id"), prepare=_fill_prepare, computed=_fill_computed, warn=_fill_warn, after_save=_fill_after_save,
))


# ================================================================ каталог деталей

def _part_prepare(db, p, v, obj, m):
    """Ключ для сравнения названий считает программа: «Фильтр масляный» и «фильтр  масляный!» — одна деталь."""
    key = part_key(m.get("name"))
    if not key:
        raise Invalid({"name": "Впишите название буквами или цифрами"})
    v["name_key"] = key


register(R(
    key="parts", title="Каталог деталей", one="деталь", model=PartCatalog, scope="global",
    fields=[
        F("name", "Название", required=True, max=200, wide=True),
        F("category", "Категория", max=60, filter=True, suggest=True),
        F("article", "Артикул", max=60),
        F("unit", "Единица", "enum", options={u: u for u in PART_UNITS}, default="шт", required=True),
        F("note", "Заметка", max=300, wide=True, list=False),
    ],
    cols=[Col("uses", "Вносили в обращения, раз", "number")],
    computed=lambda db, objs: {o.id: {"uses": o.uses} for o in objs},
    search=("name", "article", "category"), order=("category", "name"), prepare=_part_prepare,
    unique_field="name", unique_text="Такая деталь уже есть в каталоге",
))


# ================================================================ СИЗ: нормы и выдача

def _norm_prepare(db, p, v, obj, m):
    months = m.get("wear_months")
    if months is not None and not 1 <= months <= PPE_WEAR_MAX_MONTHS:
        raise Invalid({"wear_months": f"От 1 до {PPE_WEAR_MAX_MONTHS} месяцев"})
    if "name" in v:
        v["name"] = " ".join((v["name"] or "").split())


def _norm_computed(db, objs):
    ids = [o.id for o in objs]
    used = dict(db.execute(select(PpeIssue.ppe_id, func.count()).where(
        PpeIssue.ppe_id.in_(ids), PpeIssue.archived_at.is_(None), PpeIssue.returned_date.is_(None)).group_by(PpeIssue.ppe_id)).all()) if ids else {}
    return {o.id: {"used": used.get(o.id, 0)} for o in objs}


register(R(
    key="ppe_norms", title="Нормы СИЗ", one="позиция СИЗ", model=PpeNorm, scope="global",
    fields=[
        F("name", "Что выдаём", required=True, max=160, wide=True, hint="Например: «Куртка зимняя», «Перчатки рабочие», «Каска»"),
        F("wear_months", "Хватает на, месяцев", "int", lo=Decimal(1), hint="Через сколько заменить. Пусто — без срока: напоминаний о замене не будет"),
        F("note", "Заметка", max=300, wide=True, list=False),
    ],
    cols=[Col("used", "Сейчас на руках", "number")], computed=_norm_computed,
    search=("name", "note"), order=("name",), prepare=_norm_prepare,
    unique_field="name", unique_text="Такая позиция уже есть в нормах",
))


def ppe_auto_due(db, ppe_id, issue_date):
    norm = db.get(PpeNorm, ppe_id) if ppe_id else None
    return ppe_calc.due_for(issue_date, norm.wear_months if norm else None)


def _ppe_prepare(db, p, v, obj, m):
    e: dict = {}
    _not_future(m, "issue_date", "Дата выдачи", e)
    _not_future(m, "returned_date", "Дата возврата", e)
    issue, returned = m.get("issue_date"), m.get("returned_date")
    if issue and returned and returned < issue:
        e["returned_date"] = "Возврат не может быть раньше выдачи"
    if "size" in v:
        v["size"] = (v["size"] or "").strip()
    sent = v.get("due_date", obj.due_date if obj is not None else None)
    if sent is not None and issue and sent < issue:
        e["due_date"] = "Срок замены не может быть раньше выдачи"
    if e:
        raise Invalid(e)
    # срок замены: пусто — считаем сами (дата выдачи + срок из норм); если срок был посчитан сами, а выдачу или позицию поменяли, пересчитываем
    auto = ppe_auto_due(db, m.get("ppe_id"), issue)
    if sent is None:
        v["due_date"] = auto
    elif obj is not None and sent == obj.due_date and obj.due_date == ppe_auto_due(db, obj.ppe_id, obj.issue_date) and (
            ("issue_date" in v and v["issue_date"] != obj.issue_date) or ("ppe_id" in v and v["ppe_id"] != obj.ppe_id)):
        v["due_date"] = auto if auto is not None else sent


def _ppe_computed(db, objs):
    today = job_today()
    out = {}
    for o in objs:
        st = ppe_calc.state_of(o, today)
        out[o.id] = {"state": {"text": st["text"], "tone": st["tone"]}, "left": st["left_text"], "level": st["level"]}
    return out


def _ppe_filter(q, state, today):
    live = PpeIssue.returned_date.is_(None)
    if state == "active":
        return q.where(live)
    if state == "attention":
        return q.where(live, PpeIssue.due_date.is_not(None), PpeIssue.due_date <= today + dt.timedelta(days=PPE_SOON_DAYS))
    return q.where(PpeIssue.returned_date.is_not(None))


register(R(
    key="ppe", title="Выдача СИЗ", one="выдача СИЗ", model=PpeIssue,
    fields=[
        F("employee_id", "Сотрудник", "ref", ref="employee", required=True, filter=True),
        F("ppe_id", "Что выдано", "ref", ref="ppe", required=True, filter=True),
        F("issue_date", "Дата выдачи", "date", required=True),
        F("qty", "Количество", "int", lo=Decimal(1), default="1", required=True),
        F("size", "Размер", max=20, list=False),
        F("due_date", "Заменить до", "date", hint="Пусто — посчитаем сами: дата выдачи плюс срок из норм"),
        F("returned_date", "Вернул", "date", hint="Заполните, когда человек сдал или заменил вещь"),
        F("note", "Заметка", max=300, wide=True, list=False),
    ],
    cols=[Col("state", "Состояние", "badge"), Col("left", "До замены", "text")],
    search=("size", "note"), search_refs=(("employee_id", Employee, ("last_name", "first_name")), ("ppe_id", PpeNorm, ("name",))),
    states={"active": "На руках", "attention": "Пора менять", "returned": "Возвращено"}, state_filter=_ppe_filter,
    date="issue_date", order=("-issue_date",), prepare=_ppe_prepare, computed=_ppe_computed,
))


# ================================================================ события сотрудников и свои события календаря (этап 3б)

def _event_state(ev, today: dt.date) -> dict:
    if not ev.controlled or ev.due_date is None:
        return {"state": {"text": "Без срока" if ev.due_date is None else "Не контролируется", "tone": "neutral"}, "left": "—"}
    level = rem.level_of(ev.due_date, today, REMINDER_LEAD_DAYS)
    left = (ev.due_date - today).days
    text = {"due": "Срок наступил" if left >= 0 else "Просрочено", "soon": "Скоро срок", "ok": "Всё в порядке"}[level]
    return {"state": {"text": text, "tone": rem.tone_of(level)}, "left": rem.left_text(left)}


def _event_computed(db, objs):
    today = job_today()
    return {o.id: _event_state(o, today) for o in objs}


def _event_prepare(db, p, v, obj, m):
    e: dict = {}
    _not_future(m, "event_date", "Дата события", e)
    ed, dd = m.get("event_date"), m.get("due_date")
    if not ed and not dd:
        e["event_date"] = "Укажите дату события или следующий срок"
    if ed and dd and dd < ed:
        e["due_date"] = "Следующий срок не может быть раньше события"
    amount = m.get("amount")
    if amount is None:
        v["amount"] = Decimal(0)
    elif amount > 0 and m.get("kind") not in EMPLOYEE_EVENT_MONEY_KINDS:
        e["amount"] = "Сумма бывает только у премии и штрафа"
    if "description" in v:
        v["description"] = (v["description"] or "").strip()
    if e:
        raise Invalid(e)


def _event_filter(q, state, today):
    if state == "attention":
        return q.where(EmployeeEvent.controlled.is_(True), EmployeeEvent.due_date.is_not(None),
                       EmployeeEvent.due_date <= today + dt.timedelta(days=REMINDER_LEAD_DAYS))
    return q.where(EmployeeEvent.controlled.is_(True))


register(R(
    key="employee_events", title="События сотрудников", one="событие сотрудника", model=EmployeeEvent,
    fields=[
        F("employee_id", "Сотрудник", "ref", ref="employee", required=True, filter=True),
        F("kind", "Вид события", "enum", options=EMPLOYEE_EVENT_KINDS, default="medical", required=True, filter=True),
        F("event_date", "Дата события", "date"),
        F("due_date", "Следующий срок", "date", hint="Например, когда снова проходить медосмотр. За 30 дней до срока появится напоминание"),
        F("amount", "Сумма, ₽", "money", default="0", hint="Только для премии и штрафа", sum=True),
        F("description", "Описание", max=300, wide=True),
        F("controlled", "Следить за сроком", "bool", default="1", hint="Снимите, когда срок больше не нужен: напоминания прекратятся", list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("state", "Срок", "badge"), Col("left", "Осталось", "text")],
    search=("description", "note"), search_refs=(("employee_id", Employee, ("last_name", "first_name")),),
    states={"attention": "Скоро или просрочено", "controlled": "Под контролем"}, state_filter=_event_filter,
    date="event_date", order=("-event_date", "-id"), prepare=_event_prepare, computed=_event_computed,
))


def _calendar_prepare(db, p, v, obj, m):
    e: dict = {}
    start, end = m.get("start_at"), m.get("end_at")
    tz = org_tz()
    if "title" in v:
        v["title"] = (v["title"] or "").strip()
        if not v["title"]:
            e["title"] = "Введите название"
    if start and end and end < start:
        e["end_at"] = "Окончание раньше начала"
    remind = m.get("remind_min")
    if remind is not None and remind > CALENDAR_REMIND_MAX:
        e["remind_min"] = "Напомнить можно не раньше чем за 30 суток (43 200 минут)"
    if e:
        raise Invalid(e)
    if m.get("all_day") and start:
        # событие на весь день: начало — полночь по часам организации, окончание (если есть) — полночь последнего дня
        first = start.astimezone(tz).date()
        v["start_at"] = dt.datetime.combine(first, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
        if end:
            last = end.astimezone(tz).date()
            if last < first:
                raise Invalid({"end_at": "Окончание раньше начала"})
            v["end_at"] = dt.datetime.combine(last, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)


register(R(
    key="calendar_events", title="Свои события", one="событие", model=CalendarEvent,
    fields=[
        F("title", "Что", required=True, max=160, wide=True),
        F("start_at", "Начало", "datetime", required=True),
        F("end_at", "Окончание", "datetime"),
        F("all_day", "Весь день", "bool", hint="Время начала тогда не важно"),
        F("color", "Цвет", "enum", options=CALENDAR_COLORS, default="teal", required=True, list=False),
        F("vehicle_id", "Техника", "ref", ref="vehicle", list=False),
        F("employee_id", "Сотрудник", "ref", ref="employee", list=False),
        F("remind_min", "Напомнить за, минут", "int", lo=Decimal(0), list=False,
          hint="0 — в момент начала, 60 — за час, 1440 — за сутки. Пусто — не напоминать"),
        F("done", "Выполнено", "bool", filter=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    search=("title", "note"), states={"open": "Не выполнены", "done": "Выполнены"},
    state_filter=lambda q, state, today: q.where(CalendarEvent.done.is_(state == "done")),
    order=("-start_at", "-id"), prepare=_calendar_prepare,
))


# ================================================================ склад: склады и товары (этап 4-1)

def _wh_computed(db, objs):
    bal = stock_svc.balances(db)
    count: dict[int, int] = {}
    for (w, _i), q in bal.items():
        if q > 0:
            count[w] = count.get(w, 0) + 1
    return {o.id: {"positions": count.get(o.id, 0)} for o in objs}


def _wh_void(db, obj):
    left = [(w, i) for (w, i), q in stock_svc.balances(db, warehouse_id=obj.id).items() if q != 0]
    if left:
        raise Invalid({"_": "На складе есть остатки: сначала спишите их или переместите на другой склад"})


register(R(
    key="warehouses", title="Склады", one="склад", model=Warehouse,
    fields=[
        F("name", "Название", required=True, max=120, hint="Например: Главный склад, Гараж, Контейнер"),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("positions", "Позиций с остатком", "number")],
    computed=_wh_computed, search=("name",), order=("name",), before_void=_wh_void,
    unique_field="name", unique_text="Склад с таким названием в этом юрлице уже есть",
))


def _item_prepare(db, p, v, obj, m):
    """Ключ для сравнения названий считает программа: «Фильтр масляный» и «фильтр  масляный!» — один товар."""
    name = " ".join((m.get("name") or "").split())
    key = part_key(name)
    if not key:
        raise Invalid({"name": "Впишите название буквами или цифрами"})
    v["name"], v["name_key"] = name, key


def _item_computed(db, objs):
    ids = [o.id for o in objs]
    total = stock_svc.totals(db, ids)
    out = {}
    for o in objs:
        t = total.get(o.id, Decimal(0))
        low = o.min_qty > 0 and t < o.min_qty and not o.archived_at
        out[o.id] = {"total": float(t), "state": {"text": "Заканчивается", "tone": "warning"} if low else
                     ({"text": "В наличии", "tone": "success"} if t > 0 else {"text": "Нет на складе", "tone": "neutral"})}
    return out


def _item_void(db, obj):
    if stock_svc.totals(db, [obj.id]).get(obj.id, Decimal(0)) != 0:
        raise Invalid({"_": "Этот товар ещё есть на складе: сначала спишите остаток"})


register(R(
    key="stock_items", title="Товары на складе", one="товар", model=StockItem,
    fields=[
        F("name", "Наименование", required=True, max=200, wide=True, hint="Например: Масло моторное 5W-40"),
        F("sku", "Артикул", max=60),
        F("unit", "Единица", "enum", options={u: u for u in STOCK_UNITS}, default="шт", required=True),
        F("min_qty", "Минимальный остаток", "decimal", places=3, lo=Decimal(0), default=Decimal(0),
          hint="Если общий остаток станет меньше, товар попадёт в список «Заканчивается»"),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("total", "Остаток", "number"), Col("state", "Состояние", "badge")],
    computed=_item_computed, search=("name", "sku"), order=("name",), prepare=_item_prepare, before_void=_item_void,
    unique_field="name", unique_text="Такой товар в этом юрлице уже есть",
))


# ================================================================ вкладки техники: шины, ремонты, финансы, ТО, контроль

def _vehicle_now(db, vehicle_ids):
    """Текущий пробег и моточасы машин одним запросом: {id: (км, часы)}."""
    if not vehicle_ids:
        return {}
    return {i: (km, h) for i, km, h in db.execute(select(Vehicle.id, Vehicle.current_km, Vehicle.current_hours).where(Vehicle.id.in_(list(vehicle_ids))))}


def _tire_computed(db, objs):
    now = _vehicle_now(db, {o.vehicle_id for o in objs})
    out = {}
    for t in objs:
        cur = now.get(t.vehicle_id, (None, None))[0]
        end = t.remove_km if t.remove_km is not None else (int(cur) if cur is not None and t.remove_date is None else None)
        run = end - t.install_km if end is not None and t.install_km is not None and end >= t.install_km else None
        out[t.id] = {"run_km": run, "state": {"text": "Снята", "tone": "neutral"} if t.remove_date else {"text": "На машине", "tone": "success"}}
    return out


def _tire_prepare(db, p, v, obj, m):
    e: dict = {}
    if m.get("install_km") is not None and m.get("remove_km") is not None and m["remove_km"] < m["install_km"]:
        e["remove_km"] = "Пробег при снятии не может быть меньше пробега при установке"
    for name, label in (("install_date", "Дата установки"), ("remove_date", "Дата снятия")):
        _not_future(m, name, label, e)
    if e:
        raise Invalid(e)


register(R(
    key="tires", title="Шины", one="шину", model=VehicleTire, entity_from="vehicle",
    fields=[
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True, filter=True),
        F("brand", "Марка", max=80, suggest=True),
        F("model", "Модель", max=80),
        F("size", "Размер", max=40, hint="Например: 315/80 R22.5", suggest=True),
        F("season", "Сезон", "enum", options=TIRE_SEASONS, default="all", required=True, filter=True),
        F("position", "Где стоит", max=60, hint="Например: передняя ось, левая", suggest=True),
        F("serial", "Серийный номер", max=60, list=False),
        F("install_date", "Установлена", "date"),
        F("install_km", "Пробег при установке, км", "int", lo=Decimal(0), list=False),
        F("remove_date", "Снята", "date", hint="Пусто, пока стоит на машине"),
        F("remove_km", "Пробег при снятии, км", "int", lo=Decimal(0), list=False),
        F("price", "Цена, ₽", "money", default="0", list=False),
        F("wear_pct", "Износ, %", "int", lo=Decimal(0), hint="От 0 до 100", list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("run_km", "Пробег на шине, км", "number"), Col("state", "Состояние", "badge")],
    search=("brand", "model", "size", "serial", "position", "note"), search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")),),
    date="install_date", order=("-install_date", "-id"), prepare=_tire_prepare, computed=_tire_computed,
    states={"mounted": "На машине", "removed": "Сняты"},
    state_filter=lambda q, state, today: q.where(VehicleTire.remove_date.is_(None) if state == "mounted" else VehicleTire.remove_date.is_not(None)),
))


register(R(
    key="vehicle_repairs", title="История ремонтов", one="запись о ремонте", model=VehicleRepair, entity_from="vehicle",
    fields=[
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True, filter=True),
        F("repair_date", "Дата", "date", required=True),
        F("kind", "Что это", "enum", options=VEHICLE_REPAIR_KINDS, default="repair", required=True, filter=True),
        F("title", "Название", required=True, max=200, wide=True, hint="Что сделали или какую деталь поставили"),
        F("part_number", "Артикул", max=60, list=False),
        F("qty", "Количество", "decimal", default="1", lo=Decimal("0.001"), places=3, list=False),
        F("cost", "Стоимость, ₽", "money", default="0", sum=True),
        F("odometer", "Пробег, км", "int", lo=Decimal(0), list=False),
        F("executor", "Кто делал", max=120, suggest=True, list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    search=("title", "part_number", "executor", "note"), search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")),),
    date="repair_date", order=("-repair_date", "-id"), prepare=_dated_prepare(("repair_date", "Дата")),
))


def _finance_computed(db, objs):
    return {o.id: {"balance": float(o.amount if o.kind == "income" else -o.amount)} for o in objs}


register(R(
    key="vehicle_finance", title="Финансы по технике", one="операцию", model=VehicleFinance, entity_from="vehicle",
    fields=[
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True, filter=True),
        F("op_date", "Дата", "date", required=True),
        F("kind", "Вид", "enum", options=VEHICLE_FINANCE_KINDS, default="expense", required=True, filter=True),
        F("category", "Статья", max=80, suggest=True, hint="Например: страховка, мойка, штраф-стоянка"),
        F("amount", "Сумма, ₽", "money", required=True, lo=Decimal("0.01")),
        F("description", "Описание", max=300, wide=True),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("balance", "Доход минус расход, ₽", "money", sum=True)],
    search=("category", "description", "note"), search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")),),
    date="op_date", order=("-op_date", "-id"), prepare=_dated_prepare(("op_date", "Дата")), computed=_finance_computed,
))


def _control_computed(closed_of):
    def computed(db, objs):
        today = _today()
        now = _vehicle_now(db, {o.vehicle_id for o in objs})
        out = {}
        for o in objs:
            km, hours = now.get(o.vehicle_id, (None, None))
            ev = control_svc.evaluate(due_date=o.due_date, due_km=o.due_km, due_hours=getattr(o, "due_hours", None), controlled=o.controlled,
                                      closed=closed_of(o), today=today, cur_km=km, cur_hours=hours)
            out[o.id] = {"state": control_svc.badge(ev), "left": control_svc.left_text(ev["left"]) if ev["level"] != "closed" else ""}
        return out
    return computed


def _control_prepare(*, hours: bool):
    def prepare(db, p, v, obj, m):
        e: dict = {}
        if m.get("due_km") is None and m.get("due_date") is None and (not hours or m.get("due_hours") is None):
            e["due_date"] = "Укажите срок: дату" + (", пробег или моточасы" if hours else " или пробег")
        if e:
            raise Invalid(e)
    return prepare


register(R(
    key="maintenance", title="Техобслуживание", one="запись о ТО", model=MaintenanceItem, entity_from="vehicle",
    fields=[
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True, filter=True),
        F("kind", "Вид", "enum", options=MAINTENANCE_KINDS, default="to1", required=True, filter=True),
        F("work", "Что сделать", max=300, wide=True),
        F("due_date", "Срок по дате", "date"),
        F("due_km", "Срок по пробегу, км", "int", lo=Decimal(0)),
        F("interval_km", "Периодичность, км", "int", lo=Decimal(1), hint="Через сколько км повторять; по кнопке «Выполнено» создаётся следующая запись", list=False),
        F("done_at", "Выполнено", "date"),
        F("controlled", "На контроле", "bool", default="1", hint="Снимите, когда напоминать больше не нужно", list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("left", "Осталось"), Col("state", "Состояние", "badge")],
    search=("work", "note"), search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")),),
    order=("-id",), prepare=_control_prepare(hours=False), computed=_control_computed(lambda o: o.done_at is not None),
    states={"open": "На контроле", "closed": "Выполнены или сняты"},
    state_filter=lambda q, state, today: q.where(and_(MaintenanceItem.controlled.is_(True), MaintenanceItem.done_at.is_(None)) if state == "open"
                                                    else (MaintenanceItem.controlled.is_(False) | MaintenanceItem.done_at.is_not(None))),
))

register(R(
    key="vehicle_events", title="Контроль мероприятий", one="мероприятие", model=ControlEvent, entity_from="vehicle",
    fields=[
        F("vehicle_id", "Техника", "ref", ref="vehicle", required=True, filter=True),
        F("event_type", "Тип", required=True, max=80, default="Другое", suggest=True, hint="Например: тахограф, поверка, замена ремня"),
        F("title", "Что выполнить", required=True, max=200, wide=True),
        F("unit", "Агрегат", max=80, list=False),
        F("due_date", "Срок по дате", "date"),
        F("due_km", "Срок по пробегу, км", "int", lo=Decimal(0)),
        F("due_hours", "Срок по моточасам", "decimal", lo=Decimal(0), places=1),
        F("interval_km", "Периодичность, км", "int", lo=Decimal(1), list=False),
        F("controlled", "На контроле", "bool", default="1", list=False),
        F("note", "Заметка", "textarea", list=False),
    ],
    cols=[Col("left", "Осталось"), Col("state", "Состояние", "badge")],
    search=("event_type", "title", "unit", "note"), search_refs=(("vehicle_id", Vehicle, ("plate", "make", "model")),),
    order=("-id",), prepare=_control_prepare(hours=True), computed=_control_computed(lambda o: False),
    states={"open": "На контроле", "closed": "Сняты с контроля"},
    state_filter=lambda q, state, today: q.where(ControlEvent.controlled.is_(state == "open")),
))
