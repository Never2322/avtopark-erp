# -*- coding: utf-8 -*-
"""Главная и список техники: затраты по месяцам, ближайшее ТО, заказы, задачи; числа по категориям и статусам, срок ближайшего ТО в списке.
Проверяются суммы, права на деньги, разделение по юрлицам и то, что чужое в цифры не попадает."""
import datetime as dt

from sqlalchemy import update

from app.db import session_factory
from app.models import FuelFill, Vehicle
from tests.test_trade import customer, order
from app.common import org_today

TODAY = org_today()


def d(offset: int = 0) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def fill(entity: int, vehicle: int, day: dt.date, amount: str) -> None:
    with session_factory()() as db:
        db.add(FuelFill(legal_entity_id=entity, vehicle_id=vehicle, fill_date=day, liters=50, amount=amount))
        db.commit()


def month_start(back: int) -> dt.date:
    y, m = TODAY.year, TODAY.month - back
    while m < 1:
        y, m = y - 1, m + 12
    return dt.date(y, m, 1)


def add_to(c, vehicle: int, **kw):
    r = c.post("/maintenance", {"vehicle_id": vehicle, "kind": "to1", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- расходы на главной

def test_dashboard_expenses_by_month(world, make_client):
    fill(world.A, world.va1, month_start(0), "1000.50")
    fill(world.A, world.va2, month_start(0), "500")
    fill(world.A, world.va1, month_start(1), "2000")
    fill(world.A, world.va1, month_start(7), "99999")             # старше полугода: в график не попадает
    e = make_client("dir_a").get("/dashboard").json()["expenses"]
    assert len(e["months"]) == 6
    assert e["months"][-1]["fuel"] == 1500.5 and e["months"][-1]["total"] == 1500.5
    assert e["months"][-2]["fuel"] == 2000
    assert sum(m["total"] for m in e["months"]) == 3500.5
    assert e["month_total"] == 1500.5 and e["prev_total"] == 2000 and e["delta_pct"] == -25
    assert all(m["repair"] == 0 and m["other"] == 0 for m in e["months"])


def test_dashboard_expenses_need_money_right(world, make_client):
    fill(world.A, world.va1, month_start(0), "1000")
    assert make_client("dir_a").get("/dashboard").json()["expenses"] is not None
    for who in ("emp_a", "mech_a", "disp_a", "drv1"):
        assert make_client(who).get("/dashboard").json()["expenses"] is None, who


def test_dashboard_expenses_by_legal_entity(world, make_client):
    fill(world.A, world.va1, month_start(0), "1000")
    fill(world.B, world.vb1, month_start(0), "7000")
    assert make_client("dir_a").get("/dashboard").json()["expenses"]["month_total"] == 1000
    assert make_client("dir_b").get("/dashboard").json()["expenses"]["month_total"] == 7000
    admin = make_client("admin")
    assert admin.get("/dashboard").json()["expenses"]["month_total"] == 8000
    assert admin.get(f"/dashboard?legal_entity_id={world.B}").json()["expenses"]["month_total"] == 7000


def test_dashboard_expenses_without_previous_month(world, make_client):
    fill(world.A, world.va1, month_start(0), "300")
    e = make_client("dir_a").get("/dashboard").json()["expenses"]
    assert e["prev_total"] == 0 and e["delta_pct"] is None


# ---------------------------------------------------------------- ближайшее ТО

def test_dashboard_maintenance_nearest_first(world, make_client):
    mech = make_client("mech_a")
    add_to(mech, world.va1, due_date=d(10), work="Масло")
    near = add_to(mech, world.va2, due_date=d(3))
    add_to(mech, world.va3, due_date=d(90))
    m = make_client("dir_a").get("/dashboard").json()["maintenance"]
    assert [i["id"] for i in m["items"]][0] == near and len(m["items"]) == 3
    assert m["soon"] == 2                                         # «скоро» — в пределах двух недель: 3 и 10 дней; 90 дней ещё далеко
    first = m["items"][0]
    assert first["plate"] == "А002АА54" and first["kind_label"] and first["level"] == "soon" and "дн." in first["left_text"]


def test_dashboard_maintenance_skips_done_and_other_entity(world, make_client):
    mech = make_client("mech_a")
    done = add_to(mech, world.va1, due_date=d(1))
    assert mech.post(f"/maintenance/{done}/done", {}).status_code in (200, 201)
    m = make_client("dir_a").get("/dashboard").json()["maintenance"]
    assert done not in [i["id"] for i in m["items"]]               # выполненное из списка уходит (следующее ТО заводится новой записью)
    boss_b = make_client("dir_b")
    add_to(boss_b, world.vb1, due_date=d(2))
    a = make_client("dir_a").get("/dashboard").json()["maintenance"]
    assert all(i["vehicle_id"] != world.vb1 for i in a["items"])
    b = boss_b.get("/dashboard").json()["maintenance"]
    assert [i["vehicle_id"] for i in b["items"]] == [world.vb1]


def test_dashboard_maintenance_hidden_without_right(world, make_client):
    add_to(make_client("mech_a"), world.va1, due_date=d(2))
    assert make_client("drv1").get("/dashboard").json()["maintenance"] is None
    assert make_client("emp_a").get("/dashboard").json()["maintenance"] is None


# ---------------------------------------------------------------- заказы и задачи

def test_dashboard_orders_block(world, make_client):
    boss = make_client("dir_a")
    assert boss.get("/dashboard").json()["orders"]["active"] == 0
    cust = customer(boss)
    for _ in range(3):
        assert order(world, boss, cust).status_code == 201
    o = make_client("dir_a").get("/dashboard").json()["orders"]
    assert o["active"] == 3 and len(o["days"]) == 14 and o["days"][-1] == 3 and o["week"] == 3 and o["prev_week"] == 0
    assert make_client("dir_b").get("/dashboard").json()["orders"]["active"] == 0     # чужое юрлицо
    assert make_client("drv1").get("/dashboard").json()["orders"] is None


def test_dashboard_reminders_block(world, make_client):
    add_to(make_client("mech_a"), world.va1, due_date=d(-1))
    r = make_client("mech_a").get("/dashboard").json()["reminders"]
    assert r["total"] >= 1 and r["due"] >= 1 and len(r["items"]) <= 5
    assert r["items"][0]["level"] == "due" and r["items"][0]["url"].startswith("/vehicles/")
    assert make_client("drv1").get("/dashboard").json()["reminders"] is None


# ---------------------------------------------------------------- список техники

def test_vehicle_list_facets(world, make_client):
    with session_factory()() as db:
        db.execute(update(Vehicle).where(Vehicle.id == world.va3).values(type_id=world.hours_type_id))
        db.execute(update(Vehicle).where(Vehicle.id == world.va2).values(status="in_repair"))
        db.commit()
    boss = make_client("dir_a")
    r = boss.get("/vehicles").json()
    cats, sts = r["facets"]["categories"], r["facets"]["statuses"]
    assert sum(cats.values()) == r["total"] == 3 and sum(sts.values()) == 3
    assert sts == {"in_work": 2, "in_repair": 1}
    # выбранная категория сужает список и статусы, но сами категории остаются полными
    r = boss.get("/vehicles", params={"category": "special"}).json()
    assert r["total"] == 1 and r["items"][0]["id"] == world.va3
    assert sum(r["facets"]["categories"].values()) == 3 and sum(r["facets"]["statuses"].values()) == 1
    # выбранный статус сужает список и категории, но сами статусы остаются полными
    r = boss.get("/vehicles", params={"status": "in_repair"}).json()
    assert r["total"] == 1 and sum(r["facets"]["categories"].values()) == 1 and sum(r["facets"]["statuses"].values()) == 3
    # чужое юрлицо в числа не попадает
    only_b = make_client("dir_b").get("/vehicles").json()
    assert only_b["total"] == 1 and sum(only_b["facets"]["categories"].values()) == 1
    assert boss.get("/vehicles", params={"q": "нет такой машины"}).json()["facets"] == {"categories": {}, "statuses": {}}


def test_vehicle_list_facets_follow_search_and_archive(world, make_client):
    boss = make_client("dir_a")
    r = boss.get("/vehicles", params={"q": "А001"}).json()
    assert r["total"] == 1 and sum(r["facets"]["categories"].values()) == 1
    assert boss.post(f"/vehicles/{world.va2}/archive", {"reason": "тест"}).status_code in (200, 204)
    assert boss.get("/vehicles").json()["facets"]["categories"] == {"truck": 2}
    assert sum(boss.get("/vehicles", params={"archived": True}).json()["facets"]["statuses"].values()) == 1


def test_vehicle_list_next_service(world, make_client):
    mech = make_client("mech_a")
    add_to(mech, world.va1, due_date=d(30))
    add_to(mech, world.va1, due_date=d(10))
    add_to(mech, world.va2, due_km=5000)                          # срок по пробегу: даты нет
    rows = {x["id"]: x for x in make_client("dir_a").get("/vehicles").json()["items"]}
    assert rows[world.va1]["next_service"] == d(10)
    assert rows[world.va2]["next_service"] is None and rows[world.va3]["next_service"] is None
    # без права смотреть ТО срок в списке не показываем
    own = make_client("drv1").get("/vehicles").json()["items"]
    assert own and all(x["next_service"] is None for x in own)
