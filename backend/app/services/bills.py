# -*- coding: utf-8 -*-
"""Счета на ремонт: деньги и НДС, разбор строк, чтение текста счёта поставщика (подсказка, человек проверяет)."""
import datetime as dt
import io
import logging
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import fail
from app.enums import BILL_KINDS, BILL_MAX_ITEMS, BILL_STATUSES, BILL_UNITS
from app.models import RepairBill
from app.parts_seed import norm

CENT = Decimal("0.01")
MAX_MONEY = Decimal("9999999999.99")       # как в «Money»: не больше 12 знаков до запятой
MAX_QTY = Decimal("1000000")
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
NBSP = "  "
MONEY = r"\d{1,3}(?:[ " + NBSP + r"]\d{3})+,\d{2}|\d+,\d{2}"


# --------------------------------------------------------------------------
# Деньги и НДС
# --------------------------------------------------------------------------

def to_money(value, *, what: str = "Сумма", field: str = "total", allow_zero: bool = True) -> Decimal:
    """Сумма из числа или текста («1 234,56», «1234.5 ₽»). Не больше двух знаков после запятой, не отрицательная."""
    if isinstance(value, Decimal):
        d = value
    else:
        raw = re.sub(r"[\s" + NBSP + r"₽]|руб\.?", "", str(value if value is not None else ""), flags=re.I).replace(",", ".")
        if not re.fullmatch(r"\d+(\.\d+)?", raw):
            raise fail(422, "bad_money", f"{what}: введите число, например 1250,50", field)
        d = Decimal(raw)
    if not d.is_finite() or d < 0 or d.as_tuple().exponent < -2:
        raise fail(422, "bad_money", f"{what}: введите число не меньше нуля и не точнее копеек, например 1250,50", field)
    if d > MAX_MONEY:
        raise fail(422, "bad_money", f"{what}: слишком большая сумма, проверьте нули", field)
    if not allow_zero and d == 0:
        raise fail(422, "bad_money", f"{what} не может быть нулевой", field)
    return d.quantize(CENT)


