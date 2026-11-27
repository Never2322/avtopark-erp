# -*- coding: utf-8 -*-
"""Уведомления вне сайта: Telegram и почта.

Как устроено:
- всё включается ключами в `.env` (`TELEGRAM_BOT_TOKEN`, `SMTP_HOST`); пока их нет, ничего не отправляется и ничего не ломается;
- уведомление на сайте создаётся как раньше; если у человека подключён Telegram или почта, рядом в той же транзакции кладётся запись
  в очередь `outbox`, а отправляет её фоновая задача. Сбой Telegram или почты не мешает действию, неудачное повторяется с нарастающей паузой;
- Telegram привязывается одноразовым кодом (15 минут): человек присылает его боту, адрес чата вводить не нужно. Бот читает сообщения
  короткими запросами `getUpdates`, поэтому публичный адрес сайта и https для этого не нужны;
- ключи и пароли не попадают ни в журнал, ни в тексты ошибок.
"""
import asyncio
import datetime as dt
import json
import logging
import re
import secrets
import smtplib
import ssl
import time
import urllib.error
import urllib.request
import uuid
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.common import utcnow
from app.config import get_settings
from app.db import engine, session_factory
from app.enums import OUTBOX_FAILED_KEEP_DAYS, OUTBOX_KEEP_DAYS, OUTBOX_MAX_ATTEMPTS, OUTBOX_RETRY_MINUTES
from app.models import NotifyChannel, Outbox, User

log = logging.getLogger("erp.channels")
POLL_LOCK_KEY = 7302
DELIVER_LOCK_KEY = 7303
DELIVER_EVERY_SECONDS = 15
POLL_EVERY_SECONDS = 4
LINK_CODE_MINUTES = 15
HTTP_TIMEOUT = 15
SMTP_TIMEOUT = 20
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # без похожих знаков (0/O, 1/I)
CODE_RE = re.compile(r"[A-Z2-9]{8}")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

HELP_TEXT = ("Это бот уведомлений программы «Контур».\n"
             "Чтобы подключить его, откройте на сайте «Профиль», нажмите «Подключить Telegram» и пришлите сюда полученный код.\n"
             "Отключить уведомления: /stop")


