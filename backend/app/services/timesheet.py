# -*- coding: utf-8 -*-
"""Табель: разбор текста клетки («Я», «Я 6», «Б»), итоги по строке, дни месяца."""
import calendar
import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal

from app.enums import TIMESHEET_ABSENCE_CODES, TIMESHEET_CODES, TIMESHEET_DAY_CODES, TIMESHEET_DEFAULT_HOURS

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
# Часы указывают только у кодов, которые их считают: рабочие дни и сверхурочные
HOURS_CODES = TIMESHEET_DAY_CODES + ("С",)
# латинские буквы, похожие на русские: «B» вместо «В», «OT» вместо «ОТ»
LOOKALIKE = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
CELL_RE = re.compile(r"^([A-Za-zА-Яа-яЁё]{1,2})[\s/:]*(\d{1,2}(?:[.,]\d+)?)?$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


class CellError(ValueError):
    pass


def parse_cell(raw: str | None) -> tuple[str | None, Decimal | None]:
    """(код, часы) из текста клетки; (None, None) для пустой; CellError с пояснением для ошибки."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    m = CELL_RE.match(raw)
    if not m:
        raise CellError(f"«{raw}»: ожидается код, например Я, Б или «Я 6»")
    code = m.group(1).upper().translate(LOOKALIKE)
    if code not in TIMESHEET_CODES:
        raise CellError(f"«{m.group(1)}»: такого кода нет")
    hours = None
    if m.group(2):
        hours = Decimal(m.group(2).replace(",", "."))
        if hours != hours.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP):
            raise CellError(f"«{raw}»: часы указывают с точностью до десятых, например 7,5")
        if hours > 24:
            raise CellError(f"«{raw}»: в сутках не больше 24 часов")
        if code not in HOURS_CODES:
            raise CellError(f"«{raw}»: у кода «{code}» часы не указывают")
    if code == "С" and hours is None:
        raise CellError(f"«{raw}»: для сверхурочной работы укажите часы, например «С 2»")
    return code, hours


def hours_text(h) -> str:
    return ("%g" % float(h)).replace(".", ",")


def cell_text(code: str | None, hours) -> str:
    return "" if not code else code + ("" if hours is None else " " + hours_text(hours))


def sums(cells) -> tuple[int, float, int]:
    """Итоги строки: дней явки, часов, дней неявок. cells: пары (код, часы)."""
    days = absent = 0
    hours = 0.0
    for code, h in cells:
        if code in TIMESHEET_DAY_CODES:
            days += 1
            hours += TIMESHEET_DEFAULT_HOURS if h is None else float(h)
        elif code == "С":
            hours += float(h or 0)
        elif code in TIMESHEET_ABSENCE_CODES:
            absent += 1
    return days, round(hours, 1), absent


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


def code_kind(code: str) -> str:
    return "day" if code in TIMESHEET_DAY_CODES else "absence" if code in TIMESHEET_ABSENCE_CODES else "other"
