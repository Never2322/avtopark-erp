# -*- coding: utf-8 -*-
"""Отчёты: права и деньги, расчёты (пробег, топливо, затраты без двойного счёта, ремонты, простои, расход по листам, заказы),
юрлица, ошибки периода, выгрузка в Excel и CSV."""
import datetime as dt
import io
import re
import zipfile
from decimal import Decimal

from sqlalchemy import select

from app.db import session_factory
from app.models import (AuditLog, Counterparty, Fine, FuelFill, MiscExpense, Order, Rental, RepairBill, ServiceRequest, StockItem, StockMove, Trip,
                        Vehicle, VehicleReading, VehicleSpecs, VehicleStatusHistory, VehicleType, Waybill, Warehouse)
from app.services.xlsx import serial, xlsx_book

UTC = dt.timezone.utc
MAY = "2026-05"
P_MAY = "month=2026-05"
RANGE_MAY = "from=2026-05-01&to=2026-05-31"


def at(y, m, d, h=12):
    return dt.datetime(y, m, d, h, tzinfo=UTC)


def vehicle(db, entity, plate, make="ЗИЛ", created=None):
    t = db.scalar(select(VehicleType).where(VehicleType.code == "truck_dump"))
    v = Vehicle(legal_entity_id=entity, type_id=t.id, plate=plate, make=make, model="Т")
    if created:
        v.created_at = created
    db.add(v)
    db.flush()
    return v


def rep(c, name, query=""):
    r = c.get(f"/reports/{name}" + ("?" + query if query else ""))
    assert r.status_code == 200, r.text
    return r.json()


def row_of(res, plate):
    rows = [r for r in res["table"]["rows"] if r[0].startswith(plate)]
    assert len(rows) == 1, (plate, [r[0] for r in res["table"]["rows"]])
    return dict(zip([c["key"] for c in res["table"]["columns"]], rows[0]))


def costs_of(res):
    """Таблица «Затраты по статьям» из отчёта «Итоги и затраты по технике» в виде отдельного отчёта."""
    return {"table": next(e for e in res["extras"] if e["title"] == "Затраты по статьям")}


def keys(res):
    return [c["key"] for c in res["table"]["columns"]]


