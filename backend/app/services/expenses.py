# -*- coding: utf-8 -*-
"""Расходы компании одним списком за месяц. Все затраты по машинам (топливо, ремонт по счетам и обращениям, штрафы, страховка, прочие затраты)
идут одной строкой «Затраты на технику»: подробно они на странице «Затраты на спецтехнику», здесь не повторяются. Отдельно — счета без машины
и закупки на склад, общие расходы и аренда, которую берём. Эти же суммы идут на главную (плитка и график «Расходы»), поэтому цифры
в разделе «Расходы» и на главной всегда совпадают.

Детали со склада в строку техники не входят: компания платит за них один раз, счётом закупки на склад.

Что не считается в итог (показывается справочно): записи о ремонте техники со стоимостью. Ремонт обычно уже внесён счётом, и сумма
сложилась бы дважды."""
import datetime as dt
from decimal import Decimal

from sqlalchemy import exists, func, select

from app.enums import INSURANCE_DOC_KINDS
from app.models import (DossierDoc, FuelFill, Fine, MiscExpense, Rental, RepairBill, ServiceRequest, StockMove, Vehicle, VehicleFinance, VehicleRepair)
from app.registry import _vehicle_label
from app.services import reports as rep
from app.services import timesheet as ts
from app.services.reminders import local_day

# (код, название, считается ли в итог, пояснение)
CATS: tuple[tuple[str, str, bool, str], ...] = (
    ("vehicles", "Затраты на технику", True, "Топливо, ремонт по счетам и обращениям, штрафы, страховка и прочие затраты по машинам. "
                                             "Подробно по каждой машине — на странице «Затраты на спецтехнику». Детали со склада входят в закупки на склад."),
    ("bills_other", "Счета без машины и закупки на склад", True, "Счета, не привязанные к машине, и счета, по которым товар пришёл на склад. "
                                                               "По дате счёта: и оплаченные, и нет."),
    ("misc", "Общие расходы", True, "Стоянка, мойка, связь и другие траты компании."),
    ("rent_in", "Аренда техники у других", True, "По суткам внутри месяца."),
    ("repairs", "Записи о ремонте техники (справочно)", False, "В итог не входят: ремонт обычно уже внесён счётом."),
)
# из чего складывается строка «Затраты на технику» (те же суммы по отдельности нужны графику на главной)
VEHICLE_PARTS = ("fuel", "bills_vehicle", "requests", "fines", "insurance", "vehicle_finance")
CAT_LABELS = {k: label for k, label, _, _ in CATS}
COUNTED = tuple(k for k, _, counted, _ in CATS if counted)
ITEMS_LIMIT = 400
MONTH_NAMES = ts.MONTHS


def _total(c: rep.Ctx, col, *where) -> float:
    return float(c.db.scalar(select(func.coalesce(func.sum(col), 0)).where(*where)) or 0)


def _rentals(c: rep.Ctx, a: dt.date, b: dt.date):
    """Аренда, которую берём у других, за месяц: [(аренда, число суток внутри месяца, сумма)]."""
    sc = c.money
    out = []
    for r in c.db.scalars(select(Rental).where(Rental.direction != "out", Rental.archived_at.is_(None), sc.cond(Rental.legal_entity_id),
                                               Rental.start_date <= b, (Rental.end_date.is_(None)) | (Rental.end_date >= a)).order_by(Rental.id)):
        lo, hi = max(r.start_date, a), min(r.end_date or c.today, b)
        days = (hi - lo).days + 1
        if days > 0:
            out.append((r, days, float(r.rate or 0) * days))
    return out


def _to_stock():
    """Счёт, по которому товар пришёл на склад."""
    return exists().where(StockMove.bill_id == RepairBill.id, StockMove.kind == "receipt", StockMove.archived_at.is_(None))


def _vehicle_bill():
    """Счёт на ремонт машины: с машиной и не закупка на склад."""
    return RepairBill.vehicle_id.is_not(None) & ~_to_stock()


def _ins_day():
    return func.coalesce(DossierDoc.issued_on, func.date(DossierDoc.created_at))


def _ins_where(sc, a: dt.date, b: dt.date) -> tuple:
    day = _ins_day()
    return (DossierDoc.kind.in_(INSURANCE_DOC_KINDS), DossierDoc.cost.is_not(None), DossierDoc.archived_at.is_(None), sc.cond(Vehicle.legal_entity_id),
            day >= a, day <= b)


