# -*- coding: utf-8 -*-
"""Склад: остатки по движениям, проверка «не уйти в минус», средняя цена, товар по названию детали.

Остаток никогда не хранится отдельно: он считается по движениям, которые не убраны в архив. Поэтому его нельзя «испортить» правкой:
ошибочное движение убирают в архив с причиной, и остаток пересчитывается. Перед каждым движением товар блокируется в базе,
чтобы два человека одновременно не списали одну и ту же деталь.
"""
from decimal import Decimal

from sqlalchemy import case, func, select, union_all
from sqlalchemy.orm import Session

from app.common import fail
from app.models import StockItem, StockMove, Warehouse
from app.parts_seed import norm

ZERO = Decimal(0)


def qty_text(v: Decimal | float | int | None) -> str:
    """12 или 12,5 или 0,125 — без хвоста из нулей."""
    d = Decimal(str(v or 0)).quantize(Decimal("0.001")).normalize()
    s = f"{d:f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s).replace(".", ",") or "0"


def _deltas(*, entity_cond=None, item_id: int | None = None, warehouse_id: int | None = None, exclude_move_id: int | None = None):
    """Все изменения остатка: (склад, товар, +/- количество) по движениям не в архиве."""
    live = StockMove.archived_at.is_(None)
    base = [live]
    if entity_cond is not None:
        base.append(entity_cond)
    if item_id is not None:
        base.append(StockMove.item_id == item_id)
    if exclude_move_id is not None:
        base.append(StockMove.id != exclude_move_id)
    plus_minus = select(StockMove.warehouse_id.label("wh"), StockMove.item_id.label("item"),
                        case((StockMove.kind == "receipt", StockMove.qty), else_=-StockMove.qty).label("d")).where(*base)
    incoming = select(StockMove.to_warehouse_id.label("wh"), StockMove.item_id.label("item"), StockMove.qty.label("d")).where(
        *base, StockMove.kind == "transfer")
    if warehouse_id is not None:
        plus_minus = plus_minus.where(StockMove.warehouse_id == warehouse_id)
        incoming = incoming.where(StockMove.to_warehouse_id == warehouse_id)
    return union_all(plus_minus, incoming).subquery()


def balances(db: Session, *, entity_cond=None, item_id: int | None = None, warehouse_id: int | None = None,
             exclude_move_id: int | None = None) -> dict[tuple[int, int], Decimal]:
    """{(склад, товар): остаток}. Нулевые остатки не возвращаются."""
    d = _deltas(entity_cond=entity_cond, item_id=item_id, warehouse_id=warehouse_id, exclude_move_id=exclude_move_id)
    rows = db.execute(select(d.c.wh, d.c.item, func.sum(d.c.d)).group_by(d.c.wh, d.c.item)).all()
    return {(w, i): Decimal(q) for w, i, q in rows if q}


def balance(db: Session, warehouse_id: int, item_id: int, exclude_move_id: int | None = None) -> Decimal:
    return balances(db, item_id=item_id, warehouse_id=warehouse_id, exclude_move_id=exclude_move_id).get((warehouse_id, item_id), ZERO)


def totals(db: Session, item_ids: list[int]) -> dict[int, Decimal]:
    """Остаток товара по всем складам (для «заканчивается»)."""
    if not item_ids:
        return {}
    d = _deltas()
    rows = db.execute(select(d.c.item, func.sum(d.c.d)).where(d.c.item.in_(item_ids)).group_by(d.c.item)).all()
    return {i: Decimal(q) for i, q in rows}


def average_costs(db: Session, item_ids: list[int] | None = None) -> dict[int, Decimal]:
    """Средневзвешенная закупочная цена товара по поступлениям с ценой (без архива)."""
    q = (select(StockMove.item_id, func.sum(StockMove.qty * StockMove.price) / func.sum(StockMove.qty))
         .where(StockMove.kind == "receipt", StockMove.price > 0, StockMove.archived_at.is_(None)).group_by(StockMove.item_id))
    if item_ids is not None:
        q = q.where(StockMove.item_id.in_(item_ids))
    return {i: Decimal(c).quantize(Decimal("0.01")) for i, c in db.execute(q).all() if c}


def lock_item(db: Session, item_id: int) -> StockItem | None:
    """Блокирует товар до конца операции: два движения одного товара идут по очереди."""
    return db.scalar(select(StockItem).where(StockItem.id == item_id).with_for_update())


def shortage_text(db: Session, warehouse_id: int, item_id: int, left: Decimal) -> str:
    w = db.get(Warehouse, warehouse_id)
    i = db.get(StockItem, item_id)
    return f"На складе «{w.name}» не хватает «{i.name}»: нужно ещё {qty_text(-left)} {i.unit}"


def ensure_not_negative(db: Session, pairs: set[tuple[int, int]], field: str = "qty") -> None:
    """После изменения ни у одной пары «склад — товар» остаток не должен стать меньше нуля."""
    for wh, item in sorted(pairs):
        left = balance(db, wh, item)
        if left < 0:
            raise fail(422, "shortage", shortage_text(db, wh, item, left), field)


def pairs_of(m: StockMove) -> set[tuple[int, int]]:
    out = {(m.warehouse_id, m.item_id)}
    if m.to_warehouse_id:
        out.add((m.to_warehouse_id, m.item_id))
    return out


def find_item(db: Session, entity_id: int, name: str, sku: str = "") -> StockItem | None:
    """Товар юрлица, который подходит к детали: тот же артикул или то же название (без регистра и знаков)."""
    key = norm(name)
    sku = (sku or "").strip()
    q = select(StockItem).where(StockItem.legal_entity_id == entity_id, StockItem.archived_at.is_(None))
    if sku:
        hit = db.scalar(q.where(func.lower(StockItem.sku) == sku.lower()).order_by(StockItem.id).limit(1))
        if hit:
            return hit
    return db.scalar(q.where(StockItem.name_key == key).limit(1)) if key else None


def stock_for(db: Session, entity_id: int, item_id: int) -> list[dict]:
    """Где лежит товар: [{warehouse_id, warehouse, qty}] только склады не в архиве с остатком больше нуля."""
    bal = balances(db, item_id=item_id)
    names = {w.id: w.name for w in db.scalars(select(Warehouse).where(Warehouse.legal_entity_id == entity_id, Warehouse.archived_at.is_(None)))}
    return [{"warehouse_id": w, "warehouse": names[w], "qty": float(q)} for (w, _i), q in sorted(bal.items(), key=lambda x: names.get(x[0][0], "")) if q > 0 and w in names]