def seed_may(world):
    """Машина «К100» в мае 2026: топливо, ремонт со счётом и без, штрафы, заказы, аренда, склад, рейсы."""
    with session_factory()() as db:
        v = vehicle(db, world.A, "К100КК54", created=at(2026, 1, 1))
        cust = Counterparty(name="ООО Заказчик", kind="customer")
        db.add(cust)
        db.flush()
        a = world.A
        db.add_all([
            FuelFill(legal_entity_id=a, vehicle_id=v.id, fill_date=dt.date(2026, 5, 5), liters=Decimal("50"), amount=Decimal("4000"), odometer=1000, full_tank=True),
            FuelFill(legal_entity_id=a, vehicle_id=v.id, fill_date=dt.date(2026, 5, 10), liters=Decimal("20"), amount=Decimal("1500"), full_tank=False),
            FuelFill(legal_entity_id=a, vehicle_id=v.id, fill_date=dt.date(2026, 5, 20), liters=Decimal("60"), amount=Decimal("4500"), odometer=1400, full_tank=True),
            # заправка в архиве и заправка не в мае не считаются
            FuelFill(legal_entity_id=a, vehicle_id=v.id, fill_date=dt.date(2026, 5, 21), liters=Decimal("999"), amount=Decimal("99999"), archived_at=at(2026, 6, 1)),
            FuelFill(legal_entity_id=a, vehicle_id=v.id, fill_date=dt.date(2026, 4, 30), liters=Decimal("888"), amount=Decimal("88888")),
        ])
        db.add(VehicleSpecs(vehicle_id=v.id, fuel_norm=Decimal("16"), tank_liters=Decimal("300")))
        r_nobill = ServiceRequest(legal_entity_id=a, number=901, vehicle_id=v.id, title="Подтяжка", status="closed", cost_actual=Decimal("3000"), closed_at=at(2026, 5, 7))
        r_bill = ServiceRequest(legal_entity_id=a, number=902, vehicle_id=v.id, title="КПП", status="closed", cost_actual=Decimal("9999"), closed_at=at(2026, 5, 8))
        db.add_all([r_nobill, r_bill])
        db.flush()
        db.add_all([
            RepairBill(legal_entity_id=a, kind="uploaded", number="Б-1", bill_date=dt.date(2026, 5, 6), vehicle_id=v.id, total=Decimal("10000"), vat=Decimal("0")),
            RepairBill(legal_entity_id=a, kind="uploaded", number="Б-2", bill_date=dt.date(2026, 5, 8), vehicle_id=v.id, request_id=r_bill.id, total=Decimal("2000"), vat=Decimal("0")),
            RepairBill(legal_entity_id=a, kind="uploaded", number="Б-3", bill_date=dt.date(2026, 5, 9), vehicle_id=v.id, total=Decimal("77777"), vat=Decimal("0"), archived_at=at(2026, 6, 1)),
            Fine(legal_entity_id=a, vehicle_id=v.id, fine_date=dt.date(2026, 5, 11), amount=Decimal("700"), payer="company", article="=SUM(A1)"),
            Fine(legal_entity_id=a, vehicle_id=v.id, fine_date=dt.date(2026, 5, 12), amount=Decimal("300"), payer="driver", article="ст. 12"),
            Order(legal_entity_id=a, year=2026, number=1, order_date=dt.date(2026, 5, 9), status="done", customer_id=cust.id, vehicle_id=v.id, amount=Decimal("20000"), vat_pct=20),
            Order(legal_entity_id=a, year=2026, number=2, order_date=dt.date(2026, 5, 9), status="new", customer_id=cust.id, vehicle_id=v.id, amount=Decimal("5000")),
            Order(legal_entity_id=a, year=2026, number=3, order_date=dt.date(2026, 5, 9), status="cancelled", customer_id=cust.id, vehicle_id=v.id, amount=Decimal("700")),
            Rental(legal_entity_id=a, vehicle_id=v.id, direction="out", start_date=dt.date(2026, 5, 10), end_date=dt.date(2026, 5, 12), rate=Decimal("1000")),
            Rental(legal_entity_id=a, vehicle_id=v.id, direction="in", start_date=dt.date(2026, 5, 20), end_date=dt.date(2026, 5, 21), rate=Decimal("500")),
            MiscExpense(legal_entity_id=a, op_date=dt.date(2026, 5, 15), category="Связь", amount=Decimal("1234.50"), description="Интернет"),
            Trip(legal_entity_id=a, vehicle_id=v.id, trip_date=dt.date(2026, 5, 3), route="Склад — объект", cargo="Щебень", km=40),
            Trip(legal_entity_id=a, vehicle_id=v.id, trip_date=dt.date(2026, 5, 4), route="Объект — склад", cargo="", km=40),
        ])
        wh = Warehouse(legal_entity_id=a, name="Гараж")
        item = StockItem(legal_entity_id=a, name="Фильтр", name_key="фильтр", unit="шт")
        db.add_all([wh, item])
        db.flush()
        db.add(StockMove(legal_entity_id=a, move_date=dt.date(2026, 5, 13), kind="issue", item_id=item.id, warehouse_id=wh.id, qty=Decimal("2"),
                         price=Decimal("150"), vehicle_id=v.id))
        for when, val in ((at(2026, 4, 20), 5000), (at(2026, 5, 15), 5400), (at(2026, 5, 28), 5650)):
            db.add(VehicleReading(vehicle_id=v.id, kind="km", value=Decimal(val), recorded_at=when))
        db.commit()
        return v.id


# ---------------------------------------------------------------- права

