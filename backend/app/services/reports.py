# -*- coding: utf-8 -*-
"""Отчёты: итоги месяца, пробег, топливо, затраты, ремонты, расход по путевым листам, простои, заказы по заказчикам.

Правила (общие для всех отчётов):
- отчёт строится только из того, на что у человека есть права: столбцы без права не показываются (в ответе перечислено, какие
  скрыты и почему), а не заполняются нулями;
- деньги (затраты, доходы, суммы заказов) видны только с правом «reports.money», остальные столбцы нужны права на свой раздел;
- всё считается только по юрлицам, где у человека есть право на этот столбец; выбранное в переключателе юрлицо сужает данные;
- записи в архиве не учитываются; сутки считаются по часам организации.
"""
import bisect
import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, aliased

from app.common import fail
from app.deps import Principal, Scope
from app.enums import EFFECTIVE_STATES, FINE_PAYERS, REQUEST_STATUSES, VEHICLE_STATUSES, status_now
from app.models import (Counterparty, Employee, Fine, LegalEntity, FuelFill, MiscExpense, Order, Rental, RepairBill, ServiceRequest, StockMove, Trip, Vehicle,
                        VehicleReading, VehicleStatusHistory, VehicleType, Waybill, WeighTicket, WorkLog)
from app.services import payroll as pay
from app.registry import _vehicle_label
from app.services.fuel import norms, segments
from app.services.reminders import day_start, local_day

MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_SHORT = ["", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MAX_SPAN_DAYS = 1830            # диапазон дат в отчёте не длиннее пяти лет
EXTRA_LIMIT = 500               # строк в дополнительной таблице итогов месяца
STATE_COLUMNS = ["in_work", "in_repair"]



# ---------------------------------------------------------------- общее

@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"          # text | int | dec | money | date
    total: bool = False
    show: bool = True


@dataclass
class Ctx:
    db: Session
    p: Principal
    entity: int | None
    d_from: dt.date
    d_to: dt.date
    today: dt.date
    now: dt.datetime
    hidden: list[str] = field(default_factory=list)

    def sc(self, perm: str) -> Scope:
        """Где человек может пользоваться правом, с учётом выбранного юрлица."""
        return self.p.scope(perm).narrow(self.entity)

    @property
    def money(self) -> Scope:
        return self.sc("reports.money")

    def hide(self, text: str) -> None:
        if text not in self.hidden:
            self.hidden.append(text)

    @property
    def start(self) -> dt.datetime:
        return day_start(self.d_from)

    @property
    def end(self) -> dt.datetime:
        return day_start(self.d_to + dt.timedelta(days=1))


def fl(x, places: int = 2) -> float:
    return round(float(x or 0), places)


def table(cols: list[Col], rows: list[dict], totals: bool = True) -> dict:
    """Таблица из строк-словарей: только показанные столбцы, итоги по столбцам с total."""
    shown = [c for c in cols if c.show]
    body = [[r.get(c.key) for c in shown] for r in rows]
    foot = None
    if totals and rows:
        foot = []
        for i, c in enumerate(shown):
            if i == 0:
                foot.append("Итого")
            elif c.total:
                vals = [r.get(c.key) for r in rows if isinstance(r.get(c.key), (int, float))]
                foot.append(round(sum(vals), 2 if c.kind == "money" else 3 if c.kind == "ton" else 1) if c.kind != "int" else int(sum(vals)))
            else:
                foot.append(None)
    return {"columns": [{"key": c.key, "label": c.label, "kind": c.kind} for c in shown], "rows": body, "totals": foot}


def chart(labels: list[str], values: list[float], unit: str, full: list[str] | None = None) -> dict | None:
    if not labels or not any(values):
        return None
    return {"labels": labels, "values": [round(v, 2) for v in values], "unit": unit, "titles": full or labels}


def month_label(y: int, m: int) -> str:
    return f"{MONTHS_SHORT[m]} {y % 100:02d}"


def last_months(end: dt.date, n: int = 12) -> list[tuple[int, int]]:
    """n последних месяцев по месяц end включительно, от старого к новому."""
    out, y, m = [], end.year, end.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def month_bounds(y: int, m: int) -> tuple[dt.date, dt.date]:
    first = dt.date(y, m, 1)
    nxt = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt - dt.timedelta(days=1)


def load_vehicles(c: Ctx) -> list[Vehicle]:
    sc = c.sc("vehicles.view")
    if not sc:
        raise fail(403, "forbidden", "Нет доступа к технике")
    q = (select(Vehicle).join(VehicleType, VehicleType.id == Vehicle.type_id)
         .where(sc.cond(Vehicle.legal_entity_id), Vehicle.archived_at.is_(None), VehicleType.category != "trailer")
         .order_by(func.lower(Vehicle.make), Vehicle.plate, Vehicle.id))
    return list(c.db.scalars(q))


def label(v: Vehicle) -> str:
    return _vehicle_label(v)


def short(text: str, n: int = 14) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def active(row: dict, v: Vehicle, keys: list[str]) -> bool:
    """Машина в архиве без движения за период в отчёт не попадает."""
    if not v.archived_at:
        return True
    return any(isinstance(row.get(k), (int, float)) and row.get(k) for k in keys)


# ---------------------------------------------------------------- пробег

def km_series(c: Ctx, ids: list[int], upto: dt.datetime) -> dict[int, tuple[list, list]]:
    """Действующие показания пробега по машинам до момента upto: время и значение, по возрастанию."""
    if not ids:
        return {}
    newer = aliased(VehicleReading)
    rows = c.db.execute(
        select(VehicleReading.vehicle_id, VehicleReading.recorded_at, VehicleReading.value)
        .where(VehicleReading.vehicle_id.in_(ids), VehicleReading.kind == "km", VehicleReading.review_state.in_(EFFECTIVE_STATES),
               ~exists().where(newer.corrects_id == VehicleReading.id), VehicleReading.recorded_at < upto)
        .order_by(VehicleReading.vehicle_id, VehicleReading.recorded_at, VehicleReading.id)).all()
    out: dict[int, tuple[list, list]] = {}
    for vid, at, val in rows:
        t, v = out.setdefault(vid, ([], []))
        t.append(at)
        v.append(float(val))
    return out


def km_between(series: dict, vid: int, a: dt.datetime, b: dt.datetime) -> float:
    """Пробег за [a, b): показание на конец минус показание на начало; если до начала показаний не было — от первого в периоде."""
    s = series.get(vid)
    if not s:
        return 0.0
    times, vals = s
    i_end = bisect.bisect_left(times, b)
    if i_end == 0:
        return 0.0
    i_start = bisect.bisect_left(times, a)
    start = vals[i_start - 1] if i_start > 0 else vals[0]
    return round(max(vals[i_end - 1] - start, 0.0), 1)


def report_mileage(c: Ctx) -> dict:
    vehicles = load_vehicles(c)
    months = last_months(c.d_to)
    b0 = day_start(month_bounds(*months[-1])[1] + dt.timedelta(days=1))
    series = km_series(c, [v.id for v in vehicles], b0)
    cols = [Col("name", "Машина")] + [Col(f"m{i}", month_label(y, m), "dec", True) for i, (y, m) in enumerate(months)] + [Col("sum", "Всего", "dec", True)]
    rows = []
    for v in vehicles:
        row = {"name": label(v)}
        for i, (y, m) in enumerate(months):
            lo, hi = month_bounds(y, m)
            row[f"m{i}"] = km_between(series, v.id, day_start(lo), day_start(hi + dt.timedelta(days=1)))
        row["sum"] = round(sum(row[f"m{i}"] for i in range(len(months))), 1)
        if active(row, v, ["sum"]):
            rows.append(row)
    tbl = table(cols, rows)
    totals = tbl["totals"] or []
    ch = chart([month_label(y, m) for y, m in months], totals[1:-1] if totals else [], "км")
    return {"title": "Пробег по месяцам", "table": tbl, "chart": ch, "extras": [],
            "note": "Последние 12 месяцев по показаниям одометра: разница между показаниями на границах месяца. "
                    "В первом месяце учёта считается от первого показания. Выбранный месяц — последний в таблице."}


# ---------------------------------------------------------------- топливо

def fuel_data(c: Ctx, ids: list[int]) -> tuple[dict[int, dict], dict[str, float], dict]:
    """Заправки за период по машинам: число, литры, сумма (с правом на деньги), расход на 100 км по отрезкам между полными баками."""
    sc = c.sc("fuel_fills.view")
    if not sc or not ids:
        return {}, {}, {}
    fills = c.db.execute(select(FuelFill.id, FuelFill.vehicle_id, FuelFill.fill_date, FuelFill.liters, FuelFill.amount, FuelFill.legal_entity_id)
                         .where(FuelFill.vehicle_id.in_(ids), FuelFill.archived_at.is_(None), sc.cond(FuelFill.legal_entity_id),
                                FuelFill.fill_date >= c.d_from, FuelFill.fill_date <= c.d_to)).all()
    money = c.money
    out: dict[int, dict] = {}
    months: dict[str, float] = {}
    in_period: dict[int, set[int]] = {}
    for fid, vid, d, liters, amount, ent in fills:
        r = out.setdefault(vid, {"count": 0, "liters": 0.0, "amount": 0.0, "per100": None})
        r["count"] += 1
        r["liters"] += float(liters)
        if money.allows(ent):
            r["amount"] += float(amount)
        months[d.strftime("%Y-%m")] = months.get(d.strftime("%Y-%m"), 0.0) + float(liters)
        in_period.setdefault(vid, set()).add(fid)
    for vid, fids in in_period.items():
        segs = segments(c.db, vid)
        used = [segs[f] for f in fids if f in segs]
        km = sum(s["km"] for s in used)
        if km > 0:
            out[vid]["per100"] = round(float(sum(s["liters"] for s in used)) / km * 100, 1)
    return out, months, norms(c.db, ids)


def report_fuel(c: Ctx) -> dict:
    vehicles = load_vehicles(c)
    data, months, nrm = fuel_data(c, [v.id for v in vehicles])
    money = bool(c.money)
    if not money:
        c.hide("Сумма заправок: нет права видеть деньги в отчётах")
    cols = [Col("name", "Машина"), Col("count", "Заправок", "int", True), Col("liters", "Литров", "dec", True),
            Col("amount", "Сумма, ₽", "money", True, money), Col("per100", "Расход, л/100 км", "dec"),
            Col("norm", "Норма, л/100 км", "dec"), Col("dev", "К норме, %", "dec")]
    rows = []
    for v in vehicles:
        d = data.get(v.id, {})
        norm = (nrm.get(v.id) or (None, None))[1]
        per100 = d.get("per100")
        row = {"name": label(v), "count": d.get("count", 0), "liters": round(d.get("liters", 0.0), 1), "amount": fl(d.get("amount")),
               "per100": per100, "norm": float(norm) if norm else None,
               "dev": round((per100 / float(norm) - 1) * 100, 1) if per100 and norm else None}
        if active(row, v, ["count"]):
            rows.append(row)
    keys = sorted(months)
    ch = chart([month_label(int(k[:4]), int(k[5:])) for k in keys], [months[k] for k in keys], "л")
    return {"title": "Топливо", "table": table(cols, rows), "chart": ch, "extras": [],
            "note": "Расход считается между полными заправками с известным пробегом: все литры после первой, включая вторую, "
                    "делятся на пройденные километры; учитываются отрезки, закрытые в выбранные даты."}


# ---------------------------------------------------------------- деньги по технике

def grouped(c: Ctx, vid_col, expr, *where) -> dict[int, float]:
    rows = c.db.execute(select(vid_col, func.sum(expr)).where(*where).group_by(vid_col)).all()
    return {vid: float(total or 0) for vid, total in rows}


def money_parts(c: Ctx, ids: list[int]) -> dict[str, dict[int, float]]:
    """Деньги по машинам за период (только с правом reports.money). Ремонт из счетов и из обращений без счёта не пересекается."""
    sc = c.money
    if not sc or not ids:
        return {}
    a, b = c.d_from, c.d_to
    parts: dict[str, dict[int, float]] = {}
    parts["fuel"] = grouped(c, FuelFill.vehicle_id, FuelFill.amount, FuelFill.vehicle_id.in_(ids), FuelFill.archived_at.is_(None),
                            sc.cond(FuelFill.legal_entity_id), FuelFill.fill_date >= a, FuelFill.fill_date <= b)
    parts["bills"] = grouped(c, RepairBill.vehicle_id, RepairBill.total, RepairBill.vehicle_id.in_(ids), RepairBill.archived_at.is_(None),
                             sc.cond(RepairBill.legal_entity_id), RepairBill.bill_date >= a, RepairBill.bill_date <= b)
    has_bill = exists().where(RepairBill.request_id == ServiceRequest.id, RepairBill.archived_at.is_(None))
    parts["requests"] = grouped(c, ServiceRequest.vehicle_id, ServiceRequest.cost_actual, ServiceRequest.vehicle_id.in_(ids),
                                ServiceRequest.status == "closed", ServiceRequest.cost_actual.is_not(None), ~has_bill,
                                sc.cond(ServiceRequest.legal_entity_id), ServiceRequest.closed_at >= c.start, ServiceRequest.closed_at < c.end)
    parts["fines"] = grouped(c, Fine.vehicle_id, Fine.amount, Fine.vehicle_id.in_(ids), Fine.archived_at.is_(None), Fine.payer == "company",
                             sc.cond(Fine.legal_entity_id), Fine.fine_date >= a, Fine.fine_date <= b)
    parts["stock"] = grouped(c, StockMove.vehicle_id, StockMove.qty * StockMove.price, StockMove.vehicle_id.in_(ids), StockMove.archived_at.is_(None),
                             StockMove.kind.in_(("issue", "writeoff")), sc.cond(StockMove.legal_entity_id), StockMove.move_date >= a, StockMove.move_date <= b)
    parts["orders"] = grouped(c, Order.vehicle_id, Order.amount, Order.vehicle_id.in_(ids), Order.archived_at.is_(None), Order.status == "done",
                              sc.cond(Order.legal_entity_id), Order.order_date >= a, Order.order_date <= b)
    parts["rent_in"], parts["rent_out"] = {}, {}
    for r in c.db.scalars(select(Rental).where(Rental.vehicle_id.in_(ids), Rental.archived_at.is_(None), sc.cond(Rental.legal_entity_id),
                                               Rental.start_date <= b, (Rental.end_date.is_(None)) | (Rental.end_date >= a))):
        lo, hi = max(r.start_date, a), min(r.end_date or c.today, b)
        days = (hi - lo).days + 1
        if days > 0:
            bucket = parts["rent_out" if r.direction == "out" else "rent_in"]
            bucket[r.vehicle_id] = bucket.get(r.vehicle_id, 0.0) + days * float(r.rate or 0)
    return parts


def cost_total(parts: dict, vid: int) -> float:
    return sum(parts.get(k, {}).get(vid, 0.0) for k in ("fuel", "bills", "requests", "fines", "rent_in"))


def income_total(parts: dict, vid: int) -> float:
    return sum(parts.get(k, {}).get(vid, 0.0) for k in ("orders", "rent_out"))


def report_costs(c: Ctx) -> dict:
    vehicles = load_vehicles(c)
    ids = [v.id for v in vehicles]
    parts = money_parts(c, ids)
    series = km_series(c, ids, c.end)
    cols = [Col("name", "Машина"), Col("fuel", "Топливо, ₽", "money", True), Col("bills", "Ремонт: счета, ₽", "money", True),
            Col("requests", "Ремонт: обращения без счёта, ₽", "money", True), Col("fines", "Штрафы (платит компания), ₽", "money", True),
            Col("rent_in", "Аренда: берём, ₽", "money", True), Col("cost", "Всего затрат, ₽", "money", True),
            Col("orders", "Доходы: заказы без НДС, ₽", "money", True), Col("rent_out", "Доходы: аренда, ₽", "money", True),
            Col("result", "Доходы минус затраты, ₽", "money", True), Col("per_km", "Затраты на 1 км, ₽", "money"),
            Col("stock", "Детали со склада (справочно), ₽", "money", True)]
    rows = []
    for v in vehicles:
        km = km_between(series, v.id, c.start, c.end)
        cost, income = cost_total(parts, v.id), income_total(parts, v.id)
        row = {"name": label(v), **{k: fl(parts.get(k, {}).get(v.id)) for k in ("fuel", "bills", "requests", "fines", "rent_in", "orders", "rent_out", "stock")},
               "cost": fl(cost), "result": fl(income - cost), "per_km": fl(cost / km) if km > 0 and cost else None}
        if active(row, v, ["cost", "orders", "rent_out"]):
            rows.append(row)
    tbl = table(cols, rows)
    ch = chart([short(r["name"].split(" · ")[0]) for r in rows], [r["cost"] for r in rows], "₽", [r["name"] for r in rows])
    return {"title": "Затраты и доходы по технике", "table": tbl, "chart": ch, "extras": [],
            "note": "Топливо по заправкам, ремонт по счетам на ремонт (по дате счёта) и по фактическим затратам закрытых обращений, у которых нет счёта "
                    "(чтобы не считать дважды), штрафы только те, что платит компания, аренда по суткам внутри периода. Доходы: выполненные заказы без НДС "
                    "и аренда, которую сдаём. Детали со склада показаны справочно и в затраты не входят: они уже учтены в счетах, по которым их принимали."}


def report_repairs(c: Ctx) -> dict:
    vehicles = load_vehicles(c)
    ids = [v.id for v in vehicles]
    months = last_months(c.d_to)
    first = month_bounds(*months[0])[0]
    last = month_bounds(*months[-1])[1]
    sc = c.money
    by: dict[tuple[int, str], float] = {}
    if sc and ids:
        for vid, d, total in c.db.execute(select(RepairBill.vehicle_id, RepairBill.bill_date, RepairBill.total).where(
                RepairBill.vehicle_id.in_(ids), RepairBill.archived_at.is_(None), sc.cond(RepairBill.legal_entity_id),
                RepairBill.bill_date >= first, RepairBill.bill_date <= last)):
            k = (vid, d.strftime("%Y-%m"))
            by[k] = by.get(k, 0.0) + float(total)
        has_bill = exists().where(RepairBill.request_id == ServiceRequest.id, RepairBill.archived_at.is_(None))
        for vid, closed, cost in c.db.execute(select(ServiceRequest.vehicle_id, ServiceRequest.closed_at, ServiceRequest.cost_actual).where(
                ServiceRequest.vehicle_id.in_(ids), ServiceRequest.status == "closed", ServiceRequest.cost_actual.is_not(None), ~has_bill,
                sc.cond(ServiceRequest.legal_entity_id), ServiceRequest.closed_at >= day_start(first), ServiceRequest.closed_at < day_start(last + dt.timedelta(days=1)))):
            k = (vid, local_day(closed).strftime("%Y-%m"))
            by[k] = by.get(k, 0.0) + float(cost)
    cols = [Col("name", "Машина")] + [Col(f"m{i}", month_label(y, m), "money", True) for i, (y, m) in enumerate(months)] + [Col("sum", "Всего, ₽", "money", True)]
    rows = []
    for v in vehicles:
        row = {"name": label(v)}
        for i, (y, m) in enumerate(months):
            row[f"m{i}"] = fl(by.get((v.id, f"{y}-{m:02d}")))
        row["sum"] = fl(sum(row[f"m{i}"] for i in range(len(months))))
        if active(row, v, ["sum"]):
            rows.append(row)
    tbl = table(cols, rows)
    totals = tbl["totals"] or []
    return {"title": "Ремонты по месяцам", "table": tbl, "chart": chart([month_label(y, m) for y, m in months], totals[1:-1] if totals else [], "₽"),
            "extras": [], "note": "Последние 12 месяцев. Счета на ремонт по дате счёта (с НДС) плюс фактические затраты закрытых обращений без счёта "
                                  "по дате закрытия. Выбранный месяц — последний в таблице."}


# ---------------------------------------------------------------- простои

def downtime_data(c: Ctx, vehicles: list[Vehicle]) -> dict[int, dict]:
    """Сколько суток каждая машина провела в каждом статусе за период и периоды ремонта.

    До первой записи в истории статус берётся из неё (поле «был»); если записей нет, статус не менялся и равен нынешнему.
    Считается с момента добавления машины в программу и не дальше текущего момента."""
    w_start, w_end = c.start, min(c.end, c.now)
    ids = [v.id for v in vehicles]
    hist: dict[int, list[VehicleStatusHistory]] = {}
    if ids:
        for h in c.db.scalars(select(VehicleStatusHistory).where(VehicleStatusHistory.vehicle_id.in_(ids), VehicleStatusHistory.changed_at < w_end)
                              .order_by(VehicleStatusHistory.vehicle_id, VehicleStatusHistory.changed_at, VehicleStatusHistory.id)):
            hist.setdefault(h.vehicle_id, []).append(h)
    out: dict[int, dict] = {}
    for v in vehicles:
        events = hist.get(v.id, [])
        days = {k: 0.0 for k in VEHICLE_STATUSES}
        episodes: list[dict] = []
        begin = max(w_start, v.created_at)
        if begin >= w_end:
            out[v.id] = {"days": days, "episodes": episodes}
            continue
        state = status_now(events[0].from_status or "in_work") if events else status_now(v.status)
        ep = {"start": None, "reason": "", "days": 0.0} if state == "in_repair" else None
        i = 0
        while i < len(events) and events[i].changed_at <= begin:
            if status_now(events[i].status) != state:
                state = status_now(events[i].status)
                ep = {"start": events[i].changed_at, "reason": events[i].reason, "days": 0.0} if state == "in_repair" else None
            i += 1
        cursor = begin
        for h in events[i:]:
            if status_now(h.status) == state:
                continue
            span = (h.changed_at - cursor).total_seconds() / 86400
            days[state] += span
            if ep is not None:
                ep["days"] += span
                ep["end"] = h.changed_at
                episodes.append(ep)
            cursor, state = h.changed_at, status_now(h.status)
            ep = {"start": h.changed_at, "reason": h.reason, "days": 0.0} if state == "in_repair" else None
        span = (w_end - cursor).total_seconds() / 86400
        days[state] += span
        if ep is not None:
            ep["days"] += span
            ep["end"] = None
            episodes.append(ep)
        out[v.id] = {"days": days, "episodes": [e for e in episodes if e["days"] > 0]}
    return out


def report_downtime(c: Ctx) -> dict:
    vehicles = load_vehicles(c)
    data = downtime_data(c, vehicles)
    cols = [Col("name", "Машина"), Col("now", "Сейчас")] + [Col(s, VEHICLE_STATUSES[s] + ", сут.", "dec", True) for s in STATE_COLUMNS] + [
        Col("repairs", "Ремонтов, раз", "int", True), Col("longest", "Самый долгий ремонт, сут.", "dec"), Col("ready", "Готовность, %", "dec")]
    rows, episodes = [], []
    for v in vehicles:
        d = data[v.id]
        total = sum(d["days"][s] for s in STATE_COLUMNS)
        row = {"name": label(v), "now": VEHICLE_STATUSES.get(v.status, v.status), **{s: round(d["days"][s], 1) for s in STATE_COLUMNS},
               "repairs": len(d["episodes"]), "longest": round(max((e["days"] for e in d["episodes"]), default=0), 1) or None,
               "ready": round(d["days"]["in_work"] / total * 100, 1) if total else None}
        if not active(row, v, STATE_COLUMNS):
            continue
        rows.append(row)
        for e in d["episodes"]:
            episodes.append((e["start"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), label(v), e))
    episodes.sort(key=lambda x: x[0], reverse=True)
    extra_rows = [[name, e["start"].isoformat() if e["start"] else None, e["end"].isoformat() if e["end"] else None, round(e["days"], 1), e["reason"]]
                  for _, name, e in episodes]
    extras = [{"title": "Периоды ремонта", "columns": [{"key": "v", "label": "Машина", "kind": "text"}, {"key": "from", "label": "С", "kind": "datetime"},
                                                      {"key": "to", "label": "По", "kind": "datetime"}, {"key": "days", "label": "Суток в периоде", "kind": "dec"},
                                                      {"key": "why", "label": "Причина", "kind": "text"}], "rows": extra_rows[:EXTRA_LIMIT], "totals": None}] if extra_rows else []
    ch = chart([short(r["name"].split(" · ")[0]) for r in rows], [r["in_repair"] for r in rows], "сут.", [r["name"] for r in rows])
    return {"title": "Простои", "table": table(cols, rows), "chart": ch, "extras": extras,
            "note": "По истории статусов техники: сколько суток машина была в каждом статусе. Считается с даты добавления машины в программу и "
                    "не дальше текущего момента. Готовность — доля суток «На линии» от всех суток. "
                    "«Начало ремонта до учёта» значит, что ремонт начался раньше выбранных дат."}


# ---------------------------------------------------------------- расход по путевым листам

def report_waybill_fuel(c: Ctx) -> dict:
    vehicles = load_vehicles(c)
    sc = c.sc("waybills.view")
    ids = [v.id for v in vehicles]
    nrm = norms(c.db, ids)
    agg: dict[int, dict] = {}
    if sc and ids:
        for w in c.db.scalars(select(Waybill).where(Waybill.vehicle_id.in_(ids), Waybill.status == "closed", sc.cond(Waybill.legal_entity_id),
                                                     Waybill.work_date >= c.d_from, Waybill.work_date <= c.d_to)):
            a = agg.setdefault(w.vehicle_id, {"n": 0, "km": 0.0, "fact": 0.0, "plan": 0.0, "km_counted": 0.0})
            a["n"] += 1
            km = float(w.end_km - w.start_km) if w.start_km is not None and w.end_km is not None else None
            if km is not None:
                a["km"] += km
            norm = (nrm.get(w.vehicle_id) or (None, None))[1]
            if None not in (w.fuel_start_l, w.fuel_added_l, w.fuel_end_l) and km is not None and norm:
                a["fact"] += float(w.fuel_start_l + w.fuel_added_l - w.fuel_end_l)
                a["plan"] += km * float(norm) / 100
                a["km_counted"] += km
    cols = [Col("name", "Машина"), Col("n", "Листов", "int", True), Col("km", "Пробег, км", "dec", True), Col("fact", "Расход факт, л", "dec", True),
            Col("plan", "Расход по норме, л", "dec", True), Col("diff", "Экономия (+) / перерасход (−), л", "dec", True), Col("per100", "Факт, л/100 км", "dec")]
    rows = []
    for v in vehicles:
        a = agg.get(v.id)
        if not a:
            if not v.archived_at:
                rows.append({"name": label(v), "n": 0, "km": 0.0, "fact": 0.0, "plan": 0.0, "diff": 0.0, "per100": None})
            continue
        rows.append({"name": label(v), "n": a["n"], "km": round(a["km"], 1), "fact": round(a["fact"], 1), "plan": round(a["plan"], 1),
                     "diff": round(a["plan"] - a["fact"], 1), "per100": round(a["fact"] / a["km_counted"] * 100, 1) if a["km_counted"] else None})
    return {"title": "Расход по путевым листам", "table": table(cols, rows),
            "chart": chart([short(r["name"].split(" · ")[0]) for r in rows], [r["km"] for r in rows], "км", [r["name"] for r in rows]), "extras": [],
            "note": "Только закрытые листы. Расход факт: остаток на выезде плюс заправка минус остаток по возвращении; по норме: пробег листа "
                    "и норма из карточки машины. В расход попадают только листы, где есть пробег, все три остатка топлива и норма."}


# ---------------------------------------------------------------- заказы по заказчикам

def report_orders(c: Ctx) -> dict:
    sc = c.sc("orders.view")
    money = c.money
    if not money:
        c.hide("Суммы заказов: нет права видеть деньги в отчётах")
    cust: dict[int, dict] = {}
    names: dict[int, str] = {}
    from app.resources import _vat_split
    if sc:
        for o in c.db.scalars(select(Order).where(Order.archived_at.is_(None), sc.cond(Order.legal_entity_id), Order.order_date >= c.d_from,
                                                   Order.order_date <= c.d_to)):
            r = cust.setdefault(o.customer_id, {"all": 0, "done": 0, "open": 0, "cancelled": 0, "base": 0.0, "vat": 0.0})
            r["all"] += 1
            if o.status == "done":
                r["done"] += 1
                if money.allows(o.legal_entity_id):
                    vat, total = _vat_split(o.amount, o.vat_pct)
                    r["base"] += float(o.amount)
                    r["vat"] += float(vat)
            elif o.status == "cancelled":
                r["cancelled"] += 1
            else:
                r["open"] += 1
        if cust:
            names = dict(c.db.execute(select(Counterparty.id, Counterparty.name).where(Counterparty.id.in_(list(cust)))).all())
    cols = [Col("name", "Заказчик"), Col("all", "Заказов", "int", True), Col("done", "Выполнено", "int", True), Col("open", "В работе", "int", True),
            Col("cancelled", "Отменено", "int", True), Col("base", "Выполнено без НДС, ₽", "money", True, bool(money)),
            Col("vat", "НДС, ₽", "money", True, bool(money)), Col("total", "Выполнено с НДС, ₽", "money", True, bool(money))]
    rows = [{"name": names.get(cid, "—"), "all": r["all"], "done": r["done"], "open": r["open"], "cancelled": r["cancelled"],
             "base": fl(r["base"]), "vat": fl(r["vat"]), "total": fl(r["base"] + r["vat"])} for cid, r in cust.items()]
    rows.sort(key=lambda r: (-r["base"], -r["done"], -r["all"], r["name"].lower()))
    top = rows[:10]
    by_money = bool(money) and any(r["base"] for r in top)
    ch = chart([short(r["name"], 16) for r in top], [r["base" if by_money else "done"] for r in top], "₽" if by_money else "заказов", [r["name"] for r in top])
    return {"title": "Заказы по заказчикам", "table": table(cols, rows), "chart": ch, "extras": [],
            "note": "Заказы по дате заказа за выбранные даты. Деньги считаются только по выполненным заказам: сумма без НДС, НДС сверху и итого."}


# ---------------------------------------------------------------- итоги месяца

def extra(title: str, cols: list[dict], rows: list[list], c: Ctx, total_label: bool = False) -> dict:
    """Дополнительная таблица; режет длинные списки и говорит об этом в названии."""
    cut = len(rows) > EXTRA_LIMIT
    shown = rows[:EXTRA_LIMIT]
    totals = None
    if total_label and shown:
        totals = ["Итого"] + [None] * (len(cols) - 1)
        for i, col in enumerate(cols):
            if col.get("sum"):
                totals[i] = round(sum(r[i] or 0 for r in shown), 3 if col.get("kind") == "ton" else 2)
    return {"title": title + (f" (первые {EXTRA_LIMIT})" if cut else ""), "columns": [{k: v for k, v in col.items() if k != "sum"} for col in cols], "rows": shown, "totals": totals}


def report_month(c: Ctx) -> dict:
    vehicles = load_vehicles(c)
    ids = [v.id for v in vehicles]
    parts = money_parts(c, ids)
    money = bool(c.money)
    data, _, _n = fuel_data(c, ids)
    fuel_ok = bool(c.sc("fuel_fills.view"))
    trips_sc = c.sc("trips.view")
    if not money:
        c.hide("Затраты, доходы и штрафы в рублях: нет права видеть деньги в отчётах")
    if not fuel_ok:
        c.hide("Заправки и расход: нет права видеть заправки")
    if not trips_sc:
        c.hide("Рейсы: нет права видеть рейсы")
    trips: dict[int, int] = {}
    if trips_sc and ids:
        trips = {vid: int(n) for vid, n in c.db.execute(select(Trip.vehicle_id, func.count()).where(
            Trip.vehicle_id.in_(ids), Trip.archived_at.is_(None), trips_sc.cond(Trip.legal_entity_id), Trip.trip_date >= c.d_from,
            Trip.trip_date <= c.d_to).group_by(Trip.vehicle_id))}
    series = km_series(c, ids, c.end)
    down = downtime_data(c, vehicles)
    cols = [Col("name", "Машина"), Col("km", "Пробег, км", "dec", True), Col("liters", "Заправлено, л", "dec", True, fuel_ok),
            Col("per100", "Расход, л/100 км", "dec", False, fuel_ok), Col("cost", "Затраты, ₽", "money", True, money),
            Col("income", "Доходы, ₽", "money", True, money), Col("trips", "Рейсов", "int", True, bool(trips_sc)),
            Col("repair", "На ремонте, сут.", "dec", True), Col("fines", "Штрафы (платит компания), ₽", "money", True, money)]
    rows = []
    for v in vehicles:
        d = data.get(v.id, {})
        row = {"name": label(v), "km": km_between(series, v.id, c.start, c.end), "liters": round(d.get("liters", 0.0), 1) or None, "per100": d.get("per100"),
               "cost": fl(cost_total(parts, v.id)), "income": fl(income_total(parts, v.id)), "trips": trips.get(v.id, 0),
               "repair": round(down[v.id]["days"]["in_repair"], 1), "fines": fl(parts.get("fines", {}).get(v.id))}
        if active(row, v, ["km", "liters", "cost", "income", "trips", "fines"]):
            rows.append(row)
    summary = table(cols, rows)
    ch = chart([short(r["name"].split(" · ")[0]) for r in rows], [r["cost"] if money else r["km"] for r in rows], "₽" if money else "км", [r["name"] for r in rows])

    names = {v.id: label(v) for v in vehicles}
    extras: list[dict] = []
    sheets: list[tuple[str, dict]] = [("Сводка", summary)]
    if money:
        sheets.append(("Затраты", report_costs(c)["table"]))
    if fuel_ok:
        sheets.append(("Топливо", report_fuel(c)["table"]))
    dt_rep = report_downtime(c)
    sheets.append(("Простои", dt_rep["table"]))
    extras += dt_rep["extras"]

    sc_req = c.sc("requests.view")
    if sc_req and ids:
        reqs = c.db.scalars(select(ServiceRequest).where(ServiceRequest.vehicle_id.in_(ids), sc_req.cond(ServiceRequest.legal_entity_id),
                                                         ServiceRequest.created_at >= c.start, ServiceRequest.created_at < c.end).order_by(ServiceRequest.id)).all()
        if reqs:
            extras.append(extra("Обращения за месяц", [{"key": "n", "label": "№", "kind": "int"}, {"key": "v", "label": "Машина", "kind": "text"},
                                                        {"key": "t", "label": "Обращение", "kind": "text"}, {"key": "s", "label": "Состояние", "kind": "text"},
                                                        {"key": "c", "label": "Создано", "kind": "date"}, {"key": "x", "label": "Закрыто", "kind": "date"}],
                               [[r.number, names.get(r.vehicle_id, ""), r.title[:160], REQUEST_STATUSES.get(r.status, r.status), local_day(r.created_at).isoformat(),
                                 local_day(r.closed_at).isoformat() if r.closed_at else None] for r in reqs], c))
    sc_fine = c.sc("fines.view")
    if sc_fine and ids:
        fines = c.db.scalars(select(Fine).where(Fine.vehicle_id.in_(ids), Fine.archived_at.is_(None), sc_fine.cond(Fine.legal_entity_id),
                                                Fine.fine_date >= c.d_from, Fine.fine_date <= c.d_to).order_by(Fine.fine_date, Fine.id)).all()
        if fines:
            fcols = [{"key": "d", "label": "Дата", "kind": "date"}, {"key": "v", "label": "Машина", "kind": "text"}, {"key": "r", "label": "Постановление", "kind": "text"},
                     {"key": "a", "label": "Статья", "kind": "text"}]
            if money:
                fcols.append({"key": "m", "label": "Сумма, ₽", "kind": "money", "sum": True})
            fcols += [{"key": "s", "label": "Состояние", "kind": "text"}, {"key": "p", "label": "Кто платит", "kind": "text"}]
            frows = []
            for f in fines:
                row = [f.fine_date.isoformat(), names.get(f.vehicle_id, ""), f.resolution_number, f.article]
                if money:
                    row.append(fl(f.amount) if c.money.allows(f.legal_entity_id) else None)
                row += ["Оплачен " + f.paid_at.isoformat() if f.paid_at else "Не оплачен", FINE_PAYERS.get(f.payer, f.payer)]
                frows.append(row)
            extras.append(extra("Штрафы за месяц", fcols, frows, c, total_label=money))
    if trips_sc and ids:
        trs = c.db.scalars(select(Trip).where(Trip.vehicle_id.in_(ids), Trip.archived_at.is_(None), trips_sc.cond(Trip.legal_entity_id),
                                              Trip.trip_date >= c.d_from, Trip.trip_date <= c.d_to).order_by(Trip.trip_date, Trip.id)).all()
        if trs:
            extras.append(extra("Рейсы за месяц", [{"key": "d", "label": "Дата", "kind": "date"}, {"key": "v", "label": "Машина", "kind": "text"},
                                                    {"key": "r", "label": "Маршрут", "kind": "text"}, {"key": "g", "label": "Груз", "kind": "text"},
                                                    {"key": "k", "label": "Км", "kind": "int", "sum": True}],
                                [[t.trip_date.isoformat(), names.get(t.vehicle_id, ""), t.route, t.cargo, t.km] for t in trs], c, total_label=True))
    if money:
        exps = c.db.scalars(select(MiscExpense).where(MiscExpense.archived_at.is_(None), c.money.cond(MiscExpense.legal_entity_id),
                                                      MiscExpense.op_date >= c.d_from, MiscExpense.op_date <= c.d_to).order_by(MiscExpense.op_date, MiscExpense.id)).all()
        if exps:
            extras.append(extra("Общие расходы за месяц", [{"key": "d", "label": "Дата", "kind": "date"}, {"key": "c", "label": "Статья", "kind": "text"},
                                                           {"key": "t", "label": "На что", "kind": "text"}, {"key": "m", "label": "Сумма, ₽", "kind": "money", "sum": True}],
                                [[e.op_date.isoformat(), e.category, e.description, fl(e.amount)] for e in exps], c, total_label=True))
    for e in extras:
        if e["title"] != "Периоды ремонта":
            sheets.append((e["title"].replace(" за месяц", ""), {"columns": e["columns"], "rows": e["rows"], "totals": e["totals"]}))
        else:
            sheets.append(("Периоды ремонта", {"columns": e["columns"], "rows": e["rows"], "totals": None}))
    return {"title": "Итоги месяца", "table": summary, "chart": ch, "extras": extras, "sheets": sheets,
            "note": "Всё за выбранный месяц одним отчётом: пробег, топливо, затраты, простои, обращения, штрафы и рейсы. В Excel каждый раздел на своём листе."}


# ---------------------------------------------------------------- перечень отчётов

# ---------------------------------------------------------------- отчёт за день

def _num_text(v) -> str:
    x = float(v)
    return (f"{x:,.0f}" if x == int(x) else f"{x:,.1f}").replace(",", " ").replace(".", ",")


def _spedo(w: Waybill) -> str:
    """Показание счётчика по путевому листу: километры, а у техники на моточасах моточасы."""
    parts = []
    for a, b, unit in ((w.start_km, w.end_km, "км"), (w.start_hours, w.end_hours, "м/ч")):
        if a is None and b is None:
            continue
        parts.append(f"{_num_text(a)} → {_num_text(b)} {unit}" if a is not None and b is not None else f"{_num_text(a if a is not None else b)} {unit}")
    return "; ".join(parts)


def _tariff_text(t) -> str:
    return "" if t is None else _num_text(t.rate) + {"hour": " ₽/ч", "ton": " ₽/т"}.get(t.unit, " ₽/день")


def report_day(c: Ctx) -> dict:
    """По водителям за один день: техника, путевой лист, спидометр, топливо, ставки, часы и заработок. Каждая группа столбцов
    показывается только при праве на свой раздел (часы и деньги, путевые листы, заправки)."""
    day, db = c.d_from, c.db
    sc_log, sc_way, sc_fuel, sc_edit = c.sc("worklog.view"), c.sc("waybills.view"), c.sc("fuel_fills.view"), c.sc("worklog.edit")
    if not sc_log:
        c.hide("Часы, ставки и суммы: нет права видеть часы работы и ремонта")
    if not sc_way:
        c.hide("Путевой лист и спидометр: нет права видеть путевые листы")
    if not sc_fuel:
        c.hide("Топливо: нет права видеть заправки")
    acc: dict[int | None, dict] = {}

    def of(eid: int | None) -> dict:
        return acc.setdefault(eid, {"vehicles": set(), "waybills": [], "odo": [], "fuel": 0.0, "kinds": {}, "work_h": 0.0, "repair_h": 0.0, "work": 0.0,
                                    "repair": 0.0, "rate": {}, "ents": set()})

    car_of_driver: dict[int, int] = {}
    if sc_log:
        logs = list(db.scalars(select(WorkLog).where(WorkLog.work_date == day, WorkLog.archived_at.is_(None), sc_log.cond(WorkLog.legal_entity_id))
                               .order_by(WorkLog.id)))
        amt = pay.amounts(db, logs)
        for l in logs:
            a = of(l.employee_id)
            a[f"{l.kind}_h"] += float(l.hours)
            a[l.kind] += float(amt[l.id])
            a["kinds"][l.kind] = True
            a["ents"].add(l.legal_entity_id)
            a["rate"].setdefault(l.kind, pay.rate_text(l).replace("₽/час", "₽/ч"))
            if l.vehicle_id:
                a["vehicles"].add(l.vehicle_id)
        tickets = list(db.scalars(select(WeighTicket).where(WeighTicket.ticket_date == day, WeighTicket.archived_at.is_(None), WeighTicket.driver_id.is_not(None),
                                                            sc_log.cond(WeighTicket.legal_entity_id))))
        for r in pay.ton_rows(db, tickets):             # водителям с тарифом «за тонну» работа считается по талонам весов
            a = of(r["ticket"].driver_id)
            a["work"] += float(r["amount"])
            a["rate"]["work"] = pay.rate_str(r["tariff"].rate, "ton")
            if r["ticket"].vehicle_id:
                a["vehicles"].add(r["ticket"].vehicle_id)
    if sc_way:
        for w in db.scalars(select(Waybill).where(Waybill.work_date == day, Waybill.status != "cancelled", sc_way.cond(Waybill.legal_entity_id))
                            .order_by(Waybill.id)):
            a = of(w.driver_id)
            a["vehicles"].add(w.vehicle_id)
            a["waybills"].append(f"{w.year}-{w.number:04d}")
            if _spedo(w):
                a["odo"].append(_spedo(w))
            car_of_driver.setdefault(w.vehicle_id, w.driver_id)
    if sc_fuel:
        for f in db.scalars(select(FuelFill).where(FuelFill.fill_date == day, FuelFill.archived_at.is_(None), sc_fuel.cond(FuelFill.legal_entity_id))):
            who = f.driver_id if f.driver_id is not None else car_of_driver.get(f.vehicle_id)
            a = of(who)
            a["fuel"] += float(f.liters)
            a["vehicles"].add(f.vehicle_id)
    emps = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_([k for k in acc if k is not None])))} if acc else {}
    plates = {v.id: v.plate for v in db.scalars(select(Vehicle).where(Vehicle.id.in_({v for a in acc.values() for v in a["vehicles"]})))} if acc else {}
    kind_names = {("work",): "Работа", ("repair",): "Ремонт", ("repair", "work"): "Работа и ремонт"}
    rows, meta = [], []
    for eid, a in acc.items():
        e = emps.get(eid)
        name = (e.last_name + "".join(f" {x[0]}." for x in (e.first_name, e.middle_name) if x)) if e else "Без водителя"
        rate = dict(a["rate"])
        if e is not None and sc_log:                    # часов этого вида нет: показываем тариф, который назначен сейчас
            for k in ("work", "repair"):
                rate.setdefault(k, _tariff_text(pay.pay_tariff(db, e, k)))
        rows.append({
            "name": name, "vehicle": ", ".join(sorted(plates.get(v, "") for v in a["vehicles"])), "waybill": ", ".join(a["waybills"]),
            "odo": "; ".join(a["odo"]), "fuel": round(a["fuel"], 1), "rate_work": rate.get("work", ""), "rate_repair": rate.get("repair", ""),
            "kind": kind_names.get(tuple(sorted(a["kinds"])), ""), "work_h": round(a["work_h"], 1), "repair_h": round(a["repair_h"], 1),
            "work": round(a["work"], 2), "repair": round(a["repair"], 2), "total": round(a["work"] + a["repair"], 2),
            "_eid": eid if a["ents"] else None, "_edit": bool(sc_log and sc_edit and a["ents"] and all(sc_edit.allows(x) for x in a["ents"])),
            "_kind": "both" if len(a["kinds"]) == 2 else next(iter(a["kinds"]), "")})
    rows.sort(key=lambda r: (r["name"] == "Без водителя", r["name"]))
    meta = [{"employee_id": r["_eid"], "can_edit": r["_edit"], "kind": r["_kind"]} for r in rows]
    cols = [Col("name", "Водитель"), Col("vehicle", "Техника"), Col("waybill", "Путевой лист", show=bool(sc_way)), Col("odo", "Спидометр", show=bool(sc_way)),
            Col("fuel", "Топливо, л", "dec", True, bool(sc_fuel)), Col("rate_work", "Ставка", show=bool(sc_log)), Col("rate_repair", "Ставка ремонта", show=bool(sc_log)),
            Col("kind", "Тип занятости", show=bool(sc_log)), Col("work_h", "Часы работы", "dec", True, bool(sc_log)), Col("repair_h", "Часы ремонта", "dec", True, bool(sc_log)),
            Col("work", "Работа, ₽", "money", True, bool(sc_log)), Col("repair", "Ремонт, ₽", "money", True, bool(sc_log)), Col("total", "Всего, ₽", "money", True, bool(sc_log))]
    return {"title": f"Отчёт за {day.strftime('%d.%m.%Y')}", "table": table(cols, rows), "chart": None, "extras": [], "day_rows": meta,
            "note": "В отчёте водители, у которых за этот день есть часы работы или ремонта, путевой лист или заправка. Тип занятости показывается по внесённым часам: "
                    "«Работа», «Ремонт» или «Работа и ремонт»; кто может менять часы, меняет тип вручную прямо в таблице. Суммы считаются по ставкам, которые действовали, когда вносили часы."}


