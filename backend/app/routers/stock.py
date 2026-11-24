# -*- coding: utf-8 -*-
"""Склад: остатки, поступление, расход, списание, перемещение, расход деталей на обращение, приход по счёту.

Остаток нигде не хранится — он считается по движениям (services/stock.py). Движение нельзя «исправить» правкой количества:
ошибочное убирают в архив с причиной, и остаток пересчитывается. Нельзя сделать движение, после которого остаток станет меньше нуля.
Права смотрятся по юрлицу товара: склад, товар и машина в одном движении должны быть одного юрлица.
"""
import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, page_of, utcnow
from app.db import get_db
from app.exporting import check_format, table_response
from app.deps import Principal, current, entity_filter, require
from app.enums import REQUEST_PARTS_OPEN, STOCK_MAX_QTY, STOCK_MOVE_KINDS, STOCK_UNITS
from app.models import (Counterparty, LegalEntity, RepairBill, RepairBillItem, RequestPart, ServiceRequest, StockItem, StockMove, User, Vehicle,
                        Warehouse)
from app.parts_seed import norm as part_key
from app.registry import org_tz
from app.routers.requests_ import visible as request_visible
from app.services import bills as bills_svc
from app.services import notify
from app.services import parts as parts_svc
from app.services import stock as svc

router = APIRouter(prefix="/stock", tags=["Склад"])
MAX_PAGE = 100
DATE_MIN = dt.date(2000, 1, 1)


# ---------------------------------------------------------------- разное

def today() -> dt.date:
    return dt.datetime.now(org_tz()).date()


def qty_in(value: Any, field: str = "qty") -> Decimal:
    try:
        d = Decimal(str(value if value is not None else "").replace(",", ".").replace(" ", "").replace(" ", ""))
    except InvalidOperation:
        raise fail(422, "bad_qty", "Количество введите числом", field) from None
    if not d.is_finite() or d <= 0 or d > STOCK_MAX_QTY or d.as_tuple().exponent < -3:
        raise fail(422, "bad_qty", "Количество должно быть больше нуля, не больше миллиона и не точнее тысячных", field)
    return d


def day_in(value: dt.date | None) -> dt.date:
    d = value or today()
    if d > today():
        raise fail(422, "bad_date", "Дата не может быть в будущем: остаток считается по сделанным движениям", "move_date")
    if d < DATE_MIN:
        raise fail(422, "bad_date", "Дата слишком старая: проверьте год", "move_date")
    return d


def money_out(v) -> float:
    return float(v) if v is not None else 0.0


def names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {u.id: (u.display_name or u.login) for u in db.scalars(select(User).where(User.id.in_(ids)))}


def vehicle_label(v: Vehicle | None) -> str:
    return f"{v.plate} · {(v.make + ' ' + v.model).strip()}".rstrip(" ·") if v else ""


def need(p: Principal, perm: str, entity_id: int, what: str = "Нет права на это действие в этом юрлице") -> None:
    if not p.can(perm, entity_id):
        raise fail(403, "forbidden", what)


def visible_scope(p: Principal, *perms: str):
    """Юрлица, где человек имеет хотя бы одно из прав."""
    scope = None
    for perm in perms:
        s = p.scope(perm)
        scope = s if scope is None else scope | s
    return scope


def get_move(db: Session, p: Principal, mid: int, perm: str = "stock_moves.view") -> StockMove:
    m = db.get(StockMove, mid)
    if not m or not p.can(perm, m.legal_entity_id):
        raise fail(404, "not_found", "Движение не найдено")
    return m


def live_item(db: Session, p: Principal, item_id: int, field: str = "item_id") -> StockItem:
    """Товар под блокировкой. Чужого юрлица для постороннего «не существует»."""
    item = svc.lock_item(db, item_id)
    if not item or not (p.can("stock_items.view", item.legal_entity_id) or p.can("stock_moves.view", item.legal_entity_id)
                        or p.can("stock_moves.create", item.legal_entity_id)):
        raise fail(404, "not_found", "Такого товара нет", field)
    if item.archived_at:
        raise fail(409, "archived", f"Товар «{item.name}» в архиве: верните его из архива в разделе «Товары»", field)
    return item


def live_warehouse(db: Session, wid: int | None, entity_id: int, field: str) -> Warehouse:
    w = db.get(Warehouse, wid) if wid else None
    if not w or w.legal_entity_id != entity_id:
        raise fail(422, "bad_warehouse", "Выберите склад этого юрлица", field)
    if w.archived_at:
        raise fail(409, "archived", f"Склад «{w.name}» в архиве: верните его из архива в разделе «Склады»", field)
    return w


# ---------------------------------------------------------------- вывод движений

def context(db: Session, moves: list[StockMove]) -> dict:
    ids = lambda attr: {getattr(m, attr) for m in moves if getattr(m, attr)}          # noqa: E731
    items = {i.id: i for i in db.scalars(select(StockItem).where(StockItem.id.in_(ids("item_id"))))} if moves else {}
    wh_ids = ids("warehouse_id") | ids("to_warehouse_id")
    wh = {w.id: w for w in db.scalars(select(Warehouse).where(Warehouse.id.in_(wh_ids)))} if wh_ids else {}
    cp = {c.id: c.name for c in db.scalars(select(Counterparty).where(Counterparty.id.in_(ids("counterparty_id"))))} if ids("counterparty_id") else {}
    veh = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(ids("vehicle_id"))))} if ids("vehicle_id") else {}
    req = {r.id: r.number for r in db.scalars(select(ServiceRequest).where(ServiceRequest.id.in_(ids("request_id"))))} if ids("request_id") else {}
    bills = {b.id: b.number for b in db.scalars(select(RepairBill).where(RepairBill.id.in_(ids("bill_id"))))} if ids("bill_id") else {}
    users = names(db, ids("created_by_id") | ids("archived_by_id"))
    ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
    return {"items": items, "wh": wh, "cp": cp, "veh": veh, "req": req, "bills": bills, "users": users, "ents": ents}


