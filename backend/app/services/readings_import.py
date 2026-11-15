# -*- coding: utf-8 -*-
"""Загрузка показаний пробега и моточасов из файла (CSV или Excel .xlsx).

Принцип: предварительный просмотр и настоящая загрузка идут одним и тем же кодом. В просмотре всё делается в транзакции,
которая в конце откатывается, поэтому «что будет» и «что стало» не могут разойтись. Каждая строка проходит через
`readings.add_reading`, то есть через те же правила, что и ручной ввод: показание не меньше предыдущего, не позже
текущего момента, большие скачки уходят на проверку.

Строки с ошибкой пропускаются и подробно перечисляются; остальные добавляются.
"""
import csv
import datetime as dt
import io
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, aliased

from app import audit
from app.common import fail, norm_plate, utcnow
from app.deps import entity_filter
from app.enums import READING_KINDS
from app.models import Vehicle, VehicleReading
from app.services import readings as svc
from app.services.reminders import day_start, local_day, org_tz
from app.services.xlsx_read import XlsxError, read_first_sheet

MAX_BYTES = 3 * 1024 * 1024
MAX_ROWS = 5000

ALIASES = {
    "plate": ["госномер", "гос номер", "гос.номер", "номерной знак", "номер машины", "номер тс", "номер", "машина", "техника", "транспорт",
              "регистрационный номер", "plate", "vehicle", "reg"],
    "date": ["дата показания", "дата и время", "дата", "время", "когда", "date", "datetime"],
    "value": ["показание", "показания", "пробег", "одометр", "наработка", "значение", "км", "odometer", "value", "mileage"],
    "kind": ["вид", "тип", "единица измерения", "единица", "ед", "kind", "type"],
    "note": ["примечание", "комментарий", "заметка", "note", "comment"],
}
FIELD_NAMES = {"plate": "Госномер", "date": "Дата", "value": "Показание"}

_LOOK = str.maketrans("ABEKMHOPCTYXabekmhopctyx", "АВЕКМНОРСТУХАВЕКМНОРСТУХ")
_KIND_WORDS = {
    "km": {"км", "km", "пробег", "километры", "одометр", "kilometers"},
    "engine_hours": {"ч", "час", "часы", "мч", "м/ч", "м-ч", "моточасы", "моточас", "машиночасы", "hours", "engine_hours", "h", "hrs"},
}
_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M")


def plate_key(text) -> str:
    """Сравнение госномеров: без пробелов и тире, заглавные, латинские буквы-двойники заменены русскими."""
    s = norm_plate(str(text or "")).replace("-", "").replace("_", "").replace(".", "")
    return s.translate(_LOOK)


def _head(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s./]", " ", str(text or "").lower().replace("ё", "е"))).strip()


# ---------------------------------------------------------------- чтение файла

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise fail(422, "bad_file", "Не удалось прочитать текст файла. Сохраните его как CSV в кодировке UTF-8 или как .xlsx.", "file")


def _csv_rows(data: bytes) -> list[list]:
    text = _decode(data)
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = max((";", "\t", ","), key=lambda d: first.count(d))
    if first.count(delim) == 0:
        delim = ";"
    return [[(c.strip() or None) for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def read_table(filename: str, data: bytes) -> list[list]:
    if len(data) > MAX_BYTES:
        raise fail(413, "file_too_big", "Файл больше 3 МБ. Разбейте его на части.", "file")
    if not data:
        raise fail(422, "bad_file", "Файл пустой.", "file")
    name = (filename or "").lower()
    if name.endswith(".xls"):
        raise fail(422, "bad_file", "Старый формат .xls не поддерживается: сохраните файл как .xlsx или CSV.", "file")
    if name.endswith(".xlsx") or data[:2] == b"PK":
        try:
            return read_first_sheet(data)
        except XlsxError as e:
            raise fail(422, "bad_file", str(e), "file") from None
    return _csv_rows(data)


def find_columns(rows: list[list]) -> tuple[int, dict[str, int]]:
    """Строка заголовков (среди первых десяти) и номера колонок. Нужны госномер, дата и показание."""
    best: tuple[int, dict[str, int]] | None = None
    seen: list[str] = []
    for ri, row in enumerate(rows[:10]):
        found: dict[str, int] = {}
        for ci, cell in enumerate(row):
            h = _head(cell)
            if not h:
                continue
            if ri == 0 or not seen:
                seen.append(str(cell))
            for field, words in ALIASES.items():
                if field in found:
                    continue
                if h in words or any((len(w) >= 6 and w in h.split()) for w in words):
                    found[field] = ci
                    break
        if all(k in found for k in FIELD_NAMES) and (best is None or len(found) > len(best[1])):
            best = (ri, found)
            break
    if best is None:
        got = ", ".join(f"«{x}»" for x in seen[:8]) or "ничего"
        raise fail(422, "bad_columns", "В первой строке файла нужны названия колонок: Госномер, Дата, Показание "
                   f"(по желанию Вид и Примечание). Нашлось: {got}.", "file")
    return best


# ---------------------------------------------------------------- значения

def parse_date(v) -> tuple[dt.date, dt.time | None]:
    if isinstance(v, dt.datetime):
        return v.date(), (v.time().replace(microsecond=0) if v.time() != dt.time.min else None)
    if isinstance(v, dt.date):
        return v, None
    s = str(v or "").strip()
    if not s:
        raise ValueError("не указана дата")
    s = s.replace("T", " ")
    day_part, _, time_part = s.partition(" ")
    for f in _DATE_FORMATS:
        try:
            d = dt.datetime.strptime(day_part, f).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"дата «{s}» не понятна: нужен вид 31.12.2026 или 2026-12-31")
    t = None
    time_part = re.sub(r"(Z|[+-]\d\d:?\d\d)$", "", time_part.strip())
    if time_part:
        for f in _TIME_FORMATS:
            try:
                t = dt.datetime.strptime(time_part.split(".")[0], f).time()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"время «{time_part}» не понятно: нужен вид 14:30")
    if not (2000 <= d.year <= 2100):
        raise ValueError(f"год {d.year} выглядит неверно")
    return d, t


