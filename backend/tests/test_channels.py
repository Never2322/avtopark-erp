# -*- coding: utf-8 -*-
"""Уведомления вне сайта (Telegram и почта) и копия вне сервера.
Настоящие Telegram и почта не вызываются: вместо них местные заглушки (tests/fakes.py), запросы идут по сети как в жизни."""
import datetime as dt

import pytest
from sqlalchemy import select

from app.common import utcnow
from app.config import get_settings
from app.db import session_factory
from app.models import AuditLog, BackupJob, Notification, NotifyChannel, Outbox, User
from app.services import backup, channels, notify, offsite
from tests.fakes import TOKEN, FakeSMTP, FakeTelegram
from tests.test_notifications import uid

ADMIN_CHAT, MECH_CHAT, DIR_CHAT = 9001, 9002, 9003


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", TOKEN)
    monkeypatch.setattr(s, "telegram_api", fake.url)
    monkeypatch.setattr(s, "public_url", "https://erp.example.ru")
    monkeypatch.setattr(channels, "_offset", 0)
    channels._bot_cache.update(name=None, until=0.0)
    yield fake
    fake.stop()
    channels._bot_cache.update(name=None, until=0.0)


@pytest.fixture
def smtp(monkeypatch):
    fake = FakeSMTP()
    s = get_settings()
    for key, value in (("smtp_host", "127.0.0.1"), ("smtp_port", fake.port), ("smtp_security", "none"), ("smtp_from", "erp@example.ru"),
                       ("smtp_user", ""), ("smtp_password", "")):
        monkeypatch.setattr(s, key, value)
    yield fake
    fake.stop()


def link_chat(login: str, chat: int, email: str = "", email_on: bool = False):
    with session_factory()() as db:
        ch = channels.channel_of(db, uid(login), create=True)
        ch.telegram_chat_id, ch.telegram_name, ch.telegram_on = (chat or None), "Тест", True
        ch.email, ch.email_on = email, email_on
        db.commit()


def push(logins, title="Новое обращение", text="Стук в подвеске", url="/requests/5", **kw):
    with session_factory()() as db:
        n = notify.push(db, [uid(x) for x in logins], "request", title, text, url, **kw)
        db.commit()
    return n


def outbox(**where):
    with session_factory()() as db:
        return [r for r in db.scalars(select(Outbox).order_by(Outbox.id)) if all(getattr(r, k) == v for k, v in where.items())]


def deliver():
    with session_factory()() as db:
        return channels.deliver_pending(db)


def make_due():
    with session_factory()() as db:
        for r in db.scalars(select(Outbox).where(Outbox.state == "new")):
            r.next_try_at = utcnow() - dt.timedelta(seconds=1)
        db.commit()


def poll():
    with session_factory()() as db:
        return channels.poll_once(db)


def channel_row(login: str) -> NotifyChannel | None:
    with session_factory()() as db:
        return db.get(NotifyChannel, uid(login))


# ---------------------------------------------------------------- без ключей всё выключено

def test_everything_is_off_without_keys(make_client, world):
    link_chat("mech_a", MECH_CHAT, "m@example.ru", True)
    assert push(["mech_a"]) == 1                                           # на сайте уведомление есть
    assert outbox() == []                                                  # наружу ничего не поставлено
    assert deliver() == {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}
    c = make_client("mech_a")
    state = c.get("/me/channels").json()
    assert state["telegram"]["available"] is False and state["email"]["available"] is False
    assert c.post("/me/channels/telegram/code").status_code == 409
    assert c.post("/me/channels/test").status_code == 409
    assert channels.telegram_enabled() is False and channels.email_enabled() is False and channels.offsite_enabled() is False


# ---------------------------------------------------------------- привязка Telegram