def test_list_depends_on_rights(make_client, world):
    names = lambda who: {x["name"] for x in make_client(who).get("/reports").json()["items"]}          # noqa: E731
    assert names("dir_a") == {"month", "mileage", "fuel", "repairs", "waybill_fuel", "downtime", "orders", "salary", "garbage_salary", "tonnage", "garbage", "day"}
    assert names("acc_a") >= {"month", "repairs", "orders", "mileage"}
    disp = names("disp_a")
    assert {"month", "mileage", "fuel", "downtime", "orders"} <= disp and "repairs" not in disp
    mech = names("mech_a")
    assert "mileage" in mech and "fuel" in mech and not ({"repairs", "orders"} & mech)
    for who in ("drv1", "store_a", "emp_a"):
        assert make_client(who).get("/reports").status_code == 403
    can = make_client("dir_a").get("/reports").json()["can"]
    assert can == {"export": True, "money": True}
    assert make_client("mech_a").get("/reports").json()["can"] == {"export": False, "money": False}


def test_report_without_right_is_not_found(make_client, world):
    disp = make_client("disp_a")
    assert disp.get("/reports/costs").status_code == 404
    assert disp.get("/reports/repairs").status_code == 404
    assert disp.get("/reports/nope").status_code == 404
    assert make_client("mech_a").get("/reports/orders").status_code == 404
    assert make_client("drv1").get("/reports/month").status_code == 403


def test_money_is_hidden_without_right(make_client, world):
    seed_may(world)
    boss, disp = make_client("dir_a"), make_client("disp_a")
    full, plain = rep(boss, "month", P_MAY), rep(disp, "month", P_MAY)
    assert "cost" in keys(full) and "income" not in keys(full) and full["hidden"] == []
    assert "cost" not in keys(plain)
    assert any("рублях" in h for h in plain["hidden"])
    assert row_of(plain, "К100")["km"] == row_of(full, "К100")["km"]                  # остальное одинаково
    fuel_plain = rep(disp, "fuel", RANGE_MAY)
    assert "amount" not in keys(fuel_plain) and any("Сумма заправок" in h for h in fuel_plain["hidden"])
    assert "amount" in keys(rep(boss, "fuel", RANGE_MAY))
    # деньги не просачиваются ни в таблицы, ни в дополнительные листы
    assert not any("Сумма" in c["label"] or "₽" in c["label"] for e in plain["extras"] for c in e["columns"])
    assert {e["title"] for e in plain["extras"]} >= {"Штрафы за месяц", "Рейсы за месяц"}
    assert "Общие расходы за месяц" not in {e["title"] for e in plain["extras"]}
    assert "Общие расходы за месяц" in {e["title"] for e in full["extras"]}


# ---------------------------------------------------------------- расчёты

def test_mileage_uses_boundaries_and_corrections(make_client, world):
    with session_factory()() as db:
        v = vehicle(db, world.A, "П777ПП54", created=at(2025, 1, 1))
        rows = [VehicleReading(vehicle_id=v.id, kind="km", value=Decimal(val), recorded_at=when)
                for when, val in ((at(2026, 1, 10), 1000), (at(2026, 2, 10), 1500), (at(2026, 3, 10), 1800))]
        db.add_all(rows)
        db.commit()
        feb = rows[1].id
        vid = v.id
    boss = make_client("dir_a")
    q = "from=2026-02-01&to=2026-03-31"
    res = rep(boss, "mileage", q)
    row = row_of(res, "П777")
    assert (row["km_from"], row["km_to"], row["km"], row["km_days"], row["km_avg"]) == (1000.0, 1800.0, 800.0, 2, 400.0)
    assert res["period"]["kind"] == "range" and res["chart"]["unit"] == "км" and len(res["chart"]["labels"]) == 59
    daily = next(e for e in res["extras"] if e["title"].startswith("Пробег по дням"))
    feb10 = next(r for r in daily["rows"] if r[0] == "2026-02-10")
    assert feb10[1] == 500.0 and feb10[2] == 1 and daily["totals"][1] == 800.0
    with session_factory()() as db:                                               # исправление заменяет показание, старое остаётся в журнале
        db.add(VehicleReading(vehicle_id=vid, kind="km", value=Decimal(1400), recorded_at=at(2026, 2, 10), corrects_id=feb, review_state="ok"))
        db.commit()
    assert row_of(rep(boss, "mileage", q), "П777")["km"] == 800.0
    assert row_of(rep(boss, "mileage", "from=2026-02-01&to=2026-02-28"), "П777")["km"] == 400.0
    with session_factory()() as db:                                               # показание на проверке не учитывается
        db.add(VehicleReading(vehicle_id=vid, kind="km", value=Decimal(9999), recorded_at=at(2026, 3, 20), review_state="pending"))
        db.commit()
    assert row_of(rep(boss, "mileage", q), "П777")["km"] == 800.0
    long = rep(boss, "mileage", "from=2025-01-01&to=2026-03-31")                  # длинный период: без таблицы по дням
    assert row_of(long, "П777")["km"] == 800.0 and not long["extras"]


