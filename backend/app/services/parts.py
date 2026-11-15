# -*- coding: utf-8 -*-
"""Детали: запоминание введённых названий в каталоге, подсказки «эту деталь уже меняли», проверка строк списка."""
import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.common import fail
from app.enums import PART_UNITS, REQUEST_PARTS_MAX, REQUEST_PARTS_OPEN
from app.models import RequestPart, ServiceRequest, ServiceRequestEvent, User, Vehicle
from app.parts_seed import norm
from app.services import readings as readings_svc
from app.registry import org_tz

MAX_QTY = Decimal("1000000")


def clean_line(raw: dict, units=PART_UNITS, where: str = "") -> dict:
    """Одна строка списка деталей из присланных данных. Ошибка — с понятным текстом и номером строки."""
    name = str(raw.get("name") or "").strip()
    article = str(raw.get("article") or "").strip()
    unit = str(raw.get("unit") or "шт").strip() or "шт"
    if not name:
        raise fail(422, "part_name", f"{where}Впишите название детали", "name")
    if not norm(name):
        raise fail(422, "part_name", f"{where}Название детали должно содержать буквы или цифры", "name")
    if len(name) > 200:
        raise fail(422, "part_name", f"{where}Название длиннее 200 знаков", "name")
    if len(article) > 60:
        raise fail(422, "part_article", f"{where}Артикул длиннее 60 знаков", "article")
    if unit not in units:
        raise fail(422, "part_unit", f"{where}Единица «{unit}» не из списка: {', '.join(units)}", "unit")
    try:
        qty = Decimal(str(raw.get("qty") if raw.get("qty") not in (None, "") else 1).replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        raise fail(422, "part_qty", f"{where}Количество введите числом", "qty") from None
    if not qty.is_finite() or qty <= 0 or qty > MAX_QTY or qty.as_tuple().exponent < -3:
        raise fail(422, "part_qty", f"{where}Количество должно быть больше нуля, не больше миллиона и не точнее тысячных", "qty")
    return {"name": name, "article": article, "unit": unit, "qty": qty}


def clean_lines(rows: list[dict], where_row: bool = True) -> list[dict]:
    if len(rows) > REQUEST_PARTS_MAX:
        raise fail(422, "too_many_parts", f"В одном обращении не больше {REQUEST_PARTS_MAX} деталей", "items")
    return [clean_line(r, where=(f"Строка {i}: " if where_row and len(rows) > 1 else "")) for i, r in enumerate(rows, 1)]


def same_part(a: dict | RequestPart, b: dict | RequestPart) -> bool:
    """Одна и та же деталь: одинаковое название (без регистра и знаков), артикул и единица."""
    g = (lambda x, k: x[k] if isinstance(x, dict) else getattr(x, k))
    return norm(g(a, "name")) == norm(g(b, "name")) and norm(g(a, "article")) == norm(g(b, "article")) and g(a, "unit") == g(b, "unit")


def learn(db: Session, rows: list[dict]) -> None:
    """Запоминает введённые детали: счётчик использований растёт, артикул подтягивается, если у детали его не было."""
    for r in rows:
        key = norm(r["name"])
        if not key:
            continue
        unit = r["unit"] if r["unit"] in PART_UNITS else "шт"
        db.execute(text(
            "INSERT INTO parts_catalog (name, name_key, article, unit, uses) VALUES (:n, :k, :a, :u, 1) "
            "ON CONFLICT (name_key) WHERE archived_at IS NULL DO UPDATE SET uses = parts_catalog.uses + 1, "
            "article = CASE WHEN parts_catalog.article = '' THEN EXCLUDED.article ELSE parts_catalog.article END"),
            {"n": r["name"][:200], "k": key, "a": r["article"][:60], "u": unit})


def qty_text(q: Decimal | float) -> str:
    s = format(Decimal(str(q)).normalize(), "f")
    return s.replace(".", ",")


# --------------------------------------------------------------------------
# «Эту деталь уже меняли»
# --------------------------------------------------------------------------

def _reading_on(db: Session, vehicle: Vehicle, day: dt.date) -> Decimal | None:
    kind = vehicle.type.primary_reading if vehicle.type else "km"
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=org_tz())
    prev, _ = readings_svc.neighbours(db, vehicle.id, kind, end)
    return prev.value if prev else None


def vehicle_history(db: Session, vehicle_id: int, exclude_request_id: int | None = None) -> list[tuple[RequestPart, ServiceRequest]]:
    """Все детали из обращений машины (кроме отменённых и убранных), свежие обращения сначала."""
    q = (select(RequestPart, ServiceRequest).join(ServiceRequest, ServiceRequest.id == RequestPart.request_id)
         .where(ServiceRequest.vehicle_id == vehicle_id, RequestPart.removed_at.is_(None), ServiceRequest.status != "cancelled")
         .order_by(ServiceRequest.created_at.desc(), RequestPart.id))
    if exclude_request_id:
        q = q.where(ServiceRequest.id != exclude_request_id)
    return [(a, b) for a, b in db.execute(q)]


