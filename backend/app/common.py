# -*- coding: utf-8 -*-
"""Общие мелочи: ошибки с понятным текстом, постраничный вывод, поиск, CSV."""
import csv
import datetime as dt
import io
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


def fail(status: int, code: str, message: str, field: str | None = None) -> HTTPException:
    """Ошибка для человека: код для программы, текст для экрана, поле для подсветки."""
    return HTTPException(status_code=status, detail={"code": code, "message": message, "field": field})


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def norm_plate(value: str) -> str:
    """Госномер храним одинаково: заглавными, без пробелов, чтобы «а123вс54» и «А123ВС 54» были одной машиной."""
    return re.sub(r"\s+", "", value or "").upper()


def like(term: str) -> str:
    """Безопасный шаблон для ILIKE: пользовательские % и _ не работают как шаблонные символы."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def page_of(db: Session, query: Select, page: int, size: int, scalar_count: Select | None = None) -> tuple[list[Any], int]:
    """Возвращает строки страницы и общее число строк по тому же запросу."""
    page, size = max(page, 1), min(max(size, 1), 200)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) if scalar_count is None \
        else db.scalar(scalar_count)
    rows = db.execute(query.limit(size).offset((page - 1) * size)).all()
    return rows, int(total or 0)


def csv_text(rows: list[list[Any]]) -> str:
    """CSV для Excel: BOM, разделитель «;». Значения, которые Excel принял бы за формулу, обезвреживаются."""
    out = io.StringIO()
    out.write("﻿")
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    for row in rows:
        w.writerow([("'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v) for v in row])
    return out.getvalue()


def iso(value: dt.datetime | dt.date | None) -> str | None:
    return value.isoformat() if value else None


def num(v) -> str:
    """Число для сообщения человеку: без лишних нулей, с запятой (185100, 12,5)."""
    from decimal import Decimal
    return format(Decimal(str(v)).normalize(), "f").replace(".", ",")
