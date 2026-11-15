# -*- coding: utf-8 -*-
"""Календарь: свои события и сроки из других разделов (документы, СИЗ, штрафы, аренда, карты, события сотрудников, дни рождения).

Вид: день, неделя, месяц, год. Сроки из других разделов только читаются и показываются тем, у кого есть право на тот раздел.
Своими событиями управляет обычный раздел «Свои события» (`/api/calendar_events`), календарь отдаёт их вместе с готовой записью для формы.
"""
import calendar as cal
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, require
from app.enums import CALENDAR_VIEWS
from app.models import CalendarEvent
from app.registry import RESOURCES, rows_out
from app.services import reminders as rem

router = APIRouter(prefix="/calendar", tags=["Календарь"])

MONTHS = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_GEN = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def parse_day(raw: str, fallback: dt.date) -> dt.date:
    try:
        return dt.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return fallback


def view_range(view: str, anchor: dt.date) -> tuple[dt.date, dt.date]:
    if view == "day":
        return anchor, anchor
    if view == "week":
        start = anchor - dt.timedelta(days=anchor.weekday())
        return start, start + dt.timedelta(days=6)
    if view == "year":
        return dt.date(anchor.year, 1, 1), dt.date(anchor.year, 12, 31)
    weeks = cal.Calendar(0).monthdatescalendar(anchor.year, anchor.month)
    return weeks[0][0], weeks[-1][-1]


def shift(view: str, anchor: dt.date, step: int) -> dt.date:
    if view == "day":
        return anchor + dt.timedelta(days=step)
    if view == "week":
        return anchor + dt.timedelta(days=7 * step)
    if view == "year":
        return anchor.replace(year=anchor.year + step, day=28 if (anchor.month, anchor.day) == (2, 29) else anchor.day)
    n = anchor.year * 12 + anchor.month - 1 + step
    return dt.date(n // 12, n % 12 + 1, 1)


def day_cell(d: dt.date, today: dt.date, month: int | None = None) -> dict:
    return {"d": d.isoformat(), "n": d.day, "wd": WEEKDAYS[d.weekday()], "in_month": month is None or d.month == month,
            "today": d == today, "off": d.weekday() >= 5}


def title_of(view: str, anchor: dt.date, lo: dt.date, hi: dt.date) -> str:
    if view == "year":
        return str(anchor.year)
    if view == "day":
        return f"{anchor.day} {MONTHS_GEN[anchor.month]} {anchor.year}, {WEEKDAYS[anchor.weekday()]}"
    if view == "week":
        return f"{lo.day} {MONTHS_GEN[lo.month]} – {hi.day} {MONTHS_GEN[hi.month]} {hi.year}"
    return f"{MONTHS[anchor.month].capitalize()} {anchor.year}"


@router.get("")
def calendar_page(view: str = "month", d: str = "", legal_entity_id: int | None = None,
                  p: Principal = Depends(require("calendar.view")), db: Session = Depends(get_db)):
    today = rem.local_today()
    view = view if view in CALENDAR_VIEWS else "month"
    anchor = parse_day(d, today)
    lo, hi = view_range(view, anchor)
    items = rem.collect(db, p, entity_id=legal_entity_id, lo=lo, hi=hi, today=today)
    out = [rem.calendar_out(i, today) for i in items]
    own_ids = [i["ref_id"] for i in out if i["source"] == "calendar"]
    if own_ids:
        objs = db.scalars(select(CalendarEvent).where(CalendarEvent.id.in_(own_ids))).all()
        rows = {r["id"]: r for r in rows_out(db, RESOURCES["calendar_events"], list(objs), p)}
        for i in out:
            if i["source"] == "calendar":
                i["row"] = rows.get(i["ref_id"])
    out.sort(key=lambda i: (i["day"], i["time"] == "", i["time"], i["auto"], i["title"]))
    if view == "month":
        grid = {"weeks": [[day_cell(x, today, anchor.month) for x in week] for week in cal.Calendar(0).monthdatescalendar(anchor.year, anchor.month)]}
    elif view == "week":
        grid = {"days": [day_cell(lo + dt.timedelta(n), today) for n in range(7)]}
    elif view == "day":
        grid = {"days": [day_cell(anchor, today)]}
    else:
        grid = {"months": [{"m": m, "title": MONTHS[m].capitalize(), "weeks": [[day_cell(x, today, m) for x in week] for week in cal.Calendar(0).monthdatescalendar(anchor.year, m)]}
                           for m in range(1, 13)]}
    return {"view": view, "views": [{"id": k, "label": v} for k, v in CALENDAR_VIEWS.items()], "anchor": anchor.isoformat(), "today": today.isoformat(),
            "title": title_of(view, anchor, lo, hi), "from": lo.isoformat(), "to": hi.isoformat(),
            "prev": shift(view, anchor, -1).isoformat(), "next": shift(view, anchor, 1).isoformat(),
            "grid": grid, "items": out, "can_create": bool(p.scope("calendar_events.create"))}
