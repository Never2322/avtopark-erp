# -*- coding: utf-8 -*-
"""Загрузка талонов весов в «Журнал тоннажа» из файла (Excel .xlsx или CSV).

Понимает таблицу вида «Сводные данные по вывозу мусора»: Дата, Номер акта, г/н автомобиля, кол-во тн, стоимость (цена за тонну),
сумма (не нужна: стоимость рейса программа считает сама). Строка заголовков ищется среди первых десяти строк, строки «Итого» и пустые
пропускаются. Как и у показаний, просмотр и загрузка идут одним кодом: при просмотре всё откатывается в конце.

Водитель талона — тот, кто был закреплён за машиной в день талона. Цена за тонну: из файла, иначе из карточки машины.
Повтор (тот же номер акта у той же машины, а без номера — та же машина, дата и тоннаж) не добавляется второй раз.
"""
import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail
from app.deps import entity_filter
from app.models import Vehicle, WeighTicket
from app.services import weigh as weigh_svc
from app.services.readings_import import MAX_BYTES, parse_date, plate_key, read_table  # noqa: F401  (MAX_BYTES нужен роутеру)
from app.services.vehicles import current_driver_id

MAX_ROWS = 3000
ALIASES = {
    "date": ("дата",),
    "plate": ("г/н", "гн", "госномер", "гос номер", "гос. номер", "номер машины", "автомобиль", "машина", "техника", "plate"),
    "tons": ("кол-во тн", "количество тонн", "кол во тн", "тонн", "тн", "тоннаж", "вес", "нетто", "tons"),
    "doc": ("номер акта", "акт", "номер талона", "талон", "документ"),
    "price": ("стоимость", "цена", "цена за тонну", "тариф", "price"),
}
FIELD_NAMES = {"date": "Дата", "plate": "г/н автомобиля", "tons": "кол-во тн"}


def _head(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е").replace(".", " ").replace("№", " ")).strip()


def _match(field: str, h: str) -> bool:
    words = ALIASES[field]
    if field == "plate" and ("г/н" in h or h.startswith("гн ") or h == "гн"):
        return True
    if field == "tons" and ("тн" in h.split() or "тонн" in h or "тоннаж" in h):
        return True
    if field == "doc" and ("акт" in h.split() or "акта" in h or "талон" in h):
        return True
    if field == "price" and ("сумма" in h):
        return False
    return h in words or any(w in h for w in words if len(w) >= 4)


def find_columns(rows: list[list]) -> tuple[int, dict[str, int]]:
    seen: list[str] = []
    for ri, row in enumerate(rows[:10]):
        found: dict[str, int] = {}
        for ci, cell in enumerate(row):
            h = _head(cell)
            if not h:
                continue
            seen.append(str(cell))
            for field in ("date", "plate", "doc", "tons", "price"):
                if field not in found and _match(field, h):
                    found[field] = ci
                    break
        if all(k in found for k in FIELD_NAMES):
            return ri, found
    got = ", ".join(f"«{x}»" for x in seen[:10]) or "ничего"
    raise fail(422, "bad_columns", "Нужны колонки: Дата, г/н автомобиля, кол-во тн (по желанию: Номер акта и стоимость за тонну). "
               f"Нашлось: {got}.", "file")


def _num(v, what: str, places: str) -> Decimal | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, bool):
        raise ValueError(f"{what} не число")
    s = repr(v) if isinstance(v, (int, float)) else re.sub(r"[\s  ₽рубт]", "", str(v)).replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"{what} «{v}» не число") from None
    if not d.is_finite() or d < 0:
        raise ValueError(f"{what} «{v}» не подходит")
    return d.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _cell(row: list, idx: int | None):
    return row[idx] if idx is not None and idx < len(row) else None


def _is_total(row: list) -> bool:
    return any(isinstance(c, str) and c.strip().lower().startswith("итог") for c in row)


