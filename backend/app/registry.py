# -*- coding: utf-8 -*-
"""Общий каркас «учётных» разделов: справочники и денежные журналы.

Раздел описывается один раз (поля, права, правила), а каркас даёт всё остальное одинаково для всех:
  GET    /api/<раздел>/meta          описание полей для экрана и что можно этому человеку
  GET    /api/<раздел>               список: поиск, даты, фильтры, постранично, итоги по суммам
  GET    /api/<раздел>/export.csv    то же в файл (открывается в Excel)
  POST   /api/<раздел>               добавить
  PATCH  /api/<раздел>/{id}          изменить
  POST   /api/<раздел>/{id}/void     убрать в архив (причина обязательна)
  POST   /api/<раздел>/{id}/restore  вернуть из архива
Права: <раздел>.view / create / edit / void; доступ ограничен юрлицами, в которых право выдано.
Записи не удаляются, каждое действие пишется в журнал с тем, что именно изменилось.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field as PField
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.common import csv_text, fail, like, org_today, page_of, utcnow
from app.config import get_settings
from app.db import get_db
from app.deps import NOTHING, Principal, Scope, current, entity_filter
from app.models import CashAccount, Counterparty, Employee, EmploymentAssignment, LegalEntity, Order, PpeNorm, Tariff, Vehicle
from app.routers import audit_log

MAX_MONEY = Decimal("100000000000")        # 100 млрд: защита от опечатки с лишними нулями


class Invalid(Exception):
    """Ошибка проверки: поле -> понятный текст."""

    def __init__(self, fields: dict[str, str]):
        self.fields = fields


@dataclass
class F:
    """Поле раздела."""
    name: str
    label: str
    kind: str = "text"           # text | textarea | int | decimal | money | date | datetime | enum | bool | ref | entity
    required: bool = False
    options: dict[str, str] | None = None
    ref: str | None = None       # vehicle | employee | counterparty | customer | account | tariff | order
    max: int = 200
    lo: Decimal | None = None    # наименьшее значение для чисел
    places: int = 2
    default: Any = None
    hint: str = ""
    list: bool = True            # показывать в таблице
    filter: bool = False         # отбор в списке по этому полю
    sum: bool = False            # итог по колонке внизу
    locked: bool = False         # нельзя менять после добавления
    auto: bool = False           # заполняет сервер, в форме поля нет
    show_if: tuple[str, str] | None = None   # поле видно и обязательно, только когда другое поле равно значению
    suggest: bool = False        # подсказывать уже использованные значения
    wide: bool = False           # в форме на всю ширину


@dataclass
class Col:
    """Вычисляемая колонка (не хранится в базе)."""
    name: str
    label: str
    kind: str = "text"           # text | money | badge | number | date
    sum: bool = False
    first: bool = False          # показывать первой колонкой, до полей


@dataclass
class R:
    key: str                     # адрес раздела в API и имя права (fines → fines.view)
    title: str                   # «Штрафы»
    one: str                     # «штраф»
    model: type
    fields: list[F]
    scope: str = "entity"        # entity: у записи есть юрлицо; global: общий справочник
    search: tuple[str, ...] = ()
    search_refs: tuple = ()      # ((поле-ссылка, модель, (поля модели,)), ...): искать ещё и по имени того, на что ссылаемся
    states: dict[str, str] = field(default_factory=dict)       # быстрый отбор «не оплачены / оплачены»
    state_filter: Callable | None = None                        # state_filter(query, state, today) -> query
    date: str | None = None      # поле даты для отбора «с — по»
    order: tuple = ()            # поля сортировки (имена атрибутов модели; "-имя" — по убыванию)
    entity_from: str | None = None   # vehicle: юрлицо берётся из выбранной техники
    prepare: Callable | None = None  # prepare(db, p, v, obj, m): доп. проверки и вычисления; v — что запишем, m — итоговые значения целиком
    computed: Callable | None = None  # computed(db, objs) -> {id: {имя: значение}}
    cols: list[Col] = field(default_factory=list)
    warn: Callable | None = None     # warn(db, obj) -> [текст]: предупреждения после сохранения
    unique_field: str | None = None
    unique_text: str = "Такая запись уже есть"
    perm_prefix: str = ""        # если права называются не так же, как раздел
    before_void: Callable | None = None   # before_void(db, obj): можно ли убрать запись в архив; иначе Invalid
    after_save: Callable | None = None    # after_save(db, p, obj, creating) -> [текст] | None: связанные записи; до сохранения в базе (commit), ошибка отменяет всё


RESOURCES: dict[str, R] = {}


# ---------------------------------------------------------------- справочники для полей-ссылок

def _vehicle_label(v: Vehicle) -> str:
    return f"{v.plate} · {(v.make + ' ' + v.model).strip()}".rstrip(" ·")


def _emp_label(e: Employee) -> str:
    return " ".join(x for x in (e.last_name, e.first_name, e.middle_name) if x)


def order_label(number: int, year: int, customer: str) -> str:
    return f"№ {number}/{year} · {customer}"


def org_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().org_timezone)


def ref_labels(db: Session, ref: str, ids: set[int]) -> dict[int, str]:
    """Подписи для списка идентификаторов. Права на сами записи проверены выше: показываем только то, на что ссылается видимая запись."""
    if not ids:
        return {}
    if ref == "vehicle":
        return {v.id: _vehicle_label(v) for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(ids)))}
    if ref == "employee":
        return {e.id: _emp_label(e) for e in db.scalars(select(Employee).where(Employee.id.in_(ids)))}
    if ref in ("counterparty", "customer"):
        return {c.id: c.name for c in db.scalars(select(Counterparty).where(Counterparty.id.in_(ids)))}
    if ref == "tariff":
        return {t.id: t.name for t in db.scalars(select(Tariff).where(Tariff.id.in_(ids)))}
    if ref == "order":
        rows = db.execute(select(Order.id, Order.number, Order.year, Order.order_date, Counterparty.name)
                          .join(Counterparty, Counterparty.id == Order.customer_id).where(Order.id.in_(ids))).all()
        return {r[0]: order_label(r[1], r[2], r[4]) for r in rows}
    if ref == "account":
        return {a.id: a.name for a in db.scalars(select(CashAccount).where(CashAccount.id.in_(ids)))}
    if ref == "ppe":
        return {n.id: n.name for n in db.scalars(select(PpeNorm).where(PpeNorm.id.in_(ids)))}
    return {}


# ---------------------------------------------------------------- разбор значений

def _money(raw: Any, f: F) -> Decimal:
    text_ = str(raw).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", text_):             # без «1e3», «inf», «1_000» и прочих хитростей
        raise Invalid({f.name: "Введите число, например 1250,50"})
    d = Decimal(text_)
    q = Decimal(1).scaleb(-f.places)
    d = d.quantize(q, rounding=ROUND_HALF_UP)
    lo = f.lo if f.lo is not None else Decimal(0)
    if d < lo:
        raise Invalid({f.name: ("Не меньше %s" % str(lo).replace(".", ",")) if lo else "Не может быть отрицательным"})
    if d > MAX_MONEY:
        raise Invalid({f.name: "Слишком большое число: проверьте нули"})
    return d


def parse_one(f: F, raw: Any) -> Any:
    empty = raw is None or (isinstance(raw, str) and raw.strip() == "")
    if empty:
        if f.required:
            raise Invalid({f.name: "Заполните поле"})
        return False if f.kind == "bool" else ("" if f.kind in ("text", "textarea") else None)
    if f.kind in ("text", "textarea"):
        s = str(raw).strip()
        if len(s) > (f.max if f.kind == "text" else 2000):
            raise Invalid({f.name: "Слишком длинно: не больше %d знаков" % (f.max if f.kind == "text" else 2000)})
        return s
    if f.kind == "int":
        try:
            n = int(str(raw).strip().replace(" ", ""))
        except ValueError:
            raise Invalid({f.name: "Введите целое число"}) from None
        if f.lo is not None and n < f.lo:
            raise Invalid({f.name: "Не меньше %s" % f.lo})
        return n
    if f.kind in ("money", "decimal"):
        return _money(raw, f)
    if f.kind == "date":
        try:
            d = dt.date.fromisoformat(str(raw)[:10])
        except ValueError:
            raise Invalid({f.name: "Введите дату"}) from None
        if not (dt.date(2000, 1, 1) <= d <= dt.date(2100, 1, 1)):
            raise Invalid({f.name: "Проверьте год: дата вне разумных пределов"})
        return d
    if f.kind == "datetime":
        try:
            d = dt.datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
        except ValueError:
            raise Invalid({f.name: "Введите дату и время"}) from None
        d = d.replace(tzinfo=org_tz()) if d.tzinfo is None else d      # без пояса — время по часам организации
        d = d.astimezone(dt.timezone.utc)
        if not (dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc) <= d <= dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)):
            raise Invalid({f.name: "Проверьте год: дата вне разумных пределов"})
        return d
    if f.kind == "enum":
        if str(raw) not in (f.options or {}):
            raise Invalid({f.name: "Выберите значение из списка"})
        return str(raw)
    if f.kind == "bool":
        return bool(raw) if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "да", "on")
    if f.kind in ("ref", "entity"):
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise Invalid({f.name: "Выберите значение из списка"}) from None
    return raw


def _visible(f: F, values: dict) -> bool:
    return f.show_if is None or str(values.get(f.show_if[0])) == f.show_if[1]


def parse_body(res: R, body: dict, current_vals: dict | None) -> dict:
    """Разбирает присланные поля. current_vals есть при правке: тогда присланные поля меняются, остальные остаются."""
    errors: dict[str, str] = {}
    out: dict[str, Any] = {}
    merged = dict(current_vals or {})
    for f in res.fields:
        if f.auto:
            continue
        if current_vals is not None and f.name not in body:
            continue
        raw = body.get(f.name, f.default)
        merged[f.name] = raw
        try:
            out[f.name] = parse_one(f, raw) if _visible(f, {**merged}) else (None if f.kind not in ("text", "textarea") else "")
        except Invalid as e:
            errors.update(e.fields)
    # условно обязательные поля проверяем уже по итоговым значениям
    final = {**(current_vals or {}), **out}
    for f in res.fields:
        if f.auto or f.show_if is None or f.name in errors:
            continue
        if _visible(f, final) and f.required and final.get(f.name) in (None, ""):
            errors[f.name] = "Заполните поле"
    if errors:
        raise Invalid(errors)
    return out


def serialize(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def current_values(res: R, obj: Any) -> dict:
    return {f.name: getattr(obj, f.name) for f in res.fields if hasattr(obj, f.name)}


# ---------------------------------------------------------------- права и запросы

def perm(res: R, action: str) -> str:
    return f"{res.perm_prefix or res.key}.{action}"


def _denied():
    return fail(403, "forbidden", "Нет доступа к этому действию")


def allowed_entities(db: Session, p: Principal, code: str) -> list[dict]:
    scope = p.scope(code)
    q = select(LegalEntity).where(LegalEntity.archived_at.is_(None)).order_by(LegalEntity.name)
    if not scope.everything:
        q = q.where(LegalEntity.id.in_(scope.ids or {-1}))
    return [{"id": e.id, "name": e.short_name or e.name} for e in db.scalars(q)]


def base_query(res: R, p: Principal, entity_id: int | None, archived: bool):
    code = perm(res, "view")
    q = select(res.model)
    if res.scope == "entity":
        scope = entity_filter(p, code, entity_id)
        q = q.where(scope.cond(res.model.legal_entity_id))
    elif not p.can(code):
        raise _denied()
    return q.where(res.model.archived_at.is_not(None) if archived else res.model.archived_at.is_(None))


def apply_filters(res: R, q, params: dict, *, search: str, date_from: str, date_to: str):
    if search.strip() and (res.search or res.search_refs):
        t = like(search.strip())
        conds = [getattr(res.model, c).ilike(t) for c in res.search]
        for col, model, attrs in res.search_refs:
            conds.append(getattr(res.model, col).in_(select(model.id).where(or_(*[getattr(model, a).ilike(t) for a in attrs]))))
        q = q.where(or_(*conds))
    state = params.get("state")
    if state:
        if state not in res.states or res.state_filter is None:
            raise fail(422, "bad_filter", "Неверное значение отбора", "state")
        q = res.state_filter(q, state, org_today())
    if res.date:
        col = getattr(res.model, res.date)
        for raw, op in ((date_from, "ge"), (date_to, "le")):
            if raw:
                try:
                    d = dt.date.fromisoformat(raw)
                except ValueError:
                    raise fail(422, "bad_date", "Введите дату", "date_from" if op == "ge" else "date_to") from None
                q = q.where(col >= d if op == "ge" else col <= d)
    for f in res.fields:
        if f.filter and params.get(f.name) not in (None, ""):
            raw = params[f.name]
            try:
                value = parse_one(F(f.name, f.label, f.kind, options=f.options), raw)
            except Invalid:
                raise fail(422, "bad_filter", "Неверное значение отбора", f.name) from None
            q = q.where(getattr(res.model, f.name) == value)
    return q


def ordering(res: R, q):
    cols = []
    for name in res.order or ("-id",):
        desc = name.startswith("-")
        c = getattr(res.model, name.lstrip("-"))
        cols.append(c.desc() if desc else c.asc())
    return q.order_by(*cols, res.model.id.desc())


def row_out(res: R, obj: Any, labels: dict[str, dict[int, str]], extra: dict, p: Principal, entities: dict[int, str]) -> dict:
    d: dict[str, Any] = {"id": obj.id}
    for f in res.fields:
        v = getattr(obj, f.name, None)
        d[f.name] = serialize(v)
        if f.kind == "ref":
            d[f.name + "_label"] = labels.get(f.ref or "", {}).get(v, "") if v else ""
    if res.scope == "entity":
        d["legal_entity_id"] = obj.legal_entity_id
        d["legal_entity"] = entities.get(obj.legal_entity_id, "")
        d["can_edit"] = p.can(perm(res, "edit"), obj.legal_entity_id)
        d["can_void"] = p.can(perm(res, "void"), obj.legal_entity_id)
    else:
        d["can_edit"], d["can_void"] = p.can(perm(res, "edit")), p.can(perm(res, "void"))
    d["archived"] = obj.archived_at is not None
    d["archive_reason"] = obj.archive_reason
    d["created_at"] = serialize(obj.created_at)
    d.update({k: serialize(v) for k, v in extra.items()})
    return d


def rows_out(db: Session, res: R, objs: list, p: Principal) -> list[dict]:
    labels: dict[str, dict[int, str]] = {}
    for f in res.fields:
        if f.kind == "ref" and f.ref:
            ids = {getattr(o, f.name) for o in objs if getattr(o, f.name)}
            labels.setdefault(f.ref, {}).update(ref_labels(db, f.ref, ids))
    extra = res.computed(db, objs) if res.computed else {}
    entities = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))} if res.scope == "entity" else {}
    return [row_out(res, o, labels, extra.get(o.id, {}), p, entities) for o in objs]


def totals(db: Session, res: R, q) -> dict:
    sums = [f for f in res.fields if f.sum]
    if not sums:
        return {}
    ids = q.order_by(None).with_only_columns(res.model.id).subquery()
    row = db.execute(select(*[func.coalesce(func.sum(getattr(res.model, f.name)), 0) for f in sums]).where(res.model.id.in_(select(ids.c.id)))).one()
    return {f.name: float(v) for f, v in zip(sums, row)}


# ---------------------------------------------------------------- запись

class VoidIn(BaseModel):
    reason: str = PField(min_length=3, max_length=300)


def _raise(e: Invalid):
    return HTTPException(status_code=422, detail={"code": "validation", "message": "Проверьте отмеченные поля", "field": None, "fields": e.fields})


def _changes(res: R, before: dict, after: dict) -> dict:
    out = {}
    for f in res.fields:
        if f.name in after and before.get(f.name) != after[f.name]:
            out[f.name] = [serialize(before.get(f.name)), serialize(after[f.name])]
    return out


def _entity_of(res: R, db: Session, values: dict, obj: Any | None) -> int | None:
    """Юрлицо записи: у существующей остаётся прежним; у новой берётся из выбранной техники или выбирается человеком."""
    if obj is not None:
        return obj.legal_entity_id
    if res.entity_from == "vehicle":
        vid = values.get("vehicle_id")
        v = db.get(Vehicle, vid) if vid else None
        return v.legal_entity_id if v else None
    return values.get("legal_entity_id")


def validate_refs(db: Session, res: R, values: dict, entity_id: int | None, obj: Any | None) -> None:
    """Всё, на что ссылается запись, должно существовать и принадлежать тому же юрлицу (контрагенты общие)."""
    errors: dict[str, str] = {}
    for f in res.fields:
        v = values.get(f.name)
        if f.kind != "ref" or v is None:
            continue
        changed = obj is None or getattr(obj, f.name) != v
        pick = "Выберите значение из списка"
        if f.ref == "vehicle":
            row = db.get(Vehicle, v)
            if row is None or (entity_id is not None and row.legal_entity_id != entity_id):
                errors[f.name] = pick if row is None else "Эта техника из другого юрлица"
            elif changed and row.archived_at:
                errors[f.name] = "Техника в архиве"
        elif f.ref == "employee":
            row = db.get(Employee, v)
            works = db.scalar(select(EmploymentAssignment.id).where(EmploymentAssignment.employee_id == v,
                                                                   EmploymentAssignment.legal_entity_id == entity_id).limit(1)) if row else None
            if row is None:
                errors[f.name] = pick
            elif works is None:
                errors[f.name] = "Этот сотрудник не работает в выбранном юрлице"
            elif changed and row.archived_at:
                errors[f.name] = "Сотрудник в архиве"
        elif f.ref in ("counterparty", "customer"):
            row = db.get(Counterparty, v)
            if row is None:
                errors[f.name] = pick
            elif changed and row.archived_at:
                errors[f.name] = "Контрагент в архиве"
            elif f.ref == "customer" and changed and row.kind == "supplier":
                errors[f.name] = "Это поставщик, а нужен заказчик: измените вид контрагента в справочнике"
        elif f.ref == "tariff":
            row = db.get(Tariff, v)
            if row is None:
                errors[f.name] = pick
            elif changed and row.archived_at:
                errors[f.name] = "Тариф в архиве"
        elif f.ref == "order":
            row = db.get(Order, v)
            if row is None or (entity_id is not None and row.legal_entity_id != entity_id):
                errors[f.name] = pick if row is None else "Этот заказ из другого юрлица"
            elif changed and row.archived_at:
                errors[f.name] = "Заказ в архиве"
        elif f.ref == "ppe":
            row = db.get(PpeNorm, v)
            if row is None:
                errors[f.name] = pick
            elif changed and row.archived_at:
                errors[f.name] = "Эта позиция убрана из норм: выберите другую или верните её в нормах"
        elif f.ref == "account":
            row = db.get(CashAccount, v)
            if row is None or (entity_id is not None and row.legal_entity_id != entity_id):
                errors[f.name] = pick if row is None else "Этот счёт из другого юрлица"
            elif changed and row.archived_at:
                errors[f.name] = "Счёт в архиве"
    if errors:
        raise Invalid(errors)


def save(db: Session, res: R, p: Principal, body: dict, obj: Any | None, *, code: str | None = None,
         extra: dict | None = None) -> tuple[Any, list[str]]:
    """code — другое право вместо обычного (водитель вносит «своё»: trips.create_own); extra — значения, которые
    задаёт программа, а не человек (кто внёс, код повторной отправки)."""
    creating = obj is None
    code = code or perm(res, "create" if creating else "edit")
    try:
        values = parse_body(res, body, None if creating else current_values(res, obj))
        if extra:
            values.update(extra)
        if not creating:
            for f in res.fields:
                if f.locked and f.name in values and values[f.name] != getattr(obj, f.name):
                    raise Invalid({f.name: "Это поле после добавления не меняется"})
        entity_id = None
        if res.scope == "entity":
            if creating and not res.entity_from:
                values["legal_entity_id"] = parse_one(F("legal_entity_id", "Юридическое лицо", "entity", required=True), body.get("legal_entity_id"))
            entity_id = _entity_of(res, db, values, obj)
            if entity_id is None:
                raise Invalid({("vehicle_id" if res.entity_from == "vehicle" else "legal_entity_id"): "Выберите значение из списка"})
            if res.entity_from == "vehicle" and not creating and "vehicle_id" in values:
                veh = db.get(Vehicle, values["vehicle_id"])
                if veh is None or veh.legal_entity_id != entity_id:
                    raise Invalid({"vehicle_id": "Эта техника из другого юрлица: добавьте запись заново"})
            entity = db.get(LegalEntity, entity_id)
            if entity is None or (creating and entity.archived_at):
                raise Invalid({"legal_entity_id": "Выберите юридическое лицо"})
            if creating:
                values["legal_entity_id"] = entity_id
            if not p.can(code, entity_id):
                if creating and res.entity_from == "vehicle":
                    raise Invalid({"vehicle_id": "Выберите значение из списка"})
                raise fail(403, "forbidden", "Нет права на это юридическое лицо")
        elif not p.can(code):
            raise _denied()
        validate_refs(db, res, {**(current_values(res, obj) if obj is not None else {}), **values}, entity_id, obj)
        if res.prepare:
            merged = {**(current_values(res, obj) if obj is not None else {}), "legal_entity_id": entity_id, **values}
            res.prepare(db, p, values, obj, merged)
    except Invalid as e:
        raise _raise(e) from None

    before = {} if creating else current_values(res, obj)
    if creating:
        obj = res.model(**values, created_by_id=p.id, updated_by_id=p.id)
        db.add(obj)
    else:
        for k, v in values.items():
            setattr(obj, k, v)
        obj.updated_by_id = p.id
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _raise(Invalid({res.unique_field or "_": res.unique_text})) from None
    entity_id = getattr(obj, "legal_entity_id", None)
    audit.log(db, p, f"{res.key}.{'create' if creating else 'update'}", res.key, obj.id, entity_id,
              changes=_changes(res, before, values) if not creating else {k: serialize(v) for k, v in values.items() if v not in (None, "")})
    warnings = list(res.warn(db, obj)) if res.warn else []
    if res.after_save:
        warnings += res.after_save(db, p, obj, creating) or []
    db.commit()
    return obj, warnings


def get_for(db: Session, res: R, p: Principal, oid: int, action: str) -> Any:
    """Запись, к которой у человека есть право. Чужое юрлицо выглядит как «не найдено»."""
    obj = db.get(res.model, oid)
    if obj is None:
        raise fail(404, "not_found", "Запись не найдена")
    ent = getattr(obj, "legal_entity_id", None) if res.scope == "entity" else None
    if not p.can(perm(res, "view"), ent):
        raise fail(404, "not_found", "Запись не найдена")
    if not p.can(perm(res, action), ent):
        raise _denied()
    return obj


# ---------------------------------------------------------------- маршруты

def meta_out(db: Session, res: R, p: Principal) -> dict:
    code_c = perm(res, "create")
    fields = []
    suggestions = {}
    for f in res.fields:
        if f.auto:
            continue
        fields.append({"name": f.name, "label": f.label, "kind": f.kind, "required": f.required, "ref": f.ref, "hint": f.hint,
                       "options": [{"value": k, "label": v} for k, v in (f.options or {}).items()] if f.options else None,
                       "list": f.list, "filter": f.filter, "sum": f.sum, "locked": f.locked, "wide": f.wide or f.kind == "textarea",
                       "show_if": list(f.show_if) if f.show_if else None, "suggest": f.suggest, "max": f.max,
                       "default": serialize(f.default)})
        if f.suggest:
            col = getattr(res.model, f.name)
            rows = db.execute(select(col, func.count()).where(col != "").group_by(col).order_by(func.count().desc()).limit(40)).all()
            suggestions[f.name] = [r[0] for r in rows]
    return {
        "key": res.key, "title": res.title, "one": res.one, "scope": res.scope, "entity_from": res.entity_from,
        "date_field": res.date, "states": [{"value": k, "label": v} for k, v in res.states.items()], "fields": fields, "columns": [{"name": c.name, "label": c.label, "kind": c.kind, "sum": c.sum, "first": c.first} for c in res.cols],
        "entities": allowed_entities(db, p, code_c) if res.scope == "entity" else [], "suggestions": suggestions,
        "can": {"view": p.can(perm(res, "view")), "create": p.can(code_c), "edit": p.can(perm(res, "edit")), "void": p.can(perm(res, "void"))},
    }


def build_router(res: R) -> APIRouter:
    r = APIRouter(prefix=f"/{res.key}", tags=[res.title])
    view_perm = perm(res, "view")

    def need_view(p: Principal = Depends(current)) -> Principal:
        if not p.can(view_perm):
            raise _denied()
        return p

    @r.get("/meta")
    def meta(p: Principal = Depends(need_view), db: Session = Depends(get_db)):
        return meta_out(db, res, p)

    def listing(request: Request, p: Principal, db: Session, q: str, date_from: str, date_to: str, legal_entity_id: int | None, archived: bool):
        query = base_query(res, p, legal_entity_id, archived)
        query = apply_filters(res, query, dict(request.query_params), search=q, date_from=date_from, date_to=date_to)
        return ordering(res, query), query

    @r.get("")
    def list_(request: Request, q: str = "", date_from: str = "", date_to: str = "", legal_entity_id: int | None = None,
              archived: bool = False, page: int = 1, page_size: int = 50, p: Principal = Depends(need_view), db: Session = Depends(get_db)):
        ordered, plain = listing(request, p, db, q, date_from, date_to, legal_entity_id, archived)
        rows, total = page_of(db, ordered, page, page_size)
        objs = [x[0] for x in rows]
        out = rows_out(db, res, objs, p)
        sums = totals(db, res, plain)
        if res.computed and any(c.sum for c in res.cols):
            full = [x[0] for x in db.execute(plain).all()] if total <= 5000 else []
            ext = res.computed(db, full) if full else {}
            for c in res.cols:
                if c.sum:
                    sums[c.name] = float(sum((Decimal(str(ext.get(o.id, {}).get(c.name) or 0)) for o in full), Decimal(0)))
        return {"items": out, "total": total, "page": page, "page_size": page_size, "totals": sums}

    @r.get("/export.csv")
    def export(request: Request, q: str = "", date_from: str = "", date_to: str = "", legal_entity_id: int | None = None,
               archived: bool = False, p: Principal = Depends(need_view), db: Session = Depends(get_db)):
        ordered, _plain = listing(request, p, db, q, date_from, date_to, legal_entity_id, archived)
        objs = [x[0] for x in db.execute(ordered.limit(20000)).all()]
        items = rows_out(db, res, objs, p)
        shown = [f for f in res.fields if not f.auto]
        head = (["Юрлицо"] if res.scope == "entity" else []) + [f.label for f in shown] + [c.label for c in res.cols]
        rows: list[list[Any]] = [head]
        for it in items:
            line: list[Any] = [it["legal_entity"]] if res.scope == "entity" else []
            for f in shown:
                v = it.get(f.name + "_label") if f.kind == "ref" else it.get(f.name)
                if f.kind == "enum":
                    v = (f.options or {}).get(v, v)
                elif f.kind == "bool":
                    v = "да" if v else "нет"
                elif f.kind == "date" and v:
                    v = ".".join(reversed(str(v).split("-")))
                elif f.kind == "datetime" and v:
                    v = dt.datetime.fromisoformat(str(v)).astimezone(org_tz()).strftime("%d.%m.%Y %H:%M")
                elif isinstance(v, float):
                    v = f"{v:.{f.places}f}".replace(".", ",")
                line.append("" if v is None else v)
            for c in res.cols:
                v = it.get(c.name)
                if isinstance(v, dict):
                    v = v.get("text", "")
                elif c.kind == "date" and v:
                    v = ".".join(reversed(str(v).split("-")))
                elif isinstance(v, float):
                    v = f"{v:.2f}".replace(".", ",")
                line.append("" if v is None else v)
            rows.append(line)
        audit.log(db, p, f"{res.key}.export", res.key, None, legal_entity_id, rows=len(rows) - 1)
        db.commit()
        return Response(csv_text(rows), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{res.key}.csv"'})

    @r.post("", status_code=201)
    def create(body: dict, p: Principal = Depends(current), db: Session = Depends(get_db)):
        if not p.can(perm(res, "create")):
            raise _denied()
        obj, warnings = save(db, res, p, body, None)
        return {**rows_out(db, res, [obj], p)[0], "warnings": warnings}

    @r.patch("/{oid}")
    def update(oid: int, body: dict, p: Principal = Depends(current), db: Session = Depends(get_db)):
        obj = get_for(db, res, p, oid, "edit")
        if obj.archived_at:
            raise fail(409, "archived", "Запись в архиве: сначала верните её")
        obj, warnings = save(db, res, p, body, obj)
        return {**rows_out(db, res, [obj], p)[0], "warnings": warnings}

    @r.post("/{oid}/void")
    def void(oid: int, body: VoidIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
        obj = get_for(db, res, p, oid, "void")
        if obj.archived_at:
            raise fail(409, "archived", "Запись уже в архиве")
        if res.before_void:
            try:
                res.before_void(db, obj)
            except Invalid as e:
                raise fail(409, "cannot_void", next(iter(e.fields.values()))) from None
        obj.archived_at, obj.archived_by_id, obj.archive_reason, obj.updated_by_id = utcnow(), p.id, body.reason.strip(), p.id
        audit.log(db, p, f"{res.key}.void", res.key, obj.id, getattr(obj, "legal_entity_id", None), reason=body.reason.strip())
        db.commit()
        return rows_out(db, res, [obj], p)[0]

    @r.post("/{oid}/restore")
    def restore(oid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
        obj = get_for(db, res, p, oid, "void")
        if not obj.archived_at:
            raise fail(409, "not_archived", "Запись не в архиве")
        if res.prepare:                       # те же правила, что при добавлении: например, аренда не должна пересечься с другой
            merged = {**current_values(res, obj), "legal_entity_id": getattr(obj, "legal_entity_id", None)}
            try:
                res.prepare(db, p, {}, obj, merged)
            except Invalid as e:
                raise fail(409, "cannot_restore", "Нельзя вернуть: " + next(iter(e.fields.values()))) from None
        obj.archived_at, obj.archived_by_id, obj.archive_reason, obj.updated_by_id = None, None, "", p.id
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise fail(409, "duplicate", "Нельзя вернуть: " + res.unique_text.lower()) from None
        audit.log(db, p, f"{res.key}.restore", res.key, obj.id, getattr(obj, "legal_entity_id", None))
        db.commit()
        return rows_out(db, res, [obj], p)[0]

    return r


def register(res: R) -> R:
    RESOURCES[res.key] = res
    audit_log.ACTIONS.update({
        f"{res.key}.create": f"{res.title}: запись добавлена", f"{res.key}.update": f"{res.title}: запись изменена",
        f"{res.key}.void": f"{res.title}: запись в архиве", f"{res.key}.restore": f"{res.title}: запись возвращена",
        f"{res.key}.export": f"{res.title}: выгрузка списка",
    })
    return res


# ---------------------------------------------------------------- варианты для полей-ссылок

options_router = APIRouter(prefix="/options", tags=["Справочники для полей"])


def ref_scope(p: Principal, ref: str, view_perm: str | None, legal_entity_id: int | None) -> Scope:
    """Что человек может выбирать в поле-ссылке: то, что ему и так доступно для просмотра, плюс то, что нужно
    разделам, где он может добавлять или менять записи с такой ссылкой (бухгалтеру нужны водители и техника для штрафа)."""
    scope = p.scope(view_perm) if view_perm else NOTHING
    for res in RESOURCES.values():
        if any(f.kind == "ref" and f.ref == ref for f in res.fields):
            scope = scope | p.scope(perm(res, "create")) | p.scope(perm(res, "edit"))
    if legal_entity_id is not None and not scope.allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
    return scope.narrow(legal_entity_id)


@options_router.get("/{ref}")
def options(ref: str, q: str = "", legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Что можно выбрать в поле-ссылке. Показываем только записи юрлиц, к которым у человека есть доступ."""
    from app.routers.employees import visible_filter
    t = like(q.strip()) if q.strip() else None
    if ref == "vehicle":
        scope = ref_scope(p, ref, "vehicles.view", legal_entity_id)
        query = select(Vehicle).where(scope.cond(Vehicle.legal_entity_id), Vehicle.archived_at.is_(None)).order_by(Vehicle.plate)
        if t:
            query = query.where(or_(Vehicle.plate.ilike(t), Vehicle.make.ilike(t), Vehicle.model.ilike(t)))
        ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
        return {"items": [{"id": v.id, "label": _vehicle_label(v), "legal_entity_id": v.legal_entity_id, "legal_entity": ents.get(v.legal_entity_id, "")}
                          for v in db.scalars(query.limit(300))]}
    if ref == "employee":
        scope = ref_scope(p, ref, "employees.view", legal_entity_id)
        query = select(Employee).where(Employee.archived_at.is_(None)).order_by(Employee.last_name, Employee.first_name)
        cond = visible_filter(scope)
        if cond is not None:
            query = query.where(cond)
        if legal_entity_id is not None:
            query = query.where(select(EmploymentAssignment.id).where(EmploymentAssignment.employee_id == Employee.id,
                                                                    EmploymentAssignment.legal_entity_id == legal_entity_id).exists())
        if t:
            query = query.where(or_(Employee.last_name.ilike(t), Employee.first_name.ilike(t)))
        return {"items": [{"id": e.id, "label": _emp_label(e), "is_driver": e.is_driver} for e in db.scalars(query.limit(300))]}
    if ref in ("counterparty", "customer", "supplier"):
        # поставщиков для счёта на ремонт выбирает и тот, кто вносит счета (механик): справочник контрагентов целиком ему не открыт
        if not ref_scope(p, ref, "counterparties.view", None) and not (ref == "supplier" and (p.scope("bills.create") or p.scope("bills.edit"))):
            raise _denied()
        query = select(Counterparty).where(Counterparty.archived_at.is_(None)).order_by(Counterparty.name)
        if ref == "customer":
            query = query.where(Counterparty.kind.in_(("customer", "both")))
        if ref == "supplier":
            query = query.where(Counterparty.kind.in_(("supplier", "both")))
        if t:
            query = query.where(Counterparty.name.ilike(t))
        return {"items": [{"id": c.id, "label": c.name} for c in db.scalars(query.limit(300))]}
    if ref == "tariff":
        if not ref_scope(p, ref, "tariffs.view", None):
            raise _denied()
        query = select(Tariff).where(Tariff.archived_at.is_(None)).order_by(Tariff.name)
        return {"items": [{"id": x.id, "label": x.name} for x in db.scalars(query.limit(300))]}
    if ref == "order":
        scope = ref_scope(p, ref, "orders.view", legal_entity_id)
        query = (select(Order, Counterparty.name).join(Counterparty, Counterparty.id == Order.customer_id)
                 .where(scope.cond(Order.legal_entity_id), Order.archived_at.is_(None)).order_by(Order.order_date.desc(), Order.id.desc()))
        if t:
            query = query.where(or_(Counterparty.name.ilike(t), Order.point_from.ilike(t), Order.point_to.ilike(t)))
        ents = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
        return {"items": [{"id": o.id, "label": order_label(o.number, o.year, name), "legal_entity_id": o.legal_entity_id,
                           "legal_entity": ents.get(o.legal_entity_id, "")} for o, name in db.execute(query.limit(300)).all()]}
    if ref == "ppe":
        if not ref_scope(p, ref, "ppe_norms.view", None):
            raise _denied()
        query = select(PpeNorm).where(PpeNorm.archived_at.is_(None)).order_by(PpeNorm.name)
        if t:
            query = query.where(PpeNorm.name.ilike(t))
        return {"items": [{"id": n.id, "label": n.name, "wear_months": n.wear_months} for n in db.scalars(query.limit(300))]}
    if ref == "account":
        scope = ref_scope(p, ref, "cash_accounts.view", legal_entity_id)
        query = select(CashAccount).where(scope.cond(CashAccount.legal_entity_id), CashAccount.archived_at.is_(None)).order_by(CashAccount.name)
        return {"items": [{"id": a.id, "label": a.name, "legal_entity_id": a.legal_entity_id} for a in db.scalars(query)]}
    raise fail(404, "not_found", "Такого списка нет")