def to_qty(value, *, where: str = "") -> Decimal:
    try:
        q = Decimal(str(value if value not in (None, "") else 1).replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        raise fail(422, "bad_qty", f"{where}Количество введите числом", "items") from None
    if not q.is_finite() or q <= 0 or q > MAX_QTY or q.as_tuple().exponent < -3:
        raise fail(422, "bad_qty", f"{where}Количество должно быть больше нуля, не больше миллиона и не точнее тысячных", "items")
    return q


def line_sum(qty: Decimal, price: Decimal) -> Decimal:
    """Сумма строки: количество × цена, копейки округляются как принято в расчётах (0,5 вверх)."""
    return (qty * price).quantize(CENT, rounding=ROUND_HALF_UP)


def vat_included(total: Decimal, pct: int) -> Decimal:
    """НДС внутри суммы, уже включающей налог: сумма × ставка / (100 + ставка), с округлением 0,5 вверх."""
    if not pct:
        return Decimal("0.00")
    return (total * pct / (100 + pct)).quantize(CENT, rounding=ROUND_HALF_UP)


def clean_items(rows: list[dict], pct: int) -> tuple[list[dict], Decimal, Decimal]:
    """Строки выписанного счёта: проверка, суммы строк, итог и НДС. Пустые строки (форма с запасом) пропускаются."""
    out, total, vat = [], Decimal("0.00"), Decimal("0.00")
    live = [r for r in rows if any(str(r.get(k) or "").strip() for k in ("name", "article", "price")) or str(r.get("qty") or "").strip() not in ("", "1")]
    if len(live) > BILL_MAX_ITEMS:
        raise fail(422, "too_many_items", f"В счёте не больше {BILL_MAX_ITEMS} строк", "items")
    for i, r in enumerate(live, 1):
        where = f"Строка {i}: " if len(live) > 1 else ""
        name, article = str(r.get("name") or "").strip(), str(r.get("article") or "").strip()
        unit = str(r.get("unit") or "шт").strip() or "шт"
        if not name:
            raise fail(422, "item_name", f"{where}впишите название товара или услуги", "items")
        if not norm(name):
            raise fail(422, "item_name", f"{where}название должно содержать буквы или цифры", "items")
        if len(name) > 200 or len(article) > 60:
            raise fail(422, "item_long", f"{where}слишком длинное название (до 200 знаков) или артикул (до 60)", "items")
        if unit not in BILL_UNITS:
            raise fail(422, "item_unit", f"{where}единица «{unit}» не из списка: {', '.join(BILL_UNITS)}", "items")
        qty = to_qty(r.get("qty"), where=where)
        price = to_money(r.get("price") if r.get("price") not in (None, "") else 0, what=f"{where}цена", field="items")
        amount = line_sum(qty, price)
        v = vat_included(amount, pct)
        out.append({"pos": i, "name": name, "article": article, "qty": qty, "unit": unit, "price": price, "amount": amount, "vat": v})
        total += amount
        vat += v
    if not out:
        raise fail(422, "no_items", "Добавьте хотя бы одну строку", "items")
    if total <= 0:
        raise fail(422, "zero_total", "Сумма счёта получилась нулевой: впишите цены", "items")
    if total > MAX_MONEY:
        raise fail(422, "bad_money", "Слишком большая сумма счёта", "items")
    return out, total, vat


# --------------------------------------------------------------------------
# Чтение счёта поставщика
# --------------------------------------------------------------------------

def pdf_text(data: bytes) -> str:
    """Текст PDF или пустая строка, если в файле нет текста (скан) или файл не читается."""
    try:
        from pypdf import PdfReader
    except ImportError:                                    # pragma: no cover
        return ""
    logging.getLogger("pypdf").setLevel(logging.ERROR)    # битые PDF не должны шуметь в журнале
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return ""
        return "\n".join((p.extract_text() or "") for p in reader.pages[:5])[:200000]
    except Exception:
        return ""


def _kop_values(text: str) -> list[int]:
    return [int(re.sub(r"\D", "", m.replace(",", ""))) for m in re.findall(MONEY, text)]


def _plate_key(text: str) -> str:
    """Госномер для сравнения: без пробелов и знаков, заглавные, латинские двойники заменены русскими."""
    table = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")
    return "".join(ch for ch in str(text or "") if ch.isalnum()).upper().translate(table)


def parse_bill_text(text: str, vehicles=(), suppliers=()) -> dict:
    """Достаёт из текста счёта номер, дату, сумму, НДС, поставщика, машину. Только подсказка: человек проверяет.

    vehicles: пары (id, госномер); suppliers: пары (id, ИНН). Суммы возвращаются как Decimal в рублях.
    """
    out: dict = {}
    flat = re.sub(r"\s+", " ", text or "").strip()
    if not flat:
        return out
    m = re.search(r"Сч[её]т(?:[- ]договор|[- ]оферта|\s+на\s+оплату)?\s*(?:№|N|No\.?)\s*([^\s,;]+)\s+от\s+"
                  r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", flat, re.I)
    if m:
        out["number"] = m.group(1)
        try:
            out["bill_date"] = dt.date(int(m.group(4)), int(m.group(3)), int(m.group(2))).isoformat()
        except ValueError:
            pass
    else:
        m = re.search(r"Сч[её]т(?:\s+на\s+оплату)?\s*(?:№|N|No\.?)\s*([^\s,;]+)\s+от\s+«?(\d{1,2})»?\s+(" + "|".join(MONTHS_GEN) + r")\s+(\d{4})",
                      flat, re.I)
        if m:
            out["number"] = m.group(1)
            try:
                out["bill_date"] = dt.date(int(m.group(4)), MONTHS_GEN.index(m.group(3).lower()) + 1, int(m.group(2))).isoformat()
            except ValueError:
                pass
    # сумма
    total = vat = None
    m = re.search(r"(?:Всего\s+к\s+оплате|Итого\s+к\s+оплате|К\s+оплате)[^\d]{0,30}(" + MONEY + ")", flat, re.I)
    if m:
        total = _kop_values(m.group(1))[0]
    tabbed = re.sub(r"[ \t]+", " ", text)
    line = re.search(r"Итого[^\n]*", tabbed, re.I)
    amounts = _kop_values(line.group(0)) if line else []
    if total is None and amounts:
        total = max(amounts)
    m = re.search(r"НДС[^\d\n]{0,40}(" + MONEY + ")", tabbed, re.I)
    if m:
        vat = _kop_values(m.group(1))[0]
    elif len(amounts) >= 2 and total:
        small = min(amounts)
        if small < total * 0.3:
            vat = small
    if total:
        out["total"] = Decimal(total) / 100
    if vat is not None and total and vat < total:
        out["vat"] = Decimal(vat) / 100
        if vat == 0:
            out["vat_pct"] = 0
        else:
            ratio = vat * 100.0 / (total - vat)
            for pct in (22, 20, 10, 7, 5):
                if abs(ratio - pct) < 0.6:
                    out["vat_pct"] = pct
                    break
    # поставщик
    m = re.search(r"(?:Поставщик|Продавец|Исполнитель)\s*:\s*(.+?)\s*(?:Сч[её]т\s+на\s+оплату|Сч[её]т\s*(?:№|N)|Покупатель|Плательщик|Заказчик|$)", flat, re.I)
    if m:
        block = m.group(1)
        inn = re.search(r"ИНН\s*:?\s*(\d{12}|\d{10})\b", block)
        kpp = re.search(r"КПП\s*:?\s*(\d{9})\b", block)
        phone = re.search(r"тел\.?\s*:?\s*([+\d][\d\s()+\-]{6,24}\d)", block, re.I)
        if inn:
            out["supplier_inn"] = inn.group(1)
        if kpp:
            out["supplier_kpp"] = kpp.group(1)
        if phone:
            out["supplier_phone"] = phone.group(1).strip()
        rest = re.sub(r"(?:ИНН|КПП)\s*:?\s*\d+\s*,?\s*", "", block)
        rest = re.sub(r"\s*тел\.?.*$", "", rest, flags=re.I).strip(" ,")
        cut = re.search(r",\s*\d{6}\b", rest)
        name = rest[:cut.start()] if cut else re.split(r"(?<=[\"»”])\s*,\s*", rest, maxsplit=1)[0]
        name = name.strip(" ,")
        if name and len(name) <= 160:
            out["supplier_name"] = name
            if cut:
                out["supplier_addr"] = rest[cut.start():].strip(" ,")[:250]
    bik = re.search(r"БИК\s*:?\s*(\d{9})\b", flat)
    if bik:
        out["bik"] = bik.group(1)
    for a in re.findall(r"(?<!\d)(\d{20})(?!\d)", flat):
        if a.startswith("301") and "corr_account" not in out:
            out["corr_account"] = a
        elif not a.startswith("301") and "account" not in out:
            out["account"] = a
    bank = re.search(r"Банк\s+получателя\s*(?:БИК\s*\d{9})?\s*(.+?)\s*(?:Сч\.?\s*№|БИК|$)", flat)
    if bank and 3 <= len(bank.group(1)) <= 160:
        out["bank_name"] = bank.group(1).strip()
    basis = re.search(r"((?:Заказ[- ]?наряд|Договор)\s*(?:№|N)\s*[^\s,;]+(?:\s+от\s+\d{1,2}\.\d{2}\.\d{4})?)", flat, re.I)
    if basis:
        out["basis"] = basis.group(1)[:120]
    # машина по госномеру: берём ту, что названа в тексте раньше всех
    key = _plate_key(flat)
    best = None
    for vid, plate in vehicles:
        pk = _plate_key(plate)
        pos = key.find(pk) if len(pk) >= 5 else -1
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, vid)
    if best:
        out["vehicle_id"] = best[1]
    if out.get("supplier_inn"):
        for sid, sinn in suppliers:
            if (sinn or "").strip() == out["supplier_inn"]:
                out["supplier_id"] = sid
                break
    return out


# --------------------------------------------------------------------------
# Счета, привязанные к обращению (для карточки обращения)
# --------------------------------------------------------------------------

def request_block(db: Session, p, r) -> dict:
    """Какие счета оформлены по обращению. Деньги видит только тот, кому открыты счета этого юрлица."""
    if not p.can("bills.view", r.legal_entity_id):
        return {"visible": False, "items": [], "total": 0.0, "can_create": False}
    rows = list(db.scalars(select(RepairBill).where(RepairBill.request_id == r.id, RepairBill.archived_at.is_(None))
                           .order_by(RepairBill.bill_date, RepairBill.id)))
    live = [b for b in rows if b.status != "cancelled"]
    return {"visible": True, "total": float(sum((b.total for b in live), Decimal(0))), "can_create": p.can("bills.create", r.legal_entity_id) and r.status != "cancelled",
            "items": [{"id": b.id, "number": b.number, "bill_date": b.bill_date.isoformat(), "supplier_name": b.supplier_name, "total": float(b.total),
                       "status": b.status, "status_label": BILL_STATUSES[b.status], "kind_label": BILL_KINDS[b.kind]} for b in rows]}