def move_out(p: Principal, m: StockMove, c: dict) -> dict:
    item, w, w2 = c["items"].get(m.item_id), c["wh"].get(m.warehouse_id), c["wh"].get(m.to_warehouse_id)
    free = m.archived_at is None
    return {
        "id": m.id, "legal_entity_id": m.legal_entity_id, "legal_entity": c["ents"].get(m.legal_entity_id, ""), "move_date": m.move_date.isoformat(),
        "kind": m.kind, "kind_label": STOCK_MOVE_KINDS[m.kind], "item_id": m.item_id, "item": item.name if item else "", "sku": item.sku if item else "",
        "unit": item.unit if item else "", "warehouse_id": m.warehouse_id, "warehouse": w.name if w else "", "to_warehouse_id": m.to_warehouse_id,
        "to_warehouse": w2.name if w2 else "", "qty": float(m.qty), "qty_text": svc.qty_text(m.qty), "price": money_out(m.price),
        "amount": float((m.qty * m.price).quantize(Decimal("0.01"))), "counterparty_id": m.counterparty_id, "counterparty": c["cp"].get(m.counterparty_id, ""),
        "vehicle_id": m.vehicle_id, "vehicle": vehicle_label(c["veh"].get(m.vehicle_id)), "request_id": m.request_id,
        "request_number": c["req"].get(m.request_id), "bill_id": m.bill_id, "bill_number": c["bills"].get(m.bill_id), "doc_number": m.doc_number,
        "note": m.note, "archived": not free, "archive_reason": m.archive_reason, "archived_by": c["users"].get(m.archived_by_id),
        "created_by": c["users"].get(m.created_by_id), "created_at": m.created_at.isoformat(),
        "can_edit": free and p.can("stock_moves.edit", m.legal_entity_id), "can_void": free and p.can("stock_moves.void", m.legal_entity_id),
        "can_restore": (not free) and p.can("stock_moves.void", m.legal_entity_id),
    }


# ---------------------------------------------------------------- справочники для форм

