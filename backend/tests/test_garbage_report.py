# -*- coding: utf-8 -*-
"""Отчёт «Мусоровозы за месяц» и «Тоннаж самосвалов»: мусоровозы отдельно, у них ещё пробег, топливо и затраты."""
import datetime as dt
from decimal import Decimal

from app.db import session_factory
from app.models import FuelFill, VehicleReading

from tests.test_garbage_routes import TODAY, waste
from tests.test_ton_pay import ticket


def test_garbage_trucks_have_own_report(world, make_client):
    g = waste("М100ММ54", world.A, driver=world.d1)
    day = TODAY.replace(day=1)
    ticket(world, world.d1, "7.5", day=day, vehicle=g)
    ticket(world, world.d1, "4", day=day, vehicle=world.va1)                 # обычная машина с тоннажем (самосвал)
    with session_factory()() as db:
        at = dt.datetime.combine(day, dt.time(6), tzinfo=dt.timezone.utc)
        db.add_all([VehicleReading(vehicle_id=g, kind="km", value=Decimal(1000), recorded_at=at - dt.timedelta(days=3)),
                    VehicleReading(vehicle_id=g, kind="km", value=Decimal(1120), recorded_at=at + dt.timedelta(hours=8)),
                    FuelFill(legal_entity_id=world.A, vehicle_id=g, fill_date=day, liters=Decimal("50"), amount=Decimal("3000"))])
        db.commit()
    d = make_client("dir_a")
    month = day.strftime("%Y-%m")
    ton = d.get(f"/reports/tonnage?month={month}").json()
    assert all("М100ММ54" not in r[0] for r in ton["table"]["rows"])        # мусоровоза в тоннаже самосвалов нет
    assert any("А001АА54" in r[0] for r in ton["table"]["rows"])
    gar = d.get(f"/reports/garbage?month={month}").json()
    assert gar["title"].startswith("Мусоровозы за ")
    cols = [c["key"] for c in gar["table"]["columns"]]
    rows = {r[0].split(" · ")[0]: dict(zip(cols, r)) for r in gar["table"]["rows"]}
    assert set(rows) == {"М100ММ54"}
    m = rows["М100ММ54"]
    assert (m["trips"], m["tons"], m["km"], m["liters"], m["fuel"]) == (1, 7.5, 120.0, 50.0, 3000.0)
    assert m["costs"] == 3000.0 and m["t_per_l"] == 0.15
    assert any(x["name"] == "garbage" for x in d.get("/reports").json()["items"])
    assert make_client("mech_a").get(f"/reports/garbage?month={month}").status_code == 404


def test_loaders_and_graders_take_only_engine_hours(world, make_client):
    from sqlalchemy import select
    from app.models import Vehicle, VehicleType
    with session_factory()() as db:
        t = db.scalar(select(VehicleType.id).where(VehicleType.code == "grader"))
        v = Vehicle(legal_entity_id=world.A, type_id=t, plate="Г100ГГ54", make="ДЗ", model="98")
        db.add(v)
        db.commit()
        vid = v.id
    d = make_client("dir_a")
    item = next(x for x in d.get("/readings/manual").json()["items"] if x["id"] == vid)
    assert item["hours_only"] is True
    assert d.get(f"/vehicles/{vid}").json()["type"]["hours_only"] is True
    r = d.post(f"/vehicles/{vid}/readings", {"kind": "km", "value": 100})
    assert r.status_code == 422 and r.json()["code"] == "hours_only"
    assert d.post(f"/vehicles/{vid}/readings", {"kind": "engine_hours", "value": 100}).status_code == 201
    res = d.post("/readings/manual", {"items": [{"vehicle_id": vid, "kind": "km", "value": 200}]}).json()
    assert res["saved"] == 0 and "моточасы" in res["results"][0]["message"]
    assert next(x for x in d.get("/readings/manual").json()["items"] if x["id"] == world.va1)["hours_only"] is False


def test_vehicle_costs_page_collects_everything(world, make_client):
    from tests.test_dossier import add_vehicle_doc
    day = TODAY.replace(day=1)
    d = make_client("dir_a")
    r = add_vehicle_doc(d, world.va1, kind="osago", issued_on=day.isoformat(), cost="12 500,50")
    assert r.status_code == 201, r.text
    assert r.json()["cost"] == 12500.5
    assert add_vehicle_doc(d, world.va1, kind="sts", cost="999").json()["cost"] is None        # у СТС стоимости нет
    with session_factory()() as db:
        from app.models import VehicleFinance
        db.add_all([FuelFill(legal_entity_id=world.A, vehicle_id=world.va1, fill_date=day, liters=Decimal("40"), amount=Decimal("2400")),
                    VehicleFinance(legal_entity_id=world.A, vehicle_id=world.va1, op_date=day, kind="expense", category="мойка", amount=Decimal("300"))])
        db.commit()
    res = d.get("/finance/vehicle-costs", params={"month": day.strftime("%Y-%m")}).json()
    cats = {c["key"]: c["amount"] for c in res["categories"]}
    assert cats["insurance"] == 12500.5 and cats["fuel"] == 2400.0 and cats["other"] == 300.0
    row = next(x for x in res["vehicles"] if x["vehicle_id"] == world.va1)
    assert row["liters"] == 40.0 and row["total"] == 15200.5 and res["full"] is True
    assert {x["cat"] for x in res["items"]} >= {"fuel", "insurance", "other"}
    # механик видит раздел, но деньги из других разделов — нет, только внесённые вручную
    m = make_client("mech_a").get("/finance/vehicle-costs", params={"month": day.strftime("%Y-%m")}).json()
    assert m["full"] is False and {c["key"]: c["amount"] for c in m["categories"]}["fuel"] == 0 and {x["cat"] for x in m["items"]} == {"other"}
    assert make_client("drv1").get("/finance/vehicle-costs").status_code == 403