class SendError(Exception):
    """Не удалось отправить. permanent=True: повторять бессмысленно (чат закрыт, адрес не существует)."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


# ---------------------------------------------------------------- что включено

def telegram_enabled() -> bool:
    return bool(get_settings().telegram_bot_token.strip())


def sender_address() -> str:
    s = get_settings()
    return (s.smtp_from or s.smtp_user).strip()


def email_enabled() -> bool:
    return bool(get_settings().smtp_host.strip() and sender_address())


def offsite_enabled() -> bool:
    """Копии вне сервера отправляются только зашифрованными, поэтому без пароля шифрования они выключены."""
    return bool(get_settings().offsite_password)


def clean(message: str) -> str:
    """Ключ и пароль никогда не должны попасть в текст ошибки."""
    s = get_settings()
    for secret in (s.telegram_bot_token, s.smtp_password):
        if secret and len(secret) >= 4:
            message = message.replace(secret, "***")
    return " ".join(message.split())[:300]


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value)) and len(value) <= 120 and not any(c in value for c in "\r\n,;<> ")


# ---------------------------------------------------------------- Telegram

def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """files: {поле: (имя файла, содержимое, тип)}."""
    boundary = "----erp" + uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
    for key, (name, data, mime) in files.items():
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n").encode("utf-8") + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def tg_call(method: str, payload: dict | None = None, files: dict | None = None, timeout: int = HTTP_TIMEOUT):
    s = get_settings()
    if not telegram_enabled():
        raise SendError("Telegram не настроен: нет ключа бота", permanent=True)
    url = f"{s.telegram_api.rstrip('/')}/bot{s.telegram_bot_token}/{method}"
    if files:
        body, ctype = multipart(payload or {}, files)
    else:
        body, ctype = json.dumps(payload or {}).encode("utf-8"), "application/json"
    request = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:                                                          # noqa: BLE001
            data = {}
        reason = data.get("description") or f"ответ {exc.code}"
        # 400 (чат не найден) и 403 (бот заблокирован) повторять бессмысленно; 401 (неверный ключ) и 429 (слишком часто) может исправиться
        raise SendError(clean(f"Telegram: {reason}"), permanent=exc.code in (400, 403))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SendError(clean(f"Telegram недоступен: {getattr(exc, 'reason', exc)}"))
    if not data.get("ok"):
        raise SendError(clean(f"Telegram: {data.get('description') or 'ответ без «ok»'}"))
    return data.get("result")


def send_telegram(chat_id: int | str, text_: str) -> None:
    tg_call("sendMessage", {"chat_id": chat_id, "text": text_[:4000], "disable_web_page_preview": True})


def send_telegram_file(chat_id: int | str, name: str, data: bytes, caption: str = "") -> None:
    tg_call("sendDocument", {"chat_id": str(chat_id), "caption": caption[:1000]}, {"document": (name, data, "application/octet-stream")}, timeout=300)


_bot_cache: dict = {"name": None, "until": 0.0}


def bot_name() -> str:
    """Имя бота без @ (для ссылки t.me). Берётся из настроек, иначе у Telegram и запоминается."""
    s = get_settings()
    if s.telegram_bot_name:
        return s.telegram_bot_name.lstrip("@")
    if not telegram_enabled():
        return ""
    if time.monotonic() < _bot_cache["until"]:
        return _bot_cache["name"] or ""
    try:
        name = (tg_call("getMe", timeout=8) or {}).get("username") or ""
        _bot_cache.update(name=name, until=time.monotonic() + 3600)
    except SendError:
        _bot_cache.update(name="", until=time.monotonic() + 60)
    return _bot_cache["name"] or ""


# ---------------------------------------------------------------- почта

def build_mail(to: str, subject: str, body: str, attachments=()) -> EmailMessage:
    msg = EmailMessage()
    sender = sender_address()
    msg["From"] = formataddr(("Контур", sender))
    msg["To"] = to
    msg["Subject"] = " ".join(subject.split())[:200]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or "localhost")
    msg.set_content(body)
    for name, data, mime in attachments:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    return msg


def _smtp_text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def send_mail(msg: EmailMessage) -> None:
    s = get_settings()
    if not email_enabled():
        raise SendError("Почта не настроена: нет сервера исходящей почты", permanent=True)
    try:
        if s.smtp_security == "ssl":
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT)
        with server:
            server.ehlo()
            if s.smtp_security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg, from_addr=sender_address())
    except smtplib.SMTPRecipientsRefused as exc:
        why = "; ".join(_smtp_text(v[1]) for v in exc.recipients.values())
        raise SendError(clean(f"Почта: адрес отвергнут ({why})"), permanent=True)
    except smtplib.SMTPAuthenticationError as exc:
        raise SendError(clean(f"Почта: сервер не принял логин или пароль ({exc.smtp_code})"))
    except smtplib.SMTPResponseException as exc:
        # 5xx — отказ насовсем, кроме 530 «нужна авторизация» (это настройка, её можно исправить); 4xx — временная неудача
        raise SendError(clean(f"Почта: {exc.smtp_code} {_smtp_text(exc.smtp_error)}"), permanent=500 <= exc.smtp_code < 600 and exc.smtp_code != 530)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise SendError(clean(f"Почта недоступна: {exc}"))


# ---------------------------------------------------------------- очередь

def page_link(url: str) -> str:
    base = get_settings().public_url.strip().rstrip("/")
    return f"{base}{url}" if base and url.startswith("/") else ""


def telegram_text(r: Outbox) -> str:
    parts = [r.title, r.text, page_link(r.url)]
    return "\n".join(p for p in parts if p)


def mail_parts(r: Outbox) -> tuple[str, str]:
    link = page_link(r.url)
    body = "\n\n".join(p for p in (r.text, f"Открыть: {link}" if link else "",
                                   "Это автоматическое сообщение программы «Контур». Отключить такие письма можно в разделе «Профиль».") if p)
    return f"[Контур] {r.title}", body


def enqueue(db: Session, user_ids, title: str, text_: str = "", url: str = "") -> int:
    """Ставит сообщение в очередь тем, у кого подключён Telegram или почта. Вызывается из notify.push."""
    tg, ml = telegram_enabled(), email_enabled()
    ids = sorted(set(user_ids))
    if not (tg or ml) or not ids:
        return 0
    made = 0
    for ch in db.scalars(select(NotifyChannel).where(NotifyChannel.user_id.in_(ids))):
        if tg and ch.telegram_chat_id and ch.telegram_on:
            db.add(Outbox(user_id=ch.user_id, channel="telegram", address=str(ch.telegram_chat_id), title=title[:200], text=text_[:1000], url=url[:200]))
            made += 1
        if ml and ch.email_on and ch.email:
            db.add(Outbox(user_id=ch.user_id, channel="email", address=ch.email, title=title[:200], text=text_[:1000], url=url[:200]))
            made += 1
    return made


def deliver_one(db: Session, r: Outbox) -> None:
    now = utcnow()
    if (r.channel == "telegram" and not telegram_enabled()) or (r.channel == "email" and not email_enabled()):
        r.state, r.last_error = "skipped", "канал выключен: ключ убран из настроек"
        return
    user = db.get(User, r.user_id) if r.user_id else None
    if r.user_id and (not user or not user.is_active):
        r.state, r.last_error = "skipped", "пользователь отключён"
        return
    try:
        if r.channel == "telegram":
            send_telegram(int(r.address), telegram_text(r))
        else:
            subject, body = mail_parts(r)
            send_mail(build_mail(r.address, subject, body))
    except SendError as exc:
        r.attempts += 1
        r.last_error = str(exc)[:300]
        if exc.permanent or r.attempts >= OUTBOX_MAX_ATTEMPTS:
            r.state = "failed"
            if exc.permanent and r.channel == "telegram" and r.user_id:       # бот остановлен или чат удалён: больше не пытаться
                ch = db.get(NotifyChannel, r.user_id)
                if ch and str(ch.telegram_chat_id) == r.address:
                    ch.telegram_on = False
        else:
            r.next_try_at = now + dt.timedelta(minutes=OUTBOX_RETRY_MINUTES[min(r.attempts - 1, len(OUTBOX_RETRY_MINUTES) - 1)])
        return
    r.state, r.sent_at, r.last_error = "sent", now, ""
    r.attempts += 1


def deliver_pending(db: Session, limit: int = 40) -> dict:
    rows = db.scalars(select(Outbox).where(Outbox.state == "new", Outbox.next_try_at <= utcnow()).order_by(Outbox.id).limit(limit)).all()
    result = {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}
    for r in rows:
        try:
            deliver_one(db, r)
            db.commit()
        except Exception:                                                          # noqa: BLE001
            db.rollback()
            log.exception("Сообщение %s не отправлено из-за ошибки программы", r.id)
            continue
        result[{"sent": "sent", "failed": "failed", "skipped": "skipped"}.get(r.state, "retry")] += 1
    return result


def cleanup_outbox(db: Session) -> int:
    now = utcnow()
    n = db.execute(delete(Outbox).where(Outbox.state.in_(("sent", "skipped")), Outbox.created_at < now - dt.timedelta(days=OUTBOX_KEEP_DAYS))).rowcount
    n += db.execute(delete(Outbox).where(Outbox.state == "failed", Outbox.created_at < now - dt.timedelta(days=OUTBOX_FAILED_KEEP_DAYS))).rowcount
    db.commit()
    return n


def _locked(key: int, fn):
    """Выполняет fn, только если ни один другой экземпляр сервера не держит блокировку key."""
    with engine().connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
            return None
        try:
            with session_factory()() as db:
                return fn(db)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            lock.commit()


def run_delivery():
    return _locked(DELIVER_LOCK_KEY, lambda db: (deliver_pending(db), cleanup_outbox(db))[0])


# ---------------------------------------------------------------- привязка Telegram

def channel_of(db: Session, user_id: int, create: bool = False) -> NotifyChannel | None:
    ch = db.get(NotifyChannel, user_id)
    if not ch and create:
        ch = NotifyChannel(user_id=user_id)
        db.add(ch)
        db.flush()
    return ch


def new_link_code(db: Session, user_id: int) -> NotifyChannel:
    ch = channel_of(db, user_id, create=True)
    for _ in range(10):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not db.scalar(select(NotifyChannel.user_id).where(NotifyChannel.link_code == code)):
            ch.link_code, ch.link_expires_at = code, utcnow() + dt.timedelta(minutes=LINK_CODE_MINUTES)
            db.flush()
            return ch
    raise RuntimeError("не удалось подобрать код")


def confirm_link(db: Session, code: str, chat_id: int, name: str) -> str:
    """Ответ бота человеку. Код одноразовый и живёт 15 минут."""
    code = code.strip().upper()
    ch = db.scalar(select(NotifyChannel).where(NotifyChannel.link_code == code)) if CODE_RE.fullmatch(code) else None
    if not ch or not ch.link_expires_at or ch.link_expires_at < utcnow():
        mine = db.scalar(select(NotifyChannel.user_id).where(NotifyChannel.telegram_chat_id == chat_id))
        return ("Этот Telegram уже подключён. Отключить: /stop" if mine
                else "Код не подошёл или устарел. Получите новый на сайте: «Профиль» → «Подключить Telegram».")
    user = db.get(User, ch.user_id)
    if not user or not user.is_active:
        return "Пользователь отключён. Обратитесь к администратору."
    other = db.scalar(select(NotifyChannel.user_id).where(NotifyChannel.telegram_chat_id == chat_id, NotifyChannel.user_id != ch.user_id))
    if other:
        return "Этот Telegram уже подключён к другому пользователю. Сначала отключите его там командой /stop."
    ch.telegram_chat_id, ch.telegram_name, ch.telegram_on = chat_id, name[:80], True
    ch.link_code, ch.link_expires_at = None, None
    return f"Готово, {user.display_name or user.login}. Уведомления программы будут приходить сюда. Отключить: /stop"


def unlink_chat(db: Session, chat_id: int) -> bool:
    ch = db.scalar(select(NotifyChannel).where(NotifyChannel.telegram_chat_id == chat_id))
    if not ch:
        return False
    ch.telegram_chat_id, ch.telegram_name, ch.telegram_on = None, "", True
    return True


def handle_update(db: Session, update: dict) -> None:
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private" or "id" not in chat:                         # в группах бот молчит
        return
    chat_id = int(chat["id"])
    sender = msg.get("from") or {}
    name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x) or sender.get("username") or ""
    body = (msg.get("text") or "").strip()
    cmd, _, arg = body.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start" and arg.strip():
        answer = confirm_link(db, arg, chat_id, name)
    elif cmd == "/stop":
        answer = "Уведомления отключены. Подключить снова можно в «Профиле» на сайте." if unlink_chat(db, chat_id) else "Этот Telegram и не был подключён."
    elif CODE_RE.fullmatch(body.upper()):
        answer = confirm_link(db, body, chat_id, name)
    else:
        linked = db.scalar(select(NotifyChannel.user_id).where(NotifyChannel.telegram_chat_id == chat_id))
        answer = "Уведомления подключены. Отключить: /stop" if linked and cmd == "/start" else HELP_TEXT
    db.commit()
    try:
        send_telegram(chat_id, answer)
    except SendError as exc:
        log.warning("Ответ боту не отправлен: %s", exc)


_offset = 0


def poll_once(db: Session) -> int:
    """Один опрос Telegram: разбирает присланные боту сообщения. Возвращает, сколько их было."""
    global _offset
    updates = tg_call("getUpdates", {"offset": _offset, "timeout": 0, "allowed_updates": ["message"]}) or []
    for u in updates:
        _offset = max(_offset, int(u.get("update_id", 0)) + 1)
        try:
            handle_update(db, u)
        except Exception:                                                          # noqa: BLE001
            db.rollback()
            log.exception("Сообщение боту не разобрано")
    return len(updates)


def run_poll():
    return _locked(POLL_LOCK_KEY, poll_once)


# ---------------------------------------------------------------- проба и состояние для страниц

def send_test(db: Session, user: User) -> dict:
    """Пробное сообщение сразу, без очереди: человек должен увидеть результат. Возвращает итог по каждому каналу."""
    ch = channel_of(db, user.id)
    result: dict = {}
    hello = f"Проверка связи: уведомления программы «Контур» приходят сюда, {user.display_name or user.login}."
    if telegram_enabled():
        if ch and ch.telegram_chat_id:
            try:
                send_telegram(ch.telegram_chat_id, hello)
                result["telegram"] = {"ok": True}
            except SendError as exc:
                result["telegram"] = {"ok": False, "error": str(exc)}
        else:
            result["telegram"] = {"ok": False, "error": "Telegram не подключён"}
    if email_enabled():
        if ch and ch.email:
            try:
                send_mail(build_mail(ch.email, "[Контур] Проверка связи", hello + "\n\nЭто пробное письмо, отвечать на него не нужно."))
                result["email"] = {"ok": True}
            except SendError as exc:
                result["email"] = {"ok": False, "error": str(exc)}
        else:
            result["email"] = {"ok": False, "error": "Адрес почты не указан"}
    return result


def outbox_summary(db: Session) -> dict:
    since = utcnow() - dt.timedelta(days=7)
    rows = db.execute(select(Outbox.channel, Outbox.state, func.count()).where(Outbox.created_at >= since).group_by(Outbox.channel, Outbox.state)).all()
    counts: dict = {}
    for channel, state, n in rows:
        counts.setdefault(channel, {})[state] = n
    failed = db.scalars(select(Outbox).where(Outbox.state == "failed").order_by(Outbox.id.desc()).limit(10)).all()
    return {"counts": counts, "failed": [{"id": r.id, "channel": r.channel, "title": r.title, "error": r.last_error, "at": r.created_at.isoformat()} for r in failed]}


# ---------------------------------------------------------------- фоновые задачи

async def worker() -> None:
    """Отправка очереди. Запускается, только если включён хотя бы один канал."""
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(run_delivery)
        except Exception:                                                          # noqa: BLE001
            log.exception("Отправка очереди не выполнена")
        await asyncio.sleep(DELIVER_EVERY_SECONDS)


async def poller() -> None:
    """Чтение сообщений, присланных боту (привязка по коду, /stop)."""
    await asyncio.sleep(5)
    while True:
        try:
            await asyncio.to_thread(run_poll)
            await asyncio.sleep(POLL_EVERY_SECONDS)
        except Exception:                                                          # noqa: BLE001
            log.warning("Опрос Telegram не удался", exc_info=True)
            await asyncio.sleep(30)
