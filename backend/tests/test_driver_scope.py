# -*- coding: utf-8 -*-
"""Водитель видит и меняет только своё: закреплённую технику, свои путевые листы, свои показания."""


def test_driver_sees_only_assigned_vehicle(make_client, world):
    c = make_client("drv1")
    plates = [v["plate"] for v in c.get("/vehicles").json()["items"]]
    assert plates == ["А001АА54"]
    assert c.get(f"/vehicles/{world.va1}").status_code == 200
    assert c.get(f"/vehicles/{world.va2}").status_code == 404           # коллега: не существует для него
    assert c.get(f"/vehicles/{world.va3}").status_code == 404
    assert c.get(f"/vehicles/{world.va2}/readings").status_code == 404


def test_driver_enters_readings_only_for_own_vehicle(make_client, world):
    c = make_client("drv1")
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1100}).status_code == 201
    assert c.post(f"/vehicles/{world.va2}/readings", {"kind": "km", "value": 1100}).status_code == 404
    assert c.post(f"/readings/1/correct", {"value": 1, "reason": "хочу"}).status_code == 403          # исправлять водитель не может
    assert c.get("/readings/pending").status_code == 403


def test_driver_creates_and_runs_only_own_waybills(make_client, world):
    d1, d2, disp = make_client("drv1"), make_client("drv2"), make_client("disp_a")
    own = d1.post("/waybills", {"vehicle_id": world.va1})                                          # водитель по умолчанию — сам
    assert own.status_code == 201, own.text
    assert own.json()["driver"]["id"] == world.d1
    assert d1.post("/waybills", {"vehicle_id": world.va1, "driver_id": world.d2}).status_code == 403   # на другого нельзя
    assert d1.post("/waybills", {"vehicle_id": world.va2}).status_code == 422                          # чужая машина: «не найдена»
    assert d1.post("/waybills", {"vehicle_id": world.va3}).status_code == 422                          # не закреплена: так же
    foreign = disp.post("/waybills", {"vehicle_id": world.va2, "driver_id": world.d2}).json()
    assert d1.get(f"/waybills/{foreign['id']}").status_code == 404
    assert d1.post(f"/waybills/{foreign['id']}/open", {"start_km": 1000}).status_code == 404
    assert [w["id"] for w in d1.get("/waybills").json()["items"]] == [own.json()["id"]]
    assert d2.get(f"/waybills/{own.json()['id']}").status_code == 404


def test_driver_sees_only_own_requests_and_can_withdraw_new_one(make_client, world):
    d1, d2, mech = make_client("drv1"), make_client("drv2"), make_client("mech_a")
    mine = d1.post("/requests", {"vehicle_id": world.va1, "title": "Стучит двигатель"}).json()
    d2.post("/requests", {"vehicle_id": world.va2, "title": "Не горит фара"})
    assert [r["title"] for r in d1.get("/requests").json()["items"]] == ["Стучит двигатель"]
    assert d2.get(f"/requests/{mine['id']}").status_code == 404
    assert len(mech.get("/requests").json()["items"]) == 2
    assert d1.post(f"/requests/{mine['id']}/transition", {"to_status": "diagnostics"}).status_code == 403   # вести обращение — не его дело
    r = d1.post(f"/requests/{mine['id']}/transition", {"to_status": "cancelled", "comment": "Починил сам"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_driver_without_employee_card_sees_nothing_instead_of_everything(make_client, world):
    # у пользователя с ролью «Водитель» нет карточки сотрудника: закреплённой техники нет, значит список пуст
    from sqlalchemy import select
    from app.db import session_factory
    from app.models import Employee
    with session_factory()() as db:
        db.scalar(select(Employee).where(Employee.id == world.d1)).user_id = None
        db.commit()
    c = make_client("drv1")
    assert c.get("/vehicles").json()["items"] == []
    assert c.get("/waybills").json()["items"] == []