@router.get("/options")
def options(legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Всё нужное для формы движения и фильтров: склады, товары с остатками, машины, поставщики. Свои списки — чтобы кладовщику не нужны права на машины."""
    scope = visible_scope(p, "stock_moves.view", "stock_moves.create", "stock_items.view")
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к складу")
    if legal_entity_id is not None and not scope.allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    scope = scope.narrow(legal_entity_id)
    whs = list(db.scalars(select(Warehouse).where(scope.cond(Warehouse.legal_entity_id), Warehouse.archived_at.is_(None)).order_by(Warehouse.name)))
    items = list(db.scalars(select(StockItem).where(scope.cond(StockItem.legal_entity_id), StockItem.archived_at.is_(None))
                            .order_by(func.lower(StockItem.name)).limit(3000)))
    bal = svc.balances(db, entity_cond=scope.cond(StockMove.legal_entity_id))
    by_item: dict[int, list] = {}
    total: dict[int, Decimal] = {}
    for (w, i), q in bal.items():
        by_item.setdefault(i, []).append({"warehouse_id": w, "qty": float(q), "qty_text": svc.qty_text(q)})
        total[i] = total.get(i, Decimal(0)) + q
    writer = bool(p.scope("stock_moves.create") or p.scope("stock_moves.edit"))
    vehicles, suppliers = [], []
    if writer:
        vscope = p.scope("stock_moves.create") | p.scope("stock_moves.edit")
        vq = select(Vehicle).where(vscope.cond(Vehicle.legal_entity_id), Vehicle.archived_at.is_(None)).order_by(Vehicle.plate)
        if legal_entity_id is not None:
            vq = vq.where(Vehicle.legal_entity_id == legal_entity_id)
        vehicles = [{"id": v.id, "label": vehicle_label(v), "legal_entity_id": v.legal_entity_id} for v in db.scalars(vq.limit(1000))]
        suppliers = [{"id": c.id, "label": c.name} for c in db.scalars(select(Counterparty).where(
            Counterparty.archived_at.is_(None), Counterparty.kind.in_(("supplier", "both"))).order_by(Counterparty.name).limit(1000))]
    ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
    return {
        "warehouses": [{"id": w.id, "name": w.name, "legal_entity_id": w.legal_entity_id} for w in whs],
        "items": [{"id": i.id, "name": i.name, "sku": i.sku, "unit": i.unit, "min_qty": float(i.min_qty), "legal_entity_id": i.legal_entity_id,
                   "total": float(total.get(i.id, 0)), "total_text": svc.qty_text(total.get(i.id, 0)), "places": by_item.get(i.id, [])} for i in items],
        "vehicles": vehicles, "suppliers": suppliers, "kinds": [{"code": k, "label": v} for k, v in STOCK_MOVE_KINDS.items()],
        "units": STOCK_UNITS, "entities": {str(k): v for k, v in ents.items() if scope.allows(k)},
        "can": {"create": bool(p.scope("stock_moves.create")), "edit": bool(p.scope("stock_moves.edit")), "void": bool(p.scope("stock_moves.void")),
                "items_create": bool(p.scope("stock_items.create")), "warehouses_create": bool(p.scope("warehouses.create")),
                "moves_view": bool(p.scope("stock_moves.view")), "items_view": bool(p.scope("stock_items.view"))},
    }


# ---------------------------------------------------------------- остатки

def balance_rows(db: Session, p: Principal, *, q: str, warehouse_id: int | None, low: bool, show_empty: bool, legal_entity_id: int | None) -> list[dict]:
    scope = entity_filter(p, "stock_items.view", legal_entity_id)
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к складу")
    query = select(StockItem).where(scope.cond(StockItem.legal_entity_id), StockItem.archived_at.is_(None))
    t = q.strip()
    if t:
        query = query.where(or_(StockItem.name.ilike(like(t)), StockItem.sku.ilike(like(t))))
    items = list(db.scalars(query.order_by(func.lower(StockItem.name)).limit(3000)))
    if not items:
        return []
    bal = svc.balances(db, entity_cond=scope.cond(StockMove.legal_entity_id))
    wh = {w.id: w for w in db.scalars(select(Warehouse).where(scope.cond(Warehouse.legal_entity_id)))}
    if warehouse_id is not None and warehouse_id not in wh:
        raise fail(404, "not_found", "Такого склада нет")
    avg = svc.average_costs(db, [i.id for i in items])
    ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
    places: dict[int, list] = {}
    total: dict[int, Decimal] = {}
    for (w, i), qty in bal.items():
        if w not in wh:
            continue
        total[i] = total.get(i, Decimal(0)) + qty
        places.setdefault(i, []).append((w, qty))
    out = []
    for it in items:
        t_qty = total.get(it.id, Decimal(0))
        is_low = it.min_qty > 0 and t_qty < it.min_qty
        mine = sorted(places.get(it.id, []), key=lambda x: wh[x[0]].name.lower())
        if warehouse_id is not None:
            mine = [x for x in mine if x[0] == warehouse_id]
            if not mine and not (show_empty or is_low):
                continue
        elif not show_empty and not mine and not is_low:
            continue
        if low and not is_low:
            continue
        cost = avg.get(it.id)
        shown = sum((x[1] for x in mine), Decimal(0)) if warehouse_id is not None else t_qty
        out.append({
            "item_id": it.id, "name": it.name, "sku": it.sku, "unit": it.unit, "legal_entity_id": it.legal_entity_id,
            "legal_entity": ents.get(it.legal_entity_id, ""), "min_qty": float(it.min_qty), "min_text": svc.qty_text(it.min_qty),
            "total": float(t_qty), "total_text": svc.qty_text(t_qty), "qty": float(shown), "qty_text": svc.qty_text(shown), "low": is_low,
            "avg_price": float(cost) if cost is not None else None, "value": float((shown * cost).quantize(Decimal("0.01"))) if cost is not None else None,
            "places": [{"warehouse_id": w, "warehouse": wh[w].name, "qty": float(qty), "qty_text": svc.qty_text(qty)} for w, qty in mine],
        })
    return out


@router.get("/balances")
def balances(q: str = "", warehouse_id: int | None = None, low: bool = False, show_empty: bool = False, legal_entity_id: int | None = None,
             p: Principal = Depends(require("stock_items.view")), db: Session = Depends(get_db)):
    rows = balance_rows(db, p, q=q, warehouse_id=warehouse_id, low=low, show_empty=show_empty, legal_entity_id=legal_entity_id)
    scope = entity_filter(p, "stock_items.view", legal_entity_id)
    low_n = sum(1 for r in rows if r["low"])
    if low or warehouse_id is not None or q.strip():                    # «заканчивается» считаем по всему складу, а не по отбору
        low_n = sum(1 for r in balance_rows(db, p, q="", warehouse_id=None, low=True, show_empty=False, legal_entity_id=legal_entity_id))
    return {"items": rows, "totals": {"positions": len(rows), "low": low_n, "value": round(sum(r["value"] or 0 for r in rows), 2)},
            "can": {"create": bool(p.scope("stock_moves.create")), "view_moves": bool(p.scope("stock_moves.view")), "single_entity": bool(scope) and not scope.everything and len(scope.ids) == 1}}


@router.get("/balances/export.{fmt}")
def balances_file(fmt: str, q: str = "", warehouse_id: int | None = None, low: bool = False, show_empty: bool = False, legal_entity_id: int | None = None,
                  p: Principal = Depends(require("stock_items.view")), db: Session = Depends(get_db)):
    check_format(fmt)
    rows = balance_rows(db, p, q=q, warehouse_id=warehouse_id, low=low, show_empty=show_empty, legal_entity_id=legal_entity_id)
    out: list[list] = [["Юрлицо", "Товар", "Артикул", "Единица", "Склад", "Остаток", "Минимум", "Средняя цена, ₽", "Сумма, ₽"]]
    for r in rows:
        for pl in (r["places"] or [{"warehouse": "", "qty": 0}]):
            amount = (Decimal(str(pl["qty"])) * Decimal(str(r["avg_price"]))).quantize(Decimal("0.01")) if r["avg_price"] is not None else None
            out.append([r["legal_entity"] or None, r["name"], r["sku"] or None, r["unit"], pl["warehouse"] or None, pl["qty"], r["min_qty"] or None,
                        r["avg_price"], amount])
    sums = [sum((x[8] for x in out[1:] if x[8] is not None), Decimal(0))]
    if len(out) > 1:
        out.append(["Итого", None, None, None, None, None, None, None, sums[0]])
    audit.log(db, p, "stock.export", "stock_item", None, legal_entity_id, rows=len(out) - 1, what="balances")
    db.commit()
    return table_response(fmt, "stock-balances", "Остатки склада", out, [None, None, None, None, None, "num", "num", "money", "money"])


# ---------------------------------------------------------------- журнал движений

def month_range(month: str) -> tuple[dt.date, dt.date]:
    try:
        y, m = (int(x) for x in month.split("-"))
        start = dt.date(y, m, 1)
    except (ValueError, TypeError):
        raise fail(422, "bad_month", "Месяц укажите как 2026-11") from None
    return start, (dt.date(y + (m == 12), m % 12 + 1, 1))


def move_filters(p: Principal, *, kind: str, month: str, warehouse_id: int | None, item_id: int | None, vehicle_id: int | None, q: str,
                 archived: bool, legal_entity_id: int | None) -> list:
    scope = entity_filter(p, "stock_moves.view", legal_entity_id)
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к движениям склада")
    cond = [scope.cond(StockMove.legal_entity_id)]
    if kind:
        if kind not in STOCK_MOVE_KINDS:
            raise fail(422, "bad_kind", "Неизвестный вид движения", "kind")
        cond.append(StockMove.kind == kind)
    if month:
        a, b = month_range(month)
        cond.append(StockMove.move_date >= a)
        cond.append(StockMove.move_date < b)
    if warehouse_id:
        cond.append(or_(StockMove.warehouse_id == warehouse_id, StockMove.to_warehouse_id == warehouse_id))
    if item_id:
        cond.append(StockMove.item_id == item_id)
    if vehicle_id:
        cond.append(StockMove.vehicle_id == vehicle_id)
    if q.strip():
        pat = like(q.strip())
        cond.append(or_(StockMove.item_id.in_(select(StockItem.id).where(or_(StockItem.name.ilike(pat), StockItem.sku.ilike(pat)))),
                        StockMove.doc_number.ilike(pat), StockMove.note.ilike(pat)))
    cond.append(StockMove.archived_at.is_not(None) if archived else StockMove.archived_at.is_(None))
    return cond


@router.get("/moves")
def list_moves(kind: str = "", month: str = "", warehouse_id: int | None = None, item_id: int | None = None, vehicle_id: int | None = None, q: str = "",
               archived: bool = False, legal_entity_id: int | None = None, page: int = 1, page_size: int = 50,
               p: Principal = Depends(require("stock_moves.view")), db: Session = Depends(get_db)):
    cond = move_filters(p, kind=kind, month=month, warehouse_id=warehouse_id, item_id=item_id, vehicle_id=vehicle_id, q=q, archived=archived,
                        legal_entity_id=legal_entity_id)
    query = select(StockMove).where(*cond).order_by(StockMove.move_date.desc(), StockMove.id.desc())
    rows, total = page_of(db, query, page, min(page_size, MAX_PAGE))
    moves = [r[0] for r in rows]
    c = context(db, moves)
    sums = {k: Decimal(0) for k in STOCK_MOVE_KINDS}
    for k, s in db.execute(select(StockMove.kind, func.sum(StockMove.qty * StockMove.price)).where(*cond).group_by(StockMove.kind)).all():
        sums[k] = Decimal(s or 0)
    return {"items": [move_out(p, m, c) for m in moves], "total": total, "page": page,
            "sums": {k: float(v.quantize(Decimal("0.01"))) for k, v in sums.items()}}


@router.get("/moves/export.{fmt}")
def moves_file(fmt: str, kind: str = "", month: str = "", warehouse_id: int | None = None, item_id: int | None = None, vehicle_id: int | None = None, q: str = "",
               archived: bool = False, legal_entity_id: int | None = None, p: Principal = Depends(require("stock_moves.view")),
               db: Session = Depends(get_db)):
    check_format(fmt)
    cond = move_filters(p, kind=kind, month=month, warehouse_id=warehouse_id, item_id=item_id, vehicle_id=vehicle_id, q=q, archived=archived,
                        legal_entity_id=legal_entity_id)
    moves = list(db.scalars(select(StockMove).where(*cond).order_by(StockMove.move_date.desc(), StockMove.id.desc()).limit(20000)))
    c = context(db, moves)
    out: list[list] = [["Дата", "Вид", "Товар", "Артикул", "Единица", "Склад", "Куда", "Количество", "Цена, ₽", "Сумма, ₽", "Контрагент", "Машина",
                        "Обращение №", "Документ", "Заметка", "В архиве"]]
    for m in moves:
        o = move_out(p, m, c)
        out.append([dt.date.fromisoformat(o["move_date"]), o["kind_label"], o["item"], o["sku"] or None, o["unit"], o["warehouse"] or None,
                    o["to_warehouse"] or None, o["qty"], o["price"], o["amount"], o["counterparty"] or None, o["vehicle"] or None,
                    o["request_number"] or None, o["doc_number"] or None, o["note"] or None, "да" if o["archived"] else None])
    audit.log(db, p, "stock.export", "stock_move", None, legal_entity_id, rows=len(out) - 1, what="moves")
    db.commit()
    return table_response(fmt, "stock-moves", "Движения склада", out, ["date", None, None, None, None, None, None, "num", "money", "money"])


# ---------------------------------------------------------------- создание движения

class NewItemIn(BaseModel):
    """Товара на складе ещё нет: берём деталь из каталога (catalog_id) или вписываем вручную (name, sku, unit)."""
    catalog_id: int | None = None
    name: str = Field(default="", max_length=200)
    sku: str = Field(default="", max_length=60)
    unit: str = Field(default="", max_length=12)


class MoveIn(BaseModel):
    kind: str
    item_id: int | None = None
    new_item: NewItemIn | None = None
    warehouse_id: int
    to_warehouse_id: int | None = None
    qty: Any = None
    price: Any = None
    move_date: dt.date | None = None
    counterparty_id: int | None = None
    vehicle_id: int | None = None
    doc_number: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=300)


def check_links(db: Session, kind: str, entity_id: int, counterparty_id: int | None, vehicle_id: int | None) -> None:
    if counterparty_id is not None:
        if kind != "receipt":
            raise fail(422, "bad_counterparty", "Поставщик указывается только при поступлении", "counterparty_id")
        cp = db.get(Counterparty, counterparty_id)
        if not cp or cp.archived_at or cp.kind not in ("supplier", "both"):
            raise fail(422, "bad_counterparty", "Выберите поставщика из справочника контрагентов", "counterparty_id")
    if vehicle_id is not None:
        if kind not in ("issue", "writeoff"):
            raise fail(422, "bad_vehicle", "Машина указывается только при расходе и списании", "vehicle_id")
        v = db.get(Vehicle, vehicle_id)
        if not v or v.legal_entity_id != entity_id or v.archived_at:
            raise fail(422, "bad_vehicle", "Выберите машину этого юрлица (не из архива)", "vehicle_id")


def add_move(db: Session, p: Principal, item: StockItem, kind: str, wh: Warehouse, to_wh: Warehouse | None, qty: Decimal, price: Decimal | None,
             day: dt.date, **extra) -> StockMove:
    if price is None:                                                   # расход идёт по средней закупочной цене, поступление без цены — по нулю
        price = Decimal("0.00") if kind == "receipt" else svc.average_costs(db, [item.id]).get(item.id, Decimal("0.00"))
    m = StockMove(legal_entity_id=item.legal_entity_id, move_date=day, kind=kind, item_id=item.id, warehouse_id=wh.id,
                  to_warehouse_id=to_wh.id if to_wh else None, qty=qty, price=price, created_by_id=p.id, updated_by_id=p.id, **extra)
    db.add(m)
    db.flush()
    return m


def after_decrease(db: Session, item: StockItem, actor_id: int) -> None:
    """Расход или списание: если общий остаток стал меньше минимального, ответственным приходит уведомление (раз в день на товар)."""
    if item.min_qty <= 0:
        return
    left = svc.totals(db, [item.id]).get(item.id, Decimal(0))
    if left < item.min_qty:
        notify.on_stock_low(db, item, left, actor_id)


def move_result(db: Session, p: Principal, m: StockMove) -> dict:
    c = context(db, [m])
    left = svc.balances(db, item_id=m.item_id)
    where = left.get((m.warehouse_id, m.item_id), Decimal(0))
    return {"move": move_out(p, m, c), "left_here": svc.qty_text(where), "left_total": svc.qty_text(sum(left.values(), Decimal(0)))}


def item_for_receipt(db: Session, p: Principal, body: MoveIn) -> StockItem:
    """Приход товара, которого на складе ещё нет: из каталога деталей или вписанного вручную. Если такой товар в юрлице склада
    уже есть (то же название без учёта регистра и знаков), берётся он; иначе заводится новый (нужно право добавлять товары)."""
    from app.enums import STOCK_UNITS
    from app.models import PartCatalog
    from app.parts_seed import norm as part_key
    from app.services import parts as parts_svc
    if body.kind != "receipt" or body.new_item is None:
        raise fail(422, "no_item", "Выберите товар", "item_id")
    w = db.get(Warehouse, body.warehouse_id) if body.warehouse_id else None
    if not w or not p.can("stock_moves.create", w.legal_entity_id):
        raise fail(422, "bad_warehouse", "Выберите склад", "warehouse_id")
    ni = body.new_item
    name, sku, unit = " ".join(ni.name.split()), ni.sku.strip(), ni.unit.strip()
    if ni.catalog_id is not None:
        c = db.get(PartCatalog, ni.catalog_id)
        if c is None or c.archived_at is not None:
            raise fail(422, "bad_catalog", "Такой детали нет в каталоге", "item_id")
        name, sku, unit = name or c.name, sku or c.article, unit or c.unit
    unit = unit or "шт"
    key = part_key(name)
    if not key:
        raise fail(422, "part_name", "Впишите название товара буквами или цифрами", "new_name")
    if unit not in STOCK_UNITS:
        raise fail(422, "part_unit", f"Единица «{unit}» не из списка: {', '.join(STOCK_UNITS)}", "new_unit")
    found = db.scalar(select(StockItem).where(StockItem.legal_entity_id == w.legal_entity_id, StockItem.name_key == key, StockItem.archived_at.is_(None)))
    if found is not None:
        return live_item(db, p, found.id)
    need(p, "stock_items.create", w.legal_entity_id, "Нет права заводить новые товары: попросите кладовщика или директора")
    item = StockItem(legal_entity_id=w.legal_entity_id, name=name, name_key=key, sku=sku, unit=unit, created_by_id=p.id, updated_by_id=p.id)
    db.add(item)
    db.flush()
    parts_svc.learn(db, [{"name": name, "article": sku, "unit": unit}])     # вписанное вручную попадает в каталог для следующих раз
    audit.log(db, p, "stock_items.create", "stock_items", item.id, item.legal_entity_id, name=name, source="каталог" if ni.catalog_id else "вручную")
    return live_item(db, p, item.id)


@router.post("/moves", status_code=201)
def create_move(body: MoveIn, p: Principal = Depends(require("stock_moves.create")), db: Session = Depends(get_db)):
    if body.kind not in STOCK_MOVE_KINDS:
        raise fail(422, "bad_kind", "Выберите вид движения: поступление, расход, списание или перемещение", "kind")
    item = item_for_receipt(db, p, body) if body.item_id is None else live_item(db, p, body.item_id)
    need(p, "stock_moves.create", item.legal_entity_id)
    qty = qty_in(body.qty)
    price = None if body.price in (None, "") else bills_svc.to_money(body.price, what="Цена", field="price")
    day = day_in(body.move_date)
    wh = live_warehouse(db, body.warehouse_id, item.legal_entity_id, "warehouse_id")
    to_wh = None
    if body.kind == "transfer":
        to_wh = live_warehouse(db, body.to_warehouse_id, item.legal_entity_id, "to_warehouse_id")
        if to_wh.id == wh.id:
            raise fail(422, "same_warehouse", "Выберите другой склад: перемещать на тот же склад нельзя", "to_warehouse_id")
    elif body.to_warehouse_id is not None:
        raise fail(422, "bad_warehouse", "Второй склад нужен только при перемещении", "to_warehouse_id")
    check_links(db, body.kind, item.legal_entity_id, body.counterparty_id, body.vehicle_id)
    m = add_move(db, p, item, body.kind, wh, to_wh, qty, price, day, counterparty_id=body.counterparty_id, vehicle_id=body.vehicle_id,
                 doc_number=body.doc_number.strip(), note=body.note.strip())
    if body.kind != "receipt":
        svc.ensure_not_negative(db, svc.pairs_of(m), "qty")
    audit.log(db, p, f"stock.{body.kind}", "stock_move", m.id, m.legal_entity_id, item=item.name, qty=svc.qty_text(qty), unit=item.unit,
              warehouse=wh.name, to_warehouse=to_wh.name if to_wh else None)
    if body.kind in ("issue", "writeoff"):
        after_decrease(db, item, p.id)
    db.commit()
    return move_result(db, p, m)


# ---------------------------------------------------------------- правка описания

class MovePatch(BaseModel):
    move_date: dt.date | None = None
    price: Any = None
    counterparty_id: int | None = None
    vehicle_id: int | None = None
    doc_number: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=300)


@router.patch("/moves/{mid}")
def patch_move(mid: int, body: MovePatch, p: Principal = Depends(require("stock_moves.edit")), db: Session = Depends(get_db)):
    """Количество, товар и склад не меняются: ошибочное движение убирают в архив и делают заново. Здесь только описание."""
    m = get_move(db, p, mid, "stock_moves.edit")
    if m.archived_at:
        raise fail(409, "archived", "Движение в архиве: сначала верните его из архива")
    sent = body.model_fields_set
    changes: dict[str, list] = {}

    def put(field: str, new):
        old = getattr(m, field)
        if old != new:
            changes[field] = [str(old) if old is not None else None, str(new) if new is not None else None]
            setattr(m, field, new)

    if "move_date" in sent and body.move_date is not None:
        put("move_date", day_in(body.move_date))
    if "price" in sent and body.price not in (None, ""):
        put("price", bills_svc.to_money(body.price, what="Цена", field="price"))
    if "counterparty_id" in sent or "vehicle_id" in sent:
        cp = body.counterparty_id if "counterparty_id" in sent else m.counterparty_id
        veh = body.vehicle_id if "vehicle_id" in sent else m.vehicle_id
        check_links(db, m.kind, m.legal_entity_id, cp if cp != m.counterparty_id else None, veh if veh != m.vehicle_id else None)
        put("counterparty_id", cp)
        put("vehicle_id", veh)
    if body.doc_number is not None:
        put("doc_number", body.doc_number.strip())
    if body.note is not None:
        put("note", body.note.strip())
    if changes:
        m.updated_by_id, m.updated_at = p.id, utcnow()
        audit.log(db, p, "stock_moves.update", "stock_move", m.id, m.legal_entity_id, changes=changes)
        db.commit()
    return move_out(p, m, context(db, [m]))


# ---------------------------------------------------------------- архив движения

class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def recheck(db: Session, pairs: set[tuple[int, int]], what: str) -> None:
    """Проверка остатков после убирания или возврата движения; ошибка — с объяснением, почему так нельзя."""
    try:
        svc.ensure_not_negative(db, pairs, "reason")
    except HTTPException as e:
        d = e.detail
        raise fail(409, "shortage", f"Нельзя {what}: {d['message'][0].lower() + d['message'][1:]}", None) from None


def request_of(db: Session, m: StockMove) -> tuple[ServiceRequest | None, RequestPart | None]:
    r = db.get(ServiceRequest, m.request_id) if m.request_id else None
    part = db.get(RequestPart, m.request_part_id) if m.request_part_id else None
    return r, part


@router.post("/moves/{mid}/void")
def void_move(mid: int, body: ReasonIn, p: Principal = Depends(require("stock_moves.void")), db: Session = Depends(get_db)):
    m = get_move(db, p, mid, "stock_moves.void")
    if m.archived_at:
        raise fail(409, "archived", "Движение уже в архиве")
    svc.lock_item(db, m.item_id)
    r, part = request_of(db, m)
    if r and part and part.bought_at and r.status not in REQUEST_PARTS_OPEN:
        raise fail(409, "request_closed", f"Обращение № {r.number} уже выполнено или закрыто: деталь установлена. Если ошиблись, сделайте поступление")
    m.archived_at, m.archived_by_id, m.archive_reason = utcnow(), p.id, body.reason.strip()
    m.updated_by_id = p.id
    db.flush()
    recheck(db, svc.pairs_of(m), "убрать это движение")
    if r and part and part.bought_at and part.removed_at is None and r.status in REQUEST_PARTS_OPEN:
        part.bought_at = part.bought_by_id = None
        parts_svc._event(db, r, p, "edit", f"Расход со склада отменён: {part.name}")
        r.updated_by_id, r.updated_at = p.id, utcnow()
        parts_svc.after_unbought(db, r, p)
    audit.log(db, p, "stock_moves.void", "stock_move", m.id, m.legal_entity_id, reason=m.archive_reason, kind=m.kind, qty=svc.qty_text(m.qty))
    db.commit()
    return move_result(db, p, m)


@router.post("/moves/{mid}/restore")
def restore_move(mid: int, p: Principal = Depends(require("stock_moves.void")), db: Session = Depends(get_db)):
    m = get_move(db, p, mid, "stock_moves.void")
    if not m.archived_at:
        raise fail(409, "not_archived", "Движение и так действует")
    item = live_item(db, p, m.item_id)
    live_warehouse(db, m.warehouse_id, m.legal_entity_id, "warehouse_id")
    if m.to_warehouse_id:
        live_warehouse(db, m.to_warehouse_id, m.legal_entity_id, "to_warehouse_id")
    r, part = request_of(db, m)
    if r and part:
        if r.status not in REQUEST_PARTS_OPEN or part.removed_at or part.bought_at:
            raise fail(409, "request_changed", f"Эту выдачу уже нельзя вернуть: деталь в обращении № {r.number} изменилась или обращение закрыто")
    m.archived_at = m.archived_by_id = None
    m.archive_reason = ""
    m.updated_by_id = p.id
    db.flush()
    recheck(db, svc.pairs_of(m), "вернуть это движение")
    if r and part:
        part.bought_at, part.bought_by_id = utcnow(), p.id
        parts_svc._event(db, r, p, "edit", f"Со склада: {part.name} — {svc.qty_text(m.qty)} {part.unit}")
        r.updated_by_id, r.updated_at = p.id, utcnow()
        db.flush()
        parts_svc.after_bought(db, r, p)
    audit.log(db, p, "stock_moves.restore", "stock_move", m.id, m.legal_entity_id, kind=m.kind, qty=svc.qty_text(m.qty))
    if m.kind in ("issue", "writeoff"):
        after_decrease(db, item, p.id)
    db.commit()
    return move_result(db, p, m)


# ---------------------------------------------------------------- деталь из обращения — со склада

class IssueIn(BaseModel):
    part_id: int
    item_id: int
    warehouse_id: int


def part_context(db: Session, p: Principal, part_id: int) -> tuple[RequestPart, ServiceRequest]:
    part = db.get(RequestPart, part_id)
    r = db.get(ServiceRequest, part.request_id) if part else None
    if not part or part.removed_at or not r or not request_visible(p, r):
        raise fail(404, "not_found", "Такой детали в обращении нет")
    need(p, "stock_moves.create", r.legal_entity_id, "Нет права брать детали со склада")
    need(p, "purchases.mark", r.legal_entity_id, "Нет права отмечать детали полученными")
    return part, r


@router.get("/for-request/{rid}")
def for_request(rid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Что из деталей обращения есть на складе: по каждой строке — подходящий товар и где он лежит."""
    r = db.get(ServiceRequest, rid)
    if not r or not request_visible(p, r):
        raise fail(404, "not_found", "Обращение не найдено")
    if not (p.can("stock_moves.create", r.legal_entity_id) and p.can("purchases.mark", r.legal_entity_id) and p.can("stock_items.view", r.legal_entity_id)):
        return {"allowed": False, "closed": False, "items": []}
    if r.status not in REQUEST_PARTS_OPEN:
        return {"allowed": True, "closed": True, "items": []}
    out = []
    for part in parts_svc.live_parts(db, r.id):
        if part.bought_at:
            continue
        item = svc.find_item(db, r.legal_entity_id, part.name, part.article)
        places = svc.stock_for(db, r.legal_entity_id, item.id) if item else []
        for pl in places:
            pl["enough"] = Decimal(str(pl["qty"])) >= part.qty
            pl["qty_text"] = svc.qty_text(pl["qty"])
        unit_ok = bool(item) and item.unit == part.unit
        out.append({"part_id": part.id, "name": part.name, "qty_text": svc.qty_text(part.qty), "unit": part.unit,
                    "item": {"id": item.id, "name": item.name, "unit": item.unit} if item else None, "unit_ok": unit_ok,
                    "places": places if unit_ok else [], "can_issue": unit_ok and any(x["enough"] for x in places)})
    return {"allowed": True, "closed": False, "items": out}


@router.post("/issue-to-request", status_code=201)
def issue_to_request(body: IssueIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    part, r = part_context(db, p, body.part_id)
    if r.status not in REQUEST_PARTS_OPEN:
        raise fail(409, "request_closed", f"Обращение № {r.number} уже выполнено или закрыто: список деталей не меняется")
    item = live_item(db, p, body.item_id)
    if item.legal_entity_id != r.legal_entity_id:
        raise fail(422, "bad_item", "Этот товар лежит в другом юрлице", "item_id")
    db.refresh(part)
    if part.bought_at or part.removed_at:
        raise fail(409, "part_bought", "Эта деталь уже отмечена полученной: обновите страницу")
    if item.unit != part.unit:
        raise fail(422, "bad_unit", f"Единицы не совпадают: в обращении «{part.unit}», на складе «{item.unit}»", "item_id")
    wh = live_warehouse(db, body.warehouse_id, r.legal_entity_id, "warehouse_id")
    m = add_move(db, p, item, "issue", wh, None, part.qty, None, today(), vehicle_id=r.vehicle_id, request_id=r.id, request_part_id=part.id,
                 note=f"Обращение № {r.number}: {part.name}"[:300])
    svc.ensure_not_negative(db, svc.pairs_of(m), "warehouse_id")
    part.bought_at, part.bought_by_id = utcnow(), p.id
    parts_svc._event(db, r, p, "edit", f"Со склада «{wh.name}»: {part.name} — {svc.qty_text(part.qty)} {part.unit}")
    r.updated_by_id, r.updated_at = p.id, utcnow()
    db.flush()
    moved = parts_svc.after_bought(db, r, p)
    audit.log(db, p, "stock.issue_request", "stock_move", m.id, m.legal_entity_id, number=r.number, part=part.name, qty=svc.qty_text(part.qty),
              warehouse=wh.name)
    notify.on_parts_from_stock(db, r, part.name, p.id)
    after_decrease(db, item, p.id)
    db.commit()
    return {**move_result(db, p, m), "advanced": moved}


# ---------------------------------------------------------------- приход по счёту

class ReceiveIn(BaseModel):
    warehouse_id: int


def bill_context(db: Session, p: Principal, bill_id: int, *, lock: bool = False) -> RepairBill:
    q = select(RepairBill).where(RepairBill.id == bill_id)
    b = db.scalar(q.with_for_update() if lock else q)
    if not b or not p.can("bills.view", b.legal_entity_id):
        raise fail(404, "not_found", "Счёт не найден")
    need(p, "stock_moves.create", b.legal_entity_id, "Нет права принимать товар на склад")
    return b


def bill_lines(db: Session, b: RepairBill) -> list[dict]:
    out = []
    for i in db.scalars(select(RepairBillItem).where(RepairBillItem.bill_id == b.id).order_by(RepairBillItem.pos)):
        goods = i.unit in STOCK_UNITS
        item = svc.find_item(db, b.legal_entity_id, i.name, i.article) if goods else None
        out.append({"pos": i.pos, "name": i.name, "article": i.article, "qty": float(i.qty), "qty_text": svc.qty_text(i.qty), "unit": i.unit,
                    "price": money_out(i.price), "goods": goods, "item": {"id": item.id, "name": item.name, "unit": item.unit} if item else None,
                    "action": "skip" if not goods else ("existing" if item else "new"),
                    "unit_clash": bool(item and item.unit != i.unit)})
    return out


def posted_moves(db: Session, bill_id: int) -> list[StockMove]:
    return list(db.scalars(select(StockMove).where(StockMove.bill_id == bill_id, StockMove.archived_at.is_(None)).order_by(StockMove.id)))


@router.get("/bill/{bill_id}")
def bill_preview(bill_id: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    b = bill_context(db, p, bill_id)
    lines = bill_lines(db, b)
    done = posted_moves(db, b.id)
    reason = ""
    if b.archived_at:
        reason = "Счёт в архиве"
    elif b.status == "cancelled":
        reason = "Счёт отменён"
    elif not any(x["goods"] for x in lines):
        reason = "В счёте нет строк с товаром: услуги и работы на склад не принимаются"
    elif done:
        reason = "Этот счёт уже принят на склад"
    return {"bill": {"id": b.id, "number": b.number, "bill_date": b.bill_date.isoformat(), "supplier": b.supplier_name, "kind": b.kind},
            "lines": lines, "posted": len(done), "can_post": not reason, "reason": reason}


@router.post("/receive-bill/{bill_id}", status_code=201)
def receive_bill(bill_id: int, body: ReceiveIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    b = bill_context(db, p, bill_id, lock=True)
    if b.archived_at or b.status == "cancelled":
        raise fail(409, "bill_closed", "Счёт в архиве или отменён: принять по нему товар нельзя")
    if posted_moves(db, b.id):
        raise fail(409, "bill_posted", "Этот счёт уже принят на склад. Если ошиблись, уберите движения в архив в журнале склада")
    wh = live_warehouse(db, body.warehouse_id, b.legal_entity_id, "warehouse_id")
    rows = [i for i in db.scalars(select(RepairBillItem).where(RepairBillItem.bill_id == b.id).order_by(RepairBillItem.pos)) if i.unit in STOCK_UNITS]
    if not rows:
        raise fail(422, "no_goods", "В счёте нет строк с товаром: услуги и работы на склад не принимаются")
    if any(svc.find_item(db, b.legal_entity_id, i.name, i.article) is None for i in rows):
        need(p, "stock_items.create", b.legal_entity_id, "В счёте есть товары, которых ещё нет на складе: завести новые товары может кладовщик или директор")
    created, skipped, day = 0, [], today()
    for i in rows:
        item = svc.find_item(db, b.legal_entity_id, i.name, i.article)
        if item and item.unit != i.unit:
            skipped.append(f"{i.name}: на складе «{item.unit}», в счёте «{i.unit}»")
            continue
        if not item:
            item = StockItem(legal_entity_id=b.legal_entity_id, name=" ".join(i.name.split())[:200], name_key=part_key(i.name), sku=i.article[:60],
                             unit=i.unit, created_by_id=p.id, updated_by_id=p.id)
            db.add(item)
            db.flush()
            created += 1
        add_move(db, p, item, "receipt", wh, None, i.qty, i.price, day, counterparty_id=b.supplier_id, bill_id=b.id,
                 doc_number=f"Счёт {b.number}"[:60], note=f"Счёт № {b.number} от {b.bill_date.strftime('%d.%m.%Y')}")
    posted = len(rows) - len(skipped)
    if not posted:
        raise fail(422, "nothing_posted", "Ничего не принято: " + "; ".join(skipped), None)
    audit.log(db, p, "stock.receive_bill", "repair_bill", b.id, b.legal_entity_id, number=b.number, lines=posted, created_items=created, warehouse=wh.name)
    db.commit()
    return {"posted": posted, "created_items": created, "skipped": skipped}
