# -*- coding: utf-8 -*-
"""Счета на ремонт и запчасти.

Два вида записей:
  issued    счёт выписан в программе (со строками, цены с НДС), печатается как «счёт на оплату»;
  uploaded  счёт получен от поставщика: файл (PDF или фото) прикладывается, сумма вносится.

Итоги «сколько потратили на ремонт» считаются без отменённых и без счетов в архиве.
Счёт не стирается: его убирают в архив с причиной. Права смотрятся по юрлицу счёта.
"""
import datetime as dt
import json
import urllib.parse
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import csv_text, fail, like, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current, require
from app.enums import BILL_KINDS, BILL_MAX_FILES, BILL_STATUSES, BILL_UNITS, BILL_YEAR_MAX, BILL_YEAR_MIN, REQUEST_STATUSES, VAT_RATES
from app.models import (Counterparty, LegalEntity, RepairBill, RepairBillFile, RepairBillItem, RequestPart, ServiceRequest, StoredFile, User,
                        Vehicle)
from app.registry import org_tz
from app.routers.dossier import read_uploads, store
from app.routers.requests_ import visible as request_visible
from app.services import bills as svc
from app.services import ocr
from app.services import parts as parts_svc
from app.services.numbering import next_number
from app.services.words import amount_in_words

router = APIRouter(tags=["Счета на ремонт"])

SUPPLIER_FIELDS = ("supplier_name", "supplier_inn", "supplier_kpp", "supplier_addr", "supplier_phone", "bank_name", "bik", "corr_account", "account")
LIMITS = {"supplier_name": 160, "supplier_inn": 12, "supplier_kpp": 9, "supplier_addr": 250, "supplier_phone": 40, "bank_name": 160,
          "bik": 9, "corr_account": 20, "account": 20}
DIGITS = {"supplier_inn": ((10, 12), "ИНН — 10 или 12 цифр"), "supplier_kpp": ((9,), "КПП — 9 цифр"), "bik": ((9,), "БИК — 9 цифр"),
          "corr_account": ((20,), "Корреспондентский счёт — 20 цифр"), "account": ((20,), "Расчётный счёт — 20 цифр")}
FROM_COUNTERPARTY = {"supplier_name": "name", "supplier_inn": "inn", "supplier_kpp": "kpp", "supplier_addr": "address", "supplier_phone": "phone",
                     "bank_name": "bank_name", "bik": "bik", "corr_account": "corr_account", "account": "account"}
MONTH_NAMES = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
RECOGNIZE_MAX = 10 * 1024 * 1024


class BillIn(BaseModel):
    """Все поля необязательны: при создании недостающие берутся по умолчанию, при правке меняется только присланное."""
    model_config = ConfigDict(extra="forbid")
    kind: str | None = None
    legal_entity_id: int | None = None
    number: str | None = Field(default=None, max_length=60)
    bill_date: dt.date | None = None
    supplier_id: int | None = None
    supplier_name: str | None = Field(default=None, max_length=400)
    supplier_inn: str | None = Field(default=None, max_length=40)
    supplier_kpp: str | None = Field(default=None, max_length=40)
    supplier_addr: str | None = Field(default=None, max_length=600)
    supplier_phone: str | None = Field(default=None, max_length=100)
    bank_name: str | None = Field(default=None, max_length=400)
    bik: str | None = Field(default=None, max_length=40)
    corr_account: str | None = Field(default=None, max_length=60)
    account: str | None = Field(default=None, max_length=60)
    basis: str | None = Field(default=None, max_length=300)
    vehicle_id: int | None = None
    request_id: int | None = None
    vat_pct: int | None = None
    total: str | int | float | None = None
    vat: str | int | float | None = None
    status: str | None = None
    paid_date: dt.date | None = None
    note: str | None = Field(default=None, max_length=1200)
    items: list[dict] | None = Field(default=None, max_length=300)


class StatusIn(BaseModel):
    status: str
    paid_date: dt.date | None = None


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=300)


# ---------------------------------------------------------------- вспомогательное

def today():
    return dt.datetime.now(org_tz()).date()