def test_link_telegram_by_one_time_code(make_client, world, tg):
    c = make_client("mech_a")
    state = c.post("/me/channels/telegram/code").json()["telegram"]
    code = state["code"]
    assert len(code) == 8 and state["bot"] == "avtopark_test_bot" and state["link"] == f"https://t.me/avtopark_test_bot?start={code}"
    assert state["linked"] is False
    tg.say(MECH_CHAT, f"/start {code}", name="Пётр")
    assert poll() == 1
    assert "Готово" in tg.texts(MECH_CHAT)[0]
    after = c.get("/me/channels").json()["telegram"]
    assert after["linked"] is True and after["on"] is True and after["name"] == "Пётр" and after["code"] is None
    assert channel_row("mech_a").telegram_chat_id == MECH_CHAT
    # тот же код второй раз не работает
    tg.say(7777, f"/start {code}")
    poll()
    assert "не подошёл" in tg.texts(7777)[0]
    assert channel_row("mech_a").telegram_chat_id == MECH_CHAT


def test_code_can_be_sent_without_start_and_old_updates_are_confirmed(make_client, world, tg):
    code = make_client("mech_a").post("/me/channels/telegram/code").json()["telegram"]["code"]
    tg.say(MECH_CHAT, code.lower())                                        # просто код, без /start, в любом регистре
    poll()
    assert channel_row("mech_a").telegram_chat_id == MECH_CHAT
    before = len(tg.sent)
    poll()                                                                 # второй опрос подтверждает прочитанное и ничего не повторяет
    assert len(tg.sent) == before and tg.offsets[-1] > tg.offsets[0]


def test_bad_expired_and_foreign_codes_and_groups(make_client, world, tg):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    old = mech.post("/me/channels/telegram/code").json()["telegram"]["code"]
    with session_factory()() as db:
        db.get(NotifyChannel, uid("mech_a")).link_expires_at = utcnow() - dt.timedelta(minutes=1)
        db.commit()
    tg.say(MECH_CHAT, f"/start {old}")
    tg.say(MECH_CHAT, "/start ZZZZZZZZ")
    tg.say(MECH_CHAT, "привет")
    tg.say(5555, "/start AAAAAAAA", kind="group")                          # в группах бот молчит
    poll()
    texts = tg.texts(MECH_CHAT)
    assert "не подошёл или устарел" in texts[0] and "не подошёл или устарел" in texts[1] and "Это бот уведомлений" in texts[2]
    assert tg.texts(5555) == [] and channel_row("mech_a").telegram_chat_id is None
    # чат, уже занятый другим человеком, второму не отдаётся
    link_chat("dir_a", DIR_CHAT)
    code = mech.post("/me/channels/telegram/code").json()["telegram"]["code"]
    tg.say(DIR_CHAT, f"/start {code}")
    poll()
    assert "уже подключён к другому" in tg.texts(DIR_CHAT)[-1]
    assert channel_row("dir_a").telegram_chat_id == DIR_CHAT and channel_row("mech_a").telegram_chat_id is None
    assert boss.get("/me/channels").json()["telegram"]["linked"] is True


def test_stop_command_and_unlink_from_site(make_client, world, tg):
    link_chat("mech_a", MECH_CHAT)
    tg.say(MECH_CHAT, "/stop")
    tg.say(4242, "/stop")
    poll()
    assert "отключены" in tg.texts(MECH_CHAT)[0] and "не был подключён" in tg.texts(4242)[0]
    assert channel_row("mech_a").telegram_chat_id is None
    link_chat("mech_a", MECH_CHAT)
    c = make_client("mech_a")
    assert c.call("DELETE", "/me/channels/telegram").json()["telegram"]["linked"] is False
    assert channel_row("mech_a").telegram_chat_id is None


def test_deactivated_user_cannot_link(make_client, world, tg):
    code = make_client("mech_a").post("/me/channels/telegram/code").json()["telegram"]["code"]
    with session_factory()() as db:
        db.get(User, uid("mech_a")).is_active = False
        db.commit()
    tg.say(MECH_CHAT, f"/start {code}")
    poll()
    assert "отключён" in tg.texts(MECH_CHAT)[0] and channel_row("mech_a").telegram_chat_id is None


# ---------------------------------------------------------------- доставка