def test_fuel_report(make_client, world):
    seed_may(world)
    res = rep(make_client("dir_a"), "fuel", RANGE_MAY)
    row = row_of(res, "К100")
    assert (row["count"], row["liters"], row["amount"]) == (3, 130.0, 10000.0)             # архив и апрель не считаются
    assert row["per100"] == 20.0 and row["norm"] == 16.0 and row["dev"] == 25.0           # (20 + 60) л на 400 км между полными баками
    assert res["table"]["totals"][0] == "Итого" and res["table"]["totals"][1] == 3
    assert res["chart"]["labels"] == ["май 26"] and res["chart"]["values"] == [130.0]
    assert row_of(rep(make_client("dir_a"), "fuel", "from=2026-05-01&to=2026-05-09"), "К100")["per100"] is None


def test_costs_do_not_count_twice(make_client, world):
    vid = seed_may(world)
    with session_factory()() as db:
        from app.models import DossierDoc, VehicleFinance
        v = db.get(Vehicle, vid)
        db.add_all([DossierDoc(vehicle_id=vid, kind="osago", title="ОСАГО", issued_on=dt.date(2026, 5, 3), cost=Decimal("8000")),
                    DossierDoc(vehicle_id=vid, kind="osago", title="старый", issued_on=dt.date(2025, 5, 3), cost=Decimal("7000")),
                    VehicleFinance(legal_entity_id=v.legal_entity_id, vehicle_id=vid, op_date=dt.date(2026, 5, 4), kind="expense", category="мойка", amount=Decimal("500")),
                    VehicleFinance(legal_entity_id=v.legal_entity_id, vehicle_id=vid, op_date=dt.date(2026, 5, 4), kind="income", amount=Decimal("9999"))])
        # счёт, по которому деталь пришла на склад: в затраты машины идёт выдача со склада, а не сам счёт
        b = RepairBill(legal_entity_id=v.legal_entity_id, kind="uploaded", number="Б-С", bill_date=dt.date(2026, 5, 7), vehicle_id=vid, total=Decimal("450"), vat=Decimal("0"))
        db.add(b)
        db.flush()
        mv = db.scalar(select(StockMove).where(StockMove.kind == "issue"))
        db.add(StockMove(legal_entity_id=v.legal_entity_id, move_date=dt.date(2026, 5, 7), kind="receipt", item_id=mv.item_id, warehouse_id=mv.warehouse_id,
                         qty=Decimal("3"), price=Decimal("150"), bill_id=b.id))
        db.commit()
    res = costs_of(rep(make_client("dir_a"), "month", P_MAY))
    row = row_of(res, "К100")
    assert row["fuel"] == 10000.0
    assert row["bills_net"] == 12000.0                                           # счета Б-1 и Б-2; архивный Б-3 и складской Б-С не считаются
    assert row["requests"] == 3000.0                                             # только обращение без счёта: у второго есть счёт Б-2
    assert row["fines"] == 700.0                                                 # штраф, который платит водитель, не затраты компании
    assert row["stock"] == 300.0                                                 # детали со склада теперь в затратах
    assert row["insurance"] == 8000.0 and row["other"] == 500.0                  # полис за период и прочая затрата; доход не затрата
    assert row["cost"] == 10000 + 12000 + 3000 + 700 + 300 + 8000 + 500
    labels = [c["label"] for c in res["table"]["columns"]]
    assert not any("Доход" in x or "Аренда" in x or "1 км" in x for x in labels)
    assert labels.index("Детали со склада, ₽") == labels.index("Штрафы (платит компания), ₽") + 1
    assert costs_of(rep(make_client("acc_a"), "month", P_MAY))["table"]["rows"]


