# -*- coding: utf-8 -*-
"""Оплата работы «за тонну» (мусоровозы): водителю с таким тарифом заработок считается по тоннам из талонов весов, а не по часам.
Тариф ставит директор, ставка берётся из карточки сотрудника на момент расчёта, талон без тоннажа пока не оплачен."""
import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.common import org_today, utcnow
from app.db import session_factory
from app.models import WeighTicket

TODAY = org_today()
MONTH = TODAY.strftime("%Y-%m")
FIRST = TODAY.replace(day=1)


def fields(r):
    j = r.json()
    return (j["detail"] if "detail" in j else j)["fields"]


def mk_tariff(c, name, kind, unit, rate):
    r = c.post("/tariffs", {"name": name, "kind": kind, "unit": unit, "rate": str(rate)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def assign(c, eid, work=None, repair=None):
    r = c.put(f"/payroll/employees/{eid}/tariffs", {"work_tariff_id": work, "repair_tariff_id": repair})
    assert r.status_code == 200, r.text
    return r.json()


def ticket(world, driver, tons, day=None, vehicle=None, entity=None, archived=False):
    """Талон весов вставлен напрямую: чтение фото здесь не проверяется (это в test_tonnage)."""
    with session_factory()() as db:
        t = WeighTicket(legal_entity_id=entity or world.A, vehicle_id=vehicle or world.va1, driver_id=driver, ticket_date=day or FIRST,
                        tons=Decimal(str(tons)) if tons is not None else None, tons_source="manual" if tons is not None else "")
        if archived:
            t.archived_at = utcnow()
        db.add(t)
        db.commit()
        return t.id


def earned(c):
    return c.get("/driver/home").json()["earned"]


def ton_driver(world, make_client, rate=600):
    d = make_client("dir_a")
    tid = mk_tariff(d, "Мусоровоз: за тонну", "work", "ton", rate)
    assign(d, world.d1, work=tid)
    return d, tid


# ---------------------------------------------------------------- тариф

def test_director_can_create_ton_tariff_only_for_work(world, make_client):
    d = make_client("dir_a")
    r = d.post("/tariffs", {"name": "Мусоровоз: за тонну", "kind": "work", "unit": "ton", "rate": "650"})
    assert r.status_code == 201 and r.json()["unit"] == "ton"
    ton_id = r.json()["id"]
    # ремонт за тонну не бывает, заказчику тариф «за тонну» тоже можно (как и раньше)
    r = d.post("/tariffs", {"name": "Ремонт за тонну", "kind": "repair", "unit": "ton", "rate": "100"})
    assert r.status_code == 422 and "unit" in fields(r)
    assert d.post("/tariffs", {"name": "Не так", "kind": "work", "unit": "km", "rate": "10"}).status_code == 422
    assert d.post("/tariffs", {"name": "Заказчик за тонну", "kind": "customer", "unit": "ton", "rate": "900"}).status_code == 201
    # назначить водителю можно
    assert assign(d, world.d1, work=ton_id)["work_tariff_id"] == ton_id
    # а как тариф ремонта нельзя
    assert d.put(f"/payroll/employees/{world.d1}/tariffs", {"repair_tariff_id": ton_id}).status_code == 422


def test_ton_tariff_is_not_offered_for_hours_entry(world, make_client):
    d = make_client("dir_a")
    mk_tariff(d, "Мусоровоз: за тонну", "work", "ton", 600)
    hour = mk_tariff(d, "Работа, час", "work", "hour", 400)
    ids = [x["id"] for x in make_client("disp_a").get("/options/pay_tariff").json()["items"]]
    assert ids == [hour]                                        # в «Внести часы» тариф за тонну не выбирается


# ---------------------------------------------------------------- расчёт по талонам

def test_driver_earns_by_tons_from_weigh_tickets(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    drv = make_client("drv1")
    ticket(world, world.d1, "11.82")
    ticket(world, world.d1, "9.5")
    e = earned(drv)
    assert e["by_tons"] is True and e["tons"] == 21.32 and e["tons_rate"] == 600 and e["tons_pending"] == 0
    assert e["tons_amount"] == 12792.0 and e["work"] == 12792.0 and e["total"] == 12792.0
    assert e["tons_tariff"] == "Мусоровоз: за тонну"
    mine = drv.get("/payroll/mine").json()
    assert mine["totals"]["work"] == 12792.0 and mine["totals"]["total"] == 12792.0
    assert mine["tonnage"]["tons"] == 21.32 and mine["tonnage"]["tons_amount"] == 12792.0
    assert mine["tariffs"]["work"]["unit"] == "ton" and mine["tariffs"]["work"]["rate"] == 600


def test_ticket_without_tons_waits_and_archived_is_ignored(world, make_client):
    ton_driver(world, make_client, rate=500)
    drv = make_client("drv1")
    ticket(world, world.d1, "10")
    ticket(world, world.d1, None)                                # вес ещё не внесён: не оплачен, но виден как «ждёт»
    ticket(world, world.d1, "7", archived=True)                  # убранный талон не считается
    e = earned(drv)
    assert e["tons"] == 10 and e["tons_amount"] == 5000 and e["tons_pending"] == 1
    # вес внесли — талон оплачивается
    with session_factory()() as db:
        t = db.scalar(select(WeighTicket).where(WeighTicket.tons.is_(None)))
        t.tons, t.tons_source = Decimal("4.2"), "manual"
        db.commit()
    e = earned(drv)
    assert e["tons"] == 14.2 and e["tons_amount"] == 7100 and e["tons_pending"] == 0


def test_only_this_month_and_only_own_tickets(world, make_client):
    ton_driver(world, make_client, rate=100)
    drv = make_client("drv1")
    prev = FIRST - dt.timedelta(days=1)
    ticket(world, world.d1, "5", day=prev)                       # прошлый месяц
    ticket(world, world.d2, "30", vehicle=world.va2)             # чужой водитель
    ticket(world, world.d1, "3")
    assert earned(drv)["tons_amount"] == 300
    assert drv.get("/payroll/mine", params={"month": prev.strftime("%Y-%m")}).json()["totals"]["total"] == 500


def test_other_drivers_are_not_paid_by_tons(world, make_client):
    d, _ = ton_driver(world, make_client)
    ticket(world, world.d2, "12", vehicle=world.va2)             # у второго водителя тарифа «за тонну» нет
    other = earned(make_client("drv2"))
    assert other["by_tons"] is False and other["tons_amount"] == 0 and other["total"] == 0


def test_hours_of_ton_driver_are_recorded_but_not_paid(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    mech, drv = make_client("mech_a"), make_client("drv1")
    r = mech.post("/payroll/enter", {"employee_id": world.d1, "work_date": TODAY.isoformat(), "kind": "work", "hours": "9"})
    assert r.status_code == 201 and not r.json()["warnings"]    # «нет тарифа» не пугает: тариф есть, просто за тонны
    ticket(world, world.d1, "10")
    mine = drv.get("/payroll/mine").json()
    assert mine["totals"]["work_h"] == 9 and mine["totals"]["work"] == 6000
    item = mine["items"][0]
    assert item["amount"] == 0 and item["no_rate"] is False and item["rate_text"] == "за тонны"
    assert earned(drv)["no_rate"] == 0


def test_rate_comes_from_current_tariff(world, make_client):
    d, tid = ton_driver(world, make_client, rate=500)
    ticket(world, world.d1, "10")
    drv = make_client("drv1")
    assert earned(drv)["tons_amount"] == 5000
    assert d.patch(f"/tariffs/{tid}", {"rate": "700"}).status_code == 200
    assert earned(drv)["tons_amount"] == 7000                    # ставка не запоминается в талоне: меняйте с начала месяца


def test_mixed_with_repair_hours(world, make_client):
    d, tid = ton_driver(world, make_client, rate=600)
    assign(d, world.d1, work=tid, repair=mk_tariff(d, "Ремонт, час", "repair", "hour", 300))
    mech, drv = make_client("mech_a"), make_client("drv1")
    mech.post("/payroll/enter", {"employee_id": world.d1, "work_date": TODAY.isoformat(), "kind": "repair", "hours": "2", "description": "Замена шланга"})
    ticket(world, world.d1, "10")
    e = earned(drv)
    assert e["work"] == 6000 and e["repair"] == 600 and e["total"] == 6600


# ---------------------------------------------------------------- офис: карточка, табель, отчёты

def test_director_sees_tons_in_card_and_timesheet(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    ticket(world, world.d1, "10", day=FIRST)
    ticket(world, world.d1, "2.5", day=FIRST)
    ticket(world, world.d1, "4", day=FIRST + dt.timedelta(days=1))
    card = d.get(f"/employees/{world.d1}").json()["pay"]
    assert card["work_tariff"] == "Мусоровоз: за тонну"
    assert card["earned"]["tons"] == 16.5 and card["earned"]["tons_amount"] == 9900 and card["earned"]["total"] == 9900
    sheet = d.get("/timesheet", params={"month": MONTH}).json()
    row = next(r for r in sheet["rows"] if r["employee_id"] == world.d1)
    assert row["days"]["1"]["work"] == 7500 and row["days"]["1"]["tons"] == 12.5 and row["days"]["2"]["total"] == 2400
    assert row["totals"]["total"] == 9900 and row["worked_days"] == 2
    assert sheet["totals"]["work"] == 9900 and sheet["day_totals"]["1"] == 7500


def test_salary_report_includes_ton_pay(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    ticket(world, world.d1, "10")
    ticket(world, world.d1, None)
    j = d.get("/reports/salary", params={"month": MONTH}).json()
    cols = [c["label"] for c in j["table"]["columns"]]
    assert "Тонн" in cols
    row = dict(zip(cols, j["table"]["rows"][0]))
    assert row["Тонн"] == 10 and row["Работа, ₽"] == 6000 and row["К выплате, ₽"] == 6000
    tar = next(e for e in j["extras"] if e["title"] == "По тарифам")
    assert any(x[0] == "Мусоровоз: за тонну" and x[1] == "600 ₽/т" and x[-1] == 6000 for x in tar["rows"])
    assert "за тонну" in j["note"] and "без тоннажа (их 1)" in j["note"]


def test_day_report_includes_ton_pay(world, make_client):
    d, _ = ton_driver(world, make_client, rate=600)
    ticket(world, world.d1, "10", day=TODAY)
    r = d.get("/reports/day", params={"from": TODAY.isoformat()}).json()
    labels = [c["label"] for c in r["table"]["columns"]]
    row = dict(zip(labels, next(x for x in r["table"]["rows"] if x[0].startswith("Первый"))))
    assert row["Работа, ₽"] == 6000 and row["Всего, ₽"] == 6000 and row["Ставка"] == "600 ₽/т" and row["Техника"] == "А001АА54"


def test_entities_are_separated(world, make_client):
    """Талон чужого юрлица директор другой организации в суммах не видит."""
    d, _ = ton_driver(world, make_client, rate=600)
    ticket(world, world.d1, "10")
    ticket(world, world.d1, "5", entity=world.B, vehicle=world.vb1)
    card = d.get(f"/employees/{world.d1}").json()["pay"]["earned"]
    assert card["tons"] == 10 and card["tons_amount"] == 6000
    b = make_client("dir_b")
    sheet = b.get("/timesheet", params={"month": MONTH}).json()
    assert all(r["employee_id"] != world.d1 for r in sheet["rows"]) or sheet["totals"]["total"] == 0
