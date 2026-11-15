# -*- coding: utf-8 -*-
"""Путевые листы: жизненный цикл, один открытый на машину, показания, исправление закрытого листа с причиной."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import session_factory


def make(c, vid, did, **extra):
    r = c.post("/waybills", {"vehicle_id": vid, "driver_id": did, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def current_km(c, vid):
    return c.get(f"/vehicles/{vid}").json()["current_km"]


def test_lifecycle_writes_readings_and_locks(make_client, world):
    disp = make_client("disp_a")
    wb = make(disp, world.va1, world.d1, route="Склад — объект")
    assert wb["status"] == "draft" and wb["number_label"].endswith("-0001")
    opened = disp.post(f"/waybills/{wb['id']}/open", {"fuel_start_l": 80})
    assert opened.status_code == 200 and opened.json()["start_km"] == 1000          # начало берётся из последних показаний машины
    closed = disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1180.5, "fuel_added_l": 40, "fuel_end_l": 60})
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["status"] == "closed" and body["run_km"] == 180.5 and body["closed_by"] == "Disp_a"
    assert current_km(disp, world.va1) == 1180.5                                      # пробег машины обновился из листа
    log = disp.get(f"/vehicles/{world.va1}/readings").json()["items"]
    assert any(r["waybill_id"] == wb["id"] and r["source"] == "waybill" and r["value"] == 1180.5 for r in log)
    # закрытый лист не правится обычным способом
    assert disp.patch(f"/waybills/{wb['id']}", {"route": "Другой маршрут"}).status_code == 409
    assert disp.post(f"/waybills/{wb['id']}/open", {}).status_code == 409
    assert disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1200}).status_code == 409
    assert disp.post(f"/waybills/{wb['id']}/cancel", {"reason": "передумали"}).status_code == 409


def test_only_one_open_waybill_per_vehicle_and_per_driver(make_client, world):
    disp = make_client("disp_a")
    first, second = make(disp, world.va1, world.d1), make(disp, world.va1, world.d2)
    assert disp.post(f"/waybills/{first['id']}/open", {"start_km": 1000}).status_code == 200
    r = disp.post(f"/waybills/{second['id']}/open", {"start_km": 1000})
    assert r.status_code == 409 and r.json()["code"] == "vehicle_busy"
    third = make(disp, world.va2, world.d1)
    r = disp.post(f"/waybills/{third['id']}/open", {"start_km": 1000})
    assert r.status_code == 409 and r.json()["code"] == "driver_busy"
    # после закрытия машину можно брать снова
    assert disp.post(f"/waybills/{first['id']}/close", {"end_km": 1100}).status_code == 200
    assert disp.post(f"/waybills/{second['id']}/open", {}).status_code == 200


def test_database_allows_one_open_waybill_per_vehicle_even_if_code_is_bypassed(make_client, world):
    disp = make_client("disp_a")
    a, b = make(disp, world.va1, world.d1), make(disp, world.va1, world.d2)
    disp.post(f"/waybills/{a['id']}/open", {"start_km": 1000})
    with session_factory()() as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE waybills SET status = 'open' WHERE id = :i"), {"i": b["id"]})
        db.rollback()


def test_start_and_end_validation(make_client, world):
    disp = make_client("disp_a")
    wb = make(disp, world.va1, world.d1)
    r = disp.post(f"/waybills/{wb['id']}/open", {"start_km": 900})                  # меньше последнего показания машины
    assert r.status_code == 422 and r.json()["code"] == "start_below_current"
    assert disp.post(f"/waybills/{wb['id']}/open", {"start_km": 1050}).status_code == 200
    assert current_km(disp, world.va1) == 1050                                       # начальное значение стало показанием
    assert disp.post(f"/waybills/{wb['id']}/close", {}).json()["code"] == "end_required"
    assert disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1000}).json()["code"] == "end_below_start"
    assert disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1100, "end_hours": 5}).json()["code"] == "not_tracked"
    ok = disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1100})
    assert ok.status_code == 200


def test_fuel_cannot_grow_from_nowhere(make_client, world):
    disp = make_client("disp_a")
    wb = make(disp, world.va1, world.d1)
    disp.post(f"/waybills/{wb['id']}/open", {"fuel_start_l": 50})
    r = disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1100, "fuel_added_l": 10, "fuel_end_l": 100})
    assert r.status_code == 422 and r.json()["code"] == "fuel_mismatch"


def test_vehicle_in_repair_cannot_go_on_route(make_client, world):
    disp, mech = make_client("disp_a"), make_client("mech_a")
    wb = make(disp, world.va1, world.d1)
    mech.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Замена колодок"})
    r = disp.post(f"/waybills/{wb['id']}/open", {})
    assert r.status_code == 409 and r.json()["code"] == "vehicle_unavailable"


def test_big_run_goes_to_review_but_waybill_closes(make_client, world):
    disp = make_client("disp_a")
    wb = make(disp, world.va1, world.d1)
    disp.post(f"/waybills/{wb['id']}/open", {})
    r = disp.post(f"/waybills/{wb['id']}/close", {"end_km": 9000})
    assert r.status_code == 200 and r.json()["readings_need_review"] == ["km"]
    assert current_km(disp, world.va1) == 1000
    assert len(make_client("dir_a").get("/readings/pending").json()["items"]) == 1


def test_driver_runs_own_waybill_end_to_end(make_client, world):
    d1 = make_client("drv1")
    wb = d1.post("/waybills", {"vehicle_id": world.va1}).json()
    assert d1.post(f"/waybills/{wb['id']}/open", {"start_km": 1000}).status_code == 200
    assert d1.post(f"/waybills/{wb['id']}/close", {"end_km": 1075}).status_code == 200
    assert d1.post(f"/waybills/{wb['id']}/cancel", {"reason": "нет права"}).status_code == 403
    assert d1.post(f"/waybills/{wb['id']}/correct", {"reason": "исправить", "end_km": 1080}).status_code == 403


def test_correction_needs_reason_and_keeps_both_versions(make_client, world):
    disp, boss = make_client("disp_a"), make_client("dir_a")
    wb = make(disp, world.va1, world.d1)
    disp.post(f"/waybills/{wb['id']}/open", {})
    disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1180, "fuel_end_l": 10})
    assert disp.post(f"/waybills/{wb['id']}/correct", {"reason": "ошибка", "end_km": 1190}).status_code == 403   # диспетчеру нельзя
    assert boss.post(f"/waybills/{wb['id']}/correct", {"end_km": 1190}).status_code == 422                         # без причины нельзя
    assert boss.post(f"/waybills/{wb['id']}/correct", {"reason": "Нечего менять"}).status_code == 422
    assert boss.post(f"/waybills/{wb['id']}/correct", {"reason": "Конец меньше начала", "end_km": 900}).json()["code"] == "end_below_start"
    r = boss.post(f"/waybills/{wb['id']}/correct", {"reason": "Водитель ошибся в записи", "end_km": 1190})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["end_km"] == 1190 and body["run_km"] == 190
    assert len(body["corrections"]) == 1
    corr = body["corrections"][0]
    assert corr["reason"] == "Водитель ошибся в записи" and corr["before"]["end_km"] == "1180.0" and corr["after"]["end_km"] == "1190.0"
    assert current_km(boss, world.va1) == 1190
    items = boss.get(f"/vehicles/{world.va1}/readings").json()["items"]
    old = [i for i in items if i["value"] == 1180][0]
    assert old["superseded_by"] and not old["effective"]                              # прежнее показание осталось в журнале
    with session_factory()() as db:
        for sql in ("UPDATE waybill_corrections SET reason = 'нет'", "DELETE FROM waybill_corrections"):
            with pytest.raises(DBAPIError):
                db.execute(text(sql))
            db.rollback()


def test_correction_cannot_exceed_next_reading(make_client, world):
    disp, boss = make_client("disp_a"), make_client("dir_a")
    wb = make(disp, world.va1, world.d1)
    disp.post(f"/waybills/{wb['id']}/open", {})
    disp.post(f"/waybills/{wb['id']}/close", {"end_km": 1100})
    disp.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1150})
    r = boss.post(f"/waybills/{wb['id']}/correct", {"reason": "Попытка задним числом", "end_km": 1400})
    assert r.status_code == 422


def test_cancel_needs_reason_and_permission(make_client, world):
    disp, mech = make_client("disp_a"), make_client("mech_a")
    wb = make(disp, world.va1, world.d1)
    assert disp.post(f"/waybills/{wb['id']}/cancel", {"reason": ""}).status_code == 422
    assert mech.post(f"/waybills/{wb['id']}/cancel", {"reason": "Не мой лист"}).status_code == 403
    r = disp.post(f"/waybills/{wb['id']}/cancel", {"reason": "Выезд отменён"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["cancel_reason"] == "Выезд отменён"
    assert disp.post(f"/waybills/{wb['id']}/open", {}).status_code == 409


def test_draft_can_be_edited_but_not_moved_to_another_entity(make_client, world):
    disp = make_client("disp_a")
    wb = make(disp, world.va1, world.d1)
    r = disp.patch(f"/waybills/{wb['id']}", {"route": "Новый маршрут", "shift": "night", "work_site": "Объект 5"})
    assert r.status_code == 200 and r.json()["route"] == "Новый маршрут" and r.json()["shift"] == "night"
    assert disp.patch(f"/waybills/{wb['id']}", {"vehicle_id": world.vb1}).status_code in (403, 422)
    disp.post(f"/waybills/{wb['id']}/open", {})
    assert disp.patch(f"/waybills/{wb['id']}", {"vehicle_id": world.va2}).status_code == 409           # в открытом листе машину не меняют
    assert disp.patch(f"/waybills/{wb['id']}", {"fuel_added_l": 30}).status_code == 200


def test_driver_must_be_a_driver_in_same_entity(make_client, world):
    from sqlalchemy import select
    from app.models import Employee
    with session_factory()() as db:
        db.scalar(select(Employee).where(Employee.id == world.d2)).is_driver = False
        db.commit()
    disp = make_client("disp_a")
    r = disp.post("/waybills", {"vehicle_id": world.va1, "driver_id": world.d2})
    assert r.status_code == 422 and r.json()["code"] == "not_a_driver"


def test_waybill_numbers_are_unique_under_parallel_requests(make_client, world):
    clients = [make_client("disp_a") for _ in range(6)]

    def create(i):
        return clients[i % 6].post("/waybills", {"vehicle_id": world.va1, "driver_id": world.d1}).json()["number"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        numbers = list(pool.map(create, range(18)))
    assert sorted(numbers) == list(range(1, 19))


def test_waybill_filters(make_client, world):
    disp = make_client("disp_a")
    a = make(disp, world.va1, world.d1)
    make(disp, world.va2, world.d2, work_date=(dt.date.today() - dt.timedelta(days=3)).isoformat())
    assert disp.get("/waybills", params={"vehicle_id": world.va2}).json()["total"] == 1
    assert disp.get("/waybills", params={"driver_id": world.d1}).json()["total"] == 1
    assert disp.get("/waybills", params={"date_from": dt.date.today().isoformat()}).json()["total"] == 1
    assert disp.get("/waybills", params={"q": "Второй"}).json()["total"] == 1
    assert disp.get("/waybills", params={"status": "active"}).json()["total"] == 2
    assert disp.get("/waybills", params={"status": "zzz"}).status_code == 422
    assert disp.get("/waybills", params={"q": str(a["number"])}).json()["total"] >= 1
