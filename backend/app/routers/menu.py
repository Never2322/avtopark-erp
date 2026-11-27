# -*- coding: utf-8 -*-
"""Меню слева: порядок групп и пунктов и их названия. Одно на всю программу: настраивают директор и администратор (право «menu.manage»),
видят все. Какие пункты кому показывать, решают права, а не эта настройка: здесь только порядок, группа и название.

Пункты и группы заданы в интерфейсе; сервер хранит их ключи как есть и проверяет только форму и длину, чтобы новая версия меню
(новый пункт, убранный пункт) не ломала сохранённую настройку: незнакомые ключи интерфейс пропускает, новые пункты ставит на их место по умолчанию."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app import audit
from app.common import fail
from app.db import get_db
from app.deps import Principal, current, require
from app.models import AppSetting

router = APIRouter(tags=["Меню"])
KEY = "menu_layout"


class ItemIn(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(default="", max_length=60)
    sub: str = Field(default="", max_length=60)

    @field_validator("label", "sub")
    @classmethod
    def strip(cls, v: str) -> str:
        return " ".join(v.split())


class GroupIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=40)
    items: list[ItemIn] = Field(default_factory=list, max_length=300)

    @field_validator("title")
    @classmethod
    def strip(cls, v: str) -> str:
        return " ".join(v.split())


class LayoutIn(BaseModel):
    groups: list[GroupIn] = Field(max_length=40)


@router.get("/menu-layout")
def get_layout(p: Principal = Depends(current), db: Session = Depends(get_db)):
    row = db.get(AppSetting, KEY)
    return {"layout": row.value if row else None, "can_edit": p.can("menu.manage")}


@router.put("/menu-layout")
def save_layout(body: LayoutIn, p: Principal = Depends(require("menu.manage")), db: Session = Depends(get_db)):
    keys = [g.key for g in body.groups]
    ids = [i.id for g in body.groups for i in g.items]
    if len(set(keys)) != len(keys) or len(set(ids)) != len(ids):
        raise fail(422, "duplicate", "Группа или пункт меню указаны дважды")
    value = body.model_dump()
    row = db.get(AppSetting, KEY)
    if row:
        row.value, row.updated_by_id = value, p.user.id
    else:
        db.add(AppSetting(key=KEY, value=value, updated_by_id=p.user.id))
    audit.log(db, p, "menu.save", "menu", groups=len(keys), items=len(ids))
    db.commit()
    return {"layout": value, "can_edit": True}


@router.delete("/menu-layout")
def reset_layout(p: Principal = Depends(require("menu.manage")), db: Session = Depends(get_db)):
    row = db.get(AppSetting, KEY)
    if row:
        db.delete(row)
        audit.log(db, p, "menu.reset", "menu")
        db.commit()
    return {"layout": None, "can_edit": True}