def name_of(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return (u.display_name or u.login) if u else None


def vehicle_label(v: Vehicle | None) -> str:
    return f"{v.plate} · {(v.make + ' ' + v.model).strip()}".rstrip(" ·") if v else ""


def get_bill(db: Session, p: Principal, bid: int, perm: str = "bills.view") -> RepairBill:
    b = db.get(RepairBill, bid)
    if not b or not p.can(perm, b.legal_entity_id):
        raise fail(404, "not_found", "Счёт не найден")
    return b


def money(v) -> float:
    return float(v) if v is not None else 0.0


def money_cell(v) -> str:
    return format(Decimal(str(v)).quantize(svc.CENT), "f").replace(".", ",")


def live_files(db: Session, bid: int) -> list[RepairBillFile]:
    return list(db.scalars(select(RepairBillFile).where(RepairBillFile.bill_id == bid, RepairBillFile.removed_at.is_(None)).order_by(RepairBillFile.id)))


def row_out(db: Session, p: Principal, b: RepairBill, vehicles: dict, requests: dict, ents: dict, files_n: dict) -> dict:
    v = vehicles.get(b.vehicle_id)
    r = requests.get(b.request_id)
    return {"id": b.id, "legal_entity_id": b.legal_entity_id, "legal_entity": ents.get(b.legal_entity_id, ""), "kind": b.kind,
            "kind_label": BILL_KINDS[b.kind], "number": b.number, "bill_date": b.bill_date.isoformat(), "supplier_name": b.supplier_name,
            "supplier_inn": b.supplier_inn, "basis": b.basis, "vehicle_id": b.vehicle_id, "vehicle": vehicle_label(v), "request_id": b.request_id,
            "request_number": r.number if r else None, "total": money(b.total), "vat": money(b.vat), "status": b.status,
            "status_label": BILL_STATUSES[b.status], "paid_date": b.paid_date.isoformat() if b.paid_date else None,
            "files": files_n.get(b.id, 0), "archived": b.archived_at is not None}


def detail_out(db: Session, p: Principal, b: RepairBill) -> dict:
    v = db.get(Vehicle, b.vehicle_id) if b.vehicle_id else None
    r = db.get(ServiceRequest, b.request_id) if b.request_id else None
    ent = db.get(LegalEntity, b.legal_entity_id)
    items = list(db.scalars(select(RepairBillItem).where(RepairBillItem.bill_id == b.id).order_by(RepairBillItem.pos)))
    files = live_files(db, b.id)
    free = b.archived_at is None
    request_ok = bool(r and request_visible(p, r))
    return {
        "id": b.id, "legal_entity_id": b.legal_entity_id, "legal_entity": (ent.short_name or ent.name) if ent else "", "kind": b.kind,
        "kind_label": BILL_KINDS[b.kind], "number": b.number, "bill_date": b.bill_date.isoformat(), "supplier_id": b.supplier_id,
        **{k: getattr(b, k) for k in SUPPLIER_FIELDS}, "basis": b.basis, "vehicle_id": b.vehicle_id, "vehicle": vehicle_label(v),
        "request_id": b.request_id, "request": ({"id": r.id, "number": r.number, "title": r.title, "status_label": REQUEST_STATUSES[r.status]}
                                                 if request_ok else ({"id": None, "number": r.number, "title": "", "status_label": ""} if r else None)),
        "vat_pct": b.vat_pct, "vat_label": VAT_RATES[str(b.vat_pct)], "total": money(b.total), "vat": money(b.vat),
        "words": amount_in_words(b.total), "status": b.status, "status_label": BILL_STATUSES[b.status],
        "paid_date": b.paid_date.isoformat() if b.paid_date else None, "note": b.note, "archived": b.archived_at is not None,
        "archive_reason": b.archive_reason, "created_by": name_of(db, b.created_by_id), "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat(),
        "items": [{"pos": i.pos, "name": i.name, "article": i.article, "qty": float(i.qty), "qty_text": parts_svc.qty_text(i.qty), "unit": i.unit,
                   "price": money(i.price), "amount": money(i.amount), "vat": money(i.vat)} for i in items],
        "files": [{"id": f.id, "name": f.file.original_name, "content_type": f.file.content_type, "size": f.file.size_bytes,
                   "is_image": f.file.content_type.startswith("image/"), "added_at": f.added_at.isoformat(), "added_by": name_of(db, f.added_by_id)}
                  for f in files],
        "can": {"edit": free and p.can("bills.edit", b.legal_entity_id), "pay": free and p.can("bills.pay", b.legal_entity_id),
                "void": free and p.can("bills.void", b.legal_entity_id), "restore": (not free) and p.can("bills.void", b.legal_entity_id),
                "print": b.kind == "issued", "files": free and p.can("bills.edit", b.legal_entity_id) and len(files) < BILL_MAX_FILES},
    }


def filters(p: Principal, perm: str, *, month: str, vehicle_id: int | None, status: str, kind: str, q: str, legal_entity_id: int | None,
            archived: bool, date_from: str = "", date_to: str = "") -> list:
    scope = p.scope(perm)
    if not scope:
        raise fail(403, "forbidden", "Нет доступа к счетам на ремонт")
    if legal_entity_id is not None and not scope.allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    cond = [scope.cond(RepairBill.legal_entity_id), RepairBill.archived_at.is_not(None) if archived else RepairBill.archived_at.is_(None)]
    if legal_entity_id is not None:
        cond.append(RepairBill.legal_entity_id == legal_entity_id)
    month = month.strip()
    if month and month != "all":
        try:
            y, m = int(month[:4]), int(month[5:7])
            first = dt.date(y, m, 1)
            if len(month) != 7 or month[4] != "-":
                raise ValueError
        except ValueError:
            raise fail(422, "bad_month", "Месяц нужен в виде 2026-11 или «all»", "month") from None
        last = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        cond += [RepairBill.bill_date >= first, RepairBill.bill_date <= last]
    for field, value, op in (("date_from", date_from, "ge"), ("date_to", date_to, "le")):
        if value:
            try:
                d = dt.date.fromisoformat(value)
            except ValueError:
                raise fail(422, "bad_date", "Дата нужна в виде 2026-11-05", field) from None
            cond.append(RepairBill.bill_date >= d if op == "ge" else RepairBill.bill_date <= d)
    if vehicle_id:
        cond.append(RepairBill.vehicle_id == vehicle_id)
    if status:
        if status not in BILL_STATUSES:
            raise fail(422, "bad_status", "Неизвестное состояние счёта", "status")
        cond.append(RepairBill.status == status)
    if kind:
        if kind not in BILL_KINDS:
            raise fail(422, "bad_kind", "Неизвестный вид счёта", "kind")
        cond.append(RepairBill.kind == kind)
    if q.strip():
        t = like(q.strip())
        cond.append(or_(RepairBill.number.ilike(t), RepairBill.supplier_name.ilike(t), RepairBill.supplier_inn.ilike(t), RepairBill.basis.ilike(t),
                        RepairBill.vehicle_id.in_(select(Vehicle.id).where(Vehicle.plate.ilike(t)))))
    return cond


def sums(db: Session, cond: list) -> dict:
    live = RepairBill.status != "cancelled"
    row = db.execute(select(
        func.count().filter(live), func.coalesce(func.sum(RepairBill.total).filter(live), 0),
        func.coalesce(func.sum(RepairBill.total).filter(RepairBill.status == "paid"), 0),
        func.coalesce(func.sum(RepairBill.total).filter(RepairBill.status == "unpaid"), 0),
        func.coalesce(func.sum(RepairBill.vat).filter(live), 0), func.count().filter(RepairBill.status == "cancelled")).where(*cond)).one()
    return {"count": row[0], "total": money(row[1]), "paid": money(row[2]), "unpaid": money(row[3]), "vat": money(row[4]), "cancelled": row[5]}


def lookups(db: Session, rows: list[RepairBill]):
    vids = {b.vehicle_id for b in rows if b.vehicle_id}
    rids = {b.request_id for b in rows if b.request_id}
    ids = [b.id for b in rows]
    vehicles = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vids)))} if vids else {}
    requests = {r.id: r for r in db.scalars(select(ServiceRequest).where(ServiceRequest.id.in_(rids)))} if rids else {}
    ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
    files_n = dict(db.execute(select(RepairBillFile.bill_id, func.count()).where(RepairBillFile.bill_id.in_(ids), RepairBillFile.removed_at.is_(None))
                              .group_by(RepairBillFile.bill_id)).all()) if ids else {}
    return vehicles, requests, ents, files_n


# ---------------------------------------------------------------- справочная часть и список

