# -*- coding: utf-8 -*-
"""Как показывать пользователя в отметках: «Иванов Пётр» (если связан с сотрудником), иначе имя в системе, иначе логин."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, User


def user_names(db: Session, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    by_employee = {}
    for e in db.scalars(select(Employee).where(Employee.user_id.in_(ids))):
        by_employee[e.user_id] = " ".join(x for x in (e.last_name, e.first_name) if x)
    out = {}
    for u in db.scalars(select(User).where(User.id.in_(ids))):
        out[u.id] = by_employee.get(u.id) or u.display_name or u.login
    return out
