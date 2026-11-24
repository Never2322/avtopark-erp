# -*- coding: utf-8 -*-
"""Мусоровозы: тариф «за тонну с порогом за день», цена за тонну у машины и в талоне (стоимость рейса, итог журнала),
загрузка журнала тоннажа из Excel/CSV."""
import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.db import session_factory
from app.models import Vehicle, WeighTicket
from app.services.xlsx import xlsx_book

from tests.test_ton_pay import FIRST, MONTH, TODAY, assign, earned, fields, ticket


def tier_tariff(c, low=400, high=450, tier="12", name="Мусоровоз: до/от 12 т"):
    r = c.post("/tariffs", {"name": name, "kind": "work", "unit": "ton_tier", "rate": str(low), "rate_high": str(high), "tier_tons": tier})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def set_tonnage(world, vid, price=None):
    with session_factory()() as db:
        v = db.get(Vehicle, vid)
        v.tonnage, v.ton_price = True, (Decimal(str(price)) if price is not None else None)
        db.commit()


# ---------------------------------------------------------------- тариф с порогом

def test_tier_tariff_needs_second_rate_and_threshold(world, make_client):
    d = make_client("dir_a")
    r = d.post("/tariffs", {"name": "Без второй", "kind": "work", "unit": "ton_tier", "rate": "400"})
    assert r.status_code == 422 and "rate_high" in fields(r)
    r = d.post("/tariffs", {"name": "Заказчику", "kind": "customer", "unit": "ton_tier", "rate": "400", "rate_high": "450", "tier_tons": "12"})
    assert r.status_code == 422 and "unit" in fields(r)
    tier_tariff(d)
    t = next(x for x in d.get("/tariffs").json()["items"] if x["unit"] == "ton_tier")
    assert t["tier_tons"] == 12 and t["rate_high"] == 450
    # у обычного тарифа второй ставки нет, даже если её прислали
    r = d.post("/tariffs", {"name": "Час", "kind": "work", "unit": "hour", "rate": "300", "rate_high": "999", "tier_tons": "5"})
    assert r.status_code == 201 and r.json()["rate_high"] is None


def test_day_below_threshold_uses_low_rate_for_all_tons(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=tier_tariff(d))
    ticket(world, world.d1, "6.5", day=FIRST)
    ticket(world, world.d1, "5", day=FIRST)                       # за день 11,5 т < 12: все по 400
    e = earned(make_client("drv1"))
    assert e["tons"] == 11.5 and e["tons_amount"] == 4600


def test_day_at_or_over_threshold_uses_high_rate_for_all_tons(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=tier_tariff(d))
    ticket(world, world.d1, "7", day=FIRST)
    ticket(world, world.d1, "5", day=FIRST)                       # ровно 12 т: все по 450 (как =ЕСЛИ(<12;…) в Excel)
    nxt = FIRST + dt.timedelta(days=1) if FIRST + dt.timedelta(days=1) <= TODAY else FIRST
    if nxt != FIRST:
        ticket(world, world.d1, "3", day=nxt)                     # другой день: 3 т по 400
    e = earned(make_client("drv1"))
    assert e["tons_amount"] == 12 * 450 + (3 * 400 if nxt != FIRST else 0)
    assert "до 12 т в день" in e["tons_rate_text"]
    j = d.get("/reports/garbage_salary", params={"month": MONTH}).json()
    row = next(r for r in j["table"]["rows"] if r[0].startswith("Первый"))
    assert "450 ₽/т от 12 т" in " ".join(str(x) for x in row)


# ---------------------------------------------------------------- цена за тонну и стоимость рейса

