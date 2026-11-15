# -*- coding: utf-8 -*-
"""СИЗ: замена вещи одной кнопкой. Сами списки, добавление и правка идут через общий каркас (app/registry.py, раздел «ppe»)."""
import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import audit
from app.common import fail
from app.db import get_db
from app.deps import Principal, current
from app.models import PpeIssue
from app.registry import RESOURCES, get_for, rows_out
from app.resources import job_today, ppe_auto_due

router = APIRouter(prefix="/ppe", tags=["СИЗ"])


class ReplaceIn(BaseModel):
    issue_date: dt.date | None = None
    size: str | None = Field(default=None, max_length=20)
    qty: int | None = Field(default=None, ge=1, le=1000)
    note: str = Field(default="", max_length=300)


@router.post("/{oid}/replace", status_code=201)
def replace(oid: int, body: ReplaceIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Старая вещь считается возвращённой в день выдачи новой, новая выдаётся тому же сотруднику с тем же размером."""
    res = RESOURCES["ppe"]
    old = get_for(db, res, p, oid, "edit")
    if not p.can("ppe.create", old.legal_entity_id):
        raise fail(403, "forbidden", "Нет права добавлять выдачу СИЗ в этом юрлице")
    if old.archived_at:
        raise fail(409, "archived", "Запись в архиве: сначала верните её")
    if old.returned_date is not None:
        raise fail(409, "already_returned", "Эта вещь уже возвращена: выдайте новую обычной кнопкой «Добавить»")
    day = body.issue_date or job_today()
    if day < old.issue_date:
        raise fail(422, "bad_date", "Замена не может быть раньше выдачи старой вещи", "issue_date")
    if day > job_today() + dt.timedelta(days=1):
        raise fail(422, "bad_date", "Дата выдачи не может быть в будущем", "issue_date")
    new = PpeIssue(legal_entity_id=old.legal_entity_id, employee_id=old.employee_id, ppe_id=old.ppe_id, issue_date=day,
                   qty=body.qty or old.qty, size=(body.size if body.size is not None else old.size).strip(), note=body.note.strip(),
                   due_date=ppe_auto_due(db, old.ppe_id, day), created_by_id=p.id, updated_by_id=p.id)
    old.returned_date, old.updated_by_id = day, p.id
    db.add(new)
    db.flush()
    audit.log(db, p, "ppe.replace", "ppe", new.id, new.legal_entity_id, replaced=old.id, employee_id=new.employee_id, ppe_id=new.ppe_id, issue_date=day.isoformat())
    db.commit()
    return rows_out(db, res, [new], p)[0]
