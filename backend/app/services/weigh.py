# -*- coding: utf-8 -*-
"""Талоны весов: чтение тоннажа с фото и справочные расчёты.

Водитель прикладывает только фото. Программа читает с него «брутто», «тару» и «нетто» (tesseract, см. services/ocr.py) и вносит
тоннаж сама, но только когда уверена: брутто минус тара должно сойтись с нетто, либо нетто стоит на талоне дважды одинаково. Если
уверенности нет, тоннаж остаётся пустым, а то, что программа увидела, лежит рядом подсказкой (tons_guess): сотрудник смотрит на фото
и вносит вес сам. Так неверная цифра не попадёт в месячный итог молча.

Чтение идёт после ответа водителю, в фоне: телефон не ждёт, пока сервер разберёт картинку.
"""
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app import audit
from app.common import utcnow
from app.models import EntryPhoto, StoredFile, WeighTicket
from app.services import ocr

log = logging.getLogger("erp.weigh")

TONNAGE_CODES = ("truck_waste", "truck_dump")        # типы техники, у которых тоннаж включается сам
TONNAGE_WORDS = ("мусоровоз", "самосвал")             # то же по названию (типы из старой программы называются по-своему)
MAX_TONS = Decimal(80)           # больше этого за одну ходку не бывает: скорее всего ошибка чтения
TOLERANCE = Decimal("0.021")     # весы округляют до 10–20 кг: брутто − тара может отличаться от нетто на эту величину (т)

_CYR = {"gross": r"[бБ6][рРpP][уУyY][тТtT][тТtT][оОoO0]", "tare": r"[тТtT][аАaA][рРpP][аАыЫуУеЕaA]", "net": r"[нНhH][еЕeE][тТtT][тТtT][оОoO0]"}
_NUM = r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_FIELD = {k: re.compile(rf"(?P<label>{v})(?P<gap>[^\d\n]{{0,30}})(?P<num>{_NUM})(?P<unit>\s*(?:кг|kg|тн|т|t)(?![а-яa-z]))?", re.I) for k, v in _CYR.items()}
_KG_WORD = re.compile(r"(?<![а-яa-z])(?:кг|kg)(?![а-яa-z])", re.I)
_T_WORD = re.compile(r"(?<![а-яa-z])(?:тн|т|t|тонн\w*)(?![а-яa-z])", re.I)
_NUMBER = re.compile(r"(?:талон|взвешивани\w*|квитанци\w*|чек|ттн|накладн\w*)[^\d\n]{0,20}(?:№|N[º°o]?\.?|#)?\s*:?\s*(\d{2,12})|№\s*(\d{2,12})", re.I)
WORDS = ("брутто", "нетто", "тара", "масса", "вес", "взвеш", "талон", "полигон", "квитанц", " кг")


def score(text: str) -> int:
    """Насколько текст похож на талон весов: слова-метки и числа. По этому числу выбирается поворот снимка."""
    low = text.lower()
    return sum(low.count(w) for w in WORDS) * 10 + min(len(re.findall(r"\d{3,}", text)), 10)


def fmt_t(x: Decimal | float) -> str:
    s = f"{Decimal(x):.3f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _tonnes(raw: str, unit: str | None, gap: str) -> Decimal | None:
    """Число с талона в тоннах. Единица берётся рядом с числом; если её нет, угадывается: целые сотни — килограммы, дробные — тонны."""
    s = re.sub(r"[   ]", "", raw).replace(",", ".")
    try:
        v = Decimal(s)
    except Exception:
        return None
    spaced = bool(re.search(r"[   ]", raw))
    if unit and _KG_WORD.search(unit):
        kg = True
    elif unit and _T_WORD.search(unit):
        kg = False
    elif _KG_WORD.search(gap):
        kg = True
    elif _T_WORD.search(gap):
        kg = False
    else:
        kg = spaced or v >= 200
    return v / 1000 if kg else v


def _fields(text: str) -> dict[str, list[Decimal]]:
    found: dict[str, list[Decimal]] = {"gross": [], "tare": [], "net": []}
    for key, rx in _FIELD.items():
        for m in rx.finditer(text):
            t = _tonnes(m.group("num"), m.group("unit"), m.group("gap"))
            if t is not None and 0 < t < 1000:
                found[key].append(t.quantize(Decimal("0.001")))
    return found