def run(db: Session, p, filename: str, data: bytes, *, legal_entity_id: int | None, apply: bool, today: dt.date) -> dict:
    rows = read_table(filename, data)
    header_row, cols = find_columns(rows)
    key_cols = [cols[k] for k in ("date", "plate", "tons")]
    filled = lambda r: any(_cell(r, i) is not None and str(_cell(r, i)).strip() for i in key_cols)      # noqa: E731
    # строки-заготовки (только цена или формула суммы), пустые и «Итого» пропускаются молча
    body = [(i + 1, r) for i, r in enumerate(rows) if i > header_row and filled(r) and not _is_total(r)]
    if not body:
        raise fail(422, "bad_file", "В файле нет строк с талонами под заголовком.", "file")
    if len(body) > MAX_ROWS:
        raise fail(413, "too_many_rows", f"В файле больше {MAX_ROWS} строк. Разбейте его на части.", "file")
    scope = entity_filter(p, "weigh_tickets.create", legal_entity_id)
    by_plate: dict[str, list[Vehicle]] = {}
    for v in db.scalars(select(Vehicle).where(scope.cond(Vehicle.legal_entity_id))):
        by_plate.setdefault(plate_key(v.plate), []).append(v)

    out_rows = []
    seen_in_file: set[tuple] = set()
    for n, row in body:
        plate_raw = str(_cell(row, cols.get("plate")) or "").strip()
        item = {"n": n, "plate": plate_raw, "vehicle": "", "date": "", "doc_number": "", "tons": None, "price": None, "cost": None,
                "driver": "", "status": "error", "message": ""}
        out_rows.append(item)
        try:
            if not plate_raw:
                raise ValueError("не указан госномер")
            found = [x for x in by_plate.get(plate_key(plate_raw), []) if x.archived_at is None]
            if not found:
                raise ValueError(f"машина «{plate_raw}» не найдена" + ("" if legal_entity_id else " (или нет доступа)"))
            if len(found) > 1:
                raise ValueError("такой номер у нескольких машин")
            v = found[0]
            item["vehicle"] = f"{v.plate} · {v.make} {v.model}".strip()
            if not v.tonnage:
                raise ValueError("у этой машины не ведётся тоннаж: поставьте галочку «Вести тоннаж» в карточке техники")
            d, _ = parse_date(_cell(row, cols.get("date")))
            if d > today:
                raise ValueError("дата в будущем")
            item["date"] = d.isoformat()
            tons = _num(_cell(row, cols.get("tons")), "тоннаж", "0.001")
            if tons is None or tons <= 0:
                raise ValueError("не указан тоннаж")
            if tons > weigh_svc.MAX_TONS:
                raise ValueError(f"больше {weigh_svc.MAX_TONS} т за одну ходку: проверьте цифры")
            item["tons"] = float(tons)
            price = _num(_cell(row, cols.get("price")), "цена за тонну", "0.01")
            if price is None:
                price = v.ton_price
            item["price"] = float(price) if price is not None else None
            item["cost"] = float((tons * Decimal(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if price is not None else None
            doc = " ".join(str(_cell(row, cols.get("doc")) or "").split())[:60]
            if doc.endswith(".0") and doc[:-2].isdigit():
                doc = doc[:-2]                                    # номер акта числом из Excel
            item["doc_number"] = doc
            key = (v.id, doc) if doc else (v.id, d, tons)
            if key in seen_in_file:
                item["status"], item["message"] = "duplicate", "повтор строки в этом файле"
                continue
            seen_in_file.add(key)
            q = select(WeighTicket.id).where(WeighTicket.vehicle_id == v.id, WeighTicket.archived_at.is_(None))
            q = q.where(WeighTicket.doc_number == doc) if doc else q.where(WeighTicket.ticket_date == d, WeighTicket.tons == tons)
            if db.scalar(q.limit(1)) is not None:
                item["status"], item["message"] = "duplicate", "такой талон уже есть в журнале"
                continue
            driver_id = current_driver_id(db, v.id, d)
            if driver_id is not None:
                from app.models import Employee
                e = db.get(Employee, driver_id)
                item["driver"] = " ".join(x for x in (e.last_name, e.first_name) if x) if e else ""
            db.add(WeighTicket(legal_entity_id=v.legal_entity_id, vehicle_id=v.id, driver_id=driver_id, ticket_date=d, tons=tons, tons_source="manual",
                               price=price, doc_number=doc, note=f"Из файла «{(filename or 'файл')[:80]}»"[:500],
                               created_by_id=p.id, updated_by_id=p.id))
            db.flush()
            item["status"] = "ok"
            if driver_id is None:
                item["message"] = "водитель не закреплён за машиной в этот день: впишите его в талоне, иначе тонны никому не оплатятся"
        except ValueError as e:
            item["message"] = str(e)[:300]
    summary = {k: sum(1 for r in out_rows if r["status"] == k) for k in ("ok", "duplicate", "error")}
    summary["total"] = len(out_rows)
    summary["tons"] = round(sum(r["tons"] or 0 for r in out_rows if r["status"] == "ok"), 3)
    summary["cost"] = round(sum(r["cost"] or 0 for r in out_rows if r["status"] == "ok"), 2)
    if apply:
        audit.log(db, p, "weigh_tickets.import", "weigh_tickets", None, legal_entity_id, file=(filename or "")[:120],
                  total=summary["total"], added=summary["ok"], duplicates=summary["duplicate"], errors=summary["error"])
        db.commit()
    else:
        db.rollback()
    labels = {k: str(rows[header_row][i]) for k, i in cols.items() if i < len(rows[header_row])}
    return {"applied": apply, "summary": summary, "columns": labels, "rows": out_rows}


TEMPLATE = (
    "Дата;Номер акта;г/н автомобиля;кол-во тн;стоимость\r\n"
    "07.11.2026;ВГ200113312;У112АН154;13,94;3814,9\r\n"
    "07.11.2026;ВГ200113145;Т332МВ154;7,6;3814,9\r\n"
)
