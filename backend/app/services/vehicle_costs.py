# -*- coding: utf-8 -*-
"""«Затраты на спецтехнику»: все затраты по машинам за месяц из всех разделов одним экраном.

Топливо (литры и сумма) из журнала заправок, ремонт по счетам и по обращениям без счёта, детали со склада (выдача и списание на машину),
штрафы, которые платит компания, страховка (стоимость полисов ОСАГО и КАСКО в личном деле техники) и прочие затраты, внесённые вручную.
Правила те же, что в таблице «Затраты по статьям» отчёта «Итоги и затраты по технике»: счета, по которым детали пришли на склад, не считаются, считается выдача со склада."""
import datetime as dt

from sqlalchemy import exists, func, select

from app.enums import INSURANCE_DOC_KINDS
from app.models import (DossierDoc, Fine, FuelFill, RepairBill, ServiceRequest, StockItem, StockMove, Vehicle, VehicleFinance, VehicleType)
from app.registry import _vehicle_label
from app.services import reports as rep
from app.services.reminders import local_day

# (код, название, пояснение)
CATS: tuple[tuple[str, str, str], ...] = (
    ("fuel", "Топливо", "Заправки из журнала заправок: литры и сумма."),
    ("bills_net", "Ремонт по счетам", "Счета на ремонт с машиной, кроме закупок деталей на склад."),
    ("requests", "Ремонт по обращениям без счёта", "Фактическая стоимость закрытых обращений, у которых нет счёта."),
    ("stock", "Детали со склада", "Выдано и списано на машину со склада, по закупочной цене."),
    ("fines", "Штрафы", "Только те, что платит компания."),
    ("insurance", "Страховка", "Стоимость полисов ОСАГО и КАСКО из личного дела техники, по дате выдачи."),
    ("other", "Прочие затраты", "Внесённые вручную на этой странице: мойка, стоянка, шиномонтаж и другое."),
)
CAT_LABELS = {k: label for k, label, _ in CATS}
ITEMS_LIMIT = 500


class Ctx(rep.Ctx):
    """Машины и прочие затраты видит тот, у кого есть право на «Затраты на спецтехнику». Топливо, счета, склад, штрафы и страховку —
    только тот, у кого ещё и право видеть деньги в отчётах (у механика его нет)."""

    @property
    def vf(self):
        return self.sc("vehicle_finance.view")


def vehicles(c: Ctx, vehicle_id: int | None) -> list[Vehicle]:
    q = (select(Vehicle).join(VehicleType, VehicleType.id == Vehicle.type_id)
         .where(c.vf.cond(Vehicle.legal_entity_id), Vehicle.archived_at.is_(None), VehicleType.category != "trailer")
         .order_by(Vehicle.plate, Vehicle.id))
    if vehicle_id:
        q = q.where(Vehicle.id == vehicle_id)
    return list(c.db.scalars(q))


def _to_stock():
    return exists().where(StockMove.bill_id == RepairBill.id, StockMove.kind == "receipt", StockMove.archived_at.is_(None))


