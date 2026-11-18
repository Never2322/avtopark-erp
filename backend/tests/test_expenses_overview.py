# -*- coding: utf-8 -*-
"""Раздел «Расходы»: счета на ремонт и запчасти (в том числе без машины), общие расходы, затраты по технике, топливо и штрафы лежат в одном списке
за месяц, и те же суммы идут на главную и в отчёт «Затраты»."""
import datetime as dt
import json

from app.common import org_today
from app.db import session_factory
from app.models import FuelFill, Fine

TODAY = org_today()
FIRST = TODAY.replace(day=1)
PREV = (FIRST - dt.timedelta(days=1)).replace(day=1)
MONTH, PREV_MONTH = FIRST.strftime("%Y-%m"), PREV.strftime("%Y-%m")


def bill(c, number="С-1", total="12000,00", day=None, **kw):
    body = {"kind": "uploaded", "number": number, "supplier_name": "ООО «Поставщик»", "bill_date": (day or FIRST).isoformat(), "total": total, **kw}
    r = c.call("POST", "/bills", data={"data": json.dumps(body, default=str)})
    assert r.status_code == 201, r.text
    return r.json()


def overview(c, month=MONTH, **params):
    r = c.get("/finance/expenses", params={"month": month, **params})
    assert r.status_code == 200, r.text
    return r.json()


def cats(j):
    return {x["key"]: x for x in j["categories"]}


def fuel(entity, vehicle, day, amount):
    with session_factory()() as db:
        db.add(FuelFill(legal_entity_id=entity, vehicle_id=vehicle, fill_date=day, liters=40, amount=amount))
        db.commit()


# ---------------------------------------------------------------- главное: счёт виден в расходах

def test_bill_without_vehicle_is_in_expenses_and_dashboard(world, make_client):
    acc, d = make_client("acc_a"), make_client("dir_a")
    bill(acc, "З-1", "7500,50")                                          # запчасти без привязки к машине
    j = overview(d)
    assert cats(j)["bills"]["amount"] == 7500.5 and cats(j)["bills"]["count"] == 1 and j["total"] == 7500.5
    row = j["items"][0]
    assert row["cat"] == "bills" and row["title"] == "Счёт № З-1" and row["amount"] == 7500.5 and row["vehicle"] == "" and row["link"].startswith("/bills/")
    dash = d.get("/dashboard").json()["expenses"]
    assert dash["month_total"] == 7500.5 and dash["months"][-1]["repair"] == 7500.5          # на главной та же сумма


def test_bill_with_vehicle_shows_machine_and_status(world, make_client):
    acc = make_client("acc_a")
    bill(acc, "М-1", "3000", vehicle_id=world.va1)
    row = overview(acc)["items"][0]
    assert row["vehicle"] != ""
    assert row["status"] == "unpaid"
    assert overview(acc)["bills_unpaid"] == 3000


def test_all_sources_add_up(world, make_client):
    acc, d = make_client("acc_a"), make_client("dir_a")
    bill(acc, "С-5", "10000")
    assert acc.post("/expenses", {"legal_entity_id": world.A, "op_date": FIRST.isoformat(), "category": "Мойка", "amount": "1200", "description": "три машины"}).status_code == 201
    fuel(world.A, world.va1, FIRST, "2500")
    assert d.post("/vehicle_finance", {"vehicle_id": world.va1, "op_date": FIRST.isoformat(), "kind": "expense", "category": "Мойка", "amount": "800"}).status_code == 201
    assert d.post("/vehicle_finance", {"vehicle_id": world.va1, "op_date": FIRST.isoformat(), "kind": "income", "category": "Аренда", "amount": "50000"}).status_code == 201
    with session_factory()() as db:
        db.add(Fine(legal_entity_id=world.A, vehicle_id=world.va1, fine_date=FIRST, amount=500, payer="company"))
        db.add(Fine(legal_entity_id=world.A, vehicle_id=world.va1, fine_date=FIRST, amount=900, payer="driver"))
        db.commit()
    j = overview(d)
    c = cats(j)
    assert (c["bills"]["amount"], c["misc"]["amount"], c["fuel"]["amount"], c["vehicle_finance"]["amount"], c["fines"]["amount"]) == (10000, 1200, 2500, 800, 500)
    assert j["total"] == 15000                                           # доход и штраф водителя в расходы не входят
    for k, v in c.items():                                               # строки списка сходятся с суммой по статье
        if v["counted"]:
            assert round(sum(r["amount"] for r in overview(d, cat=k)["items"]), 2) == v["amount"], k
    assert d.get("/dashboard").json()["expenses"]["month_total"] == 15000
    rows = j["items"]
    assert len({(r["cat"], r["id"]) for r in rows}) == len(rows)         # у каждой строки свой ключ: по нему страница различает строки


