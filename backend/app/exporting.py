# -*- coding: utf-8 -*-
"""Выгрузка таблиц в файл: общий ответ для «В Excel» во всех разделах.

Раздел собирает таблицу один раз: первая строка — названия столбцов, дальше только данные (числа числами, даты датами, без значков и
служебных пояснений с экрана). Здесь она превращается либо в красивую книгу .xlsx, либо в CSV (для старых закладок и загрузки в другие программы).
"""
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.responses import Response

from app.common import csv_text
from app.config import get_settings
from app.services.xlsx import xlsx_book

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FORMATS = ("xlsx", "csv")
_PLACES = {"money": 2, "ton": 2, "dec2": 2, "dec": 1, "dec1": 1, "dec3": 3}


def check_format(fmt: str) -> str:
    if fmt not in FORMATS:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Такого формата выгрузки нет"})
    return fmt


def local_naive(moment: dt.datetime | None) -> dt.datetime | None:
    """Момент времени по часам организации, без часового пояса (так его записывает Excel)."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(ZoneInfo(get_settings().org_timezone)).replace(tzinfo=None)


def csv_cell(v, kind=None):
    """Значение для CSV: даты по-русски, числа с запятой (Excel с русскими настройками читает их как числа)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, dt.datetime):
        return v.strftime("%d.%m.%Y %H:%M")
    if isinstance(v, dt.date):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, (int, float, Decimal)):
        places = _PLACES.get(kind)
        if places is not None:
            return f"{Decimal(str(v)):.{places}f}".replace(".", ",")
        if isinstance(v, int):
            return str(v)
        return format(Decimal(str(v)).normalize(), "f").replace(".", ",")
    return v


def table_response(fmt: str, stem: str, title: str, rows: list[list], kinds: list | None = None, opts: dict | None = None,
                   extra_sheets: list | None = None) -> Response:
    """rows: первая строка — названия столбцов. stem — имя файла без расширения (латиницей, цифрами и дефисом)."""
    fmt = check_format(fmt)
    if fmt == "xlsx":
        sheets = [(title, rows, kinds or [], opts or {})] + list(extra_sheets or [])
        return Response(xlsx_book(sheets), media_type=XLSX_MIME, headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})
    kinds = list(kinds or [])
    body = [rows[0]] + [[csv_cell(v, kinds[c] if c < len(kinds) else None) for c, v in enumerate(r)] for r in rows[1:]]
    return Response(csv_text(body), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})