def test_repairs_by_month(make_client, world):
    with session_factory()() as db:
        v = vehicle(db, world.A, "Р555РР54", created=at(2025, 1, 1))
        db.add_all([RepairBill(legal_entity_id=world.A, kind="uploaded", number="Р-1", bill_date=dt.date(2026, 1, 15), vehicle_id=v.id, total=Decimal("5000"), vat=Decimal("0")),
                    RepairBill(legal_entity_id=world.A, kind="uploaded", number="Р-2", bill_date=dt.date(2026, 3, 2), vehicle_id=v.id, total=Decimal("7000"), vat=Decimal("0")),
                    RepairBill(legal_entity_id=world.A, kind="uploaded", number="Р-3", bill_date=dt.date(2026, 3, 3), vehicle_id=v.id, total=Decimal("1"), vat=Decimal("0"), archived_at=at(2026, 4, 1)),
                    RepairBill(legal_entity_id=world.A, kind="uploaded", number="Р-4", bill_date=dt.date(2025, 3, 31), vehicle_id=v.id, total=Decimal("111"), vat=Decimal("0"))])
        req = ServiceRequest(legal_entity_id=world.A, number=950, vehicle_id=v.id, title="Тормоза", status="closed", cost_actual=Decimal("2500"), closed_at=at(2026, 2, 20))
        db.add(req)
        db.commit()
    res = rep(make_client("dir_a"), "repairs", "month=2026-03")
    row = row_of(res, "Р555")
    assert (row["m9"], row["m10"], row["m11"], row["sum"]) == (5000.0, 2500.0, 7000.0, 14500.0)    # Р-4 старше 12 месяцев, Р-3 в архиве
    assert res["chart"]["unit"] == "₽" and res["table"]["totals"][-1] >= 14500.0


def test_downtime(make_client, world):
    with session_factory()() as db:
        v = vehicle(db, world.A, "Д321ДД54", created=at(2026, 3, 1, 0))
        db.add_all([VehicleStatusHistory(vehicle_id=v.id, from_status="in_work", status="in_repair", reason="Замена КПП", changed_at=at(2026, 3, 10, 12)),
                    VehicleStatusHistory(vehicle_id=v.id, from_status="in_repair", status="in_work", reason="", changed_at=at(2026, 3, 15, 12))])
        db.commit()
    res = rep(make_client("dir_a"), "downtime", "from=2026-03-01&to=2026-03-31")
    row = row_of(res, "Д321")
    assert row["in_repair"] == 5.0 and row["repairs"] == 1 and row["longest"] == 5.0
    assert row["in_work"] == 25.7 and row["now"] == "На линии"                      # 9,5 суток до ремонта и 16,2 после
    assert row["ready"] == 83.7
    assert res["extras"][0]["title"] == "Периоды ремонта"
    ep = [r for r in res["extras"][0]["rows"] if r[0].startswith("Д321")][0]
    assert ep[3] == 5.0 and ep[4] == "Замена КПП" and ep[2] is not None
    cut = row_of(rep(make_client("dir_a"), "downtime", "from=2026-03-12&to=2026-03-12"), "Д321")      # ремонт идёт весь этот день
    assert cut["in_repair"] == 1.0 and cut["ready"] == 0.0
    before = row_of(rep(make_client("dir_a"), "downtime", "from=2026-02-01&to=2026-02-28"), "Д321")      # до добавления машины в программу
    assert before["in_work"] == 0.0 and before["ready"] is None


