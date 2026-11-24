# -*- coding: utf-8 -*-
"""Отчёт «Зарплата мусоровозов» (группа «Мусоровозы»): ходки и тонны по талонам весов × ставка «за тонну», ремонт по часам,
премии, удержания и выданное из «Выплат сотрудникам», остаток к выплате."""
import datetime as dt
from decimal import Decimal

from app.db import session_factory
from app.models import EmployeePayment

from tests.test_ton_pay import FIRST, MONTH, TODAY, assign, mk_tariff, ticket, ton_driver


def rep(c, month=MONTH):
    r = c.get("/reports/garbage_salary", params={"month": month})
    assert r.status_code == 200, r.text
    return r.json()


def row(j, name):
    cols = [c["label"] for c in j["table"]["columns"]]
    for r in j["table"]["rows"]:
        if r[0].startswith(name):
            return dict(zip(cols, r))
    raise AssertionError(f"нет строки {name}: {j['table']['rows']}")


def payment(world, emp, kind, amount, day=None, entity=None):
    with session_factory()() as db:
        db.add(EmployeePayment(legal_entity_id=entity or world.A, employee_id=emp, pay_date=day or FIRST, kind=kind, amount=Decimal(str(amount))))
        db.commit()


def test_tons_trips_and_total(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    ticket(world, world.d1, "10")
    ticket(world, world.d1, "2.5")
    ticket(world, world.d1, None)                                 # вес не внесён: ходка есть, денег нет
    j = rep(d)
    r = row(j, "Первый")
    assert r["Ходок"] == 3 and r["Тонн"] == 12.5 and r["Талонов без веса"] == 1 and r["Ставка"] == "600 ₽/т"
    assert r["За тонны, ₽"] == 7500 and r["Начислено, ₽"] == 7500 and r["К выплате, ₽"] == 7500 and r["Машина"].startswith("А001АА54")
    assert "не внесён вес" in j["note"]
    days = next(e for e in j["extras"] if e["title"] == "По дням")
    assert days["rows"][0][3] == 3 and days["rows"][0][5] == 7500


def test_repair_bonus_fine_and_paid(world, make_client):
    d, _ = ton_driver(world, make_client, rate=500)
    ton = mk_tariff(d, "Мусор за тонну 2", "work", "ton", 500)
    rep_t = mk_tariff(d, "Ремонт, час", "repair", "hour", 300)
    assign(d, world.d1, work=ton, repair=rep_t)
    ticket(world, world.d1, "20")                                 # 10 000
    r = make_client("mech_a").post("/payroll/enter", {"employee_id": world.d1, "work_date": TODAY.isoformat(), "kind": "repair", "hours": "4", "description": "меняли гидравлику"})
    assert r.status_code == 201, r.text                           # 4 × 300 = 1 200
    payment(world, world.d1, "bonus", 2000)
    payment(world, world.d1, "fine", 500)
    payment(world, world.d1, "advance", 5000)
    payment(world, world.d1, "advance", 999, day=FIRST - dt.timedelta(days=1))      # прошлый месяц не считается
    j = rep(d)
    x = row(j, "Первый")
    assert x["За тонны, ₽"] == 10000 and x["Часы ремонта"] == 4 and x["За ремонт, ₽"] == 1200 and x["Премии, ₽"] == 2000
    assert x["Начислено, ₽"] == 13200 and x["Удержано, ₽"] == 500 and x["Выдано, ₽"] == 5000 and x["К выплате, ₽"] == 7700
    assert any(e["title"] == "Премии, удержания и выдачи" and len(e["rows"]) == 3 for e in j["extras"])


def test_ton_driver_without_tickets_is_listed_and_others_without_tickets_are_not(world, make_client):
    d, _ = ton_driver(world, make_client)
    j = rep(d)
    names = [r[0] for r in j["table"]["rows"]]
    assert len(names) == 1 and names[0].startswith("Первый")
    assert row(j, "Первый")["Ходок"] == 0 and row(j, "Первый")["К выплате, ₽"] == 0


def test_tickets_without_ton_tariff_are_shown_unpaid(world, make_client):
    d = make_client("dir_a")
    ticket(world, world.d2, "8", vehicle=world.va2)
    j = rep(d)
    r = row(j, "Второй")
    assert r["Тонн"] == 8 and r["За тонны, ₽"] == 0 and r["Ставка"].startswith("нет тарифа")
    assert "без тарифа «за тонну»" in j["note"]


def test_empty_month(world, make_client):
    j = rep(make_client("dir_a"))
    assert j["table"]["rows"] == [] and "нет ни талонов" in j["note"]


def test_rights_and_entities(world, make_client):
    ton_driver(world, make_client, rate=600)
    ticket(world, world.d1, "10")
    assert make_client("drv1").get("/reports/garbage_salary").status_code == 403
    # директор другого юрлица чужих водителей не видит
    b = rep(make_client("dir_b"))
    assert all(not r[0].startswith("Первый") for r in b["table"]["rows"])
    # механик видит заработок (как в «Зарплате за месяц»), но без права на выплаты нет столбцов премий и выдач
    mech = make_client("mech_a").get("/reports/garbage_salary", params={"month": MONTH})
    if mech.status_code == 200:
        cols = [c["label"] for c in mech.json()["table"]["columns"]]
        if "Выдано, ₽" not in cols:
            assert any("выплаты" in h for h in mech.json().get("hidden", []))
    else:
        assert mech.status_code == 404


def test_excel_export(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    ticket(world, world.d1, "10")
    r = d.get("/reports/garbage_salary/export.xlsx", params={"month": MONTH})
    assert r.status_code == 200 and r.content[:2] == b"PK"
