# -*- coding: utf-8 -*-
"""Копия вне сервера: готовая и проверенная копия базы уходит зашифрованным файлом в Telegram и/или на почту.

Зачем: копии в папке сервера не спасают, если пропал сам сервер. Зачем шифрование: в копии вся база, а Telegram и почта — чужие серверы.
Поэтому без пароля шифрования (`OFFSITE_PASSWORD`) копии вне сервера не отправляются совсем.

Формат файла `*.dump.enc`: метка `ERPBK1`, соль (16 байт), одноразовый номер (12 байт), затем данные, зашифрованные AES-256-GCM;
ключ получается из пароля функцией scrypt. Расшифровать: `python -m app.cli decrypt-backup --in ФАЙЛ --out ФАЙЛ.dump`
(пароль спросит сам или возьмёт из `OFFSITE_PASSWORD`), потом `pg_restore` (см. docs/Разработка.md).
"""
import getpass
import logging
import os
import re
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import select

from app import audit
from app.common import utcnow
from app.config import get_settings
from app.db import session_factory
from app.models import BackupJob, NotifyChannel
from app.services import backup, channels, notify

log = logging.getLogger("erp.offsite")
MAGIC = b"ERPBK1\n"
TELEGRAM_MAX_BYTES = 49 * 1024 * 1024                  # у бота предел 50 МБ на файл
SCRYPT_N = 2 ** 15


class WrongPassword(Exception):
    pass


def derive(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=8, p=1).derive(password.encode("utf-8"))


def encrypt(data: bytes, password: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(derive(password, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, password: str) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 16 + 12 + 16:
        raise ValueError("Это не зашифрованная копия программы «Контур»")
    salt, nonce, body = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:len(MAGIC) + 28], blob[len(MAGIC) + 28:]
    try:
        return AESGCM(derive(password, salt)).decrypt(nonce, body, MAGIC)
    except InvalidTag:
        raise WrongPassword("Неверный пароль или файл повреждён")


def decrypt_file(src: str, dst: str, password: str | None = None) -> int:
    """Для командной строки: возвращает 0, если всё хорошо."""
    password = password or os.environ.get("OFFSITE_PASSWORD") or getpass.getpass("Пароль шифрования копий: ")
    with open(src, "rb") as fh:
        blob = fh.read()
    try:
        data = decrypt(blob, password)
    except (ValueError, WrongPassword) as exc:
        print(str(exc))
        return 1
    with open(dst, "wb") as fh:
        fh.write(data)
    print(f"Готово: {dst} ({len(data) / 1024 / 1024:.1f} МБ). Дальше: pg_restore --no-owner -d ИМЯ_БАЗЫ {dst}")
    return 0


def email_recipients() -> list[str]:
    return [x for x in (a.strip() for a in re.split(r"[,;\s]+", get_settings().offsite_email_to)) if channels.valid_email(x)]


def telegram_targets(db) -> list[int]:
    s = get_settings()
    if s.offsite_telegram_chat_id.strip():
        try:
            return [int(s.offsite_telegram_chat_id.strip())]
        except ValueError:
            return []
    ids = notify.users_with(db, "backups.manage", None)
    rows = db.scalars(select(NotifyChannel.telegram_chat_id).where(
        NotifyChannel.user_id.in_(ids), NotifyChannel.telegram_chat_id.is_not(None), NotifyChannel.telegram_on.is_(True)))
    return sorted(set(rows)) if ids else []


def plan() -> dict:
    """Куда будут уходить копии: для страницы копий и для проверки настроек."""
    s = get_settings()
    with session_factory()() as db:
        chats = telegram_targets(db) if channels.telegram_enabled() else []
    return {"enabled": channels.offsite_enabled(), "telegram": channels.telegram_enabled() and bool(chats), "telegram_chats": len(chats),
            "email": channels.email_enabled() and bool(email_recipients()), "email_to": len(email_recipients()), "email_max_mb": s.offsite_email_max_mb}


def send(job_id: int) -> dict | None:
    """Отправляет копию, результат записывает в карточку копии. Любая неудача: запись в карточке и уведомление ответственным."""
    if not channels.offsite_enabled():
        return None
    s = get_settings()
    result: dict = {"encrypted": True}
    with session_factory()() as db:
        job = db.get(BackupJob, job_id)
        path = backup.file_path(job) if job and job.status == "ok" else None
        if not path:
            return None
        chats = telegram_targets(db) if channels.telegram_enabled() else []
        mails = email_recipients() if channels.email_enabled() else []
        if not chats and not mails:
            return None
        blob = encrypt(path.read_bytes(), s.offsite_password)
        name = path.name + ".enc"
        checked = bool((job.restore_check or {}).get("ok"))
        when = job.finished_at.astimezone(ZoneInfo(s.org_timezone)).strftime("%d.%m.%Y %H:%M") if job.finished_at else ""
        size_mb = len(blob) / 1024 / 1024
        caption = (f"Копия базы «Контур» от {when}, {size_mb:.1f} МБ, "
                   f"{'проверена восстановлением' if checked else 'проверка восстановлением не пройдена или не завершена'}. "
                   f"Файл зашифрован паролем OFFSITE_PASSWORD. Расшифровка: python -m app.cli decrypt-backup")
        if chats:
            if len(blob) > TELEGRAM_MAX_BYTES:
                result["telegram"] = {"ok": False, "error": f"файл {size_mb:.0f} МБ больше предела Telegram (49 МБ)"}
            else:
                errors, sent = [], 0
                for chat in chats:
                    try:
                        channels.send_telegram_file(chat, name, blob, caption)
                        sent += 1
                    except channels.SendError as exc:
                        errors.append(str(exc))
                result["telegram"] = {"ok": sent > 0 and not errors, "sent": sent, "error": "; ".join(dict.fromkeys(errors))[:300]}
        if mails:
            if len(blob) > s.offsite_email_max_mb * 1024 * 1024:
                result["email"] = {"ok": False, "error": f"файл {size_mb:.0f} МБ больше предела {s.offsite_email_max_mb} МБ"}
            else:
                try:
                    msg = channels.build_mail(", ".join(mails), f"[Контур] Копия базы от {when}", caption,
                                              [(name, blob, "application/octet-stream")])
                    channels.send_mail(msg)
                    result["email"] = {"ok": True, "sent": len(mails), "error": ""}
                except channels.SendError as exc:
                    result["email"] = {"ok": False, "error": str(exc)}
        result["at"] = utcnow().isoformat()
        job.offsite = result
        audit.log(db, None, "backup.offsite", "backup", job.id, None, login="система",
                  telegram=result.get("telegram", {}).get("ok"), email=result.get("email", {}).get("ok"), size=len(blob))
        failed = [k for k in ("telegram", "email") if k in result and not result[k]["ok"]]
        if failed:
            who = notify.users_with(db, "backups.manage", None)
            text_ = "; ".join(f"{'Telegram' if k == 'telegram' else 'Почта'}: {result[k]['error']}" for k in failed)
            notify.push(db, who, "info", "Копию не удалось отправить вне сервера", text_, "/backups", None, key=f"offsite:{job_id}")
        db.commit()
    return result
