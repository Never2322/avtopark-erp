# -*- coding: utf-8 -*-
"""Колокольчик: уведомления текущего пользователя. Чужие уведомления никому не видны, отдельного права на них нет."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.common import fail, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current
from app.enums import NOTIFICATION_KINDS
from app.models import Announcement, AnnouncementTarget, Notification

router = APIRouter(prefix="/notifications", tags=["Уведомления"])


def news_unread(db: Session, p: Principal) -> int:
    """Объявления для меня, которые я ещё не открывал (закрытые и убранные в архив не считаются)."""
    scope = p.scope("announcements.read_own")
    if not scope:
        return 0
    return int(db.scalar(select(func.count()).select_from(AnnouncementTarget).join(Announcement, Announcement.id == AnnouncementTarget.announcement_id)
                         .where(AnnouncementTarget.user_id == p.id, AnnouncementTarget.read_at.is_(None), Announcement.closed.is_(False),
                                Announcement.archived_at.is_(None), scope.cond(Announcement.legal_entity_id))) or 0)


def unread_count(db: Session, p: Principal) -> int:
    return int(db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None))) or 0)


def out(n: Notification) -> dict:
    return {"id": n.id, "kind": n.kind, "kind_label": NOTIFICATION_KINDS.get(n.kind, ""), "title": n.title, "text": n.text, "url": n.url,
            "created_at": n.created_at.isoformat(), "read": n.read_at is not None}


@router.get("")
def list_mine(unread: bool = False, page: int = 1, page_size: int = 30, p: Principal = Depends(current), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == p.id)
    if unread:
        q = q.where(Notification.read_at.is_(None))
    rows, total = page_of(db, q.order_by(Notification.id.desc()), page, page_size)
    return {"items": [out(r[0]) for r in rows], "total": total, "page": max(page, 1), "page_size": min(max(page_size, 1), 200),
            "unread": unread_count(db, p)}


@router.get("/summary")
def summary(p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Для колокольчика и меню: сколько непрочитанных уведомлений и объявлений."""
    return {"unread": unread_count(db, p), "news_unread": news_unread(db, p)}


@router.post("/read-all")
def read_all(p: Principal = Depends(current), db: Session = Depends(get_db)):
    n = db.execute(update(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None)).values(read_at=utcnow())).rowcount
    db.commit()
    return {"marked": n, "unread": 0}


@router.post("/{nid}/read")
def read_one(nid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if not n or n.user_id != p.id:
        raise fail(404, "not_found", "Уведомление не найдено")
    if n.read_at is None:
        n.read_at = utcnow()
        db.commit()
    return {"unread": unread_count(db, p)}
