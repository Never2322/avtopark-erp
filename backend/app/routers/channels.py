# -*- coding: utf-8 -*-
"""Уведомления вне сайта: личные настройки человека (Telegram и почта) и состояние для администратора.
Свои настройки может менять любой вошедший; чужие не видны и не меняются. Состояние каналов видит тот, кто видит пользователей."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.config import get_settings
from app.db import get_db
from app.deps import Principal, current, require_global
from app.models import NotifyChannel
from app.services import channels, offsite

router = APIRouter(tags=["Уведомления вне сайта"])


class ChannelsIn(BaseModel):
    email: str | None = None
    email_on: bool | None = None
    telegram_on: bool | None = None


def mine(db: Session, p: Principal) -> dict:
    ch = channels.channel_of(db, p.id)
    live = bool(ch and ch.link_code and ch.link_expires_at and ch.link_expires_at > utcnow())
    bot = channels.bot_name() if channels.telegram_enabled() else ""
    linked = bool(ch and ch.telegram_chat_id)
    return {
        "telegram": {
            "available": channels.telegram_enabled(), "bot": bot, "linked": linked, "name": ch.telegram_name if linked else "",
            "on": bool(linked and ch.telegram_on), "code": ch.link_code if live else None,
            "code_expires_at": ch.link_expires_at.isoformat() if live else None,
            "link": f"https://t.me/{bot}?start={ch.link_code}" if live and bot else "",
        },
        "email": {"available": channels.email_enabled(), "address": ch.email if ch else "", "on": bool(ch and ch.email_on and ch.email)},
    }


@router.get("/me/channels")
def get_mine(p: Principal = Depends(current), db: Session = Depends(get_db)):
    return mine(db, p)


@router.put("/me/channels")
def save_mine(body: ChannelsIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    ch = channels.channel_of(db, p.id, create=True)
    if body.email is not None:
        address = body.email.strip()
        if address and not channels.valid_email(address):
            raise fail(422, "validation", "Проверьте адрес почты", "email")
        ch.email = address
        if not address:
            ch.email_on = False
    if body.email_on is not None:
        if body.email_on and not ch.email:
            raise fail(422, "validation", "Сначала укажите адрес почты", "email")
        ch.email_on = body.email_on
    if body.telegram_on is not None:
        ch.telegram_on = body.telegram_on
    audit.log(db, p, "channels.update", "user", p.id, None, email=bool(ch.email), email_on=ch.email_on, telegram_on=ch.telegram_on)
    db.commit()
    return mine(db, p)


@router.post("/me/channels/telegram/code")
def make_code(p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not channels.telegram_enabled():
        raise fail(409, "channel_off", "Telegram на сервере не настроен: администратору нужно добавить ключ бота")
    channels.new_link_code(db, p.id)
    audit.log(db, p, "channels.telegram_code", "user", p.id, None)
    db.commit()
    return mine(db, p)


@router.delete("/me/channels/telegram")
def unlink(p: Principal = Depends(current), db: Session = Depends(get_db)):
    ch = channels.channel_of(db, p.id)
    if ch:
        ch.telegram_chat_id, ch.telegram_name, ch.telegram_on, ch.link_code, ch.link_expires_at = None, "", True, None, None
        audit.log(db, p, "channels.telegram_unlink", "user", p.id, None)
        db.commit()
    return mine(db, p)


@router.post("/me/channels/test")
def test(p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not (channels.telegram_enabled() or channels.email_enabled()):
        raise fail(409, "channel_off", "Ни Telegram, ни почта на сервере не настроены")
    result = channels.send_test(db, p.user)
    audit.log(db, p, "channels.test", "user", p.id, None, result={k: v["ok"] for k, v in result.items()})
    db.commit()
    return {"result": result}


@router.get("/channels/status")
def status(p: Principal = Depends(require_global("users.view")), db: Session = Depends(get_db)):
    s = get_settings()
    linked = db.scalar(select(func.count()).select_from(NotifyChannel).where(NotifyChannel.telegram_chat_id.is_not(None))) or 0
    mails = db.scalar(select(func.count()).select_from(NotifyChannel).where(NotifyChannel.email_on.is_(True), NotifyChannel.email != "")) or 0
    return {
        "telegram": {"enabled": channels.telegram_enabled(), "bot": channels.bot_name() if channels.telegram_enabled() else "", "linked_users": linked},
        "email": {"enabled": channels.email_enabled(), "sender": channels.sender_address() if channels.email_enabled() else "", "users": mails,
                  "security": s.smtp_security},
        "public_url": s.public_url,
        "offsite": offsite.plan(),
        **channels.outbox_summary(db),
    }