def test_ticket_takes_vehicle_price_and_journal_sums_cost(world, make_client):
    set_tonnage(world, world.va1, price="3814.9")
    d = make_client("dir_a")
    r = d.post("/weigh_tickets", {"ticket_date": TODAY.isoformat(), "vehicle_id": world.va1, "tons": "13.94"})
    assert r.status_code == 201, r.text
    assert r.json()["price"] == 3814.9
    r = d.post("/weigh_tickets", {"ticket_date": TODAY.isoformat(), "vehicle_id": world.va1, "tons": "2", "price": "1000"})
    assert r.json()["price"] == 1000                              # своя цена в талоне
    lst = d.get("/weigh_tickets").json()
    costs = sorted(it["cost"] for it in lst["items"])
    assert costs == [2000.0, 53179.71]                            # 13,94 × 3814,9 = 53 179,706 → 53 179,71
    assert lst["totals"]["cost"] == 55179.71 and lst["totals"]["tons"] == 15.94


def test_vehicle_price_is_hidden_from_driver(world, make_client):
    set_tonnage(world, world.va1, price="3814.9")
    assert make_client("dir_a").get(f"/vehicles/{world.va1}").json()["ton_price"] == 3814.9
    assert make_client("drv1").get(f"/vehicles/{world.va1}").json()["ton_price"] is None


def test_vehicle_price_is_set_in_card(world, make_client):
    d = make_client("dir_a")
    v = d.get(f"/vehicles/{world.va1}").json()
    body = {"type_id": v["type"]["id"], "plate": v["plate"], "make": v["make"], "model": v["model"], "ton_price": "3814.90"}
    assert d.patch(f"/vehicles/{world.va1}", body).status_code == 200
    assert d.get(f"/vehicles/{world.va1}").json()["ton_price"] == 3814.9
    body.pop("ton_price")                                         # не передали — цена остаётся
    assert d.patch(f"/vehicles/{world.va1}", body).status_code == 200
    assert d.get(f"/vehicles/{world.va1}").json()["ton_price"] == 3814.9


# ---------------------------------------------------------------- загрузка из файла

def send(c, content: bytes, name="tonny.xlsx", apply=False):
    return c.call("POST", "/tonnage/import", files={"file": (name, content, "application/octet-stream")}, data={"apply": "1" if apply else "0"})


def sample_xlsx(day: dt.date) -> bytes:
    rows = [["Сводные данные по вывозу мусора"],
            ["№ п/п", "Дата", "Номер Акта", "г/н автомобиля", "кол-во тн", "стоимость", "сумма"],
            [1, day.strftime("%d.%m.%Y"), "ВГ1", "А001АА54", 13.94, 3814.9, None],
            [2, day.strftime("%d.%m.%Y"), "ВГ2", "а 001 аа 54", 7.6, None, None],
            [3, day.strftime("%d.%m.%Y"), "ВГ3", "Х999ХХ54", 5, 3814.9, None],
            [4, day.strftime("%d.%m.%Y"), "ВГ1", "А001АА54", 13.94, 3814.9, None],
            ["Итого", None, None, None, 40.48, None, None]]
    return xlsx_book([("Тонны", rows, None)])


def test_import_preview_then_apply(world, make_client):
    set_tonnage(world, world.va1, price="3000")
    d = make_client("dir_a")
    r = send(d, sample_xlsx(TODAY))
    assert r.status_code == 200, r.text
    j = r.json()
    st = {x["n"]: x for x in j["rows"]}
    assert j["summary"]["ok"] == 2 and j["summary"]["error"] == 1 and j["summary"]["duplicate"] == 1 and j["summary"]["total"] == 4
    assert st[3]["price"] == 3814.9 and st[3]["cost"] == 53179.71 and st[3]["driver"].startswith("Первый")
    assert st[4]["price"] == 3000                                 # цены в файле нет: из машины
    assert "не найдена" in st[5]["message"]
    with session_factory()() as db:
        assert db.scalar(select(WeighTicket.id)) is None          # просмотр ничего не записал
    j = send(d, sample_xlsx(TODAY), apply=True).json()
    assert j["applied"] and j["summary"]["ok"] == 2
    with session_factory()() as db:
        ts = list(db.scalars(select(WeighTicket).order_by(WeighTicket.id)))
        assert [(t.doc_number, float(t.tons), t.driver_id, t.tons_source) for t in ts] == [("ВГ1", 13.94, world.d1, "manual"), ("ВГ2", 7.6, world.d1, "manual")]
    again = send(d, sample_xlsx(TODAY), apply=True).json()          # повторная загрузка того же файла ничего не дублирует
    assert again["summary"]["ok"] == 0 and again["summary"]["duplicate"] == 3


