# -*- coding: utf-8 -*-
"""Колокольчик и объявления: кому приходят уведомления, чужого не видно, переписка по объявлению, архив, обход напоминаний."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import AnnouncementTarget, AuditLog, Employee, EmploymentAssignment, Notification, User
from app.services import notify
from tests.test_events_calendar import d, event
from tests.test_purchases import go, ids_of, part
from app.common import org_today

TODAY = org_today()


def uid(login: str) -> int:
    with session_factory()() as db:
        return db.scalar(select(User.id).where(User.login == login))


def inbox(login: str, **where):
    with session_factory()() as db:
        q = select(Notification).where(Notification.user_id == uid(login)).order_by(Notification.id)
        return [n for n in db.scalars(q) if all(getattr(n, k) == v for k, v in where.items())]


def titles(login: str, **where):
    return [n.title for n in inbox(login, **where)]


def link_employee(world, login: str, driver: bool = False, entity: int | None = None, last: str = "Сотрудник") -> int:
    """Привязывает к пользователю карточку сотрудника в юрлице: так он становится возможным адресатом объявления."""
    with session_factory()() as db:
        e = Employee(last_name=last, first_name="Тест", is_driver=driver, position="водитель" if driver else "сотрудник", user_id=uid(login))
        db.add(e)
        db.flush()
        db.add(EmploymentAssignment(employee_id=e.id, legal_entity_id=entity or world.A, started_on=TODAY - dt.timedelta(days=100)))
        db.commit()
        return e.id


def announce(c, world, **kw):
    body = {"legal_entity_id": world.A, "title": "Вывоз снега в субботу", "body": "Все выходят на смену к восьми.", "audience": "drivers", "user_ids": []}
    body.update(kw)
    return c.post("/announcements", body)


def audit_actions():
    with session_factory()() as db:
        return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]


# ---------------------------------------------------------------- колокольчик

def test_bell_is_private_and_can_be_read(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    with session_factory()() as db:
        for i in range(3):
            db.add(Notification(user_id=uid("mech_a"), kind="info", title=f"Для механика {i}"))
        db.add(Notification(user_id=uid("dir_a"), kind="info", title="Для директора"))
        db.commit()
    assert mech.get("/notifications/summary").json() == {"unread": 3, "news_unread": 0}
    data = mech.get("/notifications").json()
    assert data["total"] == 3 and data["unread"] == 3
    assert [n["title"] for n in data["items"]] == ["Для механика 2", "Для механика 1", "Для механика 0"]            # новые сверху
    assert all(n["kind_label"] == "Сообщение" and n["read"] is False for n in data["items"])
    # чужое уведомление «не существует»
    foreign = inbox("dir_a")[0].id
    assert mech.post(f"/notifications/{foreign}/read").status_code == 404
    assert boss.get("/notifications/summary").json()["unread"] == 1
    # прочитать одно, потом всё
    first = data["items"][0]["id"]
    assert mech.post(f"/notifications/{first}/read").json() == {"unread": 2}
    assert mech.post(f"/notifications/{first}/read").json() == {"unread": 2}                                         # повтор не ломает
    assert [n["title"] for n in mech.get("/notifications", params={"unread": "true"}).json()["items"]] == ["Для механика 1", "Для механика 0"]
    assert mech.post("/notifications/read-all").json() == {"marked": 2, "unread": 0}
    assert mech.get("/notifications/summary").json()["unread"] == 0 and boss.get("/notifications/summary").json()["unread"] == 1
    # страницы
    assert len(mech.get("/notifications", params={"page_size": 2}).json()["items"]) == 2
    assert make_client().get("/notifications").status_code == 401


def test_push_skips_actor_dedupes_by_key_and_never_raises(world):
    with session_factory()() as db:
        a, b = uid("dir_a"), uid("mech_a")
        assert notify.push(db, [a, b, None], "request", "Привет", "текст", "/x", world.A, actor_id=a) == 1          # автор действия не получает
        assert notify.push(db, [b], "request", "Ключ", key="k1") == 1
        assert notify.push(db, [b], "request", "Ключ", key="k1") == 0                                              # тот же ключ — второго нет
        assert notify.push(db, [b], "странный-вид", "Без вида") == 1
        db.commit()
        kinds = {n.title: n.kind for n in db.scalars(select(Notification))}
        assert kinds["Без вида"] == "info"
        assert notify.push(db, [999999], "info", "Несуществующий пользователь") == 0                              # ошибка записи не прерывает
        assert notify.short("а" * 300, 20) == "а" * 19 + "…" and notify.short("  два   слова ") == "два слова"


# ---------------------------------------------------------------- события обращений

def test_new_request_goes_to_those_who_run_requests_not_to_author(make_client, world):
    drv = make_client("drv1")
    r = drv.post("/requests", {"vehicle_id": world.va1, "title": "Стук в подвеске", "priority": "urgent"})
    assert r.status_code == 201
    req = r.json()
    for login in ("dir_a", "mech_a", "admin"):
        got = inbox(login, kind="request")
        assert len(got) == 1, login
        assert got[0].title.startswith(f"Срочное обращение № {req['number']}") and got[0].url == f"/requests/{req['id']}"
        assert got[0].legal_entity_id == world.A and got[0].read_at is None
    for login in ("drv1", "drv2", "dir_b", "acc_a", "emp_a", "store_a", "disp_a"):        # автор, другие водители, другое юрлицо, не ведущие обращения
        assert inbox(login) == [], login


def test_request_comment_status_assign_and_parts_notifications(make_client, world):
    drv, mech, boss = make_client("drv1"), make_client("mech_a"), make_client("dir_a")
    req = drv.post("/requests", {"vehicle_id": world.va1, "title": "Течёт масло", "parts": [part("Сальник"), part("Прокладка")]}).json()
    rid = req["id"]
    # механик пишет — автор обращения получает, сам механик нет
    assert mech.post(f"/requests/{rid}/comments", {"text": "Посмотрим сегодня"}).status_code == 201
    got = inbox("drv1", kind="reply")
    assert len(got) == 1 and got[0].title.startswith("Сообщение в обращении") and got[0].text == "Посмотрим сегодня"
    assert inbox("mech_a", kind="reply") == []
    # автор отвечает — сообщают тем, кто ведёт обращения (кроме него самого)
    assert drv.post(f"/requests/{rid}/comments", {"text": "Спасибо"}).status_code == 201
    assert len(inbox("mech_a", kind="reply")) == 1 and len(inbox("dir_a", kind="reply")) == 1 and len(inbox("drv1", kind="reply")) == 1
    # исполнитель
    assert boss.patch(f"/requests/{rid}", {"assignee_id": uid("mech_a")}).status_code == 200
    assert any(t.startswith("Вам назначено обращение") for t in titles("mech_a"))
    assert not any(t.startswith("Вам назначено") for t in titles("drv1"))
    # переходы: автору и исполнителю; согласование — тем, кто согласует
    assert go(mech, rid, "diagnostics").status_code == 200
    assert any("диагностика" in t for t in titles("drv1", kind="request"))
    assert go(mech, rid, "approval", "Нужна замена").status_code == 200
    assert any(t.startswith("Ремонт ждёт согласования") for t in titles("dir_a"))
    assert not any(t.startswith("Ремонт ждёт согласования") for t in titles("mech_a"))
    assert go(boss, rid, "in_progress").status_code == 200
    assert any("в работе" in t for t in titles("drv1")) and any("в работе" in t for t in titles("mech_a"))
    # куплено: автору и исполнителю, не тому, кто купил
    ids = ids_of(mech, rid)
    assert mech.post("/purchases/bought", {"ids": [ids["Сальник"]]}).status_code == 200
    bought = inbox("drv1", kind="parts")
    assert len(bought) == 1 and "Сальник" in bought[0].text
    assert inbox("mech_a", kind="parts") == []
    # чужое юрлицо ничего не знает об этом обращении
    assert inbox("dir_b") == [] and inbox("drv3") == []


# ---------------------------------------------------------------- наряды

def test_job_notifications_go_to_drivers_of_the_vehicle_and_to_dispatchers(make_client, world):
    disp, drv = make_client("disp_a"), make_client("drv1")
    r = disp.post("/jobs", {"job_date": TODAY.isoformat(), "vehicle_id": world.va1, "customer": "ООО Заказчик", "address": "ул. Ленина, 1",
                            "task": "Вывоз снега", "time_from": "08:30"})
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    got = inbox("drv1", kind="job")
    assert len(got) == 1 and got[0].title.startswith("Новое задание: ") and "08:30" in got[0].title and got[0].url == "/jobs/mine"
    assert inbox("drv2", kind="job") == [] and inbox("drv3", kind="job") == [] and inbox("disp_a", kind="job") == []   # чужие машины и сам диспетчер
    # изменение наряда
    assert disp.patch(f"/jobs/{jid}", {"address": "ул. Мира, 5"}).status_code == 200
    assert [n.title.split(":")[0] for n in inbox("drv1", kind="job")] == ["Новое задание", "Задание изменено"]
    # водитель приехал и закончил — сообщают тем, кто ведёт наряды, а не водителю
    assert drv.post(f"/jobs/{jid}/arrive").status_code == 200
    assert drv.post(f"/jobs/{jid}/arrive").status_code == 200                                               # повтор ничего не добавляет
    assert drv.post(f"/jobs/{jid}/finish", {"note": "Вывезли три рейса"}).status_code == 200
    for login in ("disp_a", "dir_a"):
        mine = titles(login, kind="job")
        assert len([t for t in mine if t.startswith("Водитель приехал")]) == 1 and len([t for t in mine if t.startswith("Работа закончена")]) == 1, login
    assert all("приехал" not in t and "закончена" not in t for t in titles("drv1", kind="job"))
    assert any("Вывезли три рейса" in n.text for n in inbox("disp_a", kind="job"))
    # отмена и возврат
    j2 = disp.post("/jobs", {"job_date": TODAY.isoformat(), "vehicle_id": world.va1, "address": "ул. Новая, 3", "task": "Мусор"}).json()["id"]
    assert disp.post(f"/jobs/{j2}/state", {"state": "cancelled"}).status_code == 200
    assert any(t.startswith("Задание отменено") for t in titles("drv1"))
    assert disp.post(f"/jobs/{j2}/state", {"state": "planned"}).status_code == 200
    assert any(t.startswith("Задание снова в работе") for t in titles("drv1"))


# ---------------------------------------------------------------- объявления: создание и права

def test_create_announcement_audiences_and_validation(make_client, world):
    boss, disp = make_client("dir_a"), make_client("disp_a")
    # пока водителей со входом двое (drv1, drv2); emp_a и mech_a не привязаны к карточкам сотрудников
    aud = boss.get("/announcements/audience", params={"legal_entity_id": world.A}).json()
    assert [(x["name"], x["is_driver"]) for x in aud["people"]] == [("Второй Тест", True), ("Первый Тест", True)]
    assert {x["value"] for x in aud["audiences"]} == {"drivers", "all", "pick"}
    assert boss.get("/announcements/audience", params={"legal_entity_id": world.B}).status_code == 403
    # всем водителям
    r = announce(boss, world)
    assert r.status_code == 201 and r.json()["recipients"] == 2
    aid = r.json()["id"]
    for login in ("drv1", "drv2"):
        got = inbox(login, kind="announce")
        assert len(got) == 1 and got[0].title == "Объявление: Вывоз снега в субботу" and got[0].url == f"/news/{aid}"
    assert inbox("drv3") == [] and inbox("dir_a", kind="announce") == []
    # всем сотрудникам со входом: механик и бухгалтер получают карточку и попадают в список
    link_employee(world, "mech_a", last="Механиков")
    link_employee(world, "acc_a", last="Бухов")
    link_employee(world, "dir_a", last="Директоров")                                                           # автор объявления себя не адресует
    r = announce(boss, world, audience="all", title="Собрание")
    assert r.status_code == 201 and r.json()["recipients"] == 4
    assert len(inbox("mech_a", kind="announce")) == 1 and len(inbox("acc_a", kind="announce")) == 1 and inbox("dir_a", kind="announce") == []
    # выбранным
    r = announce(disp, world, audience="pick", user_ids=[uid("mech_a")], title="Только вам")
    assert r.status_code == 201 and r.json()["recipients"] == 1
    assert [n.title for n in inbox("mech_a", kind="announce")][-1] == "Объявление: Только вам"
    # проверки
    assert announce(boss, world, title="  ").status_code == 422
    assert announce(boss, world, title="я" * 121).status_code == 422
    assert announce(boss, world, body="я" * 3001).status_code == 422
    assert announce(boss, world, audience="кому-то").status_code == 422
    assert announce(boss, world, audience="pick", user_ids=[]).json()["code"] == "no_recipients"
    assert announce(boss, world, audience="pick", user_ids=[uid("drv3")]).json()["code"] == "bad_recipient"          # из другого юрлица
    assert announce(boss, world, audience="pick", user_ids=[uid("emp_a")]).json()["code"] == "bad_recipient"          # нет карточки сотрудника
    assert announce(boss, world, audience="pick", user_ids=[uid("dir_a")]).json()["code"] == "bad_recipient"          # себе нельзя
    assert announce(boss, world, legal_entity_id=world.B).status_code == 403
    assert announce(boss, world, legal_entity_id=99999).status_code == 422
    # в юрлице без водителей со входом
    with session_factory()() as db:
        db.query(Employee).filter(Employee.id == world.d3).update({"user_id": None})
        db.commit()
    assert announce(make_client("dir_b"), world, legal_entity_id=world.B).json()["code"] == "no_recipients"
    assert audit_actions().count("announcement.create") == 3


def test_announcement_rights_by_role(make_client, world):
    for login in ("mech_a", "drv1", "acc_a", "emp_a", "store_a"):
        c = make_client(login)
        assert announce(c, world).status_code == 403, login
        assert c.get("/announcements").status_code == 403, login
        assert c.get("/announcements/audience", params={"legal_entity_id": world.A}).status_code == 403, login
    boss, disp = make_client("dir_a"), make_client("disp_a")
    aid = announce(disp, world).json()["id"]
    assert disp.get("/announcements").json()["can_create"] is True
    items = boss.get("/announcements").json()["items"]
    assert items[0]["can_edit"] is True and items[0]["can_void"] is True
    row = disp.get("/announcements").json()["items"][0]
    assert row["can_edit"] is True and row["can_void"] is False                                                   # диспетчер архивирует не может
    assert disp.post(f"/announcements/{aid}/void", {"reason": "Устарело"}).status_code == 403
    # другое юрлицо объявления не видит совсем
    other = make_client("dir_b")
    assert other.get("/announcements").json()["items"] == []
    assert other.get(f"/announcements/{aid}").status_code == 404
    assert other.post(f"/announcements/{aid}/close").status_code == 404
    assert other.post(f"/announcements/{aid}/answer/{uid('drv1')}", {"text": "привет"}).status_code == 404


# ---------------------------------------------------------------- объявления: чтение и переписка

def test_driver_reads_replies_and_staff_answers(make_client, world):
    boss, disp, drv1, drv2 = make_client("dir_a"), make_client("disp_a"), make_client("drv1"), make_client("drv2")
    aid = announce(boss, world).json()["id"]
    assert drv1.get("/notifications/summary").json() == {"unread": 1, "news_unread": 1}
    news = drv1.get("/news").json()["items"]
    assert len(news) == 1 and news[0]["read"] is False and news[0]["preview"].startswith("Все выходят") and news[0]["author"] == "Dir_a"
    # открыл — прочитано и в списке, и в колокольчике
    one = drv1.get(f"/news/{aid}").json()
    assert one["title"] == "Вывоз снега в субботу" and one["thread"] == []
    assert drv1.get("/notifications/summary").json() == {"unread": 0, "news_unread": 0}
    assert drv2.get("/notifications/summary").json() == {"unread": 1, "news_unread": 1}                     # у второго по-прежнему новое
    # ответ водителя: сотрудники с правом ответа получают, водитель нет
    assert drv1.post(f"/news/{aid}/reply", {"text": " "}).status_code == 422
    assert drv1.post(f"/news/{aid}/reply", {"text": "я" * 1001}).status_code == 422
    assert drv1.post(f"/news/{aid}/reply", {"text": "Буду, но опоздаю на час"}).status_code == 201
    for login in ("dir_a", "disp_a", "admin"):
        got = inbox(login, kind="reply")
        assert len(got) == 1 and got[0].title.startswith("Первый Тест ответил на объявление") and got[0].url == f"/announcements/{aid}" and got[0].text == "Буду, но опоздаю на час", login
    assert inbox("drv1", kind="reply") == [] and inbox("mech_a", kind="reply") == []
    # у сотрудника: кто прочитал, кто ответил, кто ждёт ответа
    card = disp.get(f"/announcements/{aid}").json()
    assert card["audience_label"] == "Всем водителям" and len(card["people"]) == 2
    first = next(p for p in card["people"] if p["user_id"] == uid("drv1"))
    second = next(p for p in card["people"] if p["user_id"] == uid("drv2"))
    assert first["read_at"] and first["waiting"] is True and [m["text"] for m in first["thread"]] == ["Буду, но опоздаю на час"]
    assert second["read_at"] is None and second["waiting"] is False and second["thread"] == []
    assert card["people"][0]["user_id"] == uid("drv1")                                                      # ждущие ответа — первыми
    assert first["name"] == "Первый Тест" and second["name"] == "Второй Тест"
    # уведомление об ответе у открывшего карточку становится прочитанным
    assert disp.get("/notifications/summary").json()["unread"] == 0
    listing = disp.get("/announcements").json()["items"][0]
    assert (listing["total"], listing["seen"], listing["answers"], listing["waiting"]) == (2, 1, 1, 1)
    # ответ сотрудника — только в переписку этого водителя
    assert disp.post(f"/announcements/{aid}/answer/{uid('drv1')}", {"text": "Хорошо, предупредите диспетчера"}).status_code == 201
    assert disp.post(f"/announcements/{aid}/answer/{uid('drv1')}", {"text": "  "}).status_code == 422
    assert disp.post(f"/announcements/{aid}/answer/{uid('drv3')}", {"text": "чужому"}).status_code == 404
    mine = drv1.get(f"/news/{aid}").json()["thread"]
    assert [(m["from_staff"], m["text"]) for m in mine] == [(False, "Буду, но опоздаю на час"), (True, "Хорошо, предупредите диспетчера")]
    assert drv2.get(f"/news/{aid}").json()["thread"] == []                                                  # чужую переписку не видно
    assert any(n.title.startswith("Ответ по объявлению") and n.url == f"/news/{aid}" for n in inbox("drv1", kind="reply"))
    assert inbox("drv2", kind="reply") == []
    assert all(p["waiting"] is False for p in disp.get(f"/announcements/{aid}").json()["people"])
    after = disp.get("/announcements").json()["items"][0]
    assert after["waiting"] == 0 and after["answers"] == 1
    acts = audit_actions()
    assert acts.count("announcement.reply") == 1 and acts.count("announcement.answer") == 1


def test_news_are_only_for_addressees_and_hidden_when_closed_or_archived(make_client, world):
    boss, disp = make_client("dir_a"), make_client("disp_a")
    drv1, drv2, drv3 = make_client("drv1"), make_client("drv2"), make_client("drv3")
    aid = announce(boss, world, audience="pick", user_ids=[uid("drv1")]).json()["id"]
    assert drv2.get(f"/news/{aid}").status_code == 404 and drv3.get(f"/news/{aid}").status_code == 404
    assert drv2.post(f"/news/{aid}/reply", {"text": "я тоже"}).status_code == 404
    assert drv2.get("/news").json()["items"] == []
    assert len(drv1.get("/news").json()["items"]) == 1
    # закрытие убирает у адресата, переписка остаётся у сотрудников
    drv1.post(f"/news/{aid}/reply", {"text": "Принял"})
    assert disp.post(f"/announcements/{aid}/close").json() == {"closed": True}
    assert drv1.get("/news").json()["items"] == [] and drv1.get(f"/news/{aid}").status_code == 404
    assert drv1.post(f"/news/{aid}/reply", {"text": "ещё"}).status_code == 404
    assert drv1.get("/notifications/summary").json()["news_unread"] == 0
    card = disp.get(f"/announcements/{aid}").json()
    assert card["closed"] is True and [m["text"] for m in card["people"][0]["thread"]] == ["Принял"]
    assert disp.post(f"/announcements/{aid}/close").json() == {"closed": True}                              # повтор ничего не меняет
    assert disp.post(f"/announcements/{aid}/reopen").json() == {"closed": False}
    assert len(drv1.get("/news").json()["items"]) == 1
    # закрытые идут в списке сотрудника после открытых
    second = announce(boss, world, title="Второе").json()["id"]
    disp.post(f"/announcements/{aid}/close")
    assert [i["id"] for i in boss.get("/announcements").json()["items"]] == [second, aid]
    # архив: нужна причина, нужно право, у адресатов пропадает, в списке только в «архиве»
    assert boss.post(f"/announcements/{second}/void", {"reason": "ab"}).status_code == 422
    assert boss.post(f"/announcements/{second}/void", {"reason": "Не актуально"}).json() == {"archived": True}
    assert boss.post(f"/announcements/{second}/void", {"reason": "Ещё раз"}).status_code == 409
    assert second not in [i["id"] for i in boss.get("/announcements").json()["items"]]
    arch = boss.get("/announcements", params={"archived": "true"}).json()["items"]
    assert [i["id"] for i in arch] == [second] and arch[0]["archived"] is True and arch[0]["archive_reason"] == "Не актуально"
    assert drv1.get(f"/news/{second}").status_code == 404 and drv2.get(f"/news/{second}").status_code == 404
    assert boss.post(f"/announcements/{second}/close").status_code == 409                                   # в архиве не закрывают
    assert boss.post(f"/announcements/{second}/answer/{uid('drv1')}", {"text": "x"}).status_code == 409
    assert boss.post(f"/announcements/{second}/restore").json() == {"archived": False}
    assert boss.post(f"/announcements/{second}/restore").status_code == 409
    assert len(drv1.get("/news").json()["items"]) == 1
    # поиск по заголовку
    assert [i["title"] for i in boss.get("/announcements", params={"q": "втор"}).json()["items"]] == ["Второе"]
    acts = audit_actions()
    for name in ("announcement.close", "announcement.reopen", "announcement.void", "announcement.restore"):
        assert name in acts, name


def test_news_for_user_who_lost_the_right_or_was_deactivated(make_client, world):
    boss = make_client("dir_a")
    aid = announce(boss, world, audience="pick", user_ids=[uid("drv1")]).json()["id"]
    drv = make_client("drv1")
    assert drv.get("/notifications/summary").json()["news_unread"] == 1
    with session_factory()() as db:                                                                        # право читать объявления отозвали
        from app.models import Permission, RolePermission
        pid = db.scalar(select(Permission.id).where(Permission.code == "announcements.read_own"))
        db.query(RolePermission).filter(RolePermission.permission_id == pid).delete()
        db.commit()
    drv2 = make_client("drv1")
    assert drv2.get("/news").status_code == 403 and drv2.get(f"/news/{aid}").status_code == 404
    assert drv2.get("/notifications/summary").json()["news_unread"] == 0


# ---------------------------------------------------------------- обход напоминаний

def test_sweep_creates_keyed_reminders_once_and_digest_when_many(make_client, world):
    boss = make_client("dir_a")
    assert event(boss, world, due_date=d(5), description="Медосмотр").status_code == 201
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.login == "dir_a"))
        assert notify.sweep_user(db, user) >= 1
        db.commit()
    mine = inbox("dir_a", kind="reminder")
    assert len(mine) == 1 and mine[0].key.startswith("rem:employee_event:") and mine[0].read_at is None and mine[0].url
    # повторный обход ничего не добавляет
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.login == "dir_a"))
        assert notify.sweep_user(db, user) == 0
        db.commit()
    assert len(inbox("dir_a", kind="reminder")) == 1
    # один новый срок — одно новое уведомление
    assert event(boss, world, employee_id=world.d2, due_date=d(-2), description="Инструктаж").status_code == 201
    with session_factory()() as db:
        assert notify.sweep_user(db, db.scalar(select(User).where(User.login == "dir_a"))) == 1
        db.commit()
    assert len(inbox("dir_a", kind="reminder")) == 2
    # много новых сразу — по каждому «прочитано», и одно общее непрочитанное
    for i in range(7):
        assert event(boss, world, kind="briefing", description=f"Срок {i}", due_date=d(1 + i)).status_code == 201
    with session_factory()() as db:
        made = notify.sweep_user(db, db.scalar(select(User).where(User.login == "dir_a")))
        db.commit()
    assert made == 7
    mine = inbox("dir_a", kind="reminder")
    digests = [n for n in mine if (n.key or "").startswith("remd:")]
    singles = [n for n in mine if (n.key or "").startswith("rem:")]
    assert len(digests) == 1 and digests[0].read_at is None and digests[0].title == "Напоминания: новых 7" and digests[0].url == "/reminders"
    assert len(singles) == 9 and sum(1 for n in singles if n.read_at is None) == 2                                # два прежних непрочитанных + 7 прочитанных тихо
    # у тех, у кого нет права на напоминания, обхода нет
    with session_factory()() as db:
        assert notify.sweep_user(db, db.scalar(select(User).where(User.login == "drv1"))) == 0
    assert inbox("drv1", kind="reminder") == []


def test_sweep_all_users_cleanup_and_lock(make_client, world):
    boss = make_client("dir_a")
    event(boss, world, due_date=d(3))
    with session_factory()() as db:
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=200)
        very_old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=500)
        mech = uid("mech_a")
        db.add_all([
            Notification(user_id=mech, kind="info", title="Прочитано давно", read_at=old, created_at=old),
            Notification(user_id=mech, kind="info", title="Не прочитано давно", created_at=old),
            Notification(user_id=mech, kind="info", title="Прочитано недавно", read_at=dt.datetime.now(dt.timezone.utc)),
            Notification(user_id=mech, kind="reminder", title="Старое напоминание", key="rem:old", created_at=very_old, read_at=very_old),
            Notification(user_id=mech, kind="reminder", title="Недавнее напоминание", key="rem:new", created_at=old, read_at=old),
        ])
        db.commit()
        result = notify.sweep(db)
    assert result["removed"] == 2 and result["created"] >= 1 and result["users"] >= 10
    left = {n.title for n in inbox("mech_a")}
    assert "Прочитано давно" not in left and "Старое напоминание" not in left
    assert {"Не прочитано давно", "Прочитано недавно", "Недавнее напоминание"} <= left
    assert any(n.kind == "reminder" for n in inbox("dir_a"))
    # блокировка: пока обход занят другим экземпляром, второй пропускается
    from sqlalchemy import text
    from app.db import engine
    with engine().connect() as holder:
        assert holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": notify.SWEEP_LOCK_KEY}).scalar()
        before = len(inbox("dir_a"))
        event(boss, world, employee_id=world.d2, due_date=d(4), description="Другой")
        notify.run_sweep()
        assert len(inbox("dir_a")) == before
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": notify.SWEEP_LOCK_KEY})
        holder.commit()
    notify.run_sweep()
    assert len(inbox("dir_a")) == before + 1


# ---------------------------------------------------------------- роли и журнал

def test_role_defaults_for_announcements(make_client, world):
    def perms(login):
        return set(make_client(login).me["permissions"])
    assert {"announcements.view", "announcements.create", "announcements.edit", "announcements.void", "announcements.read_own"} <= perms("dir_a")
    disp = perms("disp_a")
    assert {"announcements.view", "announcements.create", "announcements.edit", "announcements.read_own"} <= disp and "announcements.void" not in disp
    for login in ("mech_a", "drv1", "acc_a", "emp_a", "store_a"):
        p = perms(login)
        assert "announcements.read_own" in p and not ({"announcements.view", "announcements.create", "announcements.edit", "announcements.void"} & p), login
    assert {"announcements.view", "announcements.void", "announcements.read_own"} <= perms("admin")
    with session_factory()() as db:
        assert db.scalar(select(AnnouncementTarget).limit(1)) is None


def test_notification_failure_does_not_break_the_action(make_client, world, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("сломалось")
    monkeypatch.setattr(notify, "users_with_any", boom)
    # обращение создаётся, даже если подбор получателей упал: ошибку должны гасить внутри on_request_new
    try:
        r = make_client("drv1").post("/requests", {"vehicle_id": world.va1, "title": "Не заводится"})
        ok = r.status_code == 201
    except RuntimeError:
        ok = False
    assert ok, "создание обращения не должно зависеть от уведомлений"
