# -*- coding: utf-8 -*-
"""Группа «Мусоровозы» в меню — только водителю мусоровоза, счётчик ходок — только водителю самосвала (мусоровоз с типом «Самосвал»
из старой программы самосвалом не считается). В отчёте «Ремонты по месяцам» — на что потрачено: запчасти, жидкости, расходники."""
import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.db import session_factory
from app.models import RepairBill, ServiceRequest, Vehicle, VehicleType

from tests.test_reports import at, rep, vehicle


def set_type(vid, code, model=None):
    with session_factory()() as db:
        v = db.get(Vehicle, vid)
        v.type_id = db.scalar(select(VehicleType.id).where(VehicleType.code == code))
        if model is not None:
            v.model = model
        db.commit()


def test_dump_truck_driver_has_hauls_but_no_garbage_group(make_client, world):
    d = make_client("drv1")                                            # за водителем 1 закреплён самосвал
    assert d.me["features"]["hauls"] is True and d.me["features"]["garbage"] is False


def test_garbage_truck_driver_has_garbage_group_but_no_hauls(make_client, world):
    set_type(world.va2, "truck_waste")
    d = make_client("drv2")
    assert d.me["features"]["garbage"] is True and d.me["features"]["hauls"] is False
    assert d.get("/driver/home").json()["can"]["hauls"] is False


def test_garbage_truck_typed_as_dump_truck_is_still_a_garbage_truck(make_client, world):
    set_type(world.va2, "truck_dump", model="мусоровоз")                # так машины пришли из старой программы
    d = make_client("drv2")
    assert d.me["features"]["garbage"] is True and d.me["features"]["hauls"] is False
    assert d.get("/hauls").status_code == 403


def test_repairs_report_shows_what_money_went_on(make_client, world):
    with session_factory()() as db:
        v = vehicle(db, world.A, "К777КК54", created=at(2025, 1, 1))
        bill = lambda n, total, cat, day=dt.date(2026, 3, 5), **kw: RepairBill(legal_entity_id=world.A, kind="uploaded", number=n, bill_date=day,
                                                                             vehicle_id=v.id, total=Decimal(total), vat=Decimal("0"), category=cat, **kw)
        db.add_all([bill("З-1", "10000", "parts"), bill("Ж-1", "3000", "fluids"), bill("Р-1", "700", "consumables", day=dt.date(2026, 2, 10)),
                    bill("Б-1", "500", None), bill("О-1", "999", "parts", status="cancelled")])
        db.add(ServiceRequest(legal_entity_id=world.A, number=951, vehicle_id=v.id, title="Шланг", status="closed", cost_actual=Decimal("1200"),
                              closed_at=at(2026, 3, 9)))
        db.commit()
    res = rep(make_client("dir_a"), "repairs", "month=2026-03")
    by_v = next(e for e in res["extras"] if e["title"] == "На что потрачено по машинам")
    labels = [c["label"] for c in by_v["columns"]]
    row = dict(zip(labels, next(r for r in by_v["rows"] if r[0].startswith("К777"))))
    assert row["Запчасти, ₽"] == 10000 and row["Жидкости, ₽"] == 3000 and row["Расходники, ₽"] == 700        # отменённый счёт не считается
    assert row["Счета без категории, ₽"] == 500 and row["Обращения без счёта, ₽"] == 1200 and row["Всего, ₽"] == 15400
    by_m = next(e for e in res["extras"] if e["title"] == "На что потрачено по месяцам")
    m_labels = [c["label"] for c in by_m["columns"]]
    march = dict(zip(m_labels, by_m["rows"][-1]))
    assert march["Запчасти, ₽"] == 10000 and march["Всего, ₽"] == 14700
    x = make_client("dir_a").get("/reports/repairs/export.xlsx", params={"month": "2026-03"})
    assert x.status_code == 200 and len(x.content) > 1000