def test_open_repair_has_no_end(make_client, world):
    with session_factory()() as db:
        v = vehicle(db, world.A, "О100ОО54", created=at(2026, 3, 1, 0))
        db.add(VehicleStatusHistory(vehicle_id=v.id, from_status="in_work", status="in_repair", reason="Ждём деталь", changed_at=at(2026, 3, 20, 0)))
        v.status = "in_repair"
        db.commit()
    res = rep(make_client("dir_a"), "downtime", "from=2026-03-01&to=2026-03-31")
    row = row_of(res, "О100")
    assert row["now"] == "В ремонте" and row["in_repair"] > 10 and row["repairs"] == 1
    ep = [r for r in res["extras"][0]["rows"] if r[0].startswith("О100")][0]
    assert ep[2] is None and ep[4] == "Ждём деталь"                                  # конец не задан: ремонт идёт


def test_waybill_fuel(make_client, world):
    with session_factory()() as db:
        v = vehicle(db, world.A, "Л900ЛЛ54", created=at(2026, 1, 1))
        db.add(VehicleSpecs(vehicle_id=v.id, fuel_norm=Decimal("25")))
        base = dict(legal_entity_id=world.A, year=2026, vehicle_id=v.id, driver_id=world.d1, work_date=dt.date(2026, 5, 10), status="closed")
        db.add_all([Waybill(number=1, start_km=Decimal(1000), end_km=Decimal(1200), fuel_start_l=Decimal(50), fuel_added_l=Decimal(40), fuel_end_l=Decimal(30), **base),
                    Waybill(number=2, start_km=Decimal(1200), end_km=Decimal(1300), **base),              # без остатков топлива: только пробег
                    Waybill(number=3, **{**base, "status": "draft"}),
                    Waybill(number=4, start_km=Decimal(1), end_km=Decimal(9), **{**base, "work_date": dt.date(2026, 4, 30)})])
        db.commit()
    row = row_of(rep(make_client("dir_a"), "waybill_fuel", RANGE_MAY), "Л900")
    assert (row["n"], row["km"]) == (2, 300.0)
    assert (row["fact"], row["plan"], row["diff"], row["per100"]) == (60.0, 50.0, -10.0, 30.0)         # перерасход 10 л


def test_orders_by_customer(make_client, world):
    seed_may(world)
    with session_factory()() as db:
        other = Counterparty(name="ИП Второй", kind="customer")
        db.add(other)
        db.flush()
        db.add(Order(legal_entity_id=world.A, year=2026, number=10, order_date=dt.date(2026, 5, 2), status="done", customer_id=other.id, amount=Decimal("1000"), vat_pct=0))
        db.add(Order(legal_entity_id=world.B, year=2026, number=1, order_date=dt.date(2026, 5, 2), status="done", customer_id=other.id, amount=Decimal("555"), vat_pct=0))
        db.commit()
    res = rep(make_client("dir_a"), "orders", RANGE_MAY)
    first = row_of(res, "ООО Заказчик")
    assert (first["all"], first["done"], first["open"], first["cancelled"]) == (3, 1, 1, 1)
    assert (first["base"], first["vat"], first["total"]) == (20000.0, 4000.0, 24000.0)             # НДС 20 % сверху
    assert row_of(res, "ИП Второй")["base"] == 1000.0                                               # заказ другого юрлица не виден
    assert res["table"]["rows"][0][0] == "ООО Заказчик"                                             # по убыванию выручки
    plain = rep(make_client("disp_a"), "orders", RANGE_MAY)
    assert "base" not in keys(plain) and "total" not in keys(plain) and row_of(plain, "ООО Заказчик")["done"] == 1
    assert plain["chart"]["unit"] == "заказов"


def test_month_report_summary_and_extras(make_client, world):
    seed_may(world)
    res = rep(make_client("dir_a"), "month", P_MAY)
    row = row_of(res, "К100")
    assert row["km"] == 650.0 and row["liters"] == 130.0 and row["per100"] == 20.0
    assert row["cost"] == 26000.0 and row["trips"] == 2                       # та же сумма, что «Всего затрат» в «Затратах по статьям»
    assert costs_of(res)["table"]["rows"] and row_of(costs_of(res), "К100")["cost"] == row["cost"]
    titles = [e["title"] for e in res["extras"]]
    assert titles == ["Затраты по статьям", "Штрафы за месяц", "Рейсы за месяц", "Общие расходы за месяц"]                # обращений, созданных в мае, нет
    fines = res["extras"][1]
    assert [c["key"] for c in fines["columns"]][:2] == ["d", "v"] and len(fines["rows"]) == 2 and fines["totals"][4] == 1000.0
    trips = res["extras"][2]
    assert trips["totals"][-1] == 80
    assert res["extras"][3]["totals"][-1] == 1234.5


