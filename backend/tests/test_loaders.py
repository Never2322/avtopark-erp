# -*- coding: utf-8 -*-
"""Грузчик мусоровоза: роль, закрепление за машиной (отдельно от водителя), кабинет, замечания по маршруту, выход на работу."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import Employee, EmploymentAssignment, Notification, Role, User, UserRole
from app.services.vehicles import current_driver_id

from tests.helpers import _HASH
from tests.test_garbage_routes import TODAY, plan, route, waste
from tests.test_ton_pay import ticket


def make_loader(world, last="Грузов", login="ldr1", entity=None):
    """Сотрудник-грузчик с входом и ролью «Грузчик мусоровоза»."""
    with session_factory()() as db:
        e = Employee(last_name=last, first_name="Иван", position="грузчик")
        db.add(e)
        db.flush()
        db.add(EmploymentAssignment(employee_id=e.id, legal_entity_id=entity or world.A, started_on=TODAY - dt.timedelta(days=100)))
        u = User(login=login, display_name=login, password_hash=_HASH)
        db.add(u)
        db.flush()
        role = db.scalar(select(Role.id).where(Role.code == "loader"))
        db.add(UserRole(user_id=u.id, role_id=role, legal_entity_id=entity or world.A))
        e.user_id = u.id
        db.commit()
        return e.id


def test_role_exists_with_small_rights(world, make_client):
    roles = {r["code"]: r for r in make_client("admin").get("/roles").json()["items"]}
    assert roles["loader"]["name"] == "Грузчик мусоровоза"
    make_loader(world)
    ldr = make_client("ldr1")
    perms = set(ldr.get("/auth/me").json()["permissions"])
    assert {"loaders.view_own", "attendance.mark_own", "requests.create"} <= perms
    assert not perms & {"vehicles.view", "vehicles.view_own", "weigh_tickets.view", "reports.view", "dashboard.view"}
    assert ldr.get("/vehicles").status_code == 403


def test_assign_loader_does_not_change_driver(world, make_client):
    t = waste("М100ММ54", world.A, driver=world.d1)
    lid = make_loader(world)
    d = make_client("disp_a")
    crews = d.get("/loaders/crews").json()
    truck = next(x for x in crews["items"] if x["id"] == t)
    assert truck["driver"] == "Первый Тест" and truck["loaders"] == [] and truck["can_assign"]
    assert any(x["id"] == lid and not x["is_driver"] for x in crews["people"])
    r = d.post("/loaders/assign", {"vehicle_id": t, "employee_id": lid})
    assert r.status_code == 201, r.text
    assert d.post("/loaders/assign", {"vehicle_id": t, "employee_id": lid}).status_code == 409
    t2 = waste("М200ММ54", world.A)
    msg = d.post("/loaders/assign", {"vehicle_id": t2, "employee_id": lid}).json()
    assert "М100ММ54" in str(msg)                                          # один грузчик сразу на двух машинах нельзя
    with session_factory()() as db:
        assert current_driver_id(db, t) == world.d1                       # водитель машины прежний
    truck = next(x for x in d.get("/loaders/crews").json()["items"] if x["id"] == t)
    assert [x["name"] for x in truck["loaders"]] == ["Грузов Иван"]
    # снять: закрепили сегодня и сразу сняли — запись пропадает
    assert d.post(f"/loaders/assignments/{r.json()['id']}/end").json()["removed"] is True
    assert next(x for x in d.get("/loaders/crews").json()["items"] if x["id"] == t)["loaders"] == []


def test_assign_rights_and_entity(world, make_client):
    t = waste("М100ММ54", world.A)
    lid = make_loader(world)
    other = make_loader(world, "Чужой", "ldr_b", entity=world.B)
    assert make_client("mech_a").get("/loaders/crews").status_code == 403
    assert make_client("acc_a").post("/loaders/assign", {"vehicle_id": t, "employee_id": lid}).status_code == 404
    assert make_client("dir_b").post("/loaders/assign", {"vehicle_id": t, "employee_id": lid}).status_code == 404
    assert make_client("dir_a").post("/loaders/assign", {"vehicle_id": t, "employee_id": other}).status_code == 422
    assert make_client("dir_a").get("/loaders/crews").json()["can"] == {"notes": True, "assign": True}


def test_loader_home_shows_truck_driver_mates_route_and_tons(world, make_client):
    t = waste("М100ММ54", world.A, driver=world.d1)
    with session_factory()() as db:
        db.get(__import__("app.models", fromlist=["Vehicle"]).Vehicle, t).tonnage = True
        db.commit()
    lid = make_loader(world)
    mate = make_loader(world, "Напарников", "ldr2")
    d = make_client("dir_a")
    for e in (lid, mate):
        assert d.post("/loaders/assign", {"vehicle_id": t, "employee_id": e, "started_on": (TODAY - dt.timedelta(days=3)).isoformat()}).status_code == 201
    rid = route(d, "Центр", "Ленина\nМира", entity=world.A).json()["id"]
    wd = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[TODAY.weekday()]
    assert plan(d, rid, t, kind="weekly", **{wd: True}).status_code == 201
    ticket(world, world.d1, "7.5", day=TODAY, vehicle=t)
    ticket(world, world.d1, None, day=TODAY, vehicle=t)
    h = make_client("ldr1").get("/loaders/home").json()
    assert h["has_vehicle"] and len(h["vehicles"]) == 1
    v = h["vehicles"][0]
    assert v["plate"] == "М100ММ54" and v["driver"] == "Первый Тест" and v["mates"] == ["Напарников Иван"]
    assert v["today"] == {"trips": 2, "tons": 7.5, "pending": 1, "days": 1}
    today = h["days"][0]
    assert today["is_today"] and today["items"][0]["route"] == "Центр" and today["items"][0]["streets"] == ["Ленина", "Мира"]
    assert "cost" not in str(v) and "price" not in str(v)                # денег грузчик не видит


def test_loader_without_truck_gets_empty_home(world, make_client):
    make_loader(world)
    h = make_client("ldr1").get("/loaders/home").json()
    assert h["has_vehicle"] is False and h["vehicles"] == [] and h["has_employee"] is True
    assert make_client("drv1").get("/loaders/home").status_code == 403


def test_route_note_goes_to_office_and_gets_resolved(world, make_client):
    t = waste("М100ММ54", world.A, driver=world.d1)
    lid = make_loader(world)
    d = make_client("disp_a")
    d.post("/loaders/assign", {"vehicle_id": t, "employee_id": lid, "started_on": (TODAY - dt.timedelta(days=1)).isoformat()})
    ldr = make_client("ldr1")
    assert ldr.post("/loaders/notes", {"vehicle_id": t, "kind": "overflow"}).status_code == 422          # без адреса
    assert ldr.post("/loaders/notes", {"vehicle_id": t, "kind": "boom", "street": "Ленина 1"}).status_code == 422
    assert ldr.post("/loaders/notes", {"vehicle_id": world.va2, "kind": "broken", "street": "Мира 2"}).status_code == 403
    r = ldr.post("/loaders/notes", {"vehicle_id": t, "kind": "broken", "street": "  Ленина  5 ", "note": "бак без колеса"})
    assert r.status_code == 201, r.text
    assert r.json()["street"] == "Ленина 5" and r.json()["resolved"] is False
    assert ldr.get("/loaders/home").json()["notes"][0]["kind_label"] == "Сломан контейнер"
    with session_factory()() as db:
        titles = list(db.scalars(select(Notification.title)))
    assert any("М100ММ54" in x and "Ленина 5" in x for x in titles)
    crews = d.get("/loaders/crews").json()
    assert crews["open_notes"] == 1 and crews["notes"][0]["who"] == "Грузов Иван" and crews["notes"][0]["can_resolve"]
    nid = crews["notes"][0]["id"]
    assert make_client("dir_b").post(f"/loaders/notes/{nid}/resolve").status_code == 404
    assert d.post(f"/loaders/notes/{nid}/resolve").json()["resolved"] is True
    assert d.get("/loaders/crews").json()["open_notes"] == 0
    assert ldr.get("/loaders/home").json()["notes"][0]["resolved"] is True


def test_loader_marks_attendance_and_appears_in_office_list(world, make_client):
    t = waste("М100ММ54", world.A, driver=world.d1)
    lid = make_loader(world)
    make_client("dir_a").post("/loaders/assign", {"vehicle_id": t, "employee_id": lid, "started_on": (TODAY - dt.timedelta(days=1)).isoformat()})
    day = make_client("disp_a").get("/attendance").json()
    me = next(x for x in day["items"] if x["employee_id"] == lid)
    assert me["vehicles"] == ["М100ММ54 (грузчик)"]
    ldr = make_client("ldr1")
    assert ldr.get("/attendance/mine").status_code == 200


def test_choose_crew_vehicles(world, make_client):
    t = waste("М100ММ54", world.A, driver=world.d1)
    lid = make_loader(world)
    d = make_client("dir_a")
    d.post("/loaders/assign", {"vehicle_id": t, "employee_id": lid, "started_on": (TODAY - dt.timedelta(days=2)).isoformat()})
    crews = d.get("/loaders/crews").json()
    assert [x["id"] for x in crews["items"]] == [t]
    other = next(x for x in crews["others"] if x["id"] == world.va1)      # обычная машина: её можно добавить
    assert other["plate"]
    assert make_client("mech_a").post(f"/loaders/vehicles/{world.va1}", {"in_crews": True}).status_code in (403, 404)
    assert d.post(f"/loaders/vehicles/{world.va1}", {"in_crews": True}).status_code == 200
    ids = [x["id"] for x in d.get("/loaders/crews").json()["items"]]
    assert set(ids) == {t, world.va1}
    # убрали мусоровоз: пропал из бригад, грузчик снят, водитель прежний
    r = d.post(f"/loaders/vehicles/{t}", {"in_crews": False}).json()
    assert r["loaders_removed"] == 1
    crews = d.get("/loaders/crews").json()
    assert [x["id"] for x in crews["items"]] == [world.va1] and any(x["id"] == t for x in crews["others"])
    assert make_client("ldr1").get("/loaders/home").json()["has_vehicle"] is False
    with session_factory()() as db:
        assert current_driver_id(db, t) == world.d1
    assert make_client("dir_b").post(f"/loaders/vehicles/{t}", {"in_crews": True}).status_code == 404