@dataclass
class Reading:
    tons: Decimal | None = None          # нетто, в котором программа уверена
    guess: Decimal | None = None         # что увидела, но подтвердить не смогла
    number: str = ""                     # номер талона, если он есть на снимке
    note: str = ""                       # для сотрудника: что произошло
    seen: bool = False                   # на снимке нашлось хоть что-то похожее на талон


def parse(text: str) -> Reading:
    """Разбор текста одного снимка. Правила уверенности описаны в начале файла."""
    r = Reading()
    if not text.strip():
        return r
    m = _NUMBER.search(text)
    if m:
        r.number = (m.group(1) or m.group(2) or "")[:60]
    f = _fields(text)
    r.seen = any(f.values())
    gross, tare = (f["gross"][0] if f["gross"] else None), (f["tare"][0] if f["tare"] else None)
    nets = f["net"]
    net = nets[0] if nets else None
    if net is None:
        if gross is not None and tare is not None:
            r.note = f"Программа увидела брутто {fmt_t(gross)} т и тару {fmt_t(tare)} т, а «нетто» не нашла. Впишите тоннаж по фото."
        return r
    if net > MAX_TONS:
        r.guess = net
        r.note = f"Программа увидела нетто {fmt_t(net)} т: это слишком много за одну ходку, похоже на ошибку чтения. Проверьте по фото."
        return r
    if gross is not None and tare is not None:
        if gross > tare and abs(gross - tare - net) <= TOLERANCE:
            r.tons = net
            r.note = f"Прочитано с фото: брутто {fmt_t(gross)} т, тара {fmt_t(tare)} т, нетто {fmt_t(net)} т (цифры сходятся)."
            return r
        r.guess = net
        r.note = (f"Программа увидела брутто {fmt_t(gross)} т, тару {fmt_t(tare)} т и нетто {fmt_t(net)} т, но цифры не сходятся. "
                  "Сверьте с фото и впишите тоннаж.")
        return r
    if len(nets) >= 2 and len(set(nets)) == 1:
        r.tons = net
        r.note = f"Прочитано с фото: нетто {fmt_t(net)} т (стоит на талоне дважды)."
        return r
    r.guess = net
    r.note = f"Программа увидела нетто {fmt_t(net)} т, но не смогла проверить его по брутто и таре. Сверьте с фото и впишите тоннаж."
    return r


def combine(readings: list[Reading]) -> Reading:
    """Итог по нескольким фото одного талона: первое уверенное чтение; если уверенные чтения разные, тоннаж не ставится."""
    sure = [r for r in readings if r.tons is not None]
    number = next((r.number for r in readings if r.number), "")
    if sure:
        if len({r.tons for r in sure}) > 1:
            return Reading(guess=sure[0].tons, number=number, seen=True,
                           note="На разных фото получился разный вес: проверьте по фото и впишите верный тоннаж.")
        out = sure[0]
        out.number = number
        return out
    guesses = [r for r in readings if r.guess is not None]
    if guesses:
        g = guesses[0]
        g.number = number
        return g
    notes = next((r.note for r in readings if r.note), "")
    return Reading(number=number, seen=any(r.seen for r in readings),
                   note=notes or "Тоннаж на фото прочитать не удалось. Впишите его по фото.")


def read_images(images: list[bytes]) -> Reading:
    texts = [ocr.text_from_image(data, scorer=score, enough=40) for data in images]
    return combine([parse(t) for t in texts])


def reading_available() -> bool:
    return ocr.available()


# ---------------------------------------------------------------- запись результата

def duplicate_of(db, ticket: WeighTicket, number: str) -> WeighTicket | None:
    """Талон с тем же номером по той же машине: скорее всего, одну бумагу сфотографировали дважды."""
    if not number:
        return None
    return db.scalar(select(WeighTicket).where(WeighTicket.vehicle_id == ticket.vehicle_id, WeighTicket.doc_number == number,
                                               WeighTicket.archived_at.is_(None), WeighTicket.id != ticket.id)
                     .order_by(WeighTicket.id).limit(1))