@router.get("/bills/meta")
def meta(p: Principal = Depends(require("bills.view")), db: Session = Depends(get_db)):
    scope = p.scope("bills.view")
    month_key = func.to_char(RepairBill.bill_date, "YYYY-MM").label("m")
    months = [r[0] for r in db.execute(select(month_key).where(scope.cond(RepairBill.legal_entity_id)).distinct().order_by(month_key.desc()))]
    t = today()
    cur = f"{t.year:04d}-{t.month:02d}"
    if cur not in months:
        months.insert(0, cur)
        months.sort(reverse=True)
    create = p.scope("bills.create")
    ents = [{"id": e.id, "name": e.short_name or e.name} for e in db.scalars(select(LegalEntity).where(LegalEntity.archived_at.is_(None)).order_by(LegalEntity.id))
            if create.allows(e.id)]
    return {"statuses": [{"code": k, "label": v} for k, v in BILL_STATUSES.items()], "kinds": [{"code": k, "label": v} for k, v in BILL_KINDS.items()],
            "vat_rates": [{"value": int(k), "label": v} for k, v in VAT_RATES.items()], "units": list(BILL_UNITS), "months": months,
            "month_labels": {m: f"{MONTH_NAMES[int(m[5:]) - 1]} {m[:4]}" for m in months}, "default_month": cur, "entities": ents,
            "can": {"create": bool(create), "export": bool(p.scope("bills.export")), "pay": bool(p.scope("bills.pay")), "edit": bool(p.scope("bills.edit")),
                    "void": bool(p.scope("bills.void")), "catalog": bool(p.scope("parts.view")), "purchases": bool(p.scope("purchases.view"))}}


@router.get("/bills")
def list_bills(month: str | None = None, vehicle_id: int | None = None, status: str = "", kind: str = "", q: str = "", legal_entity_id: int | None = None,
               archived: bool = False, page: int = 1, page_size: int = 50, p: Principal = Depends(require("bills.view")), db: Session = Depends(get_db)):
    if month is None:
        t = today()
        month = f"{t.year:04d}-{t.month:02d}"
    cond = filters(p, "bills.view", month=month, vehicle_id=vehicle_id, status=status, kind=kind, q=q, legal_entity_id=legal_entity_id, archived=archived)
    rows, total = page_of(db, select(RepairBill).where(*cond).order_by(RepairBill.bill_date.desc(), RepairBill.id.desc()), page, page_size)
    objs = [r[0] for r in rows]
    vehicles, requests, ents, files_n = lookups(db, objs)
    return {"items": [row_out(db, p, b, vehicles, requests, ents, files_n) for b in objs], "total": total, "page": page, "page_size": page_size,
            "totals": sums(db, cond), "month": month}


def export_rows(db: Session, cond: list) -> list[RepairBill]:
    return list(db.scalars(select(RepairBill).where(*cond).order_by(RepairBill.bill_date, RepairBill.id).limit(5000)))


@router.get("/bills/export.csv")
def export_csv(month: str | None = None, vehicle_id: int | None = None, status: str = "", kind: str = "", q: str = "", legal_entity_id: int | None = None,
               archived: bool = False, p: Principal = Depends(require("bills.export")), db: Session = Depends(get_db)):
    if month is None:
        t = today()
        month = f"{t.year:04d}-{t.month:02d}"
    cond = filters(p, "bills.export", month=month, vehicle_id=vehicle_id, status=status, kind=kind, q=q, legal_entity_id=legal_entity_id, archived=archived)
    objs = export_rows(db, cond)
    vehicles, requests, ents, _ = lookups(db, objs)
    rows = [["Юрлицо", "№ счёта", "Дата", "Поставщик", "ИНН", "Машина", "Обращение №", "Основание", "Сумма, ₽", "в т.ч. НДС, ₽", "Состояние", "Дата оплаты",
             "Как внесён"]]
    for b in objs:
        rows.append([ents.get(b.legal_entity_id, ""), b.number, b.bill_date.strftime("%d.%m.%Y"), b.supplier_name, b.supplier_inn,
                     vehicle_label(vehicles.get(b.vehicle_id)), requests[b.request_id].number if b.request_id in requests else "", b.basis,
                     money_cell(b.total), money_cell(b.vat), BILL_STATUSES[b.status], b.paid_date.strftime("%d.%m.%Y") if b.paid_date else "",
                     "выписан в программе" if b.kind == "issued" else "получен от поставщика"])
    live = [b for b in objs if b.status != "cancelled"]
    rows.append(["Итого (без отменённых)", "", "", "", "", "", "", "", money_cell(sum((b.total for b in live), Decimal(0))),
                 money_cell(sum((b.vat for b in live), Decimal(0))), "", "", ""])
    audit.log(db, p, "bill.export", "bill", None, legal_entity_id, rows=len(objs), month=month)
    db.commit()
    fname = "bills-" + (month if month and month != "all" else "all") + ".csv"
    return Response(csv_text(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/bills/summary")
def summary(group: str = "month", date_from: str = "", date_to: str = "", legal_entity_id: int | None = None, vehicle_id: int | None = None,
            p: Principal = Depends(require("bills.view")), db: Session = Depends(get_db)):
    """Сколько потратили на ремонт: по месяцам или по машинам. Без отменённых и без архива."""
    if group not in ("month", "vehicle"):
        raise fail(422, "bad_group", "Группировка: month или vehicle", "group")
    t = today()
    if not date_from:
        first = t.replace(day=1)
        date_from = (first.replace(year=first.year - 1, month=first.month + 1) if first.month < 12 else first.replace(month=1)).isoformat() \
            if group == "month" else first.isoformat()
    if not date_to:
        date_to = t.isoformat()
    cond = filters(p, "bills.view", month="all", vehicle_id=vehicle_id, status="", kind="", q="", legal_entity_id=legal_entity_id, archived=False,
                   date_from=date_from, date_to=date_to) + [RepairBill.status != "cancelled"]
    key = func.to_char(RepairBill.bill_date, "YYYY-MM") if group == "month" else RepairBill.vehicle_id
    q = (select(key, func.count(), func.sum(RepairBill.total), func.sum(case((RepairBill.status == "paid", RepairBill.total), else_=0)),
              func.sum(case((RepairBill.status == "unpaid", RepairBill.total), else_=0)), func.sum(RepairBill.vat)).where(*cond).group_by(key))
    q = q.order_by(key) if group == "month" else q.order_by(func.sum(RepairBill.total).desc())
    data = db.execute(q).all()
    vehicles = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_([r[0] for r in data if r[0]])))} if group == "vehicle" else {}
    items = []
    for k, n, total, paid, unpaid, vat in data:
        label = (f"{MONTH_NAMES[int(k[5:]) - 1].capitalize()} {k[:4]}" if group == "month" else (vehicle_label(vehicles.get(k)) if k else "Без привязки к машине"))
        items.append({"key": k, "label": label, "count": n, "total": money(total), "paid": money(paid), "unpaid": money(unpaid), "vat": money(vat)})
    return {"group": group, "date_from": date_from, "date_to": date_to, "items": items,
            "totals": {"count": sum(i["count"] for i in items), **{f: round(sum(i[f] for i in items), 2) for f in ("total", "paid", "unpaid", "vat")}}}


# ---------------------------------------------------------------- проверка и запись счёта

