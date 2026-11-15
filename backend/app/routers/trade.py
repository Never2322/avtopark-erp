# -*- coding: utf-8 -*-
"""Заказы и документы: «создать документ из заказа» и данные для печатных форм.

Печатные формы рисует сайт (страницы /print/...): сервер отдаёт готовые данные — реквизиты, суммы, сумму прописью.
Права те же, что у разделов: видеть заказ, документ или кассовую операцию можно только в своём юрлице.
"""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common import fail
from app.db import get_db
from app.deps import Principal, current
from app.enums import DOC_FULL_NAMES, DOC_TYPES, ORDER_STATUSES
from app.models import CashAccount, CashOp, Counterparty, Document, Employee, LegalEntity, Order, Tariff, Vehicle
from app.registry import RESOURCES, get_for, org_tz, rows_out, save, serialize
from app.resources import _doc_calc, _emp_label_safe, _order_no, _vat_split
from app.services.words import amount_in_words

router = APIRouter(tags=["Заказы и документы"])


def _party(c: Counterparty | None) -> dict:
    if c is None:
        return {"name": "", "inn": "", "kpp": "", "address": "", "phone": "", "bank_name": "", "bik": "", "account": "", "corr_account": ""}
    return {k: getattr(c, k) for k in ("name", "inn", "kpp", "address", "phone", "bank_name", "bik", "account", "corr_account")}


def _seller(e: LegalEntity) -> dict:
    return {"name": e.name, "short_name": e.short_name, "inn": e.inn, "kpp": e.kpp, "ogrn": e.ogrn, "address": e.address, "phone": e.phone,
            "bank_name": e.bank_name, "bik": e.bik, "account": e.account, "corr_account": e.corr_account,
            "head_name": e.head_name, "accountant_name": e.accountant_name}


def _vehicle_text(db: Session, vehicle_id: int | None) -> str:
    veh = db.get(Vehicle, vehicle_id) if vehicle_id else None
    return f"{veh.plate} · {(veh.make + ' ' + veh.model).strip()}".rstrip(" ·") if veh else ""


def _missing(e: LegalEntity, *need: tuple[str, str]) -> list[str]:
    return [label for name, label in need if not getattr(e, name)]