def apply(db, ticket: WeighTicket, r: Reading) -> None:
    """Записывает то, что прочитано, не трогая вес, который уже внёс сотрудник."""
    ticket.read_at = utcnow()
    if ticket.tons_source == "manual":
        return
    if r.number and not ticket.doc_number:
        ticket.doc_number = r.number
    note = r.note
    twin = duplicate_of(db, ticket, ticket.doc_number)
    if twin is not None:
        note = (note + " " if note else "") + f"Талон № {ticket.doc_number} по этой машине уже внесён ({twin.ticket_date.strftime('%d.%m.%Y')}): возможно, это повтор."
    ticket.read_note = note[:300]
    ticket.tons_guess = r.guess
    if r.tons is not None:
        ticket.tons, ticket.tons_source = r.tons, "read"
        ticket.tons_guess = None


def read_ticket(ticket_id: int) -> None:
    """Фоновая задача: читает все фото талона и записывает результат. Сама ловит ошибки: упасть здесь нельзя, водитель уже получил ответ."""
    from app.db import session_factory
    try:
        with session_factory()() as db:
            t = db.get(WeighTicket, ticket_id)
            if t is None or t.archived_at is not None or t.tons_source == "manual":
                return
            files = db.scalars(select(StoredFile).join(EntryPhoto, EntryPhoto.file_id == StoredFile.id)
                               .where(EntryPhoto.weigh_ticket_id == ticket_id, EntryPhoto.removed_at.is_(None)).order_by(EntryPhoto.id)).all()
            if not ocr.available():
                t.read_at = utcnow()
                t.read_note = "Чтение фото на сервере не включено: впишите тоннаж по фото."
                db.commit()
                return
            r = read_images([f.data for f in files]) if files else Reading(note="К талону не приложено фото.")
            apply(db, t, r)
            audit.log(db, None, "weigh_tickets.read", "weigh_tickets", t.id, t.legal_entity_id, login="system",
                      tons=str(r.tons) if r.tons is not None else None, guess=str(r.guess) if r.guess is not None else None)
            db.commit()
    except Exception:           # noqa: BLE001
        log.exception("не удалось прочитать талон %s", ticket_id)
        try:
            with session_factory()() as db:
                t = db.get(WeighTicket, ticket_id)
                if t is not None and t.read_at is None:
                    t.read_at = utcnow()
                    t.read_note = "Не удалось прочитать фото: впишите тоннаж по фото."
                    db.commit()
        except Exception:       # noqa: BLE001
            log.exception("и пометить талон %s тоже не удалось", ticket_id)


def tonnage_vehicle_ids(db, vehicle_ids) -> list[int]:
    """Из машин, закреплённых за человеком, те, по которым ведётся тоннаж (галочка «Вести тоннаж» на технике)."""
    ids = list(vehicle_ids or ())
    if not ids:
        return []
    from app.models import Vehicle
    return list(db.scalars(select(Vehicle.id).where(Vehicle.id.in_(ids), Vehicle.tonnage.is_(True), Vehicle.archived_at.is_(None))))


def has_tonnage(db, p) -> bool:
    """Нужна ли человеку вкладка «Тоннаж»: есть право вносить талоны и закреплена машина, по которой ведётся тоннаж."""
    from app.services.vehicles import assigned_vehicle_ids
    return bool(p.can("weigh_tickets.create_own") and p.employee_id is not None
                and tonnage_vehicle_ids(db, assigned_vehicle_ids(db, p.employee_id)))


def type_tonnage(vtype) -> bool:
    """Включать ли тоннаж новой машине этого типа: мусоровозы и самосвалы да, остальным его включают галочкой в карточке."""
    name = (vtype.name or "").lower()
    return vtype.code in TONNAGE_CODES or any(w in name for w in TONNAGE_WORDS)


def trip_cost(ticket, vehicle=None) -> Decimal | None:
    """Стоимость рейса для заказчика: тонны × цена за тонну из талона (нет в талоне — из карточки машины), до копеек.
    Нет веса или цены — None. Округляется каждый рейс, а итоги складываются из округлённых, как в журнале и в Excel."""
    from decimal import ROUND_HALF_UP
    price = ticket.price if ticket.price is not None else (vehicle.ton_price if vehicle is not None else None)
    if ticket.tons is None or price is None:
        return None
    return (Decimal(ticket.tons) * Decimal(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
