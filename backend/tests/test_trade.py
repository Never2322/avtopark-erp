# -*- coding: utf-8 -*-
"""Заказы и документы: расчёт по тарифу, нумерация, один документ каждого вида на заказ, права, печатные данные."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, LegalEntity
from tests.test_registry import err_fields
from app.common import org_today

TODAY = org_today().isoformat()


def customer(c, name="ООО Заказчик", **kw):
    r = c.post("/counterparties", {"name": name, "kind": "customer", "inn": "5400123456", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def tariff(c, name="За километр", unit="km", rate="50", min_amount="0"):
    r = c.post("/tariffs", {"name": name, "unit": unit, "rate": rate, "min_amount": min_amount})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def order(w, c, cust, **kw):
    body = {"legal_entity_id": w.A, "order_date": TODAY, "customer_id": cust, "point_from": "Новосибирск", "point_to": "Бердск", "cargo": "Щебень"}
    body.update(kw)
    return c.post("/orders", body)


# ---------------------------------------------------------------- расчёт

def test_order_tariff_calc_and_numbering(world, make_client):
    d = make_client("dir_a")
    cust, t = customer(d), tariff(d, rate="50", min_amount="1000")
    r = order(world, d, cust, tariff_id=t, distance_km=100, vat_pct="20")
    assert r.status_code == 201, r.text
    o = r.json()
    assert (o["number"] if "number" in o else None) in (None, 1)
    assert o["doc_no"].startswith("№ 1/")
    assert o["rate"] == 50 and o["qty"] == 100 and o["amount"] == 5000 and o["vat_pct"] == 20
    assert o["vat"] == 1000 and o["total"] == 6000

    # минимальная сумма тарифа
    o2 = order(world, d, cust, tariff_id=t, distance_km=5).json()
    assert o2["amount"] == 1000 and o2["doc_no"].startswith("№ 2/")

    # ставка, вписанная вручную (скидка), сохраняется
    o3 = order(world, d, cust, tariff_id=t, distance_km=100, rate="40").json()
    assert o3["rate"] == 40 and o3["amount"] == 4000

    # пересчёт при смене расстояния
    r = d.patch(f"/orders/{o['id']}", {"distance_km": 200})
    assert r.json()["amount"] == 10000 and r.json()["qty"] == 200

    # смена тарифа без правки ставки берёт ставку нового тарифа
    t2 = tariff(d, name="За час", unit="hour", rate="1500")
    r = d.patch(f"/orders/{o['id']}", {"tariff_id": t2, "hours": "2,5"})
    assert r.json()["rate"] == 1500 and r.json()["qty"] == 2.5 and r.json()["amount"] == 3750


def test_order_tariff_units(world, make_client):
    d = make_client("dir_a")
    cust = customer(d)
    ton = tariff(d, name="За тонну", unit="ton", rate="300")
    r = order(world, d, cust, tariff_id=ton, cargo_kg="12345,6")
    assert r.json()["qty"] == 12.346 and r.json()["amount"] == 3703.8
    trip = tariff(d, name="За рейс", unit="trip", rate="7000")
    r = order(world, d, cust, tariff_id=trip)
    assert r.json()["qty"] == 1 and r.json()["amount"] == 7000
    day = tariff(d, name="За сутки", unit="day", rate="20000")
    r = order(world, d, cust, tariff_id=day, qty="3")
    assert r.json()["amount"] == 60000


def test_order_tariff_needs_volume(world, make_client):
    d = make_client("dir_a")
    cust, t = customer(d), tariff(d)
    assert "distance_km" in err_fields(order(world, d, cust, tariff_id=t))
    h = tariff(d, name="Час", unit="hour")
    assert "hours" in err_fields(order(world, d, cust, tariff_id=h))
    k = tariff(d, name="Тонна", unit="ton")
    assert "cargo_kg" in err_fields(order(world, d, cust, tariff_id=k))


def test_order_manual_amount_without_tariff(world, make_client):
    d = make_client("dir_a")
    cust = customer(d)
    r = order(world, d, cust, amount="12345,50")
    assert r.status_code == 201 and r.json()["amount"] == 12345.5
    r = order(world, d, cust, qty="4", rate="250")
    assert r.json()["amount"] == 1000


def test_order_validation(world, make_client):
    d = make_client("dir_a")
    cust = customer(d)
    base = dict(order_date=TODAY, customer_id=cust)
    assert "deliver_at" in err_fields(order(world, d, cust, depart_at="2026-03-02T10:00:00+07:00", deliver_at="2026-03-02T09:00:00+07:00"))
    assert "distance_km" in err_fields(order(world, d, cust, distance_km=-5))
    assert "amount" in err_fields(order(world, d, cust, amount="-1"))
    assert "amount" in err_fields(order(world, d, cust, amount="9e9999"))
    assert "vat_pct" in err_fields(order(world, d, cust, vat_pct="13"))
    assert "customer_id" in err_fields(order(world, d, None))
    sup = d.post("/counterparties", {"name": "Поставщик", "kind": "supplier"}).json()["id"]
    assert "customer_id" in err_fields(order(world, d, sup))
    assert "tariff_id" in err_fields(order(world, d, cust, tariff_id=9999))
    assert "vehicle_id" in err_fields(order(world, d, cust, vehicle_id=world.vb1))        # техника другого юрлица
    assert "driver_id" in err_fields(order(world, d, cust, driver_id=world.d3))           # водитель другого юрлица
    assert order(world, d, cust, vehicle_id=world.va1, driver_id=world.d1).status_code == 201
    # время без пояса считается по часам организации: 09:00 в Новосибирске = 02:00 UTC
    r = order(world, d, cust, depart_at="2026-03-02T09:00")
    assert r.json()["depart_at"].startswith("2026-03-02T02:00:00")


def test_order_year_is_fixed_and_numbers_are_per_year(world, make_client):
    d = make_client("dir_a")
    cust = customer(d)
    a = order(world, d, cust, order_date="2025-12-31").json()
    b = order(world, d, cust, order_date="2026-01-01").json()
    assert a["doc_no"] == "№ 1/2025" and b["doc_no"] == "№ 1/2026"
    assert "order_date" in err_fields(d.patch(f"/orders/{b['id']}", {"order_date": "2025-06-01"}))


# ---------------------------------------------------------------- документы

def test_make_documents_from_order(world, make_client):
    d = make_client("dir_a")
    cust, t = customer(d), tariff(d, rate="50")
    o = order(world, d, cust, tariff_id=t, distance_km=100, vat_pct="20").json()
    r = d.post(f"/orders/{o['id']}/make-document/invoice")
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["doc_no"].startswith("Счёт № 1/") and inv["counterparty_id"] == cust and inv["order_id"] == o["id"]
    assert inv["amount"] == 5000 and inv["vat"] == 1000 and inv["total"] == 6000 and inv["state"]["text"] == "Не оплачен"
    assert "по заказу № 1/" in inv["subject"] and "Новосибирск — Бердск" in inv["subject"]
    # второй такой же — нельзя; другой вид — можно, нумерация по видам отдельная
    assert d.post(f"/orders/{o['id']}/make-document/invoice").status_code == 422
    act = d.post(f"/orders/{o['id']}/make-document/act").json()
    assert act["doc_no"].startswith("Акт № 1/")
    o2 = order(world, d, cust, amount="100").json()
    assert d.post(f"/orders/{o2['id']}/make-document/invoice").json()["doc_no"].startswith("Счёт № 2/")
    assert d.post(f"/orders/{o['id']}/make-document/bogus").status_code == 404
    # в списке заказа видны его документы
    row = next(x for x in d.get("/orders").json()["items"] if x["id"] == o["id"])
    assert "Счёт 1" in row["docs"] and "Акт 1" in row["docs"]
    assert d.get("/documents?state=unpaid").json()["total"] == 3
    assert d.get("/documents?doc_type=act").json()["total"] == 1
    assert d.get(f"/documents?order_id={o['id']}").json()["total"] == 2
    assert d.get("/documents?order_id=abc").status_code == 422


def test_document_rules(world, make_client):
    d = make_client("dir_a")
    c1, c2 = customer(d), customer(d, name="Другой заказчик")
    o = order(world, d, c1, amount="1000").json()
    base = {"legal_entity_id": world.A, "doc_type": "act", "doc_date": TODAY, "order_id": o["id"], "price": "1000"}
    assert "counterparty_id" in err_fields(d.post("/documents", {**base, "counterparty_id": c2}))      # не заказчик из заказа
    r = d.post("/documents", base)                                                                      # заказчик берётся из заказа
    assert r.status_code == 201 and r.json()["counterparty_id"] == c1
    assert "order_id" in err_fields(d.post("/documents", base))                                         # дубль
    assert "counterparty_id" in err_fields(d.post("/documents", {"legal_entity_id": world.A, "doc_type": "tn", "doc_date": TODAY, "price": "5"}))
    assert "qty" in err_fields(d.post("/documents", {**base, "doc_type": "tn", "qty": "0"}))
    # вид документа не меняется
    doc_id = r.json()["id"]
    assert "doc_type" in err_fields(d.patch(f"/documents/{doc_id}", {"doc_type": "invoice", "price": "1500"}))
    assert d.patch(f"/documents/{doc_id}", {"price": "1500"}).status_code == 200
    got = d.get("/documents").json()["items"][0]
    assert got["doc_type"] == "act" and got["price"] == 1500
    # оплата
    assert "paid_date" in err_fields(d.patch(f"/documents/{doc_id}", {"paid_date": "2099-01-01"}))
    r = d.patch(f"/documents/{doc_id}", {"paid_date": TODAY})
    assert r.json()["state"]["text"] == "Оплачен"
    assert d.get("/documents?state=paid").json()["total"] == 1
    # заказ нельзя убрать в архив, пока есть документы, и заказчика сменить тоже
    assert d.post(f"/orders/{o['id']}/void", {"reason": "ошибка"}).status_code == 409
    assert "customer_id" in err_fields(d.patch(f"/orders/{o['id']}", {"customer_id": c2}))
    d.post(f"/documents/{doc_id}/void", {"reason": "ошибка"})
    assert d.post(f"/orders/{o['id']}/void", {"reason": "ошибка"}).status_code == 200
    # по архивному заказу документ не сделать; отменённый — тоже
    assert d.post(f"/orders/{o['id']}/make-document/act").status_code == 409
    o3 = order(world, d, c1, amount="1", status="cancelled").json()
    assert "order_id" in err_fields(d.post("/documents", {**base, "order_id": o3["id"]}))


def test_document_restore_checks_duplicates(world, make_client):
    d = make_client("dir_a")
    cust = customer(d)
    o = order(world, d, cust, amount="500").json()
    first = d.post(f"/orders/{o['id']}/make-document/invoice").json()
    d.post(f"/documents/{first['id']}/void", {"reason": "ошибка"})
    second = d.post(f"/orders/{o['id']}/make-document/invoice")
    assert second.status_code == 201
    back = d.post(f"/documents/{first['id']}/restore")
    assert back.status_code == 409 and back.json()["message"].startswith("Нельзя вернуть")


def test_document_numbers_are_unique_under_concurrency(world, make_client):
    import concurrent.futures as cf
    d = make_client("dir_a")
    cust = customer(d)
    ids = [order(world, d, cust, amount="10").json()["id"] for _ in range(6)]

    def go(oid):
        c = make_client("dir_a")
        return c.post(f"/orders/{oid}/make-document/invoice")
    with cf.ThreadPoolExecutor(6) as ex:
        res = list(ex.map(go, ids))
    assert all(r.status_code == 201 for r in res), [r.text for r in res if r.status_code != 201]
    assert sorted(r.json()["doc_no"] for r in res) == sorted(f"Счёт № {i}/{org_today().year}" for i in range(1, 7))


# ---------------------------------------------------------------- права и юрлица

def test_permissions_and_entity_isolation(world, make_client):
    d, a = make_client("dir_a"), make_client("acc_a")
    cust = customer(d)
    o = order(world, d, cust, amount="100").json()
    doc = d.post(f"/orders/{o['id']}/make-document/invoice").json()

    disp, mech, drv = make_client("disp_a"), make_client("mech_a"), make_client("drv1")
    assert disp.get("/orders").status_code == 200 and disp.get("/documents").status_code == 200
    assert order(world, disp, cust, amount="5").status_code == 201                    # диспетчер заказы ведёт
    assert disp.post(f"/orders/{o['id']}/make-document/act").status_code == 403       # а документы только смотрит
    assert disp.post("/documents", {"legal_entity_id": world.A, "doc_type": "act", "doc_date": TODAY, "counterparty_id": cust, "price": "1"}).status_code == 403
    assert disp.post(f"/orders/{o['id']}/void", {"reason": "тест"}).status_code == 403
    assert mech.get("/orders").status_code == 403 and mech.get("/documents").status_code == 403
    assert drv.get("/orders").status_code == 403
    assert mech.get(f"/print/documents/{doc['id']}").status_code in (403, 404)
    assert a.post(f"/orders/{o['id']}/make-document/act").status_code == 201          # бухгалтер ведёт всё

    # юрлицо Б не видит ничего из юрлица А
    b = make_client("dir_b")
    assert b.get("/orders").json()["total"] == 0 and b.get("/documents").json()["total"] == 0
    assert b.patch(f"/orders/{o['id']}", {"cargo": "x"}).status_code == 404
    assert b.post(f"/orders/{o['id']}/make-document/tn").status_code == 404
    assert b.get(f"/print/orders/{o['id']}").status_code == 404
    assert b.get(f"/print/documents/{doc['id']}").status_code == 404
    assert b.post("/orders", {"legal_entity_id": world.A, "order_date": TODAY, "customer_id": cust}).status_code == 403
    # документ нельзя привязать к заказу другого юрлица
    ob = order(world, b, cust, legal_entity_id=world.B, amount="1")
    assert ob.status_code == 201, ob.text
    assert "order_id" in err_fields(d.post("/documents", {"legal_entity_id": world.A, "doc_type": "act", "doc_date": TODAY, "order_id": ob.json()["id"], "price": "1"}))
    # списки для полей-ссылок
    assert [x["label"] for x in d.get(f"/options/order?legal_entity_id={world.A}").json()["items"]][0].startswith("№")
    assert b.get(f"/options/order?legal_entity_id={world.A}").status_code == 403
    assert {x["label"] for x in d.get("/options/customer").json()["items"]} == {"ООО Заказчик"}


def test_journal_and_csv(world, make_client):
    d = make_client("dir_a")
    cust = customer(d)
    o = order(world, d, cust, amount="100", depart_at="2026-03-02T09:00").json()
    d.post(f"/orders/{o['id']}/make-document/invoice")
    r = d.get("/orders/export.csv")
    assert r.status_code == 200
    text_ = r.content.decode("utf-8-sig")
    assert "02.03.2026 09:00" in text_ and "ООО Заказчик" in text_
    with session_factory()() as db:
        acts = set(db.scalars(select(AuditLog.action)))
    assert {"orders.create", "documents.create", "orders.export"} <= acts


# ---------------------------------------------------------------- печатные данные

def test_print_data(world, make_client):
    d = make_client("dir_a")
    with session_factory()() as db:
        e = db.get(LegalEntity, world.A)
        e.bank_name, e.bik, e.account, e.corr_account = "Сибирский банк", "045004641", "40702810000000000001", "30101810500000000641"
        e.head_name, e.accountant_name = "Иванов И. И.", "Петрова А. А."
        e.inn, e.address = "5405123456", "г. Новосибирск, ул. Ленина, 5"
        db.commit()
    cust = customer(d, kpp="540101001", address="г. Новосибирск, ул. Мира, 1")
    t = tariff(d, rate="1000")
    o = order(world, d, cust, tariff_id=t, distance_km=2, vat_pct="20", vehicle_id=world.va1, driver_id=world.d1).json()
    inv = d.post(f"/orders/{o['id']}/make-document/invoice").json()
    r = d.get(f"/print/documents/{inv['id']}")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["title"] == "Счёт на оплату" and j["number"] == 1 and j["total"] == 2400 and j["vat"] == 400 and j["amount"] == 2000
    assert j["words"] == "Две тысячи четыреста рублей 00 копеек"
    assert j["seller"]["bank_name"] == "Сибирский банк" and j["buyer"]["kpp"] == "540101001" and j["order"]["point_to"] == "Бердск"
    assert j["missing"] == []
    p = d.get(f"/print/orders/{o['id']}").json()
    assert p["vehicle"].startswith("А001АА54") and p["driver"] and p["tariff"] == "За километр" and p["total"] == 2400

    # без реквизитов печать работает, но сервер перечисляет, чего не хватает
    with session_factory()() as db:
        e = db.get(LegalEntity, world.A)
        e.bank_name = e.account = ""
        db.commit()
    j = d.get(f"/print/documents/{inv['id']}").json()
    assert "банк" in j["missing"] and "расчётный счёт" in j["missing"]


def test_print_cash_orders(world, make_client):
    d = make_client("dir_a")
    acc = d.post("/cash_accounts", {"legal_entity_id": world.A, "name": "Касса", "kind": "cash", "opening_balance": "0"}).json()["id"]
    cash = d.post("/cash", {"legal_entity_id": world.A, "op_date": TODAY, "kind": "in", "account_id": acc, "amount": "1250,50", "note": "Оплата по счёту 5"})
    assert cash.status_code == 201, cash.text
    j = d.get(f"/print/cash/{cash.json()['id']}").json()
    assert j["prefix"] == "ПКО" and j["amount"] == 1250.5 and j["words"] == "Одна тысяча двести пятьдесят рублей 50 копеек" and j["basis"] == "Оплата по счёту 5"
    acc2 = d.post("/cash_accounts", {"legal_entity_id": world.A, "name": "Касса 2", "kind": "cash", "opening_balance": "0"}).json()["id"]
    tr = d.post("/cash", {"legal_entity_id": world.A, "op_date": TODAY, "kind": "transfer", "account_id": acc, "to_account_id": acc2, "amount": "10"}).json()
    assert d.get(f"/print/cash/{tr['id']}").status_code == 422


def test_amount_in_words():
    from app.services.words import amount_in_words as w
    assert w(0) == "Ноль рублей 00 копеек"
    assert w("1") == "Один рубль 00 копеек"
    assert w("2.5") == "Два рубля 50 копеек"
    assert w("11111.11") == "Одиннадцать тысяч сто одиннадцать рублей 11 копеек"
    assert w("1234567.89").startswith("Один миллион двести тридцать четыре тысячи")
    assert w("2000.01") == "Две тысячи рублей 01 копейка"


def test_legal_entity_requisites(world, make_client):
    d = make_client("dir_a")
    e = d.get("/legal-entities").json()["items"][0]
    for k in ("bank_name", "bik", "account", "corr_account", "head_name", "accountant_name"):
        assert k in e
    body = {"name": e["name"], "bik": "045004641", "account": "40702810000000000001", "corr_account": "30101810500000000641",
            "bank_name": "Банк", "head_name": "Иванов И. И."}
    r = d.patch(f"/legal-entities/{e['id']}", body)
    # править юрлица может только тот, у кого есть право на всё; у директора юрлица его нет
    assert r.status_code in (200, 403)
    adm = make_client("admin")
    r = adm.patch(f"/legal-entities/{e['id']}", body)
    assert r.status_code == 200 and r.json()["bik"] == "045004641" and r.json()["head_name"] == "Иванов И. И."
    for bad in ({"bik": "12"}, {"account": "1234"}, {"corr_account": "abcdefghijklmnopqrst"}):
        r = adm.patch(f"/legal-entities/{e['id']}", {**body, **bad})
        assert r.status_code == 422 and r.json()["field"] == list(bad)[0]
