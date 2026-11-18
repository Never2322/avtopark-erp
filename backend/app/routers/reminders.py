# -*- coding: utf-8 -*-
"""Напоминания: что наступило и что наступит в ближайшие дни, из всех разделов, на которые у человека есть право."""
import datetime as dt

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app import audit
from app.exporting import check_format, table_response
from app.db import get_db
from app.deps import Principal, require
from app.services import reminders as rem

router = APIRouter(prefix="/reminders", tags=["Напоминания"])

LEVELS = {"due": "Наступило", "soon": "Скоро"}


def counts(items: list[dict]) -> dict:
    return {"due": sum(1 for i in items if i["level"] == "due"), "soon": sum(1 for i in items if i["level"] == "soon"), "total": len(items)}


@router.get("")
def list_reminders(level: str = "", source: str = "", legal_entity_id: int | None = None,
                   p: Principal = Depends(require("reminders.view")), db: Session = Depends(get_db)):
    rows = rem.reminders(db, p, legal_entity_id)
    by_source = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    shown = [r for r in rows if (not level or r["level"] == level) and (not source or r["source"] == source)]
    return {"items": shown, "counts": counts(rows), "sources": [{"code": k, "label": rem.SOURCES[k], "count": v} for k, v in by_source.items()],
            "levels": LEVELS}


@router.get("/summary")
def summary(legal_entity_id: int | None = None, p: Principal = Depends(require("reminders.view")), db: Session = Depends(get_db)):
    """Сколько всего наступило и скоро: для плашки на главной."""
    return counts(rem.reminders(db, p, legal_entity_id))


@router.get("/export.{fmt}")
def export(fmt: str, legal_entity_id: int | None = None, p: Principal = Depends(require("reminders.view")), db: Session = Depends(get_db)):
    check_format(fmt)
    rows = rem.reminders(db, p, legal_entity_id)
    table: list[list] = [["Состояние", "Раздел", "Что", "Кто или что", "Срок", "Осталось"]]
    for r in rows:
        table.append([LEVELS[r["level"]], r["source_label"], r["title"], r["subject"] or None, dt.date.fromisoformat(r["day"]), r["left_text"] or None])
    audit.log(db, p, "reminders.export", "reminders", None, legal_entity_id, rows=len(rows))
    db.commit()
    return table_response(fmt, "reminders", "Напоминания", table, [None, None, None, None, "date", None])