# ---------------------------------------------------------------- юрлица и периоды

def test_entities_are_separated(make_client, world):
    seed_may(world)
    with session_factory()() as db:
        vehicle(db, world.B, "Б200ББ54", created=at(2026, 1, 1))
        db.commit()
    a, b = make_client("dir_a"), make_client("dir_b")
    assert [r[0] for r in rep(a, "month", P_MAY)["table"]["rows"] if r[0].startswith("Б200")] == []
    plates_b = [r[0][:4] for r in rep(b, "month", P_MAY)["table"]["rows"]]
    assert "Б200" in plates_b and "К100" not in plates_b
    assert a.get(f"/reports/month?{P_MAY}&legal_entity_id={world.B}").status_code == 403
    assert a.get(f"/reports/month/export.xlsx?{P_MAY}&legal_entity_id={world.B}").status_code == 403
    narrowed = [r[0][:4] for r in rep(make_client("admin"), "month", f"{P_MAY}&legal_entity_id={world.B}")["table"]["rows"]]
    assert "Б200" in narrowed and "К100" not in narrowed
    both = rep(make_client("admin"), "month", P_MAY)
    assert {"К100", "Б200"} <= {r[0][:4] for r in both["table"]["rows"]}


def test_bad_periods_are_rejected(make_client, world):
    boss = make_client("dir_a")
    for url, field in (("/reports/month?month=2026-13", "month"), ("/reports/month?month=abc", "month"), ("/reports/mileage?from=2026-5", "from"),
                       ("/reports/fuel?from=вчера", "from"), ("/reports/fuel?from=2026-05-10&to=2026-05-01", "to"), ("/reports/fuel?from=2015-01-01&to=2026-05-01", "to")):
        r = boss.get(url)
        assert r.status_code == 422 and r.json()["code"] == "bad_period" and r.json()["field"] == field, (url, r.text)
    assert boss.get("/reports/fuel").status_code == 200                                              # по умолчанию с начала года по сегодня
    assert boss.get("/reports/month").status_code == 200


# ---------------------------------------------------------------- выгрузка

def sheets_of(data: bytes) -> dict[str, list[list]]:
    """Читает книгу .xlsx стандартными средствами: имя листа → строки."""
    z = zipfile.ZipFile(io.BytesIO(data))
    wb = z.read("xl/workbook.xml").decode()
    names = re.findall(r'<sheet name="([^"]*)"', wb)
    out = {}
    for i, name in enumerate(names, start=1):
        xml = z.read(f"xl/worksheets/sheet{i}.xml").decode()
        assert "<f>" not in xml                                                                      # формул в файле нет
        rows = []
        for r in re.findall(r"<row [^>]*>(.*?)</row>", xml):
            cells = []
            for ref, body in re.findall(r'<c r="([A-Z]+)\d+"[^>]*?(?:/>|>(.*?)</c>)', r):
                t = re.search(r"<t[^>]*>(.*?)</t>", body)
                v = re.search(r"<v>(.*?)</v>", body)
                if t or v:                                                                           # пустые ячейки с оформлением пропускаем
                    cells.append(t.group(1) if t else float(v.group(1)))
            rows.append(cells)
        out[name] = rows
    return out


