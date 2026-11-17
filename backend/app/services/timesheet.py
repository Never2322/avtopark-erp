# -*- coding: utf-8 -*-
"""Табель: названия месяцев и дней недели, разбор месяца «2026-11», число дней в месяце."""
import calendar
import datetime as dt
import re

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def hours_text(h) -> str:
    return ("%g" % float(h)).replace(".", ",")


def parse_month(raw: str | None, default: dt.date) -> dt.date:
    """Первый день месяца из «2026-11»; пусто — месяц по умолчанию."""
    if not raw:
        return default.replace(day=1)
    m = MONTH_RE.match(raw.strip())
    if not m or not 1 <= int(m.group(2)) <= 12 or not 2000 <= int(m.group(1)) <= 2100:
        raise ValueError("Месяц в формате ГГГГ-ММ, например 2026-11")
    return dt.date(int(m.group(1)), int(m.group(2)), 1)


def month_days(first: dt.date) -> int:
    return calendar.monthrange(first.year, first.month)[1]