@router.post("/orders/{oid}/make-document/{doc_type}", status_code=201)
def make_document(oid: int, doc_type: str, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Составляет счёт, акт, накладную или УПД по заказу: заказчик, сумма и НДС берутся из заказа."""
    if doc_type not in DOC_TYPES:
        raise fail(404, "not_found", "Такого документа нет")
    order = get_for(db, RESOURCES["orders"], p, oid, "view")
    if order.archived_at:
        raise fail(409, "archived", "Заказ в архиве: сначала верните его")
    if not p.can("documents.create", order.legal_entity_id):
        raise fail(403, "forbidden", "Нет права составлять документы")
    route = " — ".join(x for x in (order.point_from, order.point_to) if x)
    subject = f"Транспортные услуги по заказу {_order_no(order)}" + (f": {route}" if route else "")
    body = {"legal_entity_id": order.legal_entity_id, "doc_type": doc_type, "doc_date": dt.datetime.now(org_tz()).date().isoformat(),
            "order_id": order.id, "counterparty_id": order.customer_id, "subject": subject, "qty": "1", "unit": "усл.",
            "price": str(order.amount), "vat_pct": str(order.vat_pct)}
    obj, warnings = save(db, RESOURCES["documents"], p, body, None)
    return {**rows_out(db, RESOURCES["documents"], [obj], p)[0], "warnings": warnings}


# ---------------------------------------------------------------- данные для печати

@router.get("/print/orders/{oid}")
def print_order(oid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    o = get_for(db, RESOURCES["orders"], p, oid, "view")
    ent = db.get(LegalEntity, o.legal_entity_id)
    drv = db.get(Employee, o.driver_id) if o.driver_id else None
    tariff = db.get(Tariff, o.tariff_id) if o.tariff_id else None
    vat, total = _vat_split(o.amount, o.vat_pct)
    return {"kind": "order", "title": "Заявка на перевозку", "number": o.number, "year": o.year, "date": serialize(o.order_date),
            "status": ORDER_STATUSES[o.status], "archived": o.archived_at is not None,
            "seller": _seller(ent), "customer": _party(db.get(Counterparty, o.customer_id)),
            "point_from": o.point_from, "point_to": o.point_to, "cargo": o.cargo, "cargo_kg": serialize(o.cargo_kg),
            "distance_km": o.distance_km, "hours": serialize(o.hours), "depart_at": serialize(o.depart_at), "deliver_at": serialize(o.deliver_at),
            "vehicle": _vehicle_text(db, o.vehicle_id), "driver": _emp_label_safe(drv),
            "tariff": tariff.name if tariff else "", "qty": serialize(o.qty), "rate": serialize(o.rate),
            "amount": serialize(o.amount), "vat_pct": o.vat_pct, "vat": serialize(vat), "total": serialize(total),
            "words": amount_in_words(total), "note": o.note,
            "missing": _missing(ent, ("inn", "ИНН"), ("head_name", "руководитель"))}


@router.get("/print/documents/{did}")
def print_document(did: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    d = get_for(db, RESOURCES["documents"], p, did, "view")
    ent = db.get(LegalEntity, d.legal_entity_id)
    buyer = db.get(Counterparty, d.counterparty_id)
    order = db.get(Order, d.order_id) if d.order_id else None
    amount, vat, total = _doc_calc(d)
    need = [("inn", "ИНН"), ("address", "адрес")]
    if d.doc_type == "invoice":
        need += [("bank_name", "банк"), ("bik", "БИК"), ("account", "расчётный счёт"), ("corr_account", "корр. счёт")]
    if d.doc_type in ("act", "upd"):
        need += [("head_name", "руководитель")]
    missing = _missing(ent, *need)
    if not buyer.inn:
        missing.append("ИНН заказчика")
    return {"kind": "document", "doc_type": d.doc_type, "title": DOC_FULL_NAMES[d.doc_type], "number": d.number, "year": d.year,
            "date": serialize(d.doc_date), "archived": d.archived_at is not None, "seller": _seller(ent), "buyer": _party(buyer),
            "order": {"number": order.number, "year": order.year, "date": serialize(order.order_date), "point_from": order.point_from,
                      "point_to": order.point_to, "cargo": order.cargo, "cargo_kg": serialize(order.cargo_kg), "distance_km": order.distance_km,
                      "vehicle": _vehicle_text(db, order.vehicle_id), "driver": _emp_label_safe(db.get(Employee, order.driver_id) if order.driver_id else None),
                      } if order else None,
            "subject": d.subject or "Услуги", "unit": d.unit, "qty": serialize(d.qty), "price": serialize(d.price),
            "amount": serialize(amount), "vat_pct": d.vat_pct, "vat": serialize(vat), "total": serialize(total),
            "words": amount_in_words(total), "paid_date": serialize(d.paid_date), "missing": missing}


@router.get("/print/cash/{cid}")
def print_cash(cid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    c = get_for(db, RESOURCES["cash"], p, cid, "view")
    if c.kind == "transfer":
        raise fail(422, "no_form", "Для перевода между счетами печатная форма не нужна")
    ent = db.get(LegalEntity, c.legal_entity_id)
    acc = db.get(CashAccount, c.account_id)
    cp = db.get(Counterparty, c.counterparty_id) if c.counterparty_id else None
    emp = db.get(Employee, c.employee_id) if c.employee_id else None
    who = (cp.name if cp else "") or _emp_label_safe(emp)
    prefix = "ПКО" if c.kind == "in" else "РКО"
    return {"kind": "cash", "cash_kind": c.kind, "title": "Приходный кассовый ордер" if c.kind == "in" else "Расходный кассовый ордер",
            "prefix": prefix, "number": c.number, "year": c.year, "date": serialize(c.op_date), "archived": c.archived_at is not None,
            "seller": _seller(ent), "account": acc.name, "who": who, "amount": serialize(c.amount), "words": amount_in_words(c.amount),
            "basis": c.note or c.category, "category": c.category, "missing": _missing(ent, ("inn", "ИНН"), ("head_name", "руководитель"), ("accountant_name", "главный бухгалтер"))}
