# -*- coding: utf-8 -*-
"""Правки 23.11.2026: карточка «Мусоровозы» на главной вместо «Сейчас в рейсе», ручной ввод показаний списком,
приход на склад из каталога деталей или вписанного вручную товара."""
from decimal import Decimal

from sqlalchemy import select

from app.db import session_factory
from app.models import PartCatalog, StockItem, Vehicle

from tests.test_ton_pay import TODAY, assign, ticket
from tests.test_garbage_pricing import set_tonnage, tier_tariff


# ---------------------------------------------------------------- главная: мусоровозы

def test_dashboard_garbage_card(world, make_client):
    set_tonnage(world, world.va1, price="3814.9")
    d = make_client("dir_a")
    assign(d, world.d1, work=tier_tariff(d))
    ticket(world, world.d1, "13.94", day=TODAY)                    # ≥ 12 т за день: 450 ₽/т
    ticket(world, world.d1, None, day=TODAY)                       # без веса
    g = d.get("/dashboard").json()["garbage"]
    assert g["trips"] == 2 and g["tons"] == 13.94 and g["today_tons"] == 13.94 and g["pending"] == 1
    assert g["cost"] == 53179.71 and g["fot"] == 6273.0 and g["fr"] == 46906.71
    assert g["trucks"][0]["plate"] == "А001АА54" and g["trucks"][0]["trips"] == 2
    # водителю карточка не нужна, бухгалтер видит без ФОТ, если нет права на заработок
    assert make_client("drv1").get("/dashboard").json()["garbage"] is None


def test_dashboard_without_garbage_trucks_has_no_card(world, make_client):
    with session_factory()() as db:
        for v in db.scalars(select(Vehicle)):
            v.tonnage = False
        db.commit()
    assert make_client("dir_a").get("/dashboard").json()["garbage"] is None


# ---------------------------------------------------------------- показания вручную списком

def test_manual_readings_list_and_batch(world, make_client):
    d = make_client("dir_a")
    lst = d.get("/readings/manual").json()
    plates = {x["plate"] for x in lst["items"]}
    assert lst["all"] is True and {"А001АА54", "А002АА54", "А003АА54"} <= plates and "В001ВВ54" not in plates
    r = d.post("/readings/manual", {"items": [{"vehicle_id": world.va1, "kind": "km", "value": "1250"},
                                              {"vehicle_id": world.va2, "kind": "km", "value": "500"},          # меньше прежнего (1000)
                                              {"vehicle_id": world.vb1, "kind": "km", "value": "2000"}]}).json()  # чужое юрлицо
    res = {x["vehicle_id"]: x for x in r["results"]}
    assert r["saved"] == 1 and res[world.va1]["ok"] and res[world.va1]["current_km"] == 1250
    assert not res[world.va2]["ok"] and res[world.va2]["message"]
    assert not res[world.vb1]["ok"] and "Нет права" in res[world.vb1]["message"]


def test_driver_enters_only_own_vehicle(world, make_client):
    drv = make_client("drv1")
    lst = drv.get("/readings/manual").json()
    assert [x["id"] for x in lst["items"]] == [world.va1] and lst["all"] is False
    r = drv.post("/readings/manual", {"items": [{"vehicle_id": world.va1, "value": "1100"}, {"vehicle_id": world.va2, "value": "1100"}]}).json()
    assert r["saved"] == 1 and [x["ok"] for x in r["results"]] == [True, False]
    assert make_client("store_a").get("/readings/manual").status_code == 403


# ---------------------------------------------------------------- склад: приход из каталога или вручную

def _wh(c, world):
    r = c.post("/warehouses", {"legal_entity_id": world.A, "name": "Главный склад"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_receipt_from_catalog_creates_item_once(world, make_client):
    d = make_client("dir_a")
    w = _wh(d, world)
    with session_factory()() as db:
        c = PartCatalog(name="Фильтр масляный КАМАЗ", name_key="фильтр масляный камаз", article="740.1012040", unit="шт")
        db.add(c)
        db.commit()
        cid = c.id
    found = d.get("/part-options", params={"q": "масляный"}).json()["items"]
    assert any(x["id"] == cid for x in found)
    r = d.post("/stock/moves", {"kind": "receipt", "new_item": {"catalog_id": cid}, "warehouse_id": w, "qty": "4", "price": "650"})
    assert r.status_code == 201, r.text
    assert r.json()["left_total"] == "4" and r.json()["move"]["item"] == "Фильтр масляный КАМАЗ" and r.json()["move"]["sku"] == "740.1012040"
    r = d.post("/stock/moves", {"kind": "receipt", "new_item": {"catalog_id": cid}, "warehouse_id": w, "qty": "2"})
    assert r.json()["left_total"] == "6"                           # второй раз тот же товар, а не новый
    with session_factory()() as db:
        assert len(list(db.scalars(select(StockItem)))) == 1


def test_receipt_manual_item_goes_to_catalog(world, make_client):
    d = make_client("dir_a")
    w = _wh(d, world)
    r = d.post("/stock/moves", {"kind": "receipt", "new_item": {"name": "  Антифриз  G12 ", "sku": "AF-12", "unit": "л"}, "warehouse_id": w, "qty": "20"})
    assert r.status_code == 201, r.text
    assert r.json()["move"]["item"] == "Антифриз G12" and r.json()["move"]["unit"] == "л"
    with session_factory()() as db:
        assert db.scalar(select(PartCatalog).where(PartCatalog.name == "Антифриз G12")) is not None
    bad = d.post("/stock/moves", {"kind": "receipt", "new_item": {"name": "Болт", "unit": "ведро"}, "warehouse_id": w, "qty": "1"})
    assert bad.status_code == 422
    assert d.post("/stock/moves", {"kind": "issue", "new_item": {"name": "Болт"}, "warehouse_id": w, "qty": "1"}).status_code == 422
    assert d.post("/stock/moves", {"kind": "receipt", "warehouse_id": w, "qty": "1"}).status_code == 422