def pydantic_fields(e: ValidationError) -> dict:
    fields = {}
    for err in e.errors():
        fields.setdefault(".".join(str(x) for x in err["loc"]) or "_", "Некорректное значение")
    return fields


def parse_body(raw: str) -> BillIn:
    try:
        return BillIn.model_validate(json.loads(raw))
    except ValidationError as e:                           # ValidationError — тоже ValueError, поэтому он первым
        raise HTTPException(422, detail={"code": "validation", "message": "Проверьте отмеченные поля", "field": None, "fields": pydantic_fields(e)}) from None
    except (ValueError, TypeError):
        raise fail(422, "bad_form", "Данные счёта не прочитались: обновите страницу и повторите") from None


def resolve_links(db: Session, p: Principal, perm: str, v: dict, bill: RepairBill | None):
    """Юрлицо счёта, машина и обращение. Юрлицо берётся из обращения или машины, а если их нет — из выбора человека."""
    req = db.get(ServiceRequest, v["request_id"]) if v.get("request_id") else None
    if v.get("request_id") and (req is None or not request_visible(p, req)):
        raise fail(422, "bad_request", "Такого обращения нет", "request_id")
    veh = db.get(Vehicle, v["vehicle_id"]) if v.get("vehicle_id") else None
    if v.get("vehicle_id") and veh is None:
        raise fail(422, "bad_vehicle", "Такой машины нет", "vehicle_id")
    if req is not None:
        if veh is not None and veh.id != req.vehicle_id:
            raise fail(422, "request_vehicle", f"Обращение № {req.number} относится к другой машине", "request_id")
        veh = veh or req.vehicle
    if bill is not None:
        entity = bill.legal_entity_id
        for obj, field in ((req, "request_id"), (veh, "vehicle_id")):
            if obj is not None and obj.legal_entity_id != entity:
                raise fail(422, "other_entity", "Машина и обращение должны быть из того же юрлица, что и счёт", field)
        return entity, veh, req
    entity = req.legal_entity_id if req else veh.legal_entity_id if veh else v.get("legal_entity_id")
    if entity is None:
        scope = p.scope(perm)
        if not scope.everything and len(scope.ids) == 1:
            entity = next(iter(scope.ids))
        else:
            raise fail(422, "entity_required", "Выберите юрлицо, на которое оформлен счёт", "legal_entity_id")
    if v.get("legal_entity_id") not in (None, entity):
        raise fail(422, "other_entity", "Машина или обращение относятся к другому юрлицу", "legal_entity_id")
    if not db.get(LegalEntity, entity):
        raise fail(422, "bad_entity", "Такого юрлица нет", "legal_entity_id")
    if not p.can(perm, entity):
        raise fail(403, "forbidden", "Нет права вносить счета по этому юрлицу")
    return entity, veh, req


def merged(b: RepairBill | None, body: BillIn, items: list | None) -> dict:
    """Текущие значения счёта, поверх них то, что прислали. Так создание и правка проверяются одним кодом."""
    cur = {} if b is None else {k: getattr(b, k) for k in ("number", "bill_date", "supplier_id", "basis", "vehicle_id", "request_id", "vat_pct", "status",
                                                         "paid_date", "note", "total", "vat") + SUPPLIER_FIELDS}
    given = body.model_dump(exclude_unset=True)
    cur.update({k: val for k, val in given.items() if k not in ("items", "kind")})
    cur["_given"] = set(given)
    cur["_items"] = items
    return cur


def check_paid(status: str, paid_date: dt.date | None, bill_date: dt.date, p: Principal, entity: int) -> dt.date | None:
    if status not in BILL_STATUSES:
        raise fail(422, "bad_status", "Выберите состояние счёта из списка", "status")
    if status != "paid":
        return None
    if not p.can("bills.pay", entity):
        raise fail(403, "forbidden", "Нет права отмечать счета оплаченными")
    d = paid_date or today()
    if d > today():
        raise fail(422, "paid_future", "Дата оплаты не может быть в будущем", "paid_date")
    if d < bill_date:
        raise fail(422, "paid_before", "Дата оплаты раньше даты счёта: проверьте обе даты", "paid_date")
    return d