def parse_value(v) -> Decimal:
    if isinstance(v, bool):
        raise ValueError("показание не число")
    if isinstance(v, (int, float)):
        s = repr(v)
    else:
        s = re.sub(r"[\s  ]", "", str(v or ""))
        s = re.sub(r"(км|km|ч|h|мч)$", "", s, flags=re.I)
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+,\d+", s):      # 12.345,6
            s = s.replace(".", "")
        s = s.replace(",", ".")
    if not s:
        raise ValueError("не указано показание")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"показание «{v}» не число") from None
    if not d.is_finite():
        raise ValueError(f"показание «{v}» не число")
    if d < 0:
        raise ValueError("показание не может быть отрицательным")
    if d > Decimal("9999999"):
        raise ValueError("показание слишком большое")
    return d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def parse_kind(v, default: str) -> str:
    if v is None or str(v).strip() == "":
        return default
    w = _head(v)
    for kind, words in _KIND_WORDS.items():
        if w in words:
            return kind
    raise ValueError(f"вид «{v}» не понятен: нужен «км» или «моточасы»")


def moment(d: dt.date, t: dt.time | None, now: dt.datetime) -> dt.datetime:
    """Время записи: если в файле только дата, то полдень по часам организации (но не позже «сейчас»)."""
    at = dt.datetime.combine(d, t or dt.time(12, 0), tzinfo=org_tz()).astimezone(dt.timezone.utc)
    if t is None and at > now and local_day(now) == d:
        return now
    return at


# ---------------------------------------------------------------- сама загрузка

def _cell(row: list, idx: int | None):
    return row[idx] if idx is not None and idx < len(row) else None


def already_there(db: Session, vehicle_id: int, kind: str, value: Decimal, day: dt.date) -> bool:
    """Такое же показание за эти местные сутки уже есть: действующее, подтверждённое или ещё ждущее проверки (чтобы повторная
    загрузка файла не создавала вторую запись в очереди проверки). Исправленные и отклонённые не считаются."""
    newer = aliased(VehicleReading)
    return db.scalar(select(exists().where(
        VehicleReading.vehicle_id == vehicle_id, VehicleReading.kind == kind, VehicleReading.value == value,
        VehicleReading.review_state.in_(("ok", "confirmed", "pending")),
        VehicleReading.recorded_at >= day_start(day), VehicleReading.recorded_at < day_start(day + dt.timedelta(days=1)),
        ~exists().where(newer.corrects_id == VehicleReading.id)))) is True