def report_salary(c: Ctx) -> dict:
    """Зарплата за месяц по сотрудникам: часы работы и ремонта, суммы по тарифам, итог. Те же записи и ставки, что в разделе «Часы работы
    и ремонта» и в табеле: сумма за день у человека складывается из записей дня, за месяц — из дней. Налоги и вычеты не считаются."""
    db = c.db
    sc = c.sc("worklog.view")
    if not sc:
        raise fail(403, "forbidden", "Нет доступа к часам работы и ремонта")
    logs = list(db.scalars(select(WorkLog).where(WorkLog.work_date >= c.d_from, WorkLog.work_date <= c.d_to, WorkLog.archived_at.is_(None),
                                                 sc.cond(WorkLog.legal_entity_id)).order_by(WorkLog.work_date, WorkLog.id)))
    amt = pay.amounts(db, logs)
    tickets = list(db.scalars(select(WeighTicket).where(WeighTicket.ticket_date >= c.d_from, WeighTicket.ticket_date <= c.d_to, WeighTicket.archived_at.is_(None),
                                                        WeighTicket.driver_id.is_not(None), sc.cond(WeighTicket.legal_entity_id)).order_by(WeighTicket.id)))
    ton_rows = pay.ton_rows(db, tickets)                # водителям с тарифом «за тонну» работа считается по талонам весов
    ton_wait = pay.ton_pending(db, tickets)
    emp_ids = {l.employee_id for l in logs} | {r["ticket"].driver_id for r in ton_rows}
    veh_ids = {l.vehicle_id for l in logs if l.vehicle_id} | {r["ticket"].vehicle_id for r in ton_rows if r["ticket"].vehicle_id}
    emps = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_(emp_ids)))} if emp_ids else {}
    plates = {v.id: label(v) for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(veh_ids)))} if veh_ids else {}
    ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))} if emp_ids else {}
    people: dict[tuple[int, int], dict] = {}
    by_tariff: dict[tuple, dict] = {}
    by_vehicle: dict[int | None, dict] = {}
    no_rate = 0

    def person(eid: int, ent: int) -> dict:
        return people.setdefault((eid, ent), {"days": set(), "work_h": 0.0, "repair_h": 0.0, "work": 0.0, "repair": 0.0, "tons": 0.0})

    for r in ton_rows:
        tk = r["ticket"]
        a = person(tk.driver_id, tk.legal_entity_id)
        a["days"].add(tk.ticket_date)
        a["work"] += float(r["amount"])
        a["tons"] += float(r["tons"])
        t = by_tariff.setdefault((r["tariff"].name, pay.rate_str(r["tariff"].rate, "ton")), {"h": 0.0, "sum": 0.0, "tons": 0.0})
        t["sum"] += float(r["amount"])
        t["tons"] += float(r["tons"])
        v = by_vehicle.setdefault(tk.vehicle_id, {"h": 0.0, "sum": 0.0})
        v["sum"] += float(r["amount"])
    for l in logs:
        a = person(l.employee_id, l.legal_entity_id)
        a["days"].add(l.work_date)
        a[f"{l.kind}_h"] += float(l.hours)
        a[l.kind] += float(amt[l.id])
        t = by_tariff.setdefault((l.tariff_name or "Без тарифа", pay.rate_text(l)), {"h": 0.0, "sum": 0.0, "tons": 0.0})
        t["h"] += float(l.hours)
        t["sum"] += float(amt[l.id])
        v = by_vehicle.setdefault(l.vehicle_id, {"h": 0.0, "sum": 0.0})
        v["h"] += float(l.hours)
        v["sum"] += float(amt[l.id])
        no_rate += 1 if l.tariff_id is None and not l.rate else 0
    many = len({k[1] for k in people}) > 1

    def who(eid: int) -> str:
        e = emps.get(eid)
        return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x) if e else "—"

    cols = [Col("name", "Сотрудник"), Col("ent", "Юрлицо", show=many), Col("days", "Дней с часами", "int", True), Col("work_h", "Часы работы", "dec", True),
            Col("repair_h", "Часы ремонта", "dec", True), Col("hours", "Часов всего", "dec", True), Col("tons", "Тонн", "dec", True, bool(ton_rows)), Col("work", "Работа, ₽", "money", True),
            Col("repair", "Ремонт, ₽", "money", True), Col("total", "К выплате, ₽", "money", True)]
    rows = [{"name": who(eid), "ent": ents.get(ent, ""), "days": len(a["days"]), "work_h": round(a["work_h"], 1), "repair_h": round(a["repair_h"], 1),
             "hours": round(a["work_h"] + a["repair_h"], 1), "tons": round(a["tons"], 3), "work": fl(a["work"]), "repair": fl(a["repair"]), "total": fl(a["work"] + a["repair"])}
            for (eid, ent), a in people.items()]
    rows.sort(key=lambda r: (r["name"].lower(), r["ent"]))
    top = sorted(rows, key=lambda r: -r["total"])[:10]
    ch = chart([short(r["name"].split(" ")[0] + "".join(" " + w[0] + "." for w in r["name"].split(" ")[1:3]), 16) for r in top], [r["total"] for r in top], "₽", [r["name"] for r in top])
    extras = []
    if by_tariff:
        tcols = [{"key": "t", "label": "Тариф", "kind": "text"}, {"key": "r", "label": "Ставка", "kind": "text"}, {"key": "h", "label": "Часов", "kind": "dec", "sum": True}]
        if ton_rows:
            tcols.append({"key": "tn", "label": "Тонн", "kind": "dec", "sum": True})
        tcols.append({"key": "s", "label": "Сумма, ₽", "kind": "money", "sum": True})
        extras.append(extra("По тарифам", tcols, [[k[0], k[1], round(v["h"], 1)] + ([round(v["tons"], 3)] if ton_rows else []) + [fl(v["sum"])]
                                                  for k, v in sorted(by_tariff.items(), key=lambda kv: (-kv[1]["sum"], kv[0]))], c, True))
    if by_vehicle:
        extras.append(extra("По технике", [{"key": "v", "label": "Техника", "kind": "text"}, {"key": "h", "label": "Часов", "kind": "dec", "sum": True},
                                           {"key": "s", "label": "Сумма, ₽", "kind": "money", "sum": True}],
                            [[plates.get(k, "Без техники"), round(v["h"], 1), fl(v["sum"])] for k, v in sorted(by_vehicle.items(), key=lambda kv: (-kv[1]["sum"], str(kv[0])))], c, True))
    note = ("Все записи часов за месяц по дате работы. Сумма за день у человека складывается из его записей (часы × ставка тарифа, суточный тариф "
            "один раз в день и вид), сумма за месяц — из дней. Ставка запоминается при внесении часов. Налоги и вычеты не считаются: суммы до вычета налогов.")
    if ton_rows or ton_wait:
        note += " Водителям с тарифом «за тонну» работа считается по тоннам из талонов весов (тонны × ставка по тарифу сотрудника сейчас), часы у них учитываются, но не оплачиваются."
    if ton_wait:
        note += f" Внимание: не оплачены талоны водителей «за тонну» без тоннажа (их {ton_wait}): внесите вес в «Журнале тоннажа»."
    if no_rate:
        note += f" Внимание: у {no_rate} записей нет тарифа, они считаются как 0 ₽: выберите тариф в разделе «Часы работы и ремонта»."
    return {"title": f"Зарплата за {MONTHS_RU[c.d_from.month - 1]} {c.d_from.year}", "table": table(cols, rows), "chart": ch, "extras": extras, "note": note}