def validate(db: Session, p: Principal, v: dict, kind: str, bill: RepairBill | None, perm: str):
    """Проверяет счёт целиком. Возвращает значения для записи, строки и признак «номер выдать автоматически»."""
    entity, veh, req = resolve_links(db, p, perm, v, bill)
    d = v.get("bill_date")
    if not d:
        raise fail(422, "bill_date", "Выберите дату счёта", "bill_date")
    if not BILL_YEAR_MIN <= d.year <= BILL_YEAR_MAX:
        raise fail(422, "bill_date", f"Год счёта должен быть от {BILL_YEAR_MIN} до {BILL_YEAR_MAX}", "bill_date")
    number = (v.get("number") or "").strip()
    auto = False
    if not number:
        if kind == "issued" and bill is None:
            auto = True
        else:
            raise fail(422, "number", "Введите номер счёта", "number")
    elif len(number) > 40:
        raise fail(422, "number", "Номер длиннее 40 знаков", "number")
    # поставщик
    sup = None
    if v.get("supplier_id"):
        sup = db.get(Counterparty, v["supplier_id"])
        if sup is None or (sup.archived_at and (bill is None or bill.supplier_id != sup.id)):
            raise fail(422, "bad_supplier", "Выберите поставщика из списка", "supplier_id")
    values = {"legal_entity_id": entity, "bill_date": d, "supplier_id": sup.id if sup else None, "vehicle_id": veh.id if veh else None,
              "request_id": req.id if req else None}
    for k in SUPPLIER_FIELDS:
        val = (v.get(k) or "").strip()
        if not val and sup is not None and (bill is None or "supplier_id" in v["_given"]):
            val = (getattr(sup, FROM_COUNTERPARTY[k]) or "").strip()[:LIMITS[k]]
        if len(val) > LIMITS[k]:
            raise fail(422, "too_long", f"Слишком длинно: не больше {LIMITS[k]} знаков", k)
        if val and k in DIGITS:
            lengths, hint = DIGITS[k]
            if not val.isdigit() or len(val) not in lengths:
                raise fail(422, "bad_digits", f"{hint}, без пробелов и букв", k)
        values[k] = val
    if not values["supplier_name"]:
        raise fail(422, "supplier_required", "Укажите поставщика", "supplier_name")
    basis, note = (v.get("basis") or "").strip(), (v.get("note") or "").strip()
    if len(basis) > 120:
        raise fail(422, "too_long", "Слишком длинно: не больше 120 знаков", "basis")
    if len(note) > 500:
        raise fail(422, "too_long", "Слишком длинно: не больше 500 знаков", "note")
    values["basis"], values["note"] = basis, note
    pct = v.get("vat_pct") if v.get("vat_pct") is not None else 22
    if str(pct) not in VAT_RATES:
        raise fail(422, "bad_vat", "Выберите ставку НДС из списка", "vat_pct")
    values["vat_pct"] = pct
    items = None
    if kind == "issued":
        rows = v["_items"]
        if rows is None and bill is not None:                     # правка без строк: пересчитываем сохранённые (например, сменили ставку НДС)
            rows = [{"name": i.name, "article": i.article, "qty": i.qty, "unit": i.unit, "price": i.price}
                    for i in db.scalars(select(RepairBillItem).where(RepairBillItem.bill_id == bill.id).order_by(RepairBillItem.pos))]
        items, total, vat = svc.clean_items(rows or [], pct)
        values["total"], values["vat"] = total, vat
    else:
        if v.get("total") in (None, "") and bill is None:
            raise fail(422, "total", "Введите сумму счёта, например 90804,92", "total")
        total = svc.to_money(v["total"], what="Сумма счёта", field="total", allow_zero=False) if "total" in v["_given"] or bill is None else bill.total
        recount = bill is None or "total" in v["_given"] or "vat_pct" in v["_given"]
        raw_vat = v.get("vat") if "vat" in v["_given"] or bill is None else None
        if raw_vat not in (None, ""):
            vat = svc.to_money(raw_vat, what="НДС", field="vat")
            if vat > total:
                raise fail(422, "vat_over", "НДС не может быть больше суммы счёта", "vat")
        else:
            vat = svc.vat_included(total, pct) if (recount or "vat" in v["_given"]) else bill.vat
        values["total"], values["vat"] = total, vat
    status = v.get("status") or "unpaid"
    values["status"] = status
    values["paid_date"] = check_paid(status, v.get("paid_date"), d, p, entity)
    # такой счёт уже внесён?
    cands = db.execute(select(RepairBill.id, RepairBill.number, RepairBill.kind).where(
        RepairBill.legal_entity_id == entity, RepairBill.archived_at.is_(None), RepairBill.id != (bill.id if bill else 0),
        or_(and_(RepairBill.bill_date == d, RepairBill.total == values["total"]),
            and_(RepairBill.kind == "issued", RepairBill.bill_date >= dt.date(d.year, 1, 1), RepairBill.bill_date <= dt.date(d.year, 12, 31))))).all()
    if not auto:
        for cid, cnum, ckind in cands:
            same_amount = False
            if cnum.casefold() == number.casefold():
                if kind == "issued" and ckind == "issued":
                    raise fail(422, "number_taken", f"Номер {number} в {d.year} году уже занят другим выписанным счётом (запись № {cid})", "number")
                same_amount = db.scalar(select(RepairBill.id).where(RepairBill.id == cid, RepairBill.bill_date == d, RepairBill.total == values["total"]))
                if same_amount:
                    raise fail(422, "duplicate_bill", f"Такой счёт уже внесён (запись № {cid}). Проверьте номер, дату и сумму.", "number")
    values["number"] = number
    return values, items, auto


def write_items(db: Session, bill: RepairBill, items: list[dict]) -> None:
    for old in db.scalars(select(RepairBillItem).where(RepairBillItem.bill_id == bill.id)):
        db.delete(old)
    db.flush()
    for it in items:
        db.add(RepairBillItem(bill_id=bill.id, **it))


def attach_files(db: Session, p: Principal, bill: RepairBill, uploads: list[UploadFile]) -> list[str]:
    return attach_prepared(db, p, bill, read_uploads(uploads, images_only=False, required=False))


def attach_prepared(db: Session, p: Principal, bill: RepairBill, prepared: list) -> list[str]:
    if not prepared:
        return []
    have = set(db.scalars(select(StoredFile.sha256).join(RepairBillFile, RepairBillFile.file_id == StoredFile.id).where(
        RepairBillFile.bill_id == bill.id, RepairBillFile.removed_at.is_(None))))
    fresh = []
    for f in prepared:
        if f.sha256 not in have:                         # тот же файл второй раз не добавляется (повторная отправка)
            have.add(f.sha256)
            fresh.append(f)
    if not fresh:
        return []
    if len(live_files(db, bill.id)) + len(fresh) > BILL_MAX_FILES:
        raise fail(422, "too_many_files", f"К счёту можно приложить не больше {BILL_MAX_FILES} файлов", "files")
    for sf in store(db, p, fresh):
        db.add(RepairBillFile(bill_id=bill.id, file_id=sf.id, added_by_id=p.id))
    return [f.name for f in fresh]


def describe(db: Session, b: RepairBill) -> dict:
    return {"number": b.number, "bill_date": b.bill_date.isoformat(), "supplier": b.supplier_name, "total": str(b.total), "vat": str(b.vat),
            "vat_pct": b.vat_pct, "vehicle_id": b.vehicle_id, "request_id": b.request_id, "basis": b.basis, "note": b.note}


# ---------------------------------------------------------------- создание, чтение, правка

@router.post("/bills", status_code=201)
def create_bill(data: str = Form(...), files: list[UploadFile] = File(default=[]), p: Principal = Depends(require("bills.create")),
                db: Session = Depends(get_db)):
    body = parse_body(data)
    kind = body.kind or "uploaded"
    if kind not in BILL_KINDS:
        raise fail(422, "bad_kind", "Вид счёта: выписан в программе или получен от поставщика", "kind")
    if kind == "issued" and files:
        raise fail(422, "files_not_allowed", "К счёту, выписанному в программе, файл не прикладывают: он печатается из программы", "files")
    v = merged(None, body, body.items)
    values, items, auto = validate(db, p, v, kind, None, "bills.create")
    if auto:
        values["number"] = str(next_number(db, values["legal_entity_id"], "repair_bill", values["bill_date"].year))
    b = RepairBill(kind=kind, created_by_id=p.id, updated_by_id=p.id, **values)
    db.add(b)
    db.flush()
    if items:
        write_items(db, b, items)
    names = attach_files(db, p, b, files)
    audit.log(db, p, "bill.create", "bill", b.id, b.legal_entity_id, kind=kind, files=names, **describe(db, b))
    db.commit()
    db.refresh(b)
    return detail_out(db, p, b)