def run(db: Session, p, filename: str, data: bytes, *, default_kind: str, legal_entity_id: int | None, apply: bool) -> dict:
    """Возвращает итог по строкам. При apply=False в конце откатывает всё, что было записано при пробе."""
    if default_kind not in READING_KINDS:
        raise fail(422, "bad_kind", "Неизвестный вид показания", "kind")
    rows = read_table(filename, data)
    header_row, cols = find_columns(rows)
    body = [(i + 1, r) for i, r in enumerate(rows) if i > header_row and any(c is not None and str(c).strip() for c in r)]
    if not body:
        raise fail(422, "bad_file", "В файле нет строк с данными под заголовком.", "file")
    if len(body) > MAX_ROWS:
        raise fail(413, "too_many_rows", f"В файле больше {MAX_ROWS} строк. Разбейте его на части.", "file")

    scope = entity_filter(p, "readings.import", legal_entity_id)
    by_plate: dict[str, list[Vehicle]] = {}
    for v in db.scalars(select(Vehicle).where(scope.cond(Vehicle.legal_entity_id))):
        by_plate.setdefault(plate_key(v.plate), []).append(v)

    now = utcnow()
    results: dict[int, dict] = {}
    todo: list[tuple[Vehicle, str, dt.datetime, Decimal, str, int]] = []
    for n, row in body:
        plate_raw = _cell(row, cols.get("plate"))
        item = {"n": n, "plate": str(plate_raw or "").strip(), "vehicle_id": None, "vehicle": "", "kind": default_kind,
                "date": "", "value": None, "status": "error", "message": ""}
        results[n] = item
        try:
            if not item["plate"]:
                raise ValueError("не указан госномер")
            found = by_plate.get(plate_key(plate_raw), [])
            found = [x for x in found if x.archived_at is None] or found                  # старая машина в архиве и новая с тем же номером
            if not found:
                raise ValueError(f"машина с номером «{item['plate']}» не найдена" + ("" if legal_entity_id else " (или нет доступа)"))
            if len(found) > 1:
                raise ValueError("такой номер есть у нескольких машин в архиве: выберите нужную в карточке вручную")
            v = found[0]
            item["vehicle_id"], item["vehicle"] = v.id, f"{v.plate} · {v.make} {v.model}".strip()
            kind = parse_kind(_cell(row, cols.get("kind")), default_kind)
            item["kind"] = kind
            d, t = parse_date(_cell(row, cols.get("date")))
            item["date"] = d.isoformat() + (f" {t.strftime('%H:%M')}" if t else "")
            at = moment(d, t, now)
            if at > now + dt.timedelta(minutes=5):
                raise ValueError("дата в будущем")
            value = parse_value(_cell(row, cols.get("value")))
            item["value"] = float(value)
            note = str(_cell(row, cols.get("note")) or "").strip()
            todo.append((v, kind, at, value, note, n))
        except ValueError as e:
            item["message"] = str(e)[:300]

    todo.sort(key=lambda x: (x[0].id, x[1], x[2], x[5]))
    label = f"Импорт из файла «{(filename or 'файл')[:80]}»"
    touched: dict[int, Vehicle] = {}
    for v, kind, at, value, note, n in todo:
        item = results[n]
        if already_there(db, v.id, kind, value, local_day(at)):
            item["status"], item["message"] = "duplicate", "уже есть такое показание за эту дату"
            continue
        try:
            # все проверки в add_reading идут до записи, поэтому отказ ничего не оставляет и отдельная транзакция на строку не нужна
            r = svc.add_reading(db, p, v, kind, value, recorded_at=at, source="import", note=(label + (": " + note if note else ""))[:300], recompute=False)
            touched[v.id] = v
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, dict) else {}
            item["message"] = str(detail.get("message") or "не удалось добавить")[:300]
            continue
        if r.is_anomaly:
            item["status"], item["message"] = "review", "большой скачок: показание уйдёт на проверку"
        else:
            item["status"], item["message"] = "ok", ""

    for v in touched.values():
        svc.recompute_current(db, v)
    out_rows = [results[n] for n, _ in body]
    summary = {k: sum(1 for r in out_rows if r["status"] == k) for k in ("ok", "review", "duplicate", "error")}
    summary["total"] = len(out_rows)
    if apply:
        audit.log(db, p, "readings.import", "readings_import", None, legal_entity_id, file=(filename or "")[:120],
                  total=summary["total"], added=summary["ok"] + summary["review"], review=summary["review"],
                  duplicates=summary["duplicate"], errors=summary["error"])
        db.commit()
    else:
        db.rollback()
    labels = {k: str(rows[header_row][i]) for k, i in cols.items() if i < len(rows[header_row])}
    return {"applied": apply, "summary": summary, "columns": labels, "rows": out_rows, "default_kind": default_kind}


TEMPLATE = (
    "Госномер;Дата;Показание;Вид;Примечание\r\n"
    "А123ВС154;01.11.2026;152340;км;\r\n"
    "А123ВС154;15.11.2026;152980,5;км;по бортовому компьютеру\r\n"
    "Е777КХ154;15.11.2026;4812;моточасы;\r\n"
)