def test_notification_goes_to_telegram_and_email_with_link(make_client, world, tg, smtp):
    link_chat("mech_a", MECH_CHAT, "mech@example.ru", True)
    link_chat("dir_a", DIR_CHAT)                                           # у директора только Telegram
    assert push(["mech_a", "dir_a", "disp_a"]) == 3                        # у диспетчера ничего не подключено
    assert sorted((r.channel, r.address) for r in outbox()) == [("email", "mech@example.ru"), ("telegram", str(MECH_CHAT)), ("telegram", str(DIR_CHAT))]
    assert deliver() == {"sent": 3, "failed": 0, "retry": 0, "skipped": 0}
    assert tg.texts(MECH_CHAT) == ["Новое обращение\nСтук в подвеске\nhttps://erp.example.ru/requests/5"]
    assert len(tg.texts(DIR_CHAT)) == 1
    mail = smtp.messages[0]
    assert mail["to"] == ["mech@example.ru"] and mail["from"] == "erp@example.ru"
    assert mail["msg"]["Subject"] == "[Автопарк] Новое обращение"
    body = mail["msg"].get_content()
    assert "Стук в подвеске" in body and "Открыть: https://erp.example.ru/requests/5" in body
    assert all(r.state == "sent" and r.sent_at for r in outbox())
    assert deliver() == {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}  # второй раз не отправляет


def test_no_link_without_public_url_and_actor_gets_nothing(make_client, world, tg, monkeypatch):
    monkeypatch.setattr(get_settings(), "public_url", "")
    link_chat("mech_a", MECH_CHAT)
    link_chat("dir_a", DIR_CHAT)
    push(["mech_a", "dir_a"], actor_id=uid("dir_a"))                       # автор действия своё уведомление не получает
    deliver()
    assert tg.texts(MECH_CHAT) == ["Новое обращение\nСтук в подвеске"] and tg.texts(DIR_CHAT) == []


def test_parts_of_a_digest_do_not_go_out_only_the_digest(make_client, world, tg):
    link_chat("mech_a", MECH_CHAT)
    with session_factory()() as db:
        notify.push(db, [uid("mech_a")], "reminder", "Срок: страховка", "А001АА54", "/reminders", key="a", read=True)
        notify.push(db, [uid("mech_a")], "reminder", "Срок: тахограф", "А002АА54", "/reminders", key="b", read=True)
        notify.push(db, [uid("mech_a")], "reminder", "Напоминания: новых 2", "Наступило: 0, скоро: 2", "/reminders", key="d")
        db.commit()
    deliver()
    assert [t.split("\n")[0] for t in tg.texts(MECH_CHAT)] == ["Напоминания: новых 2"]


def test_user_switched_off_or_deactivated_gets_nothing(make_client, world, tg, smtp):
    link_chat("mech_a", MECH_CHAT, "mech@example.ru", True)
    c = make_client("mech_a")
    assert c.put("/me/channels", {"telegram_on": False, "email_on": False}).status_code == 200
    push(["mech_a"])
    assert outbox() == []
    c.put("/me/channels", {"telegram_on": True, "email_on": True})
    push(["mech_a"])
    assert len(outbox()) == 2
    with session_factory()() as db:                                        # человека отключили, пока сообщения ждали
        db.get(User, uid("mech_a")).is_active = False
        db.commit()
    assert deliver()["skipped"] == 2 and tg.sent == [] and smtp.messages == []


def test_failed_sending_is_retried_with_pause_then_succeeds(make_client, world, tg):
    link_chat("mech_a", MECH_CHAT)
    push(["mech_a"])
    tg.fail_times = 2
    assert deliver()["retry"] == 1
    row = outbox()[0]
    assert row.state == "new" and row.attempts == 1 and "Bad Gateway" in row.last_error and row.next_try_at > utcnow()
    assert deliver() == {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}  # пауза ещё не прошла
    make_due()
    assert deliver()["retry"] == 1 and outbox()[0].attempts == 2
    make_due()
    assert deliver()["sent"] == 1
    row = outbox()[0]
    assert row.state == "sent" and row.attempts == 3 and row.last_error == ""
    assert len(tg.texts(MECH_CHAT)) == 1                                   # сообщение пришло один раз