def report_tonnage(c: Ctx) -> dict:
    """Тоннаж за месяц по мусоровозам и самосвалам: сколько ходок и тонн привезла каждая машина. Тонны берутся с талонов весов
    (нетто), которые водители прикладывают в «Тоннаже». Талон без тоннажа считается ходкой, но в тонны не входит: его вес ещё нужно внести
    в «Журнале тоннажа»; такие талоны и возможные повторы одного талона перечислены в отдельной таблице."""
    db = c.db
    sc = c.sc("weigh_tickets.view")
    if not sc:
        raise fail(403, "forbidden", "Нет доступа к тоннажу")
    tickets = list(db.scalars(select(WeighTicket).where(WeighTicket.ticket_date >= c.d_from, WeighTicket.ticket_date <= c.d_to, WeighTicket.archived_at.is_(None),
                                                        sc.cond(WeighTicket.legal_entity_id)).order_by(WeighTicket.ticket_date, WeighTicket.id)))
    flagged = list(db.scalars(select(Vehicle).where(Vehicle.tonnage.is_(True), Vehicle.archived_at.is_(None), sc.cond(Vehicle.legal_entity_id))))
    ids = {t.vehicle_id for t in tickets} | {v.id for v in flagged}
    vehicles = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(ids)))} if ids else {}
    types = {t.id: t.name for t in db.scalars(select(VehicleType))}
    ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
    emps = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_({t.driver_id for t in tickets if t.driver_id})))} if tickets else {}

    def who(eid: int | None) -> str:
        e = emps.get(eid)
        return " ".join(x for x in (e.last_name, e.first_name) if x) if e else "Без водителя"

    by_v: dict[int, dict] = {vid: {"trips": 0, "tons": 0.0, "pending": 0} for vid in ids}
    by_day: dict[dt.date, dict] = {}
    by_driver: dict[int | None, dict] = {}
    twins: dict[tuple[int, str], list[WeighTicket]] = {}
    for t in tickets:
        for a in (by_v[t.vehicle_id], by_day.setdefault(t.ticket_date, {"trips": 0, "tons": 0.0}), by_driver.setdefault(t.driver_id, {"trips": 0, "tons": 0.0})):
            a["trips"] += 1
            a["tons"] += float(t.tons) if t.tons is not None else 0.0
        if t.tons is None:
            by_v[t.vehicle_id]["pending"] += 1
        if t.doc_number:
            twins.setdefault((t.vehicle_id, t.doc_number), []).append(t)
    many = len({vehicles[vid].legal_entity_id for vid in ids if vid in vehicles}) > 1
    cols = [Col("veh", "Машина"), Col("type", "Тип"), Col("ent", "Юрлицо", show=many), Col("trips", "Ходок", "int", True), Col("tons", "Тонн", "ton", True),
            Col("avg", "В среднем за ходку, т", "ton"), Col("pending", "Талонов без тоннажа", "int", True)]
    rows = []
    for vid, a in by_v.items():
        v = vehicles.get(vid)
        if v is None:
            continue
        counted = a["trips"] - a["pending"]
        rows.append({"veh": label(v), "type": types.get(v.type_id, ""), "ent": ents.get(v.legal_entity_id, ""), "trips": a["trips"], "tons": round(a["tons"], 3),
                     "avg": round(a["tons"] / counted, 3) if counted else None, "pending": a["pending"]})
    rows.sort(key=lambda r: (-r["tons"], r["veh"]))
    top = [r for r in rows if r["tons"]][:10]
    ch = chart([short(r["veh"].split(" · ")[0], 12) for r in top], [r["tons"] for r in top], "т", [r["veh"] for r in top])
    extras = []
    if by_day:
        extras.append(extra("По дням", [{"key": "d", "label": "День", "kind": "date"}, {"key": "n", "label": "Ходок", "kind": "int", "sum": True},
                                        {"key": "t", "label": "Тонн", "kind": "ton", "sum": True}],
                            [[d.isoformat(), a["trips"], round(a["tons"], 3)] for d, a in sorted(by_day.items())], c, True))
    if by_driver:
        extras.append(extra("По водителям", [{"key": "w", "label": "Водитель", "kind": "text"}, {"key": "n", "label": "Ходок", "kind": "int", "sum": True},
                                             {"key": "t", "label": "Тонн", "kind": "ton", "sum": True}],
                            [[who(k), a["trips"], round(a["tons"], 3)] for k, a in sorted(by_driver.items(), key=lambda kv: (-kv[1]["tons"], who(kv[0])))], c, True))
    problems = []
    for t in tickets:
        if t.tons is None:
            problems.append([t.ticket_date.isoformat(), label(vehicles[t.vehicle_id]) if t.vehicle_id in vehicles else "", who(t.driver_id), "Тоннаж не внесён"])
    for (vid, number), group in twins.items():
        if len(group) > 1:
            for t in group[1:]:
                problems.append([t.ticket_date.isoformat(), label(vehicles[vid]) if vid in vehicles else "", who(t.driver_id), f"Возможный повтор талона № {number}"])
    problems.sort()
    if problems:
        extras.append(extra("Что проверить", [{"key": "d", "label": "День", "kind": "date"}, {"key": "v", "label": "Машина", "kind": "text"},
                                              {"key": "w", "label": "Водитель", "kind": "text"}, {"key": "p", "label": "Что не так", "kind": "text"}], problems, c))
    pending = sum(a["pending"] for a in by_v.values())
    note = ("Тонны берутся с талонов весов полигона (нетто), которые водители прикладывают во вкладке «Тоннаж»; считаются талоны за выбранный месяц по дате талона, "
            "убранные в архив не учитываются. В таблице машины, по которым ведётся тоннаж, в том числе те, что за месяц ничего не привезли.")
    if pending:
        note += (f" Внимание: у {pending} талонов тоннаж ещё не внесён (программа не смогла уверенно прочитать вес с фото). Они посчитаны как ходки, но тонн в итоге нет: "
                 "откройте «Журнал тоннажа», отберите «Тоннаж не внесён» и впишите вес по фото.")
    return {"title": f"Тоннаж за {MONTHS_RU[c.d_from.month - 1]} {c.d_from.year}", "table": table(cols, rows), "chart": ch, "extras": extras, "note": note}


