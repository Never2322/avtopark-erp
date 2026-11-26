# -*- coding: utf-8 -*-
"""Критерий: история статусов и изменений доступна. Обращения проходят строго по схеме, согласование — отдельное право."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import session_factory


def history(c, vid):
    return c.get(f"/vehicles/{vid}/status-history").json()["items"]


def test_status_history_keeps_who_when_why(make_client, world):
    c = make_client("mech_a")
    assert c.post(f"/vehicles/{world.va1}/status", {"status": "in_repair"}).status_code == 422          # причина обязательна
    r = c.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Замена сцепления"})
    assert r.status_code == 200 and r.json()["status"] == "in_repair"
    assert c.post(f"/vehicles/{world.va1}/status", {"status": "in_work"}).status_code == 200
    items = history(c, world.va1)
    assert [i["status"] for i in items][:2] == ["in_work", "in_repair"]
    repair = items[1]
    assert repair["reason"] == "Замена сцепления" and repair["by"] == "Mech_a" and repair["from_status"] == "in_work"


def test_same_status_does_not_duplicate_history(make_client, world):
    c = make_client("mech_a")
    n = len(history(c, world.va1))
    r = c.post(f"/vehicles/{world.va1}/status", {"status": "in_work"})
    assert r.status_code == 200 and r.json()["changed"] is False
    assert len(history(c, world.va1)) == n


def test_only_two_statuses_and_archived_vehicle_is_final(make_client, world):
    c = make_client("dir_a")
    assert c.post(f"/vehicles/{world.va3}/status", {"status": "flying"}).status_code == 422
    for old in ("written_off", "parked", "day_off", "rented", "unused", "awaiting_repair"):       # старые статусы убраны
        assert c.post(f"/vehicles/{world.va3}/status", {"status": old, "reason": "проверка"}).status_code == 422, old
    types = c.get("/vehicles/types").json()
    assert [(s["code"], s["label"]) for s in types["statuses"]] == [("in_work", "На линии"), ("in_repair", "В ремонте")]
    assert c.post(f"/vehicles/{world.va3}/status", {"status": "in_repair"}).json()["code"] == "reason_required"   # для ремонта нужна причина
    assert c.post(f"/vehicles/{world.va3}/status", {"status": "in_repair", "reason": "Авария"}).status_code == 200
    assert c.post(f"/vehicles/{world.va3}/archive", {"reason": "Списана"}).status_code == 200
    assert c.post(f"/vehicles/{world.va3}/status", {"status": "in_work"}).status_code == 409                       # в архиве статус не меняется


def test_status_history_cannot_be_rewritten_in_database(make_client, world):
    make_client("mech_a").post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Поломка"})
    with session_factory()() as db:
        for sql in ("UPDATE vehicle_status_history SET reason = 'нет'", "DELETE FROM vehicle_status_history"):
            with pytest.raises(DBAPIError):
                db.execute(text(sql))
            db.rollback()


def open_request(c, vid, title="Не заводится"):
    r = c.post("/requests", {"vehicle_id": vid, "title": title, "priority": "high"})
    assert r.status_code == 201, r.text
    return r.json()


def go(c, rid, to, comment="", **extra):
    return c.post(f"/requests/{rid}/transition", {"to_status": to, "comment": comment, **extra})


def test_full_request_path_with_approval(make_client, world):
    driver, mech, boss = make_client("drv1"), make_client("mech_a"), make_client("dir_a")
    req = open_request(driver, world.va1)
    assert req["status"] == "new" and req["number"] == 1
    assert go(mech, req["id"], "diagnostics").status_code == 200
    assert go(mech, req["id"], "done").status_code == 409                            # перескочить нельзя
    assert go(mech, req["id"], "approval", "Нужна замена КПП, 85 000 ₽").status_code == 200
    r = go(mech, req["id"], "in_progress")                                           # механик сам себе согласовать не может
    assert r.status_code == 403 and r.json()["code"] == "approve_required"
    r = go(boss, req["id"], "in_progress", "Согласовано", vehicle_status="in_repair")
    assert r.status_code == 200 and r.json()["status"] == "in_progress"
    assert go(mech, req["id"], "done", "Работы выполнены").status_code == 200
    assert go(boss, req["id"], "closed").json()["closed_at"]
    detail = boss.get(f"/requests/{req['id']}").json()
    assert [e["to_status"] for e in detail["events"] if e["kind"] == "status"] == ["new", "diagnostics", "approval", "in_progress", "done", "closed"]
    assert detail["allowed_transitions"] == []
    # смена статуса машины связана с обращением и осталась в истории
    link = [h for h in history(boss, world.va1) if h["request_id"] == req["id"]]
    assert link and link[0]["status"] == "in_repair" and link[0]["request_number"] == 1


def test_closed_request_is_frozen(make_client, world):
    boss = make_client("dir_a")
    req = open_request(boss, world.va1)
    assert go(boss, req["id"], "cancelled").status_code == 422                       # причина отмены обязательна
    assert go(boss, req["id"], "cancelled", "Дубль").status_code == 200
    assert boss.patch(f"/requests/{req['id']}", {"title": "Новое название"}).status_code == 409
    assert boss.post(f"/requests/{req['id']}/comments", {"text": "ещё"}).status_code == 409
    assert go(boss, req["id"], "diagnostics").status_code == 409


def test_cost_is_hidden_from_people_without_edit_rights(make_client, world):
    boss, driver = make_client("dir_a"), make_client("drv1")
    req = open_request(driver, world.va1)
    assert boss.patch(f"/requests/{req['id']}", {"cost_estimate": 85000, "planned_work": "Замена КПП"}).status_code == 200
    assert boss.get(f"/requests/{req['id']}").json()["cost_estimate"] == 85000
    seen = driver.get(f"/requests/{req['id']}").json()
    assert seen["cost_estimate"] is None and seen["money_visible"] is False


def test_assignee_must_be_someone_who_can_work_on_requests(make_client, world):
    from sqlalchemy import select
    from app.models import User
    with session_factory()() as db:
        mech_id = db.scalar(select(User.id).where(User.login == "mech_a"))
        drv_id = db.scalar(select(User.id).where(User.login == "drv1"))
        other_entity = db.scalar(select(User.id).where(User.login == "dir_b"))
    boss = make_client("dir_a")
    req = open_request(boss, world.va1)
    assert boss.patch(f"/requests/{req['id']}", {"assignee_id": drv_id}).status_code == 422
    assert boss.patch(f"/requests/{req['id']}", {"assignee_id": other_entity}).status_code == 422
    r = boss.patch(f"/requests/{req['id']}", {"assignee_id": mech_id})
    assert r.status_code == 200 and r.json()["assignee"] == "Mech_a"


def test_assignee_list_contains_only_people_who_can_take_requests(make_client, world):
    boss = make_client("dir_a")
    names = [x["name"] for x in boss.get("/requests/assignees", params={"legal_entity_id": world.A}).json()["items"]]
    assert "Mech_a" in names and "Drv1" not in names and "Dir_b" not in names
    assert make_client("drv1").get("/requests/assignees", params={"legal_entity_id": world.A}).status_code == 403
    assert boss.get("/requests/assignees", params={"legal_entity_id": world.B}).status_code == 403


def test_request_events_are_append_only_in_database(make_client, world):
    open_request(make_client("dir_a"), world.va1)
    with session_factory()() as db:
        for sql in ("UPDATE service_request_events SET text = 'x'", "DELETE FROM service_request_events"):
            with pytest.raises(DBAPIError):
                db.execute(text(sql))
            db.rollback()


def test_request_filters_and_search(make_client, world):
    boss = make_client("dir_a")
    a = open_request(boss, world.va1, "Течёт масло")
    open_request(boss, world.va2, "Не работает печка")
    assert boss.get("/requests", params={"q": "масло"}).json()["total"] == 1
    assert boss.get("/requests", params={"q": "А002АА54"}).json()["total"] == 1
    assert boss.get("/requests", params={"q": str(a["number"])}).json()["total"] >= 1
    assert boss.get("/requests", params={"vehicle_id": world.va2}).json()["total"] == 1
    assert boss.get("/requests", params={"priority": "bogus"}).status_code == 422
    assert boss.get("/requests", params={"status": "open"}).json()["total"] == 2
    assert boss.get("/requests", params={"q": "%"}).json()["total"] == 0           # % не работает как шаблон