@router.get("/bills/prefill")
def prefill(request: int, p: Principal = Depends(require("bills.create")), db: Session = Depends(get_db)):
    """Заготовка счёта по обращению: машина, основание и детали из списка обращения (цены вписывает человек)."""
    r = db.get(ServiceRequest, request)
    if not r or not request_visible(p, r) or not p.can("bills.create", r.legal_entity_id):
        raise fail(404, "not_found", "Обращение не найдено")
    rows = [x for x in db.scalars(select(RequestPart).where(RequestPart.request_id == r.id, RequestPart.removed_at.is_(None)).order_by(RequestPart.id))]
    return {"legal_entity_id": r.legal_entity_id, "vehicle_id": r.vehicle_id, "vehicle": vehicle_label(r.vehicle), "request_id": r.id,
            "request_number": r.number, "request_title": r.title, "basis": f"Обращение № {r.number}",
            "items": [{"name": x.name, "article": x.article, "qty": parts_svc.qty_text(x.qty), "unit": x.unit if x.unit in BILL_UNITS else "шт", "price": ""}
                      for x in rows]}


def lookups_for_reading(db: Session, p: Principal):
    scope = p.scope("bills.create")
    vehicles = list(db.scalars(select(Vehicle).where(scope.cond(Vehicle.legal_entity_id), Vehicle.archived_at.is_(None))))
    suppliers = [(c.id, c.inn) for c in db.scalars(select(Counterparty).where(Counterparty.archived_at.is_(None), Counterparty.inn != ""))]
    return scope, vehicles, suppliers


def plain(found: dict) -> dict:
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in found.items()}


@router.post("/bills/recognize")
def recognize(file: UploadFile = File(...), p: Principal = Depends(require("bills.create")), db: Session = Depends(get_db)):
    """Подсказка для формы: прочитать счёт поставщика из PDF, скана или фото. Ничего не сохраняет, файл прикладывается позже вместе со счётом."""
    from app.services import files as filesvc
    prepared = filesvc.prepare(file.filename, file.file.read(filesvc.MAX_FILE_BYTES + 1))
    is_pdf = prepared.content_type == "application/pdf"
    text, how = svc.extract_text([prepared])
    if not text.strip():
        if is_pdf:
            return {"found": {}, "how": "pdf", "readable": False,
                    "note": "В PDF нет текста (это скан), и прочитать его не удалось. Заполните поля вручную, файл приложится к счёту."}
        why = "Фото прочитать не удалось: снимите ровнее и крупнее." if ocr.available() else "Чтение фото на этом сервере не включено."
        return {"found": {}, "how": "photo", "readable": False, "note": f"{why} Заполните поля вручную, фото приложится к счёту."}
    _scope, vehicles, suppliers = lookups_for_reading(db, p)
    found = plain(svc.parse_bill_text(text, [(v.id, v.plate) for v in vehicles], suppliers))
    found.pop("buyer_inn", None)
    shown = "pdf" if how == "pdf" else "photo" if how == "photo" else "pdf"
    if not found:
        return {"found": {}, "how": shown, "readable": False, "note": "Из файла не удалось прочитать данные (другой вид счёта). Заполните поля вручную."}
    tail = "" if how == "pdf" else " Это распознавание со скана или фото: в цифрах бывают ошибки, сверьте с бумагой."
    return {"found": found, "how": shown, "readable": True,
            "note": "Поля заполнены из файла. Проверьте номер, дату, сумму и поставщика: программа может ошибиться." + tail}


# ---------------------------------------------------------------- «Загрузить счёт»: прочитать файл и внести счёт самой

READ_LABELS = {"number": "номер счёта", "bill_date": "дата счёта", "total": "сумма", "supplier_name": "поставщик", "legal_entity_id": "юрлицо"}