@dataclass
class Report:
    name: str
    title: str
    period: str                  # month | range | year12
    needs: tuple[str, ...]       # хватает любого из этих прав, чтобы отчёт имел смысл
    build: object
    money: bool = False          # без права на деньги отчёт пуст
    desc: str = ""


REPORTS: dict[str, Report] = {r.name: r for r in (
    Report("month", "Итоги месяца", "month", ("vehicles.view",), report_month, desc="Всё за месяц по каждой машине одной таблицей"),
    Report("mileage", "Пробег по месяцам", "year12", ("vehicles.view",), report_mileage, desc="Сколько проехала каждая машина за 12 месяцев"),
    Report("fuel", "Топливо", "range", ("fuel_fills.view",), report_fuel, desc="Литры, расход на 100 км и сравнение с нормой"),
    Report("costs", "Затраты и доходы по технике", "range", ("vehicles.view",), report_costs, True, "Топливо, ремонт, штрафы, аренда и доходы по машинам"),
    Report("repairs", "Ремонты по месяцам", "year12", ("vehicles.view",), report_repairs, True, "Во что обошёлся ремонт каждой машины по месяцам"),
    Report("waybill_fuel", "Расход по путевым листам", "range", ("waybills.view",), report_waybill_fuel, desc="Расход топлива по закрытым листам и перерасход"),
    Report("downtime", "Простои", "range", ("vehicles.view",), report_downtime, desc="Сколько суток машина в работе, на ремонте, в отпуске"),
    Report("orders", "Заказы по заказчикам", "range", ("orders.view",), report_orders, desc="Заказы и выручка по заказчикам"),
    Report("salary", "Зарплата за месяц", "month", ("worklog.view",), report_salary,
           desc="Сколько заработал каждый сотрудник за месяц по часам работы и ремонта, итог к выплате"),
    Report("tonnage", "Тоннаж за месяц", "month", ("weigh_tickets.view",), report_tonnage,
           desc="Сколько тонн привёз каждый мусоровоз и самосвал за месяц по талонам весов, итог"),
    Report("day", "Отчёт за день", "day", ("worklog.view", "waybills.view", "fuel_fills.view"), report_day,
           desc="По водителям за один день: техника, путевой лист, спидометр, топливо, часы и заработок"),
)}