def sums(c: rep.Ctx, a: dt.date, b: dt.date) -> dict[str, float]:
    """Сумма по каждой статье за период [a, b] и по частям строки «Затраты на технику».
    Только то, что человек может видеть по праву «деньги в отчётах» и выбранному юрлицу."""
    sc = c.money
    if not sc:
        return {k: 0.0 for k in (*CAT_LABELS, *VEHICLE_PARTS, "bills")}
    has_bill = exists().where(RepairBill.request_id == ServiceRequest.id, RepairBill.archived_at.is_(None))
    bill_w = (RepairBill.archived_at.is_(None), sc.cond(RepairBill.legal_entity_id), RepairBill.bill_date >= a, RepairBill.bill_date <= b)
    s = {
        "bills": _total(c, RepairBill.total, *bill_w),
        "bills_vehicle": _total(c, RepairBill.total, *bill_w, _vehicle_bill()),
        "bills_other": _total(c, RepairBill.total, *bill_w, ~_vehicle_bill()),
        "insurance": float(c.db.scalar(select(func.coalesce(func.sum(DossierDoc.cost), 0)).join(Vehicle, Vehicle.id == DossierDoc.vehicle_id)
                                       .where(*_ins_where(sc, a, b))) or 0),
        "misc": _total(c, MiscExpense.amount, MiscExpense.archived_at.is_(None), sc.cond(MiscExpense.legal_entity_id), MiscExpense.op_date >= a, MiscExpense.op_date <= b),
        "fuel": _total(c, FuelFill.amount, FuelFill.archived_at.is_(None), sc.cond(FuelFill.legal_entity_id), FuelFill.fill_date >= a, FuelFill.fill_date <= b),
        "requests": _total(c, ServiceRequest.cost_actual, ServiceRequest.status == "closed", ServiceRequest.cost_actual.is_not(None), ~has_bill,
                           sc.cond(ServiceRequest.legal_entity_id), ServiceRequest.closed_at >= rep.day_start(a), ServiceRequest.closed_at < rep.day_start(b + dt.timedelta(days=1))),
        "vehicle_finance": _total(c, VehicleFinance.amount, VehicleFinance.kind == "expense", VehicleFinance.archived_at.is_(None),
                                  sc.cond(VehicleFinance.legal_entity_id), VehicleFinance.op_date >= a, VehicleFinance.op_date <= b),
        "fines": _total(c, Fine.amount, Fine.archived_at.is_(None), Fine.payer == "company", sc.cond(Fine.legal_entity_id), Fine.fine_date >= a, Fine.fine_date <= b),
        "rent_in": sum(x[2] for x in _rentals(c, a, b)),
        "repairs": _total(c, VehicleRepair.cost, VehicleRepair.archived_at.is_(None), sc.cond(VehicleRepair.legal_entity_id),
                          VehicleRepair.repair_date >= a, VehicleRepair.repair_date <= b),
    }
    s["vehicles"] = sum(s[k] for k in VEHICLE_PARTS)
    return {k: round(v, 2) for k, v in s.items()}


def counted_total(s: dict[str, float]) -> float:
    return round(sum(s.get(k, 0.0) for k in COUNTED), 2)


def _items(c: rep.Ctx, a: dt.date, b: dt.date) -> list[dict]:
    """Все строки за месяц, свежие сверху. Каждая: дата, статья, что это, машина, сумма и, если можно открыть, ссылка."""
    sc, db, p = c.money, c.db, c.p
    rows: list[dict] = []
    veh_ids: set[int] = set()

    def add(cat: str, day: dt.date, title: str, sub: str, amount, *, vehicle_id: int | None = None, status: str = "", link: str = "", rid: int = 0):
        if vehicle_id:
            veh_ids.add(vehicle_id)
        rows.append({"cat": cat, "cat_label": CAT_LABELS[cat], "counted": cat in COUNTED, "date": day.isoformat(), "title": title, "sub": sub,
                     "amount": round(float(amount or 0), 2), "vehicle_id": vehicle_id, "status": status, "link": link, "id": rid, "_k": (day, rid)})

    link_ok = p.can("bills.view")
    for x in db.scalars(select(RepairBill).where(RepairBill.archived_at.is_(None), ~_vehicle_bill(), sc.cond(RepairBill.legal_entity_id),
                                                 RepairBill.bill_date >= a, RepairBill.bill_date <= b)):
        add("bills_other", x.bill_date, f"Счёт № {x.number}", x.supplier_name or x.basis, x.total, vehicle_id=x.vehicle_id, status=x.status,
            link=f"/bills/{x.id}" if link_ok else "", rid=x.id)
    for x in db.scalars(select(MiscExpense).where(MiscExpense.archived_at.is_(None), sc.cond(MiscExpense.legal_entity_id), MiscExpense.op_date >= a, MiscExpense.op_date <= b)):
        add("misc", x.op_date, x.category or "Расход", x.description, x.amount, rid=x.id)
    for r, days, amount in _rentals(c, a, b):
        add("rent_in", max(r.start_date, a), "Аренда техники", f"{days} сут. по {rep_num(r.rate)} ₽", amount, vehicle_id=r.vehicle_id, rid=r.id)
    for x in db.scalars(select(VehicleRepair).where(VehicleRepair.archived_at.is_(None), VehicleRepair.cost > 0, sc.cond(VehicleRepair.legal_entity_id),
                                                    VehicleRepair.repair_date >= a, VehicleRepair.repair_date <= b)):
        add("repairs", x.repair_date, x.title, x.executor, x.cost, vehicle_id=x.vehicle_id, rid=x.id)
    names = {v.id: _vehicle_label(v) for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(veh_ids)))} if veh_ids else {}
    for r in rows:
        r["vehicle"] = names.get(r["vehicle_id"], "")
    rows.sort(key=lambda r: r.pop("_k"), reverse=True)
    return rows