@router.post("/bills/auto")
def auto_bill(files: list[UploadFile] = File(default=[]), note: str = Form(""), legal_entity_id: int | None = Form(default=None),
              p: Principal = Depends(require("bills.create")), db: Session = Depends(get_db)):
    """Человек прикладывает только файл счёта (PDF, скан, фото) и, если хочет, заметку. Программа читает номер, дату, сумму, НДС,
    поставщика и юрлицо (по ИНН покупателя) и вносит счёт. Если что-то не прочиталось или сумму со скана не удалось подтвердить,
    счёт НЕ вносится: ответ несёт прочитанное, а сайт открывает форму, где остаётся дописать и нажать «Внести счёт»."""
    prepared = read_uploads(files, images_only=False, required=True)
    if len(prepared) > BILL_MAX_FILES:
        raise fail(422, "too_many_files", f"К счёту можно приложить не больше {BILL_MAX_FILES} файлов", "files")
    note = (note or "").strip()
    if len(note) > 500:
        raise fail(422, "too_long", "Заметка длиннее 500 знаков", "note")
    text, how = svc.extract_text(prepared)
    scope, vehicles, suppliers = lookups_for_reading(db, p)
    found = svc.parse_bill_text(text, [(v.id, v.plate) for v in vehicles], suppliers) if text.strip() else {}
    ents = list(db.scalars(select(LegalEntity).where(LegalEntity.archived_at.is_(None), scope.cond(LegalEntity.id)).order_by(LegalEntity.name)))
    read_any = bool(found)

    # юрлицо: выбор человека, иначе покупатель из счёта (по ИНН), иначе юрлицо машины, иначе единственное доступное
    buyer = [e for e in ents if e.inn and e.inn == found.get("buyer_inn")]
    veh = next((v for v in vehicles if v.id == found.get("vehicle_id")), None)
    entity, problems = None, []
    if legal_entity_id is not None:
        if legal_entity_id not in {e.id for e in ents}:
            raise fail(403, "forbidden", "Нет права вносить счета по этому юрлицу")
        entity = legal_entity_id
        if buyer and buyer[0].id != entity:
            problems.append(f"в счёте покупатель «{buyer[0].name}», а выбрано другое юрлицо")
    elif len(buyer) == 1:
        entity = buyer[0].id
    elif veh is not None:
        entity = veh.legal_entity_id
    elif len(ents) == 1:
        entity = ents[0].id
    if veh is not None and entity is not None and veh.legal_entity_id != entity:
        found.pop("vehicle_id", None)                      # машина другого юрлица: только подсказка, не привязываем
    if entity is not None:
        found["legal_entity_id"] = entity
    # необязательные реквизиты с явным мусором (искажены при распознавании) отбрасываются, а не мешают внести счёт
    for k, (lengths, _hint) in DIGITS.items():
        if found.get(k) and (not str(found[k]).isdigit() or len(str(found[k])) not in lengths):
            found.pop(k)

    missing = [k for k in ("number", "bill_date", "total") if not found.get(k)]
    if not (found.get("supplier_name") or found.get("supplier_id")):
        missing.append("supplier_name")
    if entity is None:
        missing.append("legal_entity_id")

    def reply(created: bool, text_note: str, **extra) -> dict:
        return {"created": created, "bill": None, "duplicate": None, "found": plain(found), "missing": missing, "how": how, "readable": read_any,
                "confirmed": confirmed, "note": text_note, "errors": {}, **extra}

    confirmed = False
    if how == "none":
        if ocr.available():
            return reply(False, "Не удалось прочитать счёт: на фото или скане не нашлось текста. Заполните данные вручную, файлы уже приложены.")
        return reply(False, "Чтение фото и сканов на этом сервере не включено, а в файле нет текста. Заполните данные вручную, файлы уже приложены.")
    if missing:
        names = ", ".join(READ_LABELS[k] for k in missing)
        return reply(False, f"Не удалось прочитать: {names}. Впишите это в форму и нажмите «Внести счёт», файлы уже приложены.")
    d = dt.date.fromisoformat(found["bill_date"])
    if d > today() + dt.timedelta(days=30):
        problems.append("дата счёта в будущем, возможно, прочитана с ошибкой")
    confirmed, why = svc.confirm_total(text, found, how)
    if not confirmed:
        problems.append(why)
    if problems:
        return reply(False, "Счёт прочитан, но его не стоит вносить без проверки: " + "; ".join(problems) + ". Проверьте данные и нажмите «Внести счёт».")

    total = svc.to_money(found["total"], allow_zero=False)
    twin = db.scalar(select(RepairBill).where(RepairBill.legal_entity_id == entity, RepairBill.archived_at.is_(None), RepairBill.bill_date == d,
                                              func.lower(RepairBill.number) == str(found["number"]).lower(), RepairBill.total == total))
    if twin is not None:
        return reply(False, f"Такой счёт уже внесён: № {twin.number}.", duplicate={"id": twin.id, "number": twin.number})

    body = {"kind": "uploaded", "legal_entity_id": entity, "number": str(found["number"]), "bill_date": found["bill_date"], "total": str(total), "note": note}
    for k in SUPPLIER_FIELDS + ("basis", "supplier_id", "vehicle_id", "vat_pct"):
        if found.get(k) not in (None, ""):
            body[k] = found[k]
    if found.get("vat") is not None:
        body["vat"] = str(found["vat"])
    try:
        values, _items, _auto = validate(db, p, merged(None, BillIn.model_validate(body), None), "uploaded", None, "bills.create")
    except ValidationError as e:
        return reply(False, "Данные счёта не прошли проверку. Исправьте отмеченное и нажмите «Внести счёт».", errors=pydantic_fields(e))
    except HTTPException as e:
        d_ = e.detail if isinstance(e.detail, dict) else {}
        if e.status_code == 403:
            raise
        errs = {d_["field"]: d_.get("message", "Проверьте значение")} if d_.get("field") else {}
        return reply(False, d_.get("message", "Данные счёта не прошли проверку.") + " Исправьте и нажмите «Внести счёт».", errors=errs)
    b = RepairBill(kind="uploaded", created_by_id=p.id, updated_by_id=p.id, **values)
    db.add(b)
    db.flush()
    names = attach_prepared(db, p, b, prepared)
    audit.log(db, p, "bill.create", "bill", b.id, b.legal_entity_id, kind="uploaded", files=names, auto_read=how, **describe(db, b))
    db.commit()
    db.refresh(b)
    src = {"pdf": "Счёт прочитан из текста PDF.", "scan": f"Счёт прочитан со скана ({why}).", "photo": f"Счёт прочитан с фото ({why})."}[how]
    return reply(True, src + " Сверьте номер, дату и сумму с бумагой.",
                 bill={"id": b.id, "number": b.number, "total": float(b.total), "supplier_name": b.supplier_name, "bill_date": b.bill_date.isoformat()})


@router.get("/bills/files/{fid}")
def file_get(fid: int, thumb: bool = False, p: Principal = Depends(current), db: Session = Depends(get_db)):
    f = db.get(RepairBillFile, fid)
    if not f or f.removed_at or not p.can("bills.view", db.get(RepairBill, f.bill_id).legal_entity_id):
        raise fail(404, "not_found", "Файл не найден")
    sf = db.get(StoredFile, f.file_id)
    data, ctype = (sf.thumb, "image/jpeg") if thumb and sf.thumb else (sf.data, sf.content_type)
    name = urllib.parse.quote(sf.original_name or "bill")
    return Response(content=data, media_type=ctype,
                    headers={"Content-Disposition": f"inline; filename*=UTF-8''{name}", "Cache-Control": "private, no-store"})