def available(p: Principal, rep: Report) -> bool:
    if not p.scope("reports.view"):
        return False
    if rep.money and not p.scope("reports.money"):
        return False
    return any(p.scope(perm) for perm in rep.needs)


def month_period(text: str | None, today: dt.date) -> tuple[dt.date, dt.date]:
    if not text:
        return month_bounds(today.year, today.month)
    try:
        y, m = int(text[:4]), int(text[5:7])
        if len(text) != 7 or text[4] != "-" or not (2000 <= y <= 2100 and 1 <= m <= 12):
            raise ValueError
    except ValueError:
        raise fail(422, "bad_period", "Месяц нужно указать как ГГГГ-ММ, например 2026-11", "month") from None
    return month_bounds(y, m)


def single_day(text: str | None, today: dt.date) -> dt.date:
    if not text:
        return today
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise fail(422, "bad_period", "Дата указана неверно: нужен вид ГГГГ-ММ-ДД", "from") from None


def range_period(frm: str | None, to: str | None, today: dt.date) -> tuple[dt.date, dt.date]:
    def one(text, default, field_name):
        if not text:
            return default
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            raise fail(422, "bad_period", "Дата указана неверно: нужен вид ГГГГ-ММ-ДД", field_name) from None
    d_from, d_to = one(frm, dt.date(today.year, 1, 1), "from"), one(to, today, "to")
    if d_to < d_from:
        raise fail(422, "bad_period", "Дата «по» раньше даты «с»", "to")
    if (d_to - d_from).days > MAX_SPAN_DAYS:
        raise fail(422, "bad_period", "Период не длиннее пяти лет", "to")
    return d_from, d_to


def run(db: Session, p: Principal, rep: Report, *, legal_entity_id: int | None, month: str | None, frm: str | None, to: str | None,
        today: dt.date, now: dt.datetime) -> tuple[dict, Ctx]:
    if rep.period == "day":
        d_from = d_to = single_day(frm, today)
    elif rep.period == "range":
        d_from, d_to = range_period(frm, to, today)
    elif rep.period == "month":
        d_from, d_to = month_period(month, today)
    else:
        d_from, d_to = month_period(month, today)
        d_from = month_bounds(*last_months(d_to)[0])[0]
    c = Ctx(db, p, legal_entity_id, d_from, d_to, today, now)
    res = rep.build(c)
    res.update({"name": rep.name, "money": rep.money,
                "period": {"kind": rep.period, "from": d_from.isoformat(), "to": d_to.isoformat(), "month": d_to.strftime("%Y-%m")},
                "hidden": c.hidden})
    return res, c