def overview(c: Ctx, vehicle_id: int | None = None) -> dict:
    a, b = c.d_from, c.d_to
    sc, vf, db = c.money, c.vf, c.db
    full = bool(sc)
    vs = vehicles(c, vehicle_id)
    ids = [v.id for v in vs]
    names = {v.id: _vehicle_label(v) for v in vs}
    parts = rep.money_parts(c, ids) if ids and full else {}
    parts["other"] = rep.grouped(c, VehicleFinance.vehicle_id, VehicleFinance.amount, VehicleFinance.vehicle_id.in_(ids or [0]), VehicleFinance.kind == "expense",
                                 VehicleFinance.archived_at.is_(None), vf.cond(VehicleFinance.legal_entity_id), VehicleFinance.op_date >= a, VehicleFinance.op_date <= b)
    liters = {} if not full else {vid: float(x or 0) for vid, x in db.execute(
        select(FuelFill.vehicle_id, func.sum(FuelFill.liters)).where(FuelFill.vehicle_id.in_(ids or [0]), FuelFill.archived_at.is_(None),
                                                                     sc.cond(FuelFill.legal_entity_id), FuelFill.fill_date >= a, FuelFill.fill_date <= b)
        .group_by(FuelFill.vehicle_id))}

    rows = []
    for v in vs:
        r = {"vehicle_id": v.id, "vehicle": names[v.id], "liters": round(liters.get(v.id, 0.0), 1)}
        r.update({k: round(parts.get(k, {}).get(v.id, 0.0), 2) for k in CAT_LABELS})
        r["total"] = round(sum(r[k] for k in CAT_LABELS), 2)
        if r["total"] or r["liters"]:
            rows.append(r)
    rows.sort(key=lambda r: -r["total"])
    totals = {k: round(sum(r[k] for r in rows), 2) for k in CAT_LABELS}

    # журнал: каждая операция из своего раздела
    items: list[dict] = []
    p = c.p

    def add(cat: str, day: dt.date, title: str, sub: str, amount, vid: int | None, *, link: str = "", qty: str = ""):
        items.append({"cat": cat, "cat_label": CAT_LABELS[cat], "date": day.isoformat(), "title": title, "sub": sub or "", "qty": qty,
                      "amount": round(float(amount or 0), 2), "vehicle_id": vid, "vehicle": names.get(vid, ""), "link": link})

    if ids and full:
        for x in db.scalars(select(FuelFill).where(FuelFill.vehicle_id.in_(ids), FuelFill.archived_at.is_(None), sc.cond(FuelFill.legal_entity_id),
                                                   FuelFill.fill_date >= a, FuelFill.fill_date <= b)):
            add("fuel", x.fill_date, "Заправка", x.station, x.amount, x.vehicle_id, qty=f"{float(x.liters):g} л".replace(".", ","),
                link="/fuel-fills" if p.can("fuel_fills.view") else "")
        for x in db.scalars(select(RepairBill).where(RepairBill.vehicle_id.in_(ids), RepairBill.archived_at.is_(None), ~_to_stock(),
                                                     sc.cond(RepairBill.legal_entity_id), RepairBill.bill_date >= a, RepairBill.bill_date <= b)):
            add("bills_net", x.bill_date, f"Счёт № {x.number}", x.supplier_name or x.basis, x.total, x.vehicle_id, link=f"/bills/{x.id}" if p.can("bills.view") else "")
        has_bill = exists().where(RepairBill.request_id == ServiceRequest.id, RepairBill.archived_at.is_(None))
        for x in db.scalars(select(ServiceRequest).where(ServiceRequest.vehicle_id.in_(ids), ServiceRequest.status == "closed", ServiceRequest.cost_actual.is_not(None),
                                                         ~has_bill, sc.cond(ServiceRequest.legal_entity_id), ServiceRequest.closed_at >= c.start,
                                                         ServiceRequest.closed_at < c.end)):
            add("requests", local_day(x.closed_at), f"Обращение № {x.number}", x.title, x.cost_actual, x.vehicle_id,
                link=f"/requests/{x.id}" if p.can("requests.view") else "")
        for x, item in db.execute(select(StockMove, StockItem).join(StockItem, StockItem.id == StockMove.item_id).where(
                StockMove.vehicle_id.in_(ids), StockMove.archived_at.is_(None), StockMove.kind.in_(("issue", "writeoff")),
                sc.cond(StockMove.legal_entity_id), StockMove.move_date >= a, StockMove.move_date <= b)):
            add("stock", x.move_date, item.name, "выдано со склада" if x.kind == "issue" else "списано", x.qty * x.price, x.vehicle_id,
                qty=f"{float(x.qty):g} {item.unit}".replace(".", ","), link="/stock/moves" if p.can("stock_moves.view") else "")
        for x in db.scalars(select(Fine).where(Fine.vehicle_id.in_(ids), Fine.archived_at.is_(None), Fine.payer == "company", sc.cond(Fine.legal_entity_id),
                                               Fine.fine_date >= a, Fine.fine_date <= b)):
            add("fines", x.fine_date, "Штраф" + (f" № {x.resolution_number}" if x.resolution_number else ""), x.article, x.amount, x.vehicle_id,
                link="/fines" if p.can("fines.view") else "")
        day = func.coalesce(DossierDoc.issued_on, func.date(DossierDoc.created_at))
        for x, d in db.execute(select(DossierDoc, day).join(Vehicle, Vehicle.id == DossierDoc.vehicle_id).where(
                DossierDoc.vehicle_id.in_(ids), sc.cond(Vehicle.legal_entity_id), DossierDoc.kind.in_(INSURANCE_DOC_KINDS), DossierDoc.cost.is_not(None),
                                                             DossierDoc.archived_at.is_(None), day >= a, day <= b)):
            add("insurance", d, x.title or x.kind.upper(), f"№ {x.doc_number}" if x.doc_number else "", x.cost, x.vehicle_id,
                link=f"/dossier/vehicles/{x.vehicle_id}" if p.can("vehicle_files.view") else "")
    if ids:
        for x in db.scalars(select(VehicleFinance).where(VehicleFinance.vehicle_id.in_(ids), VehicleFinance.kind == "expense", VehicleFinance.archived_at.is_(None),
                                                         vf.cond(VehicleFinance.legal_entity_id), VehicleFinance.op_date >= a, VehicleFinance.op_date <= b)):
            add("other", x.op_date, x.category or "Затрата", x.description, x.amount, x.vehicle_id)
    items.sort(key=lambda r: (r["date"], r["amount"]), reverse=True)
    counts: dict[str, int] = {}
    for r in items:
        counts[r["cat"]] = counts.get(r["cat"], 0) + 1
    return {
        "from": a.isoformat(), "to": b.isoformat(), "total": round(sum(totals.values()), 2), "liters": round(sum(r["liters"] for r in rows), 1),
        "categories": [{"key": k, "label": label, "hint": hint, "amount": totals[k], "count": counts.get(k, 0)} for k, label, hint in CATS],
        "vehicles": rows, "items": items[:ITEMS_LIMIT], "found": len(items), "full": full,
        "vehicle_options": [{"id": v.id, "label": names[v.id]} for v in vehicles(c, None)] if vehicle_id else [{"id": v.id, "label": names[v.id]} for v in vs],
    }