def _plural(n: int) -> str:
    return "запись" if n % 10 == 1 and n % 100 != 11 else "записи" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "записей"


def _vehicle_count(c: rep.Ctx, a: dt.date, b: dt.date) -> int:
    """Сколько записей вошло в строку «Затраты на технику»."""
    sc, db = c.money, c.db
    has_bill = exists().where(RepairBill.request_id == ServiceRequest.id, RepairBill.archived_at.is_(None))

    def cnt(model, *where, join=None) -> int:
        q = select(func.count()).select_from(model)
        if join is not None:
            q = q.join(*join)
        return int(db.scalar(q.where(*where)) or 0)

    return (cnt(RepairBill, RepairBill.archived_at.is_(None), _vehicle_bill(), sc.cond(RepairBill.legal_entity_id), RepairBill.bill_date >= a, RepairBill.bill_date <= b)
            + cnt(FuelFill, FuelFill.archived_at.is_(None), sc.cond(FuelFill.legal_entity_id), FuelFill.fill_date >= a, FuelFill.fill_date <= b, FuelFill.amount > 0)
            + cnt(ServiceRequest, ServiceRequest.status == "closed", ServiceRequest.cost_actual.is_not(None), ~has_bill, sc.cond(ServiceRequest.legal_entity_id),
                  ServiceRequest.closed_at >= rep.day_start(a), ServiceRequest.closed_at < rep.day_start(b + dt.timedelta(days=1)))
            + cnt(VehicleFinance, VehicleFinance.kind == "expense", VehicleFinance.archived_at.is_(None), sc.cond(VehicleFinance.legal_entity_id),
                  VehicleFinance.op_date >= a, VehicleFinance.op_date <= b)
            + cnt(Fine, Fine.archived_at.is_(None), Fine.payer == "company", sc.cond(Fine.legal_entity_id), Fine.fine_date >= a, Fine.fine_date <= b)
            + cnt(DossierDoc, *_ins_where(sc, a, b), join=(Vehicle, Vehicle.id == DossierDoc.vehicle_id)))


def _vehicles_row(c: rep.Ctx, a: dt.date, b: dt.date, amount: float, n: int) -> dict:
    """Одна строка за все затраты на технику: подробности на странице «Затраты на спецтехнику»."""
    return {"cat": "vehicles", "cat_label": CAT_LABELS["vehicles"], "counted": True, "date": min(b, max(a, c.today)).isoformat(),
            "title": "Затраты на технику за месяц", "sub": f"{n} {_plural(n)}: топливо, ремонт, штрафы, страховка и прочее. Подробно — в «Затратах на спецтехнику»",
            "amount": amount, "vehicle_id": None, "vehicle": "", "status": "",
            "link": f"/vehicle-finance?month={a.strftime('%Y-%m')}" if c.p.can("vehicle_finance.view") else "", "id": 0}


def rep_num(v) -> str:
    x = float(v or 0)
    return (f"{x:,.2f}".rstrip("0").rstrip(".") if x != int(x) else f"{x:,.0f}").replace(",", " ").replace(".", ",")


def overview(c: rep.Ctx, first: dt.date, only: str = "") -> dict:
    """Месяц целиком: итог, разбивка по статьям, сравнение с прошлым месяцем и строки (первые ITEMS_LIMIT по свежести). only отбирает одну статью."""
    a, b = rep.month_bounds(first.year, first.month)
    prev_a, prev_b = rep.month_bounds(*((first.year, first.month - 1) if first.month > 1 else (first.year - 1, 12)))
    s = sums(c, a, b)
    prev = counted_total(sums(c, prev_a, prev_b))
    total = counted_total(s)
    everything = _items(c, a, b)
    counts: dict[str, int] = {}
    for r in everything:
        counts[r["cat"]] = counts.get(r["cat"], 0) + 1
    if s["vehicles"]:
        counts["vehicles"] = _vehicle_count(c, a, b)
        everything.insert(0, _vehicles_row(c, a, b, s["vehicles"], counts["vehicles"]))
    unpaid = 0.0
    if c.money:                                                                              # все счета месяца, ждущие оплаты, и с машиной, и без
        unpaid = round(float(c.db.scalar(select(func.coalesce(func.sum(RepairBill.total), 0)).where(
            RepairBill.archived_at.is_(None), RepairBill.status == "unpaid", c.money.cond(RepairBill.legal_entity_id),
            RepairBill.bill_date >= a, RepairBill.bill_date <= b)) or 0), 2)
    items = [r for r in everything if not only or r["cat"] == only]
    return {
        "month": first.strftime("%Y-%m"), "title": f"{MONTH_NAMES[first.month - 1]} {first.year}", "total": total, "prev_total": prev,
        "delta_pct": round((total - prev) / prev * 100) if prev > 0 else None, "bills_unpaid": unpaid,
        "categories": [{"key": k, "label": label, "hint": hint, "counted": counted, "amount": s[k], "count": counts.get(k, 0)} for k, label, counted, hint in CATS],
        "items": items[:ITEMS_LIMIT], "shown": min(len(items), ITEMS_LIMIT), "found": len(items),
    }