def test_import_needs_tonnage_flag_and_right(world, make_client):
    with session_factory()() as db:
        db.get(Vehicle, world.va1).tonnage = False
        db.commit()
    j = send(make_client("dir_a"), sample_xlsx(TODAY)).json()
    assert any("не ведётся тоннаж" in x["message"] for x in j["rows"])
    assert send(make_client("drv1"), sample_xlsx(TODAY)).status_code == 403
    assert send(make_client("dir_a"), b"\xd0\x9d\xd0\xb5\xd1\x82;\xd0\xba\xd0\xbe\xd0\xbb\xd0\xbe\xd0\xbd\xd0\xbe\xd0\xba\r\n1;2\r\n", name="x.csv").status_code == 422


def test_import_csv_template_works(world, make_client):
    set_tonnage(world, world.va1)
    d = make_client("dir_a")
    tpl = d.get("/tonnage/import/template.csv")
    assert tpl.status_code == 200 and "г/н автомобиля" in tpl.content.decode("utf-8-sig")
    csv = f"Дата;Номер акта;г/н автомобиля;кол-во тн;стоимость\r\n{TODAY.strftime('%d.%m.%Y')};77;А001АА54;10,5;3814,9\r\n".encode("utf-8")
    j = send(d, csv, name="t.csv", apply=True).json()
    assert j["summary"]["ok"] == 1 and j["summary"]["cost"] == 40056.45


# ---------------------------------------------------------------- листы по машинам, как «ЗП Ноябрь»

def test_salary_matrix_like_excel(world, make_client):
    set_tonnage(world, world.va1, price="3814.9")
    d = make_client("dir_a")
    assign(d, world.d1, work=tier_tariff(d))
    for tons in ("9.84", "8.62"):                                  # 18,46 т за день ≥ 12: все по 450
        d.post("/weigh_tickets", {"ticket_date": FIRST.isoformat(), "vehicle_id": world.va1, "tons": tons, "driver_id": world.d1})
    j = d.get("/reports/garbage_salary", params={"month": MONTH}).json()
    m = j["matrix"]
    assert m["days"] == [FIRST.isoformat()] and m["money"] is True and m["footer"] is None
    b = m["blocks"][0]
    rows = {r["label"]: r for r in b["rows"] if r["label"]}
    assert b["title"].startswith("А001АА54")
    assert [r["values"][0] for r in b["rows"][:2]] == [9.84, 8.62]
    assert rows["Итого тонн в день"]["values"] == [18.46]
    assert rows["Водитель"]["values"] == ["Первый"]
    assert rows["ФОТ водителя, ₽"]["values"] == [8307.0]            # 18,46 × 450
    assert rows["Стоимость вывезенных тонн, ₽"]["values"] == [70423.06]          # каждый рейс до копеек: 37 538,62 + 32 884,44
    assert rows["ФР (стоимость − ФОТ), ₽"]["values"] == [62116.06]
    x = d.get("/reports/garbage_salary/export.xlsx", params={"month": MONTH})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    # без права на журнал тоннажа стоимости и ФР нет
    mech = make_client("mech_a").get("/reports/garbage_salary", params={"month": MONTH})
    if mech.status_code == 200 and mech.json().get("matrix"):
        labels = [r["label"] for r in mech.json()["matrix"]["blocks"][0]["rows"]]
        assert "ФР (стоимость − ФОТ), ₽" not in labels
