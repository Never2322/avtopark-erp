# -*- coding: utf-8 -*-
"""27.11: зарплата мусоровозов по дням (экипаж с грузчиками, ставка за тонну на день, итог за месяц), категории и бюджеты счетов,
моточасы в заправках."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import LoaderAssignment, VehicleReading

from tests.test_expenses_overview import bill
from tests.test_loaders import make_loader
from tests.test_ton_pay import FIRST, MONTH, ticket, ton_driver


def crew(world, loader, vehicle=None, start=None):
    with session_factory()() as db:
        db.add(LoaderAssignment(vehicle_id=vehicle or world.va1, employee_id=loader, started_on=start or FIRST))
        db.commit()


def salary(c, month=MONTH):
    r = c.get("/garbage-salary", params={"month": month})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- зарплата мусоровозов

def test_day_table_has_driver_and_loaders_and_month_total(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    lid = make_loader(world)
    crew(world, lid)
    ticket(world, world.d1, "10")
    ticket(world, world.d1, "2.5")
    ticket(world, world.d1, "3", day=FIRST + dt.timedelta(days=1))
    j = salary(d)
    first = j["days"][0]
    assert first["trips"] == 2 and first["tons"] == 12.5
    drv, ldr = first["people"]
    assert drv["role"] == "driver" and drv["rate"] == 600 and drv["rate_source"] == "tariff" and drv["sum"] == 7500
    assert ldr["role"] == "loader" and ldr["tons"] == 12.5 and ldr["rate"] is None and ldr["sum"] == 0
    # ставка грузчику на день — и итог за месяц пересчитался сам
    r = d.put("/garbage-salary/rate", {"date": first["date"], "vehicle_id": first["vehicle_id"], "employee_id": lid, "rate": "200"})
    assert r.status_code == 200, r.text
    j = salary(d)
    assert j["days"][0]["people"][1]["sum"] == 2500 and j["days"][0]["people"][1]["rate_source"] == "day"
    month = {m["employee_id"]: m for m in j["month"]}
    assert month[lid]["days"] == 2 and month[lid]["sum"] == 2500 and month[lid]["no_rate"] == 1
    assert month[world.d1]["days"] == 2 and month[world.d1]["tons"] == 15.5 and month[world.d1]["sum"] == 9300
    assert j["total"] == 9300 + 2500


def test_bulk_rate_fills_only_empty_and_driver_day_rate_reaches_payroll(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    lid = make_loader(world)
    crew(world, lid)
    ticket(world, world.d1, "10")
    r = d.post("/garbage-salary/rates", {"month": MONTH, "role": "all", "rate": "150"})
    assert r.status_code == 200 and r.json()["changed"] == 1          # у водителя есть тариф: его не трогаем
    day = salary(d)["days"][0]
    assert day["people"][1]["sum"] == 1500 and day["people"][0]["sum"] == 6000
    # своя ставка водителю на день действует и в «Зарплате за месяц»
    d.put("/garbage-salary/rate", {"date": day["date"], "vehicle_id": day["vehicle_id"], "employee_id": world.d1, "rate": "700"})
    rep = d.get("/reports/salary", params={"month": MONTH}).json()
    assert any(7000 in row for row in rep["table"]["rows"]), rep["table"]["rows"]
    # пустая ставка — снова по тарифу
    d.put("/garbage-salary/rate", {"date": day["date"], "vehicle_id": day["vehicle_id"], "employee_id": world.d1, "rate": ""})
    assert salary(d)["days"][0]["people"][0]["sum"] == 6000


def test_salary_rights(world, make_client):
    ton_driver(world, make_client)
    ticket(world, world.d1, "5")
    assert make_client("drv1").get("/garbage-salary").status_code == 403
    mech = make_client("mech_a")
    assert mech.get("/garbage-salary").status_code == 403
    day = salary(make_client("dir_a"))["days"][0]
    assert make_client("acc_a").put("/garbage-salary/rate", {"date": day["date"], "vehicle_id": day["vehicle_id"], "employee_id": world.d1, "rate": "1"}).status_code in (200, 403)
    assert make_client("dir_a").put("/garbage-salary/rate", {"date": day["date"], "vehicle_id": day["vehicle_id"], "employee_id": 999999, "rate": "1"}).status_code == 404


# ---------------------------------------------------------------- счета: категории и бюджет

def test_bill_category_filter_and_budget(world, make_client):
    d = make_client("dir_a")
    a = bill(d, "К-1", "10000", category="parts")
    bill(d, "К-2", "3000", category="fluids")
    c = bill(d, "К-3", "500")
    assert d.call("PUT", "/bills/budget", json={"month": MONTH, "category": "parts", "amount": "50000"}).status_code == 200
    j = d.get("/bills", params={"month": MONTH}).json()
    b = {x["code"]: x for x in j["budget"]["items"]}
    assert b["parts"]["budget"] == 50000 and b["parts"]["spent"] == 10000 and b["parts"]["left"] == 40000
    assert b["fluids"]["budget"] is None and b["fluids"]["spent"] == 3000 and j["budget"]["none"] == 500
    assert [x["number"] for x in d.get("/bills", params={"month": MONTH, "category": "fluids"}).json()["items"]] == ["К-2"]
    assert [x["number"] for x in d.get("/bills", params={"month": MONTH, "category": "none"}).json()["items"]] == ["К-3"]
    # категорию ставят прямо из списка; перерасход виден как минус
    assert d.post(f"/bills/{c['id']}/category", {"category": "parts"}).json()["category_label"] == "Запчасти"
    d.call("PUT", "/bills/budget", json={"month": MONTH, "category": "parts", "amount": "10000"})
    b = {x["code"]: x for x in d.get("/bills", params={"month": MONTH}).json()["budget"]["items"]}
    assert b["parts"]["left"] == -500
    assert d.patch(f"/bills/{a['id']}", {"category": "consumables"}).json()["category"] == "consumables"
    assert d.post(f"/bills/{a['id']}/category", {"category": "food"}).status_code == 422
    assert make_client("mech_a").call("PUT", "/bills/budget", json={"month": MONTH, "category": "parts", "amount": "1"}).status_code == 403


# ---------------------------------------------------------------- заправки: моточасы

def test_fuel_fill_with_engine_hours_goes_to_readings_and_counts_per_hour(world, make_client):
    d = make_client("dir_a")
    day = FIRST.isoformat()
    r = d.post("/fuel_fills", {"fill_date": day, "vehicle_id": world.va1, "liters": "40", "engine_hours": "1200"})
    assert r.status_code == 201, r.text
    r = d.post("/fuel_fills", {"fill_date": day, "vehicle_id": world.va1, "liters": "50", "engine_hours": "1210"})
    assert r.status_code == 201, r.text
    rows = d.get("/fuel_fills").json()["items"]
    assert any(x["consumption"] == "5,0 л/ч" for x in rows), [x["consumption"] for x in rows]
    with session_factory()() as db:
        kinds = set(db.scalars(select(VehicleReading.kind).where(VehicleReading.vehicle_id == world.va1, VehicleReading.source == "fuel")))
    assert kinds == {"engine_hours"}
    both = d.post("/fuel_fills", {"fill_date": day, "vehicle_id": world.va1, "liters": "10", "engine_hours": "1220", "odometer": 5000})
    assert both.status_code == 422
    meta = [f["name"] for f in d.get("/fuel_fills/meta").json()["fields"]]
    assert "engine_hours" in meta and "full_tank" not in meta