def previous_use(db: Session, vehicle: Vehicle, name: str, article: str, exclude_request_id: int | None = None, limit: int = 20,
                 history: list | None = None) -> list[dict]:
    """Прошлые случаи этой детали у этой машины в других обращениях (кроме отменённых). Свежие сначала."""
    key, art = norm(name), norm(article)
    if not key and not art:
        return []
    rows = history if history is not None else vehicle_history(db, vehicle.id, exclude_request_id)
    found, seen = [], set()
    for part, req in rows:
        if req.id == exclude_request_id or req.id in seen:
            continue
        if (key and norm(part.name) == key) or (art and norm(part.article) == art):
            seen.add(req.id)                            # одно обращение считаем один раз, даже если там две строки
            found.append({"request_id": req.id, "number": req.number, "day": req.created_at.astimezone(org_tz()).date(),
                          "open": req.status in REQUEST_PARTS_OPEN})
        if len(found) >= limit:
            break
    if found:
        kind = vehicle.type.primary_reading if vehicle.type else "km"
        now = vehicle.current_km if kind == "km" else vehicle.current_hours
        for item in found[:3]:
            then = _reading_on(db, vehicle, item["day"])
            item["since"] = float(now - then) if (now is not None and then is not None and now >= then) else None
            item["since_unit"] = "км" if kind == "km" else "моточасов"
    return found


def hint_text(uses: list[dict]) -> str:
    """Короткая подсказка по списку из previous_use; пустая строка, если подсказывать нечего."""
    if not uses:
        return ""
    opened = [u for u in uses if u["open"]]
    if opened:
        return f"Уже есть в открытом обращении № {opened[0]['number']}"
    last = uses[0]
    text_ = f"Уже меняли: {last['day'].strftime('%d.%m.%Y')} (обращение № {last['number']})"
    if last.get("since"):
        text_ += ", с тех пор " + f"{int(last['since']):,}".replace(",", chr(160)) + f" {last['since_unit']}"
    if len(uses) > 1:
        text_ += f", всего раз: {len(uses)}"
    return text_


# --------------------------------------------------------------------------
# Список деталей обращения для карточки
# --------------------------------------------------------------------------

def live_parts(db: Session, request_id: int) -> list[RequestPart]:
    return list(db.scalars(select(RequestPart).where(RequestPart.request_id == request_id, RequestPart.removed_at.is_(None))
                           .order_by(RequestPart.id)))


def can_change(p, r: ServiceRequest) -> bool:
    """Добавлять и убирать детали: ведущий обращения, а автор заявки — пока её ещё не взяли в работу."""
    if r.status not in REQUEST_PARTS_OPEN:
        return False
    if p.can("requests.edit", r.legal_entity_id):
        return True
    return r.reporter_id == p.id and p.can("requests.create", r.legal_entity_id) and r.status in ("new", "diagnostics")


def _name(db: Session, uid: int | None) -> str | None:
    u = db.get(User, uid) if uid else None
    return (u.display_name or u.login) if u else None


def part_out(db: Session, p, r: ServiceRequest, part: RequestPart, history: list | None, editable: bool) -> dict:
    hint, uses = "", []
    if history is not None:
        uses = previous_use(db, r.vehicle, part.name, part.article, r.id, history=history)
        hint = hint_text(uses)
    return {"id": part.id, "name": part.name, "article": part.article, "qty": float(part.qty), "unit": part.unit,
            "qty_text": qty_text(part.qty), "added_at": part.added_at.isoformat(), "added_by": _name(db, part.added_by_id),
            "bought": part.bought_at is not None, "bought_at": part.bought_at.isoformat() if part.bought_at else None,
            "bought_by": _name(db, part.bought_by_id), "hint": hint, "hint_open": bool(uses and uses[0]["open"]),
            "can_remove": editable and part.bought_at is None}


def block(db: Session, p, r: ServiceRequest) -> dict:
    """Блок «Детали» для карточки обращения: список, что уже куплено, подсказки (подсказки видит только тот, кто видит все обращения)."""
    rows = live_parts(db, r.id)
    history = vehicle_history(db, r.vehicle_id, r.id) if rows and p.can("requests.view", r.legal_entity_id) else None
    editable = can_change(p, r)
    items = [part_out(db, p, r, x, history, editable) for x in rows]
    bought = sum(1 for x in rows if x.bought_at)
    return {"items": items, "total": len(rows), "bought": bought, "to_buy": len(rows) - bought,
            "can_add": editable and len(rows) < REQUEST_PARTS_MAX, "max": REQUEST_PARTS_MAX}


# --------------------------------------------------------------------------
# Покупка деталей двигает обращение
# --------------------------------------------------------------------------

def _event(db: Session, r: ServiceRequest, p, kind: str, text_: str, old=None, new=None):
    db.add(ServiceRequestEvent(request_id=r.id, kind=kind, text=text_, author_id=p.id, from_status=old, to_status=new))


def after_bought(db: Session, r: ServiceRequest, p) -> bool:
    """Все детали куплены, а обращение стояло «Ждёт запчасти» — возвращаем его в работу. Само не закрываем: это решает механик."""
    if r.status != "waiting_parts":
        return False
    rows = live_parts(db, r.id)
    if not rows or any(x.bought_at is None for x in rows):
        return False
    _event(db, r, p, "status", "Все детали куплены", "waiting_parts", "in_progress")
    r.status, r.updated_by_id = "in_progress", p.id
    return True


def after_unbought(db: Session, r: ServiceRequest, p) -> bool:
    """Покупку отменили, а обращение уже в работе — снова ждём запчасти."""
    if r.status != "in_progress":
        return False
    _event(db, r, p, "status", "Покупку деталей отменили: ждём запчасти", "in_progress", "waiting_parts")
    r.status, r.updated_by_id = "waiting_parts", p.id
    return True
