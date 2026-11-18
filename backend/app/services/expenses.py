# -*- coding: utf-8 -*-
"""Расходы компании одним списком за месяц: счета на ремонт и запчасти (в том числе без привязки к машине), общие расходы, затраты по технике,
топливо, штрафы, которые платит компания, и аренда, которую берём. Эти же суммы идут на главную (плитка и график «Расходы»), поэтому цифры
в разделе «Расходы» и на главной всегда совпадают.

Что не считается в итог (показывается справочно): записи о ремонте техники со стоимостью. Ремонт обычно уже внесён счётом, и сумма
сложилась бы дважды."""
import datetime as dt
from decimal import Decimal

from sqlalchemy import exists, func, select

from app.models import (FuelFill, Fine, MiscExpense, Rental, RepairBill, ServiceRequest, Vehicle, VehicleFinance, VehicleRepair)
from app.registry import _vehicle_label
from app.services import reports as rep
from app.services import timesheet as ts
from app.services.reminders import local_day

# (код, название, считается ли в итог, пояснение)
CATS: tuple[tuple[str, str, bool, str], ...] = (
    ("bills", "Счета на ремонт и запчасти", True, "Все счета по дате счёта: и оплаченные, и нет. С машиной или без неё."),
    ("misc", "Общие расходы", True, "Стоянка, мойка, связь и другие траты компании."),
    ("fuel", "Топливо", True, "Заправки по сумме."),
    ("requests", "Ремонт по обращениям без счёта", True, "Фактическая стоимость закрытых обращений, у которых нет счёта."),
    ("vehicle_finance", "Прочие затраты по технике", True, "Расходы, внесённые в «Затраты на спецтехнику»."),
    ("fines", "Штрафы (платит компания)", True, "Только те, что платит компания, не водитель."),
    ("rent_in", "Аренда техники у других", True, "По суткам внутри месяца."),
    ("repairs", "Записи о ремонте техники (справочно)", False, "В итог не входят: ремонт обычно уже внесён счётом."),
)
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


def sums(c: rep.Ctx, a: dt.date, b: dt.date) -> dict[str, float]:
    """Сумма по каждой статье за период [a, b]. Только то, что человек может видеть по праву «деньги в отчётах» и выбранному юрлицу."""
    sc = c.money
    if not sc:
        return {k: 0.0 for k in CAT_LABELS}
    has_bill = exists().where(RepairBill.request_id == ServiceRequest.id, RepairBill.archived_at.is_(None))
    s = {
        "bills": _total(c, RepairBill.total, RepairBill.archived_at.is_(None), sc.cond(RepairBill.legal_entity_id), RepairBill.bill_date >= a, RepairBill.bill_date <= b),
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
    for x in db.scalars(select(RepairBill).where(RepairBill.archived_at.is_(None), sc.cond(RepairBill.legal_entity_id), RepairBill.bill_date >= a, RepairBill.bill_date <= b)):
        add("bills", x.bill_date, f"Счёт № {x.number}", x.supplier_name or x.basis, x.total, vehicle_id=x.vehicle_id, status=x.status,
            link=f"/bills/{x.id}" if link_ok else "", rid=x.id)
    for x in db.scalars(select(MiscExpense).where(MiscExpense.archived_at.is_(None), sc.cond(MiscExpense.legal_entity_id), MiscExpense.op_date >= a, MiscExpense.op_date <= b)):
        add("misc", x.op_date, x.category or "Расход", x.description, x.amount, rid=x.id)
    for x in db.scalars(select(FuelFill).where(FuelFill.archived_at.is_(None), sc.cond(FuelFill.legal_entity_id), FuelFill.fill_date >= a, FuelFill.fill_date <= b,
                                               FuelFill.amount > 0)):
        add("fuel", x.fill_date, f"Заправка {rep_num(x.liters)} л", x.station, x.amount, vehicle_id=x.vehicle_id, rid=x.id)
    has_bill = exists().where(RepairBill.request_id == ServiceRequest.id, RepairBill.archived_at.is_(None))
    link_ok = p.can("requests.view")
    for x in db.scalars(select(ServiceRequest).where(ServiceRequest.status == "closed", ServiceRequest.cost_actual.is_not(None), ~has_bill,
                                                     sc.cond(ServiceRequest.legal_entity_id), ServiceRequest.closed_at >= rep.day_start(a),
                                                     ServiceRequest.closed_at < rep.day_start(b + dt.timedelta(days=1)))):
        add("requests", local_day(x.closed_at), f"Обращение № {x.number}", x.title, x.cost_actual, vehicle_id=x.vehicle_id,
            link=f"/requests/{x.id}" if link_ok else "", rid=x.id)
    for x in db.scalars(select(VehicleFinance).where(VehicleFinance.kind == "expense", VehicleFinance.archived_at.is_(None), sc.cond(VehicleFinance.legal_entity_id),
                                                     VehicleFinance.op_date >= a, VehicleFinance.op_date <= b)):
        add("vehicle_finance", x.op_date, x.category or "Затрата по технике", x.description, x.amount, vehicle_id=x.vehicle_id, rid=x.id)
    for x in db.scalars(select(Fine).where(Fine.archived_at.is_(None), Fine.payer == "company", sc.cond(Fine.legal_entity_id), Fine.fine_date >= a, Fine.fine_date <= b)):
        add("fines", x.fine_date, "Штраф" + (f" № {x.resolution_number}" if x.resolution_number else ""), x.article, x.amount, vehicle_id=x.vehicle_id,
            status="paid" if x.paid_at else "unpaid", rid=x.id)
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
    unpaid = round(sum(r["amount"] for r in everything if r["cat"] == "bills" and r["status"] == "unpaid"), 2)
    items = [r for r in everything if not only or r["cat"] == only]
    return {
        "month": first.strftime("%Y-%m"), "title": f"{MONTH_NAMES[first.month - 1]} {first.year}", "total": total, "prev_total": prev,
        "delta_pct": round((total - prev) / prev * 100) if prev > 0 else None, "bills_unpaid": unpaid,
        "categories": [{"key": k, "label": label, "hint": hint, "counted": counted, "amount": s[k], "count": counts.get(k, 0)} for k, label, counted, hint in CATS],
        "items": items[:ITEMS_LIMIT], "shown": min(len(items), ITEMS_LIMIT), "found": len(items),
    }
