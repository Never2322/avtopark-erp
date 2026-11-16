# -*- coding: utf-8 -*-
"""Сводка «Водители и техника»: строки и замечания, отборы, права на каждый блок данных, изоляция юрлиц, выгрузка и журнал."""
import datetime as dt

from sqlalchemy import func, select

from app.db import session_factory
from app.models import AuditLog
from tests.test_entries import day, fuel_body, set_specs, trip_body
from tests.test_jobs import new_job
from app.common import org_today

TODAY = org_today()


def rows(c, **params):
    r = c.get("/roster", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def by_plate(data):
    return {i["plate"]: i for i in data["items"]}


def codes(item):
    return {f["code"] for f in item["flags"]}


def test_rows_drivers_and_flags(world, make_client):
    d = make_client("disp_a")
    data = rows(d)
    items = by_plate(data)
    assert set(items) == {"А001АА54", "А002АА54", "А003АА54"}          # чужое юрлицо не попало
    a1 = items["А001АА54"]
    assert [x["name"] for x in a1["drivers"]] == ["Первый Тест"] and a1["drivers"][0]["role"] == "primary"
    assert a1["status"] == "in_work" and a1["current_km"] == 1000.0
    # у третьей машины никто не закреплён; у всех старое показание (10 дней назад, порог 7)
    assert "no_driver" in codes(items["А003АА54"]) and "no_driver" not in codes(a1)
    assert all("old_reading" in codes(i) for i in items.values())
    assert a1["reading_age_days"] in (9, 10)
    assert data["totals"]["vehicles"] == 3 and data["totals"]["no_driver"] == 1 and data["totals"]["attention"] == 3
    assert data["can"]["export"] and data["can"]["assign"]
    assert d.get("/roster", params={"legal_entity_id": world.B}).status_code == 403


def test_fresh_reading_clears_old_reading_flag(world, make_client):
    d = make_client("disp_a")
    assert d.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": "1100"}).status_code in (200, 201)
    items = by_plate(rows(d))
    assert "old_reading" not in codes(items["А001АА54"]) and "old_reading" in codes(items["А002АА54"])
    assert items["А001АА54"]["current_km"] == 1100.0


def test_status_filter_flags_and_search(world, make_client):
    d = make_client("disp_a")
    assert d.post(f"/vehicles/{world.va2}/status", {"status": "in_repair", "reason": "Сцепление"}).status_code == 200
    items = by_plate(rows(d))
    assert items["А002АА54"]["status_label"] == "В ремонте" and "old_reading" not in codes(items["А002АА54"])   # старое показание — только у машин «в работе»
    assert set(by_plate(rows(d, status="in_repair"))) == {"А002АА54"}
    assert set(by_plate(rows(d, only="no_driver"))) == {"А003АА54"}
    assert set(by_plate(rows(d, q="второй"))) == {"А002АА54"}              # поиск по фамилии водителя
    assert set(by_plate(rows(d, q="а001 аа54"))) == {"А001АА54"}           # и по номеру, с пробелом
    # цифры сверху не зависят от отбора
    assert rows(d, only="no_driver")["totals"]["vehicles"] == 3
    assert d.get("/roster", params={"status": "nope"}).status_code == 422
    assert d.get("/roster", params={"only": "nope"}).status_code == 422


def test_archived_vehicles_are_not_listed(world, make_client):
    dr, adm = make_client("dir_a"), make_client("admin")
    assert adm.post(f"/vehicles/{world.va3}/archive", {"reason": "Продана"}).status_code == 200
    assert adm.post(f"/vehicles/{world.va2}/archive", {"reason": "Ошибочно добавлена"}).status_code == 200
    assert set(by_plate(rows(dr))) == {"А001АА54"}


def test_job_trip_fuel_requests_blocks(world, make_client):
    d, adm = make_client("disp_a"), make_client("admin")
    # наряд на сегодня и незакрытый вчерашний
    assert new_job(world, d, vehicle=world.va1, time_from="09:00").status_code == 201
    r = new_job(world, d, vehicle=world.va1, time_from="07:00", address="Лесная, 5")
    assert r.status_code == 201
    old = d.post("/jobs", {"job_date": (TODAY - dt.timedelta(days=1)).isoformat(), "vehicle_id": world.va2, "address": "Вчера", "customer": "", "task": "", "time_from": ""})
    assert old.status_code == 201, old.text
    # рейс и заправки: расход выше нормы
    assert d.post("/trips", trip_body(world, route="Склад — карьер")).status_code == 201
    assert set_specs(world, adm, world.va1, tank_liters="100", fuel_norm="25").status_code == 200
    assert d.post("/fuel_fills", fuel_body(world, fill_date=day(5), liters="90", odometer=1000)).status_code == 201
    assert d.post("/fuel_fills", fuel_body(world, fill_date=day(1), liters="80", odometer=1200)).status_code == 201     # 40 л/100 км при норме 25
    assert d.post("/requests", {"vehicle_id": world.va1, "title": "Стук в подвеске", "priority": "normal"}).status_code == 201

    items = by_plate(rows(d))
    a1, a2 = items["А001АА54"], items["А002АА54"]
    assert a1["job"]["today"] == 2 and a1["job"]["open"] == 2 and a1["job"]["stale"] == 0
    assert a1["job"]["next"]["address"] == "Лесная, 5"                      # ближайший по времени
    assert a2["job"]["today"] == 0 and a2["job"]["stale"] == 1 and "stale_job" in codes(a2)
    assert a1["last_trip"]["route"] == "Склад — карьер"
    assert a1["fuel"]["per100"] == 40.0 and a1["fuel"]["norm"] == 25.0 and a1["fuel"]["over_pct"] == 60 and "fuel_over" in codes(a1)
    assert a1["open_requests"] == 1 and a2["open_requests"] == 0
    assert set(by_plate(rows(d, only="attention"))) == {"А001АА54", "А002АА54", "А003АА54"}


def test_done_job_goes_after_open_ones(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    j1 = new_job(world, d, vehicle=world.va1, time_from="06:00").json()
    new_job(world, d, vehicle=world.va1, time_from="15:00", address="Вечерний")
    assert drv.post(f"/jobs/{j1['id']}/finish", {}).status_code == 200
    a1 = by_plate(rows(d))["А001АА54"]
    assert a1["job"]["today"] == 2 and a1["job"]["done"] == 1 and a1["job"]["open"] == 1
    assert a1["job"]["next"]["address"] == "Вечерний"


def test_free_drivers_and_unassign(world, make_client):
    d = make_client("disp_a")
    assert rows(d)["free_drivers"] == []
    aid = d.get(f"/vehicles/{world.va2}/assignments").json()["items"][0]["id"]
    # «последний день» включается: закрепление, снятое сегодняшним числом, действует до конца дня
    assert d.post(f"/vehicles/{world.va2}/assignments/{aid}/end", {"ended_on": (TODAY - dt.timedelta(days=1)).isoformat()}).status_code == 200
    data = rows(d)
    assert [x["name"] for x in data["free_drivers"]] == ["Второй Тест"]
    assert "no_driver" in codes(by_plate(data)["А002АА54"])
    # водитель из другого юрлица в список не попадает
    assert all(x["name"] != "Третий Тест" for x in data["free_drivers"])


def test_permissions_per_block_and_roles(world, make_client):
    # механик видит сводку и заправки, но не наряды, рейсы и выгрузку
    m = make_client("mech_a")
    data = rows(m)
    assert data["can"] == {"jobs": False, "trips": False, "fuel": True, "requests": True, "export": False, "assign": False, "employees": True}
    a1 = by_plate(data)["А001АА54"]
    assert a1["job"] is None and a1["last_trip"] is None and a1["open_requests"] == 0
    assert m.get("/roster/export.csv").status_code == 403
    # без права на сводку: водитель, бухгалтер, сотрудник, кладовщик
    for login in ("drv1", "acc_a", "emp_a", "store_a"):
        assert make_client(login).get("/roster").status_code == 403, login
    assert make_client("drv1").get("/roster/export.csv").status_code == 403


def test_entity_isolation(world, make_client):
    b = make_client("dir_b")
    assert set(by_plate(rows(b))) == {"В001ВВ54"}
    assert b.get("/roster", params={"legal_entity_id": world.A}).status_code == 403
    assert [x["name"] for x in rows(b)["free_drivers"]] == []
    adm = rows(make_client("admin"))
    assert len(adm["items"]) == 4
    assert rows(make_client("admin"), legal_entity_id=world.B)["totals"]["vehicles"] == 1


def test_export_csv_audit_and_scope(world, make_client):
    d, b = make_client("disp_a"), make_client("dir_b")
    r = d.get("/roster/export.csv", params={"only": "no_driver"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    assert "А003АА54" in text and "А001АА54" not in text and "В001ВВ54" not in text
    head = text.splitlines()[0].split(";")
    assert head[:4] == ["Госномер", "Машина", "Юрлицо", "Водитель"] and "Замечания" in head and "Заданий сегодня" in head
    assert "Нет водителя" in text
    rb = b.get("/roster/export.csv").content.decode("utf-8-sig")
    assert "В001ВВ54" in rb and "А001АА54" not in rb
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "roster.export")) == 2
    # директор не видит заправок? видит: у него есть все блоки, в файле они есть; а у механика выгрузки нет вовсе
    assert "Расход, л/100 км" in text