def test_gives_up_after_max_attempts(make_client, world, tg):
    link_chat("mech_a", MECH_CHAT)
    push(["mech_a"])
    tg.fail_times = 99
    for _ in range(5):
        make_due()
        deliver()
    row = outbox()[0]
    assert row.state == "failed" and row.attempts == 5
    assert channel_row("mech_a").telegram_on is True                       # временные сбои канал не выключают


def test_blocked_bot_is_permanent_and_switches_channel_off(make_client, world, tg):
    link_chat("mech_a", MECH_CHAT)
    link_chat("dir_a", DIR_CHAT)
    tg.fail[str(MECH_CHAT)] = (403, "Forbidden: bot was blocked by the user")
    push(["mech_a", "dir_a"])
    result = deliver()
    assert result["failed"] == 1 and result["sent"] == 1                   # чужой сбой не мешает остальным
    row = outbox(user_id=uid("mech_a"))[0]
    assert row.state == "failed" and row.attempts == 1 and "blocked" in row.last_error
    ch = channel_row("mech_a")
    assert ch.telegram_on is False and ch.telegram_chat_id == MECH_CHAT
    push(["mech_a"])
    assert len(outbox(user_id=uid("mech_a"))) == 1                         # новых сообщений в закрытый чат не ставится


def test_email_errors_permanent_temporary_and_login(make_client, world, smtp, monkeypatch):
    link_chat("mech_a", 0, "good@example.ru", True)
    link_chat("dir_a", 0, "gone@example.ru", True)
    smtp.reject.add("gone@example.ru")
    push(["mech_a", "dir_a"])
    smtp.defer_times = 1                                                   # первое письмо сервер просит повторить позже
    result = deliver()
    assert result["failed"] == 1                                           # несуществующий адрес: сразу «не доставлено»
    gone = outbox(user_id=uid("dir_a"))[0]
    assert gone.state == "failed" and "адрес отвергнут" in gone.last_error
    assert outbox(user_id=uid("mech_a"))[0].state in ("new", "sent")
    make_due()
    deliver()
    assert outbox(user_id=uid("mech_a"))[0].state == "sent" and [m["to"] for m in smtp.messages] == [["good@example.ru"]]


def test_smtp_login_is_used_and_wrong_password_is_not_leaked(make_client, world, monkeypatch):
    fake = FakeSMTP(user="robot@example.ru", password="s3cret-pass-word")
    s = get_settings()
    try:
        for key, value in (("smtp_host", "127.0.0.1"), ("smtp_port", fake.port), ("smtp_security", "none"), ("smtp_from", "robot@example.ru"),
                           ("smtp_user", "robot@example.ru"), ("smtp_password", "wrong-password-xyz")):
            monkeypatch.setattr(s, key, value)
        link_chat("mech_a", 0, "good@example.ru", True)
        push(["mech_a"])
        deliver()
        row = outbox()[0]
        assert row.state == "new" and "логин или пароль" in row.last_error and "wrong-password-xyz" not in row.last_error
        monkeypatch.setattr(s, "smtp_password", "s3cret-pass-word")
        make_due()
        assert deliver()["sent"] == 1 and fake.auth_seen[-1] == "robot@example.ru" and len(fake.messages) == 1
    finally:
        fake.stop()