def test_repair_records_are_shown_but_not_summed(world, make_client):
    d = make_client("dir_a")
    assert d.post("/vehicle_repairs", {"vehicle_id": world.va1, "repair_date": FIRST.isoformat(), "title": "Замена масла", "cost": "4500"}).status_code == 201
    j = overview(d)
    assert cats(j)["repairs"]["amount"] == 4500 and cats(j)["repairs"]["counted"] is False and j["total"] == 0
    assert any(r["cat"] == "repairs" and not r["counted"] for r in j["items"])


def test_archived_other_month_and_comparison(world, make_client):
    acc, d = make_client("acc_a"), make_client("dir_a")
    gone = bill(acc, "С-9", "9999")
    assert acc.post(f"/bills/{gone['id']}/void", {"reason": "Внесён дважды"}).status_code == 200
    bill(acc, "С-10", "2000", day=PREV)
    bill(acc, "С-11", "3000")
    j = overview(d)
    assert j["total"] == 3000 and j["prev_total"] == 2000 and j["delta_pct"] == 50
    assert [r["title"] for r in j["items"]] == ["Счёт № С-11"]
    assert overview(d, PREV_MONTH)["total"] == 2000


def test_filter_by_category(world, make_client):
    acc, d = make_client("acc_a"), make_client("dir_a")
    bill(acc, "С-12", "1000")
    fuel(world.A, world.va1, FIRST, "400")
    j = overview(d, cat="fuel")
    assert [r["cat"] for r in j["items"]] == ["fuel"] and j["found"] == 1
    assert j["total"] == 1400 and cats(j)["bills"]["count"] == 1                       # итог и счётчики всегда за весь месяц
    assert d.get("/finance/expenses", params={"cat": "nope"}).status_code == 422
    assert d.get("/finance/expenses", params={"month": "вчера"}).status_code == 422


# ---------------------------------------------------------------- права и юрлица

def test_only_people_with_money_right_see_expenses(world, make_client):
    bill(make_client("acc_a"), "С-20", "100")
    for who in ("dir_a", "acc_a", "admin"):
        assert make_client(who).get("/finance/expenses").status_code == 200, who
    for who in ("disp_a", "mech_a", "emp_a", "store_a", "drv1"):
        assert make_client(who).get("/finance/expenses").status_code == 403, who


def test_expenses_are_separated_by_legal_entity(world, make_client):
    bill(make_client("acc_a"), "С-30", "5000")
    fuel(world.B, world.vb1, FIRST, "700")
    assert overview(make_client("dir_a"))["total"] == 5000
    assert overview(make_client("dir_b"))["total"] == 700
    admin = make_client("admin")
    assert overview(admin)["total"] == 5700
    assert overview(admin, legal_entity_id=world.B)["total"] == 700
    assert make_client("dir_a").get("/finance/expenses", params={"legal_entity_id": world.B}).status_code == 403


# ---------------------------------------------------------------- отчёт «Затраты»

def test_costs_report_keeps_bills_without_vehicle(world, make_client):
    acc = make_client("acc_a")
    bill(acc, "С-40", "6000")
    bill(acc, "С-41", "1000", vehicle_id=world.va1)
    r = acc.get("/reports/costs", params={"from": FIRST.isoformat(), "to": TODAY.isoformat()}).json()
    labels = [c["label"] for c in r["table"]["columns"]]
    rows = {x[0]: dict(zip(labels, x)) for x in r["table"]["rows"]}
    assert rows["Без привязки к технике"]["Ремонт: счета, ₽"] == 6000 and rows["Без привязки к технике"]["Всего затрат, ₽"] == 6000
    assert r["table"]["totals"][labels.index("Ремонт: счета, ₽")] == 7000