def test_excel_export_month(make_client, world):
    seed_may(world)
    r = make_client("dir_a").get(f"/reports/month/export.xlsx?{P_MAY}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert 'filename="report-month-2026-05.xlsx"' in r.headers["content-disposition"]
    book = sheets_of(r.content)
    assert list(book) == ["Сводка", "Затраты", "Топливо", "Простои", "Штрафы", "Рейсы", "Общие расходы", "О отчёте"]
    summary = book["Сводка"]
    assert summary[0][0] == "Машина" and "Всего затрат, ₽" in summary[0]
    k100 = [x for x in summary if str(x[0]).startswith("К100")][0]
    assert 26000.0 in k100 and summary[-1][0] == "Итого"
    fines = book["Штрафы"]
    assert fines[1][0] == serial(dt.date(2026, 5, 11)) and "=SUM(A1)" in fines[1]                    # дата настоящей датой; текст, не формула
    about = dict((x[0], x[1]) for x in book["О отчёте"][1:] if len(x) > 1)
    assert about["Отчёт"] == "Итоги и затраты по технике" and about["Период"] == "2026-05" and "Сформирован" in about
    with session_factory()() as db:
        log = db.scalar(select(AuditLog).where(AuditLog.action == "reports.export"))
        assert log.details["report"] == "month" and log.details["format"] == "xlsx"


def test_excel_export_hides_money_like_screen(make_client, world):
    seed_may(world)
    r = make_client("disp_a").get(f"/reports/month/export.xlsx?{P_MAY}")
    assert r.status_code == 200
    book = sheets_of(r.content)
    assert "Затраты" not in book and "Общие расходы" not in book
    assert not any("₽" in str(c) for row in book["Сводка"][:1] for c in row)
    assert not any("₽" in str(c) for row in book["Штрафы"][:1] for c in row)
    assert any("рублях" in str(x) for x in book["О отчёте"])


def test_export_needs_its_own_right(make_client, world):
    seed_may(world)
    mech = make_client("mech_a")
    assert mech.get("/reports/mileage").status_code == 200
    assert mech.get("/reports/mileage/export.xlsx").status_code == 403
    assert mech.get("/reports/mileage/export.csv").status_code == 403
    assert make_client("disp_a").get("/reports/costs/export.xlsx?" + RANGE_MAY).status_code == 404


def test_csv_export(make_client, world):
    seed_may(world)
    r = make_client("dir_a").get(f"/reports/month/export.csv?{P_MAY}")
    assert r.status_code == 200 and r.content.startswith("\ufeff".encode())
    text = r.content.decode("utf-8-sig")
    lines = text.split("\r\n")
    head = next(i for i, x in enumerate(lines) if x.startswith("Машина;Топливо, л;Топливо, ₽;"))
    k = [x for x in lines[head:] if x.startswith("К100")][0].split(";")
    assert "26000,0" in k and "10000,0" in k                                          # десятичная запятая, как ждёт Excel
    m = make_client("dir_a").get(f"/reports/month/export.csv?{P_MAY}").content.decode("utf-8-sig")
    assert "Штрафы за месяц" in m and "'=SUM(A1)" in m                                                  # формулу Excel обезвреживает апостроф
    assert "11.05.2026" in m


def test_xlsx_writer_is_safe():
    data = xlsx_book([("Сводка/итог: очень длинное название листа больше тридцати одного знака", [["A", "Б"], ["=1+1", 1.5], ["x\x00y", float("nan")]], [None, "money"]),
                      ("Сводка/итог: очень длинное название листа больше тридцати одного знака", [["Название", "Сумма"], ["Итого", 3]], [None, "int"])])
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    names = re.findall(r'<sheet name="([^"]*)"', z.read("xl/workbook.xml").decode())
    assert len(names) == 2 and len(set(n.lower() for n in names)) == 2 and all(len(n) <= 31 and "/" not in n and ":" not in n for n in names)
    s1 = z.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in s1 and "=1+1" in s1 and "\x00" not in s1 and "nan" not in s1
    s2, styles = z.read("xl/worksheets/sheet2.xml").decode(), z.read("xl/styles.xml").decode()
    total = int(re.search(r'<c r="B2" s="(\d+)"><v>3</v>', s2).group(1))
    xf = re.findall(r"<xf [^>]*>", re.search(r"<cellXfs.*?</cellXfs>", styles, re.S).group(0))[total]
    assert 'fontId="1"' in xf                                                                         # «Итого» жирным числом
    assert xlsx_book([])[:2] == b"PK"