@router.post("/bills/files/{fid}/remove")
def file_remove(fid: int, body: ReasonIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    f = db.get(RepairBillFile, fid)
    b = db.get(RepairBill, f.bill_id) if f else None
    if not f or f.removed_at or not p.can("bills.view", b.legal_entity_id):
        raise fail(404, "not_found", "Файл не найден")
    if not p.can("bills.edit", b.legal_entity_id):
        raise fail(403, "forbidden", "Нет права менять счета")
    if b.archived_at:
        raise fail(409, "bill_archived", "Счёт в архиве: сначала верните его")
    f.removed_at, f.removed_by_id, f.remove_reason = utcnow(), p.id, body.reason.strip() or "Убрано"
    b.updated_by_id = p.id
    audit.log(db, p, "bill.file.remove", "bill", b.id, b.legal_entity_id, number=b.number, file=f.file.original_name, reason=f.remove_reason)
    db.commit()
    return detail_out(db, p, b)


@router.get("/bills/{bid}")
def read_bill(bid: int, p: Principal = Depends(require("bills.view")), db: Session = Depends(get_db)):
    return detail_out(db, p, get_bill(db, p, bid))


@router.patch("/bills/{bid}")
def update_bill(bid: int, body: BillIn, p: Principal = Depends(require("bills.edit")), db: Session = Depends(get_db)):
    b = get_bill(db, p, bid, "bills.edit")
    if b.archived_at:
        raise fail(409, "bill_archived", "Счёт в архиве: сначала верните его")
    if body.kind not in (None, b.kind):
        raise fail(422, "kind_fixed", "Вид счёта менять нельзя: создайте новый счёт", "kind")
    if body.legal_entity_id not in (None, b.legal_entity_id):
        raise fail(422, "entity_fixed", "Юрлицо счёта менять нельзя", "legal_entity_id")
    if body.status is not None or body.paid_date is not None:
        raise fail(422, "use_status", "Состояние оплаты меняется отдельным действием «Оплата»", "status")
    if body.items is not None and b.kind != "issued":
        raise fail(422, "items_not_allowed", "Строки есть только у счёта, выписанного в программе", "items")
    before = describe(db, b)
    v = merged(b, body, body.items)
    values, items, _ = validate(db, p, v, b.kind, b, "bills.edit")
    for k, val in values.items():
        setattr(b, k, val)
    if items is not None:
        write_items(db, b, items)
    after = describe(db, b)
    changed = {k: {"было": before[k], "стало": after[k]} for k in after if str(before[k]) != str(after[k])}
    if body.items is not None:
        changed["items"] = f"строк: {len(items or [])}"
    if changed:
        b.updated_by_id = p.id
        audit.log(db, p, "bill.update", "bill", b.id, b.legal_entity_id, number=b.number, changes=changed)
    db.commit()
    db.refresh(b)
    return detail_out(db, p, b)


@router.post("/bills/{bid}/status")
def set_status(bid: int, body: StatusIn, p: Principal = Depends(require("bills.pay")), db: Session = Depends(get_db)):
    b = get_bill(db, p, bid, "bills.pay")
    if b.archived_at:
        raise fail(409, "bill_archived", "Счёт в архиве: сначала верните его")
    paid = check_paid(body.status, body.paid_date, b.bill_date, p, b.legal_entity_id)
    if (body.status, paid) != (b.status, b.paid_date):
        old = b.status
        b.status, b.paid_date, b.updated_by_id = body.status, paid, p.id
        audit.log(db, p, "bill.status", "bill", b.id, b.legal_entity_id, number=b.number, from_status=old, to_status=body.status,
                  paid_date=paid.isoformat() if paid else None)
    db.commit()
    return detail_out(db, p, b)


@router.post("/bills/{bid}/files")
def add_files(bid: int, files: list[UploadFile] = File(default=[]), p: Principal = Depends(require("bills.edit")), db: Session = Depends(get_db)):
    b = get_bill(db, p, bid, "bills.edit")
    if b.archived_at:
        raise fail(409, "bill_archived", "Счёт в архиве: сначала верните его")
    if b.kind == "issued":
        raise fail(422, "files_not_allowed", "К счёту, выписанному в программе, файл не прикладывают: он печатается из программы", "files")
    names = attach_files(db, p, b, files)
    if not names and not files:
        raise fail(422, "no_file", "Приложите файл", "files")
    if names:
        b.updated_by_id = p.id
        audit.log(db, p, "bill.file.add", "bill", b.id, b.legal_entity_id, number=b.number, files=names)
    db.commit()
    return {**detail_out(db, p, b), "added": len(names)}


@router.post("/bills/{bid}/void")
def void_bill(bid: int, body: ReasonIn, p: Principal = Depends(require("bills.void")), db: Session = Depends(get_db)):
    b = get_bill(db, p, bid, "bills.void")
    if b.archived_at:
        raise fail(409, "bill_archived", "Счёт уже в архиве")
    reason = body.reason.strip()
    if len(reason) < 3:
        raise fail(422, "reason_required", "Напишите причину (хотя бы несколько слов)", "reason")
    b.archived_at, b.archived_by_id, b.archive_reason, b.updated_by_id = utcnow(), p.id, reason, p.id
    audit.log(db, p, "bill.void", "bill", b.id, b.legal_entity_id, number=b.number, total=str(b.total), reason=reason)
    db.commit()
    return detail_out(db, p, b)


@router.post("/bills/{bid}/restore")
def restore_bill(bid: int, p: Principal = Depends(require("bills.void")), db: Session = Depends(get_db)):
    b = get_bill(db, p, bid, "bills.void")
    if not b.archived_at:
        raise fail(409, "not_archived", "Счёт не в архиве")
    clash = db.scalar(select(RepairBill.id).where(RepairBill.legal_entity_id == b.legal_entity_id, RepairBill.archived_at.is_(None), RepairBill.id != b.id,
                                                  RepairBill.bill_date == b.bill_date, RepairBill.total == b.total,
                                                  func.lower(RepairBill.number) == b.number.lower()))
    if clash:
        raise fail(409, "duplicate_bill", f"Нельзя вернуть: такой счёт уже внесён (запись № {clash})")
    if b.kind == "issued":
        taken = [n for (n,) in db.execute(select(RepairBill.number).where(
            RepairBill.legal_entity_id == b.legal_entity_id, RepairBill.kind == "issued", RepairBill.archived_at.is_(None), RepairBill.id != b.id,
            RepairBill.bill_date >= dt.date(b.bill_date.year, 1, 1), RepairBill.bill_date <= dt.date(b.bill_date.year, 12, 31)))]
        if b.number.casefold() in {n.casefold() for n in taken}:
            raise fail(409, "number_taken", f"Нельзя вернуть: номер {b.number} уже занят другим выписанным счётом")
    b.archived_at = b.archived_by_id = None
    b.archive_reason, b.updated_by_id = "", p.id
    audit.log(db, p, "bill.restore", "bill", b.id, b.legal_entity_id, number=b.number)
    db.commit()
    return detail_out(db, p, b)


# ---------------------------------------------------------------- данные для печати

@router.get("/print/bills/{bid}")
def print_bill(bid: int, p: Principal = Depends(require("bills.view")), db: Session = Depends(get_db)):
    from app.routers.trade import _seller
    b = get_bill(db, p, bid)
    if b.kind != "issued":
        raise fail(404, "not_found", "Печатается только счёт, выписанный в программе")
    ent = db.get(LegalEntity, b.legal_entity_id)
    v = db.get(Vehicle, b.vehicle_id) if b.vehicle_id else None
    items = list(db.scalars(select(RepairBillItem).where(RepairBillItem.bill_id == b.id).order_by(RepairBillItem.pos)))
    missing = [label for name, label in (("inn", "ИНН"), ("address", "адрес")) if not getattr(ent, name)]
    missing += [label for name, label in (("bank_name", "банк поставщика"), ("bik", "БИК поставщика"), ("account", "расчётный счёт поставщика")) if not getattr(b, name)]
    return {"kind": "repair_bill", "title": "Счёт на оплату", "number": b.number, "date": b.bill_date.isoformat(), "archived": b.archived_at is not None,
            "buyer": _seller(ent),
            "supplier": {"name": b.supplier_name, "inn": b.supplier_inn, "kpp": b.supplier_kpp, "address": b.supplier_addr, "phone": b.supplier_phone,
                         "bank_name": b.bank_name, "bik": b.bik, "account": b.account, "corr_account": b.corr_account},
            "basis": b.basis, "vehicle": vehicle_label(v), "vat_pct": b.vat_pct, "vat_label": VAT_RATES[str(b.vat_pct)],
            "items": [{"pos": i.pos, "name": i.name, "article": i.article, "qty": parts_svc.qty_text(i.qty), "unit": i.unit, "price": money(i.price),
                       "amount": money(i.amount), "vat": money(i.vat)} for i in items],
            "total": money(b.total), "vat": money(b.vat), "words": amount_in_words(b.total), "note": b.note, "missing": missing}