def test_unreachable_servers_do_not_break_the_action_and_hide_secrets(make_client, world, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "telegram_bot_token", TOKEN)
    monkeypatch.setattr(s, "telegram_api", "http://127.0.0.1:9")           # там никого нет
    monkeypatch.setattr(s, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(s, "smtp_port", 9)
    monkeypatch.setattr(s, "smtp_security", "none")
    monkeypatch.setattr(s, "smtp_from", "erp@example.ru")
    monkeypatch.setattr(s, "smtp_password", "pass-1234-secret")
    link_chat("mech_a", MECH_CHAT, "m@example.ru", True)
    r = make_client("drv1").post("/requests", {"vehicle_id": world.va1, "title": "Течёт масло"})
    assert r.status_code == 201 or r.status_code == 200
    with session_factory()() as db:
        assert db.scalar(select(Notification.id).where(Notification.user_id == uid("mech_a"))) is not None   # на сайте уведомление есть
    result = deliver()
    assert result["retry"] == 2
    for row in outbox():
        assert row.state == "new" and row.last_error and TOKEN not in row.last_error and "pass-1234-secret" not in row.last_error


def test_channel_turned_off_in_settings_skips_waiting_messages(make_client, world, tg, monkeypatch):
    link_chat("mech_a", MECH_CHAT)
    push(["mech_a"])
    monkeypatch.setattr(get_settings(), "telegram_bot_token", "")
    assert deliver()["skipped"] == 1 and outbox()[0].state == "skipped"


def test_real_flow_new_request_reaches_telegram_of_those_who_run_requests(make_client, world, tg):
    link_chat("mech_a", MECH_CHAT)
    link_chat("disp_a", DIR_CHAT)
    link_chat("drv1", 9100)                                                # автор обращения о себе не получает
    r = make_client("drv1").post("/requests", {"vehicle_id": world.va1, "title": "Стук в подвеске", "priority": "urgent"})
    assert r.status_code in (200, 201), r.text
    assert channels.run_delivery() is not None
    first = tg.texts(MECH_CHAT)[0]
    assert first.startswith("Срочное обращение") and "А001АА54" in first and "https://erp.example.ru/requests/" in first
    assert tg.texts(9100) == []


def test_worker_pass_removes_old_sent_messages(make_client, world, tg):
    link_chat("mech_a", MECH_CHAT)
    push(["mech_a"])
    deliver()
    with session_factory()() as db:
        db.execute(Outbox.__table__.update().values(created_at=utcnow() - dt.timedelta(days=30)))
        db.commit()
    channels.run_delivery()
    assert outbox() == []


# ---------------------------------------------------------------- личные настройки и страница администратора

def test_my_settings_validation_and_privacy(make_client, world, tg, smtp):
    c = make_client("mech_a")
    other = make_client("dir_a")
    r = c.put("/me/channels", {"email": "не-адрес"})
    assert r.status_code == 422 and r.json()["field"] == "email"
    assert c.put("/me/channels", {"email": "a@b.ru\nBcc: x@y.ru"}).status_code == 422
    assert c.put("/me/channels", {"email_on": True}).status_code == 422     # включить почту без адреса нельзя
    ok = c.put("/me/channels", {"email": " mech@example.ru ", "email_on": True}).json()
    assert ok["email"] == {"available": True, "address": "mech@example.ru", "on": True}
    assert other.get("/me/channels").json()["email"]["address"] == ""      # чужие настройки не видны
    cleared = c.put("/me/channels", {"email": ""}).json()
    assert cleared["email"]["on"] is False and cleared["email"]["address"] == ""
    with session_factory()() as db:
        logged = [a for a in db.scalars(select(AuditLog).where(AuditLog.action.like("channels.%")))]
    assert logged and all("mech@example.ru" not in str(a.details) for a in logged)
    assert make_client().get("/me/channels").status_code == 401


def test_test_message_reports_each_channel(make_client, world, tg, smtp):
    c = make_client("mech_a")
    assert c.post("/me/channels/test").json()["result"] == {"telegram": {"ok": False, "error": "Telegram не подключён"},
                                                           "email": {"ok": False, "error": "Адрес почты не указан"}}
    link_chat("mech_a", MECH_CHAT, "mech@example.ru", True)
    assert c.post("/me/channels/test").json()["result"] == {"telegram": {"ok": True}, "email": {"ok": True}}
    assert "Проверка связи" in tg.texts(MECH_CHAT)[0] and smtp.messages[0]["msg"]["Subject"] == "[Автопарк] Проверка связи"
    tg.fail["sendMessage"] = (403, "Forbidden: bot was blocked by the user")
    assert c.post("/me/channels/test").json()["result"]["telegram"]["ok"] is False


def test_status_page_is_for_admins_and_has_no_secrets(make_client, world, tg, smtp, monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_password", "pass-1234-secret")
    link_chat("mech_a", MECH_CHAT, "mech@example.ru", True)
    tg.fail[str(MECH_CHAT)] = (403, "Forbidden: bot was blocked by the user")
    push(["mech_a"])
    deliver()
    assert make_client("mech_a").get("/channels/status").status_code == 403
    r = make_client("admin").get("/channels/status")
    assert r.status_code == 200
    data = r.json()
    assert data["telegram"] == {"enabled": True, "bot": "avtopark_test_bot", "linked_users": 1}
    assert data["email"]["enabled"] is True and data["email"]["sender"] == "erp@example.ru" and data["email"]["users"] == 1
    assert data["counts"]["telegram"]["failed"] == 1 and data["failed"][0]["error"].endswith("blocked by the user")
    assert TOKEN not in r.text and "pass-1234-secret" not in r.text


# ---------------------------------------------------------------- копия вне сервера

def test_encryption_roundtrip_and_wrong_password(tmp_path):
    data = b"PGDMP" + bytes(range(256)) * 50
    blob = offsite.encrypt(data, "пароль-для-копий")
    assert blob.startswith(offsite.MAGIC) and data not in blob and offsite.encrypt(data, "пароль-для-копий") != blob
    assert offsite.decrypt(blob, "пароль-для-копий") == data
    with pytest.raises(offsite.WrongPassword):
        offsite.decrypt(blob, "другой")
    with pytest.raises(offsite.WrongPassword):
        offsite.decrypt(blob[:-1] + bytes([blob[-1] ^ 1]), "пароль-для-копий")      # файл повреждён
    with pytest.raises(ValueError):
        offsite.decrypt(b"not ours at all, not ours at all, not ours at all", "x")
    src, dst = tmp_path / "a.dump.enc", tmp_path / "a.dump"
    src.write_bytes(blob)
    from app.cli import main
    assert main(["decrypt-backup", "--in", str(src), "--out", str(dst), "--password", "пароль-для-копий"]) == 0 and dst.read_bytes() == data
    assert main(["decrypt-backup", "--in", str(src), "--out", str(dst) + "2", "--password", "нет"]) == 1


def make_backup(world) -> int:
    with session_factory()() as db:
        job = backup.create_job(db, None, "auto")
    backup.run_job(job.id, verify=False)
    return job.id


def job_of(bid: int) -> BackupJob:
    with session_factory()() as db:
        return db.get(BackupJob, bid)


def offsite_on(monkeypatch, mails: str = "boss@example.ru"):
    s = get_settings()
    monkeypatch.setattr(s, "offsite_password", "parol-dlya-kopiy")
    monkeypatch.setattr(s, "offsite_email_to", mails)


def test_without_password_nothing_leaves_the_server(make_client, world, tg, smtp):
    link_chat("admin", ADMIN_CHAT)
    bid = make_backup(world)
    assert job_of(bid).status == "ok" and job_of(bid).offsite == {}
    assert tg.sent == [] and smtp.messages == []
    admin = make_client("admin")
    r = admin.post(f"/backups/{bid}/send-offsite")
    assert r.status_code == 409 and r.json()["code"] == "offsite_off"
    assert admin.get("/backups").json()["settings"]["offsite"]["enabled"] is False


def test_backup_goes_out_encrypted_to_telegram_and_mail(make_client, world, tg, smtp, monkeypatch):
    offsite_on(monkeypatch, "boss@example.ru, не-адрес, owner@example.ru")
    link_chat("admin", ADMIN_CHAT)
    link_chat("mech_a", MECH_CHAT)                                         # у механика нет права на копии: ему файл не уходит
    bid = make_backup(world)
    info = job_of(bid).offsite
    assert info["encrypted"] is True and info["telegram"]["ok"] and info["email"]["ok"] and info["telegram"]["sent"] == 1 and info["email"]["sent"] == 2
    docs = [m for m in tg.sent if "file" in m]
    assert len(docs) == 1 and docs[0]["chat_id"] == str(ADMIN_CHAT) and docs[0]["file"].endswith(".dump.enc")
    plain = backup_file_bytes(bid)
    assert plain not in docs[0]["bytes"] and offsite.decrypt(docs[0]["bytes"], "parol-dlya-kopiy") == plain and plain.startswith(b"PGDMP")
    mail = smtp.messages[0]
    assert mail["to"] == ["boss@example.ru", "owner@example.ru"]
    attached = list(mail["msg"].iter_attachments())
    assert len(attached) == 1 and offsite.decrypt(attached[0].get_content(), "parol-dlya-kopiy") == plain
    assert tg.texts(MECH_CHAT) == []
    with session_factory()() as db:
        assert "backup.offsite" in [a.action for a in db.scalars(select(AuditLog))]
    assert make_client("admin").get("/backups").json()["items"][0]["offsite"]["telegram"]["ok"] is True


def backup_file_bytes(bid: int) -> bytes:
    with session_factory()() as db:
        return backup.file_path(db.get(BackupJob, bid)).read_bytes()


def test_explicit_chat_id_overrides_users(make_client, world, tg, monkeypatch):
    offsite_on(monkeypatch, "")
    monkeypatch.setattr(get_settings(), "offsite_telegram_chat_id", "-100500")
    link_chat("admin", ADMIN_CHAT)
    bid = make_backup(world)
    assert [m["chat_id"] for m in tg.sent if "file" in m] == ["-100500"] and job_of(bid).offsite["telegram"]["ok"]


def test_too_big_or_failing_sending_is_recorded_and_told_to_admins(make_client, world, tg, smtp, monkeypatch):
    offsite_on(monkeypatch)
    link_chat("admin", ADMIN_CHAT)
    monkeypatch.setattr(offsite, "TELEGRAM_MAX_BYTES", 10)
    monkeypatch.setattr(get_settings(), "offsite_email_max_mb", 0)
    bid = make_backup(world)
    job = job_of(bid)
    assert job.status == "ok"                                              # сама копия сделана, сбой отправки её не отменяет
    assert job.offsite["telegram"]["ok"] is False and "больше предела Telegram" in job.offsite["telegram"]["error"]
    assert job.offsite["email"]["ok"] is False and "больше предела" in job.offsite["email"]["error"]
    assert not any("file" in m for m in tg.sent) and smtp.messages == []
    with session_factory()() as db:
        titles = [n.title for n in db.scalars(select(Notification).where(Notification.user_id == uid("admin")))]
    assert "Копию не удалось отправить вне сервера" in titles


def test_offsite_failure_never_breaks_the_backup(make_client, world, tg, monkeypatch):
    offsite_on(monkeypatch, "")
    link_chat("admin", ADMIN_CHAT)
    tg.fail["sendDocument"] = (400, "Bad Request: chat not found")
    bid = make_backup(world)
    job = job_of(bid)
    assert job.status == "ok" and job.offsite["telegram"]["ok"] is False and "chat not found" in job.offsite["telegram"]["error"]


def test_manual_send_endpoint_and_rights(make_client, world, tg, smtp, monkeypatch):
    bid = make_backup(world)
    admin = make_client("admin")
    offsite_on(monkeypatch, "")
    r = admin.post(f"/backups/{bid}/send-offsite")
    assert r.status_code == 409 and r.json()["code"] == "offsite_no_target"
    link_chat("admin", ADMIN_CHAT)
    assert make_client("dir_a").post(f"/backups/{bid}/send-offsite").status_code == 403
    assert admin.post(f"/backups/{bid}/send-offsite").status_code == 202
    assert job_of(bid).offsite["telegram"]["ok"] is True and len([m for m in tg.sent if "file" in m]) == 1
    assert admin.post("/backups/99999/send-offsite").status_code == 404
