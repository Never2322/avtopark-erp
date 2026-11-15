# -*- coding: utf-8 -*-
"""Склад: остатки по движениям, «не уйти в минус», отмена движений, расход деталей на обращение, приход по счёту, права и юрлица."""
import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, Notification, RequestPart, ServiceRequest, StockMove, User
from tests.test_bills import issued as issued_bill
from tests.test_purchases import go, new_request, part


def today() -> str:
    return dt.date.today().isoformat()


def make(c, path, body):
    r = c.post(path, body)
    assert r.status_code == 201, r.text
    return r.json()


def wh(c, world, name="Главный склад", entity=None):
    return make(c, "/warehouses", {"legal_entity_id": entity or world.A, "name": name})["id"]


def item(c, world, name="Фильтр масляный", unit="шт", min_qty=0, sku="", entity=None):
    return make(c, "/stock_items", {"legal_entity_id": entity or world.A, "name": name, "unit": unit, "min_qty": min_qty, "sku": sku})["id"]


def move(c, kind, item_id, wh_id, qty, **kw):
    return c.post("/stock/moves", {"kind": kind, "item_id": item_id, "warehouse_id": wh_id, "qty": qty, **kw})


def ok(r, status=201):
    assert r.status_code == status, r.text
    return r.json()


def err(r, status=422):
    assert r.status_code == status, r.text
    return r.json()


def balances(c, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return c.get("/stock/balances" + ("?" + qs if qs else "")).json()


def one(c, name, **q):
    rows = [r for r in balances(c, **q)["items"] if r["name"] == name]
    assert len(rows) == 1, rows
    return rows[0]


def uid(login):
    with session_factory()() as db:
        return db.scalar(select(User.id).where(User.login == login))


def audit_actions():
    with session_factory()() as db:
        return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]


def moves_n(archived=False):
    with session_factory()() as db:
        return len(list(db.scalars(select(StockMove).where(StockMove.archived_at.is_(None) if not archived else StockMove.archived_at.is_not(None)))))


# ---------------------------------------------------------------- остатки

def test_balances_follow_moves(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    main, garage = wh(store, world), wh(store, world, "Гараж")
    f = item(store, world, min_qty=5)
    ok(move(store, "receipt", f, main, "10", price="1000,50", doc_number="УПД 15"))
    assert one(store, "Фильтр масляный")["total_text"] == "10"
    r = ok(move(store, "issue", f, main, 3, vehicle_id=world.va1))
    assert r["left_here"] == "7" and r["left_total"] == "7"
    assert r["move"]["price"] == 1000.5 and r["move"]["amount"] == 3001.5          # расход идёт по средней закупочной цене
    ok(move(store, "transfer", f, main, "2", to_warehouse_id=garage))
    ok(move(store, "writeoff", f, garage, "0,5", note="Порван при установке"))
    row = one(boss, "Фильтр масляный")
    assert row["total_text"] == "6,5" and row["low"] is False and row["avg_price"] == 1000.5
    assert [(x["warehouse"], x["qty_text"]) for x in row["places"]] == [("Гараж", "1,5"), ("Главный склад", "5")]            # склады по алфавиту
    assert row["value"] == 6503.25
    only_garage = one(boss, "Фильтр масляный", warehouse_id=garage)
    assert only_garage["qty_text"] == "1,5" and only_garage["total_text"] == "6,5" and [x["warehouse"] for x in only_garage["places"]] == ["Гараж"]
    data = balances(boss)
    assert data["totals"]["positions"] == 1 and data["totals"]["low"] == 0 and data["totals"]["value"] == 6503.25


def test_items_that_ran_out_show_as_low_and_empty_ones_are_hidden(make_client, world):
    store = make_client("store_a")
    w = wh(store, world)
    a, b, c = item(store, world, "Свеча", min_qty=4), item(store, world, "Ремень"), item(store, world, "Лампа", min_qty=2)
    ok(move(store, "receipt", a, w, 10))
    ok(move(store, "receipt", b, w, 1))
    ok(move(store, "issue", a, w, 7))                                                   # осталось 3 при минимуме 4
    names = {r["name"]: r for r in balances(store)["items"]}
    assert set(names) == {"Свеча", "Ремень", "Лампа"} and names["Свеча"]["low"] and names["Лампа"]["low"] and not names["Ремень"]["low"]
    assert names["Лампа"]["total_text"] == "0"                                          # нулевой, но нужный товар виден: его надо купить
    assert [r["name"] for r in balances(store, low=1)["items"]] == ["Лампа", "Свеча"]
    assert balances(store, low=1)["totals"]["low"] == 2
    ok(move(store, "issue", b, w, 1))
    assert "Ремень" not in {r["name"] for r in balances(store)["items"]}                # ни остатка, ни минимума — не показываем
    assert "Ремень" in {r["name"] for r in balances(store, show_empty=1)["items"]}
    assert [r["name"] for r in balances(store, q="свеч")["items"]] == ["Свеча"]


def test_cannot_go_negative(make_client, world):
    store = make_client("store_a")
    main, garage = wh(store, world), wh(store, world, "Гараж")
    f = item(store, world)
    ok(move(store, "receipt", f, main, 4))
    for kind, extra in (("issue", {}), ("writeoff", {}), ("transfer", {"to_warehouse_id": garage})):
        e = err(move(store, kind, f, main, "4,001", **extra))
        assert e["code"] == "shortage" and e["field"] == "qty" and "не хватает «Фильтр масляный»" in e["message"] and "Главный склад" in e["message"]
    assert err(move(store, "issue", f, garage, 1))["code"] == "shortage"                  # на другом складе его нет
    assert one(store, "Фильтр масляный")["total_text"] == "4" and moves_n() == 1          # неудачные попытки ничего не записали
    ok(move(store, "issue", f, main, 4))                                                  # ровно в ноль можно
    assert one(store, "Фильтр масляный", show_empty=1)["total_text"] == "0"


def test_move_validation(make_client, world):
    store = make_client("store_a")
    main, garage = wh(store, world), wh(store, world, "Гараж")
    f = item(store, world)
    ok(move(store, "receipt", f, main, 10))
    cases = [
        (dict(kind="receipt", qty=0), "qty"), (dict(kind="receipt", qty=-1), "qty"), (dict(kind="receipt", qty="abc"), "qty"),
        (dict(kind="receipt", qty="1,0001"), "qty"), (dict(kind="receipt", qty=1_000_001), "qty"), (dict(kind="receipt", qty=None), "qty"),
        (dict(kind="receipt", qty=1, price="-5"), "price"), (dict(kind="receipt", qty=1, price="много"), "price"),
        (dict(kind="receipt", qty=1, move_date=(dt.date.today() + dt.timedelta(days=1)).isoformat()), "move_date"),
        (dict(kind="receipt", qty=1, move_date="1990-01-01"), "move_date"),
        (dict(kind="transfer", qty=1, to_warehouse_id=main), "to_warehouse_id"), (dict(kind="transfer", qty=1), "to_warehouse_id"),
        (dict(kind="issue", qty=1, to_warehouse_id=garage), "to_warehouse_id"),
        (dict(kind="issue", qty=1, counterparty_id=1), "counterparty_id"), (dict(kind="receipt", qty=1, vehicle_id=world.va1), "vehicle_id"),
        (dict(kind="issue", qty=1, vehicle_id=world.vb1), "vehicle_id"),
        (dict(kind="issue", qty=1, warehouse_id=99999), "warehouse_id"),
        (dict(kind="receipt", qty=1, doc_number="x" * 61), "doc_number"),
    ]
    for body, field in cases:
        body = {"item_id": f, "warehouse_id": main, **body}
        r = store.post("/stock/moves", body)
        assert r.status_code == 422, (body, r.text)
        j = r.json()
        assert j["field"] == field or field in j.get("fields", {}), (body, j)
    assert err(store.post("/stock/moves", {"kind": "fly", "item_id": f, "warehouse_id": main, "qty": 1}))["field"] == "kind"
    assert err(store.post("/stock/moves", {"kind": "receipt", "item_id": 99999, "warehouse_id": main, "qty": 1}), 404)["code"] == "not_found"
    assert moves_n() == 1


def test_receipt_with_supplier_and_defaults(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    sup = make(boss, "/counterparties", {"name": "ООО Запчасти", "kind": "supplier"})["id"]
    cust = make(boss, "/counterparties", {"name": "ООО Заказчик", "kind": "customer"})["id"]
    w, f = wh(store, world), item(store, world)
    r = ok(move(store, "receipt", f, w, 2, counterparty_id=sup, price="10"))
    assert r["move"]["counterparty"] == "ООО Запчасти" and r["move"]["move_date"] == today() and r["move"]["created_by"]
    assert err(move(store, "receipt", f, w, 1, counterparty_id=cust))["field"] == "counterparty_id"       # заказчик — не поставщик
    r0 = ok(move(store, "receipt", f, w, 1))                                                             # без цены — ноль
    assert r0["move"]["price"] == 0
    assert one(store, "Фильтр масляный")["avg_price"] == 10.0                                              # нулевые цены в среднюю не идут


def test_archived_item_and_warehouse_are_refused(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    w, w2, f = wh(store, world), wh(store, world, "Старый"), item(store, world)
    assert boss.post(f"/warehouses/{w2}/void", {"reason": "Закрыт"}).status_code == 200
    e = err(move(store, "receipt", f, w2, 1), 409)
    assert e["code"] == "archived" and "Старый" in e["message"]
    assert boss.post(f"/stock_items/{f}/void", {"reason": "Не нужен"}).status_code == 200
    assert err(move(store, "receipt", f, w, 1), 409)["code"] == "archived"
    assert moves_n() == 0


def test_cannot_archive_item_or_warehouse_with_balance(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    w, f = wh(store, world), item(store, world)
    ok(move(store, "receipt", f, w, 2))
    e = err(boss.post(f"/warehouses/{w}/void", {"reason": "Закрываем"}), 409)
    assert "остатки" in e["message"] and e["code"] == "cannot_void"
    assert err(boss.post(f"/stock_items/{f}/void", {"reason": "Не нужен"}), 409)["code"] == "cannot_void"
    ok(move(store, "writeoff", f, w, 2))
    assert boss.post(f"/stock_items/{f}/void", {"reason": "Не нужен"}).status_code == 200
    assert boss.post(f"/warehouses/{w}/void", {"reason": "Закрываем"}).status_code == 200


def test_names_are_unique_per_entity(make_client, world):
    store, boss_b = make_client("store_a"), make_client("dir_b")
    wh(store, world)
    item(store, world, "Фильтр масляный")
    e = err(store.post("/stock_items", {"legal_entity_id": world.A, "name": "  фильтр  МАСЛЯНЫЙ! "}))
    assert "name" in e.get("fields", {}) or e["field"] == "name"
    assert store.post("/warehouses", {"legal_entity_id": world.A, "name": "главный склад"}).status_code == 422
    item(boss_b, world, "Фильтр масляный", entity=world.B)                                # в другом юрлице такой же товар — отдельный
    wh(boss_b, world, "Главный склад", entity=world.B)
    assert [r["legal_entity_id"] for r in balances(boss_b, show_empty=1)["items"]] == [world.B]


# ---------------------------------------------------------------- отмена и правка

def test_void_and_restore_recheck_balances(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    w, f = wh(store, world), item(store, world)
    rec = ok(move(store, "receipt", f, w, 10))["move"]["id"]
    iss = ok(move(store, "issue", f, w, 4))["move"]["id"]
    assert err(store.post(f"/stock/moves/{iss}/void", {"reason": "Ошибка"}), 403)["code"] == "forbidden"      # кладовщик не убирает в архив
    assert err(boss.post(f"/stock/moves/{iss}/void", {"reason": "ab"}))["code"] == "validation"
    e = err(boss.post(f"/stock/moves/{rec}/void", {"reason": "Не принимали"}), 409)                            # приход уже частично израсходован? нет — 10-4=6>=0, значит можно
    # убрать приход при остатке 6 нельзя: после этого было бы −4
    assert e["code"] == "shortage" and "Нельзя убрать это движение" in e["message"]
    assert one(boss, "Фильтр масляный")["total_text"] == "6"
    ok(boss.post(f"/stock/moves/{iss}/void", {"reason": "Выдали по ошибке"}), 200)
    assert one(boss, "Фильтр масляный")["total_text"] == "10"
    ok(boss.post(f"/stock/moves/{rec}/void", {"reason": "Не принимали"}), 200)
    assert balances(boss)["items"] == []
    e = err(boss.post(f"/stock/moves/{iss}/restore"), 409)                                                    # вернуть расход нечем
    assert e["code"] == "shortage" and "Нельзя вернуть это движение" in e["message"]
    ok(boss.post(f"/stock/moves/{rec}/restore"), 200)
    ok(boss.post(f"/stock/moves/{iss}/restore"), 200)
    assert one(boss, "Фильтр масляный")["total_text"] == "6"
    assert err(boss.post(f"/stock/moves/{iss}/restore"), 409)["code"] == "not_archived"
    archived = boss.get("/stock/moves?archived=1").json()
    assert archived["total"] == 0
    a = audit_actions()
    assert a.count("stock_moves.void") == 2 and a.count("stock_moves.restore") == 2


def test_transfer_void_checks_both_warehouses(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    a, b = wh(store, world), wh(store, world, "Гараж")
    f = item(store, world)
    ok(move(store, "receipt", f, a, 5))
    tr = ok(move(store, "transfer", f, a, 5, to_warehouse_id=b))["move"]["id"]
    ok(move(store, "issue", f, b, 3))                                                   # из гаража уже взяли
    e = err(boss.post(f"/stock/moves/{tr}/void", {"reason": "Переложили зря"}), 409)    # вернуть 5 на главный нельзя: в гараже осталось 2
    assert e["code"] == "shortage" and "Гараж" in e["message"]


def test_patch_changes_only_description(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    sup = make(boss, "/counterparties", {"name": "ООО Запчасти", "kind": "supplier"})["id"]
    w, f = wh(store, world), item(store, world)
    m = ok(move(store, "receipt", f, w, 5, price="100"))["move"]
    day = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    r = ok(store.patch(f"/stock/moves/{m['id']}", {"price": "120,50", "note": "Пересчитали", "doc_number": "УПД 7", "counterparty_id": sup,
                                                   "move_date": day, "qty": 999, "item_id": 12345, "kind": "issue", "warehouse_id": 777}), 200)
    assert (r["price"], r["note"], r["doc_number"], r["counterparty"], r["move_date"]) == (120.5, "Пересчитали", "УПД 7", "ООО Запчасти", day)
    assert (r["qty"], r["kind"], r["item_id"], r["warehouse_id"]) == (5, "receipt", f, w)                # количество, товар и склад — не меняются
    assert one(store, "Фильтр масляный")["total_text"] == "5"
    ok(store.patch(f"/stock/moves/{m['id']}", {"counterparty_id": None}), 200)                           # поставщика можно убрать
    assert store.get("/stock/moves").json()["items"][0]["counterparty"] == ""
    assert err(store.patch(f"/stock/moves/{m['id']}", {"vehicle_id": world.va1}))["field"] == "vehicle_id"
    assert err(store.patch(f"/stock/moves/{m['id']}", {"price": "-1"}))["field"] == "price"
    ok(boss.post(f"/stock/moves/{m['id']}/void", {"reason": "Ошибочный приход"}), 200)
    assert err(store.patch(f"/stock/moves/{m['id']}", {"note": "ещё"}), 409)["code"] == "archived"
    assert "stock_moves.update" in audit_actions()


# ---------------------------------------------------------------- права и юрлица

def test_rights_by_role(make_client, world):
    store, boss, mech, acc = make_client("store_a"), make_client("dir_a"), make_client("mech_a"), make_client("acc_a")
    w, f = wh(store, world), item(store, world)
    ok(move(store, "receipt", f, w, 5))
    for login in ("drv1", "disp_a", "emp_a"):
        c = make_client(login)
        assert c.get("/stock/balances").status_code == 403 and c.get("/stock/moves").status_code == 403 and c.get("/stock/options").status_code == 403
        assert c.post("/stock/moves", {"kind": "receipt", "item_id": f, "warehouse_id": w, "qty": 1}).status_code in (403, 404)
    assert acc.get("/stock/balances").status_code == 200 and acc.get("/stock/moves").status_code == 200
    assert acc.post("/stock/moves", {"kind": "receipt", "item_id": f, "warehouse_id": w, "qty": 1}).status_code == 403
    assert mech.get("/stock/balances").status_code == 200
    ok(move(mech, "issue", f, w, 1, vehicle_id=world.va1))                                                # механик списывает детали на машину
    assert mech.post("/stock_items", {"legal_entity_id": world.A, "name": "Новый"}).status_code == 403
    assert mech.post("/warehouses", {"legal_entity_id": world.A, "name": "Свой"}).status_code == 403
    mid = store.get("/stock/moves").json()["items"][0]["id"]
    assert mech.post(f"/stock/moves/{mid}/void", {"reason": "Ошибка"}).status_code == 403
    assert store.post(f"/stock/moves/{mid}/void", {"reason": "Ошибка"}).status_code == 403
    assert mech.patch(f"/stock/moves/{mid}", {"note": "х"}).status_code == 403
    assert store.patch(f"/stock/moves/{mid}", {"note": "х"}).status_code == 200
    assert boss.post(f"/stock/moves/{mid}/void", {"reason": "Ошибка"}).status_code == 200
    assert make_client().get("/stock/balances").status_code == 401


def test_other_entity_is_invisible(make_client, world):
    store, boss_b = make_client("store_a"), make_client("dir_b")
    w, f = wh(store, world), item(store, world)
    m = ok(move(store, "receipt", f, w, 5))["move"]["id"]
    assert balances(boss_b)["items"] == [] and boss_b.get("/stock/moves").json()["total"] == 0
    assert boss_b.get(f"/stock/balances?legal_entity_id={world.A}").status_code == 403
    assert boss_b.get(f"/stock/moves?legal_entity_id={world.A}").status_code == 403
    wb = wh(boss_b, world, "Склад Б", entity=world.B)
    assert boss_b.post("/stock/moves", {"kind": "receipt", "item_id": f, "warehouse_id": wb, "qty": 1}).status_code == 404       # чужого товара «нет»
    assert boss_b.post("/stock/moves", {"kind": "receipt", "item_id": item(boss_b, world, "Свой", entity=world.B), "warehouse_id": w, "qty": 1}).status_code == 422
    assert boss_b.patch(f"/stock/moves/{m}", {"note": "х"}).status_code == 404
    assert boss_b.post(f"/stock/moves/{m}/void", {"reason": "Чужое"}).status_code == 404
    opt = boss_b.get("/stock/options").json()
    assert [x["name"] for x in opt["warehouses"]] == ["Склад Б"] and [x["name"] for x in opt["items"]] == ["Свой"]
    assert one(store, "Фильтр масляный")["total_text"] == "5"


def test_options_for_storekeeper_include_own_lists(make_client, world):
    store = make_client("store_a")
    sup = make(make_client("dir_a"), "/counterparties", {"name": "ООО Запчасти", "kind": "both"})["id"]
    assert store.get("/vehicles/lookup").status_code in (403, 404, 200)
    w, f = wh(store, world), item(store, world)
    ok(move(store, "receipt", f, w, 3))
    o = store.get("/stock/options").json()
    assert [v["label"][:9] for v in o["vehicles"]][:1] == ["А001АА54 "] and any(s["id"] == sup for s in o["suppliers"])
    it = o["items"][0]
    assert it["total"] == 3 and it["places"] == [{"warehouse_id": w, "qty": 3.0, "qty_text": "3"}]
    assert o["can"]["create"] and o["can"]["edit"] and not o["can"]["void"]
    assert make_client("acc_a").get("/stock/options").json()["vehicles"] == []                         # у бухгалтера списков для записи нет


# ---------------------------------------------------------------- журнал

def test_journal_filters_and_sums(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    w, g = wh(store, world), wh(store, world, "Гараж")
    f, s = item(store, world, "Фильтр", sku="WIX-1"), item(store, world, "Свеча")
    old = (dt.date.today().replace(day=1) - dt.timedelta(days=5)).isoformat()
    ok(move(store, "receipt", f, w, 10, price="100", move_date=old, doc_number="УПД-1"))
    ok(move(store, "receipt", s, w, 4, price="50"))
    ok(move(store, "issue", f, w, 2, vehicle_id=world.va1, note="на А001"))
    ok(move(store, "transfer", s, w, 1, to_warehouse_id=g))
    ok(move(store, "writeoff", f, w, 1))
    j = boss.get("/stock/moves").json()
    assert j["total"] == 5 and j["items"][0]["kind"] == "writeoff"                                       # свежие сверху
    assert j["sums"] == {"receipt": 1200.0, "issue": 200.0, "writeoff": 100.0, "transfer": 50.0}
    assert boss.get("/stock/moves?kind=receipt").json()["total"] == 2
    assert boss.get(f"/stock/moves?month={old[:7]}").json()["total"] == 1
    assert boss.get(f"/stock/moves?warehouse_id={g}").json()["total"] == 1                              # перемещение видно и на складе-получателе
    assert boss.get(f"/stock/moves?item_id={s}").json()["total"] == 2
    assert boss.get(f"/stock/moves?vehicle_id={world.va1}").json()["total"] == 1
    assert boss.get("/stock/moves?q=wix").json()["total"] == 3 and boss.get("/stock/moves?q=УПД").json()["total"] == 1
    assert boss.get("/stock/moves?q=на а001").json()["total"] == 1
    assert boss.get("/stock/moves?kind=bad").status_code == 422 and boss.get("/stock/moves?month=2026-13").status_code == 422
    assert boss.get("/stock/moves?page_size=2&page=3").json()["items"][0]["kind"] == "receipt" and len(boss.get("/stock/moves?page_size=2").json()["items"]) == 2
    first = j["items"][0]
    assert first["can_edit"] and first["can_void"] and not store.get("/stock/moves").json()["items"][0]["can_void"]


def test_csv_exports_and_audit(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    w, f = wh(store, world), item(store, world, "=Опасное название")
    ok(move(store, "receipt", f, w, "2,5", price="10"))
    ok(move(store, "issue", f, w, 1, note="для проверки"))
    ok(move(store, "writeoff", f, w, "0,5"))
    b = boss.get("/stock/balances/export.csv")
    assert b.status_code == 200 and b.headers["content-type"].startswith("text/csv") and b.content.startswith(b"\xef\xbb\xbf")
    text = b.content.decode("utf-8-sig")
    assert "'=Опасное название" in text and ";1;" in text and "Главный склад" in text                       # формула в ячейке обезврежена
    m = boss.get("/stock/moves/export.csv")
    rows = m.content.decode("utf-8-sig").splitlines()
    assert rows[0].startswith("Дата;Вид;Товар") and len(rows) == 4 and "Расход" in "".join(rows) and "для проверки" in "".join(rows)
    assert make_client("drv1").get("/stock/moves/export.csv").status_code == 403
    a = audit_actions()
    for code in ("stock.receipt", "stock.issue", "stock.writeoff", "stock.export"):
        assert code in a
    with session_factory()() as db:
        e = db.scalar(select(AuditLog).where(AuditLog.action == "stock.issue"))
        assert e.legal_entity_id == world.A and e.details["warehouse"] == "Главный склад" and e.details["qty"] == "1"


# ---------------------------------------------------------------- «заканчивается»

def low_titles(login):
    with session_factory()() as db:
        return [n.title for n in db.scalars(select(Notification).where(Notification.user_id == uid(login), Notification.kind == "stock").order_by(Notification.id))]


def test_low_stock_notifies_once_a_day(make_client, world):
    store, mech = make_client("store_a"), make_client("mech_a")
    w, f = wh(store, world), item(store, world, "Фильтр масляный", min_qty=5)
    ok(move(store, "receipt", f, w, 6))
    ok(move(store, "issue", f, w, 1))                                                   # осталось 5 — это ещё не меньше минимума
    assert low_titles("dir_a") == []
    ok(move(store, "issue", f, w, 1))                                                   # осталось 4
    assert low_titles("dir_a") == ["Заканчивается: Фильтр масляный"]
    ok(move(store, "writeoff", f, w, 1))                                                # в тот же день второго уведомления нет
    assert low_titles("dir_a") == ["Заканчивается: Фильтр масляный"]
    assert low_titles("store_a") == [] and low_titles("mech_a") == [] and low_titles("drv1") == []        # сам кладовщик, механик и водитель не получают
    with session_factory()() as db:
        n = db.scalar(select(Notification).where(Notification.user_id == uid("dir_a"), Notification.kind == "stock"))
        assert n.url == "/stock?low=1" and "Осталось 4 шт, минимум 5" in n.text and n.legal_entity_id == world.A
    out = make_client("dir_a").get("/notifications").json()["items"][0]
    assert out["kind_label"] == "Склад"
    # механик списывает — директор получает, кладовщик (есть право менять товары) тоже
    g = item(store, world, "Масло", unit="л", min_qty=10)
    ok(move(store, "receipt", g, w, 12))
    ok(move(mech, "issue", g, w, 3))
    assert low_titles("store_a") == ["Заканчивается: Масло"] and "Заканчивается: Масло" in low_titles("dir_a")


def test_transfer_does_not_trigger_low_stock(make_client, world):
    store = make_client("store_a")
    a, b = wh(store, world), wh(store, world, "Гараж")
    f = item(store, world, min_qty=5)
    ok(move(store, "receipt", f, a, 5))
    ok(move(store, "transfer", f, a, 5, to_warehouse_id=b))
    assert low_titles("dir_a") == []


# ---------------------------------------------------------------- расход деталей на обращение

def waiting_request(world, mech, drv, parts):
    req = new_request(drv, world.va1, "Течёт масло", parts)
    for st in ("diagnostics", "in_progress", "waiting_parts"):
        assert go(mech, req["id"], st).status_code == 200
    return req


def test_issue_part_to_request(make_client, world):
    store, mech, drv = make_client("store_a"), make_client("mech_a"), make_client("drv1")
    w, f = wh(store, world), item(store, world, "Фильтр масляный", sku="WIX-1")
    ok(move(store, "receipt", f, w, 5, price="800"))
    req = waiting_request(world, mech, drv, [part("фильтр  масляный", 2), part("Свеча", 4)])
    info = mech.get(f"/stock/for-request/{req['id']}").json()
    by = {x["name"]: x for x in info["items"]}
    assert info["allowed"] and by["фильтр  масляный"]["item"]["name"] == "Фильтр масляный" and by["фильтр  масляный"]["can_issue"]
    assert by["фильтр  масляный"]["places"] == [{"warehouse_id": w, "warehouse": "Главный склад", "qty": 5.0, "enough": True, "qty_text": "5"}]
    assert by["Свеча"]["item"] is None and by["Свеча"]["can_issue"] is False
    ids = {p["name"]: p["id"] for p in mech.get(f"/requests/{req['id']}/parts").json()["items"]}
    res = ok(mech.post("/stock/issue-to-request", {"part_id": ids["фильтр  масляный"], "item_id": f, "warehouse_id": w}))
    mv = res["move"]
    assert mv["kind"] == "issue" and mv["qty"] == 2 and mv["request_number"] == req["number"] and mv["vehicle"].startswith("А001АА54") and mv["price"] == 800
    assert res["left_total"] == "3" and res["advanced"] is False                                       # свеча ещё не получена
    parts = {x["name"]: x for x in mech.get(f"/requests/{req['id']}/parts").json()["items"]}
    assert parts["фильтр  масляный"]["bought"] and not parts["Свеча"]["bought"]
    assert "фильтр  масляный" not in {g["name"] for g in mech.get("/purchases").json()["items"]}       # из «что купить» ушла
    events = [e["text"] for e in mech.get(f"/requests/{req['id']}").json()["events"]]
    assert any(t.startswith("Со склада «Главный склад»: фильтр  масляный — 2 шт") for t in events)
    assert err(mech.post("/stock/issue-to-request", {"part_id": ids["фильтр  масляный"], "item_id": f, "warehouse_id": w}), 409)["code"] == "part_bought"
    with session_factory()() as db:                                                                     # водитель-автор заявки получил уведомление
        assert [n.title for n in db.scalars(select(Notification).where(Notification.user_id == uid("drv1"), Notification.kind == "parts"))] == [
            f"Деталь со склада для обращения № {req['number']}"]
    assert "stock.issue_request" in audit_actions()
    assert mech.get(f"/stock/for-request/{req['id']}").json()["items"][0]["name"] == "Свеча"          # полученная строка из списка ушла


def test_last_part_from_stock_returns_request_to_work_and_void_undoes_it(make_client, world):
    store, mech, boss, drv = make_client("store_a"), make_client("mech_a"), make_client("dir_a"), make_client("drv1")
    w, f = wh(store, world), item(store, world, "Фильтр масляный")
    ok(move(store, "receipt", f, w, 5))
    req = waiting_request(world, mech, drv, [part("Фильтр масляный", 2)])
    pid = mech.get(f"/requests/{req['id']}/parts").json()["items"][0]["id"]
    res = ok(mech.post("/stock/issue-to-request", {"part_id": pid, "item_id": f, "warehouse_id": w}))
    assert res["advanced"] is True and mech.get(f"/requests/{req['id']}").json()["status"] == "in_progress"
    mid = res["move"]["id"]
    assert err(mech.post(f"/stock/moves/{mid}/void", {"reason": "Ошибка"}), 403)["code"] == "forbidden"       # механик не убирает движения
    ok(boss.post(f"/stock/moves/{mid}/void", {"reason": "Взяли не ту деталь"}), 200)
    r = mech.get(f"/requests/{req['id']}").json()
    assert r["status"] == "waiting_parts" and not mech.get(f"/requests/{req['id']}/parts").json()["items"][0]["bought"]
    assert one(boss, "Фильтр масляный")["total_text"] == "5"
    ok(boss.post(f"/stock/moves/{mid}/restore"), 200)                                                   # вернули — деталь снова получена
    assert mech.get(f"/requests/{req['id']}/parts").json()["items"][0]["bought"] and mech.get(f"/requests/{req['id']}").json()["status"] == "in_progress"
    # обращение выполнено — выдачу уже не убрать
    assert go(mech, req["id"], "done").status_code == 200
    e = err(boss.post(f"/stock/moves/{mid}/void", {"reason": "Поздно"}), 409)
    assert e["code"] == "request_closed"


def test_issue_to_request_rules(make_client, world):
    store, mech, drv, boss_b = make_client("store_a"), make_client("mech_a"), make_client("drv1"), make_client("dir_b")
    w, f = wh(store, world), item(store, world, "Масло 10W-40", unit="л")
    ok(move(store, "receipt", f, w, 20))
    req = new_request(mech, world.va1, "Замена масла", [part("Масло 10W-40", 24, "л"), part("Масло 10W-40", 4, "шт"), part("Прокладка", 1)])
    ids = {(p["name"], p["unit"]): p["id"] for p in mech.get(f"/requests/{req['id']}/parts").json()["items"]}
    big = ids[("Масло 10W-40", "л")]
    e = err(mech.post("/stock/issue-to-request", {"part_id": big, "item_id": f, "warehouse_id": w}))      # 24 л при остатке 20
    assert e["code"] == "shortage" and e["field"] == "warehouse_id"
    assert one(mech, "Масло 10W-40")["total_text"] == "20"
    assert mech.get(f"/requests/{req['id']}/parts").json()["items"][0]["bought"] is False
    e = err(mech.post("/stock/issue-to-request", {"part_id": ids[("Масло 10W-40", "шт")], "item_id": f, "warehouse_id": w}))
    assert e["code"] == "bad_unit"
    info = {(x["name"], x["unit"]): x for x in mech.get(f"/stock/for-request/{req['id']}").json()["items"]}
    assert info[("Масло 10W-40", "шт")]["unit_ok"] is False and info[("Масло 10W-40", "шт")]["can_issue"] is False
    assert info[("Масло 10W-40", "л")]["places"][0]["enough"] is False and info[("Масло 10W-40", "л")]["can_issue"] is False
    # права: водитель и кладовщик (нет права отмечать покупки), чужое юрлицо
    pid = ids[("Прокладка", "шт")]
    g = item(store, world, "Прокладка")
    ok(move(store, "receipt", g, w, 1))
    assert drv.post("/stock/issue-to-request", {"part_id": pid, "item_id": g, "warehouse_id": w}).status_code in (403, 404)
    assert store.post("/stock/issue-to-request", {"part_id": pid, "item_id": g, "warehouse_id": w}).status_code in (403, 404)
    assert boss_b.post("/stock/issue-to-request", {"part_id": pid, "item_id": g, "warehouse_id": w}).status_code == 404
    assert store.get(f"/stock/for-request/{req['id']}").status_code == 404                              # кладовщик обращений не видит
    assert boss_b.get(f"/stock/for-request/{req['id']}").status_code == 404
    # закрытое обращение
    with session_factory()() as db:
        r = db.get(ServiceRequest, req["id"])
        r.status = "closed"
        db.commit()
    assert err(mech.post("/stock/issue-to-request", {"part_id": pid, "item_id": g, "warehouse_id": w}), 409)["code"] == "request_closed"
    assert mech.get(f"/stock/for-request/{req['id']}").json()["closed"] is True
    with session_factory()() as db:
        assert db.scalar(select(RequestPart).where(RequestPart.id == pid)).bought_at is None


# ---------------------------------------------------------------- приход по счёту

def bill_with(c, items, **kw):
    return issued_bill(c, items=items, **kw)


def test_receive_goods_by_bill(make_client, world):
    store, boss = make_client("store_a"), make_client("dir_a")
    w = wh(store, world)
    known = item(store, world, "Фильтр масляный", sku="WIX-1")
    sup = make(boss, "/counterparties", {"name": "ООО Запчасти", "kind": "supplier"})["id"]
    b = bill_with(boss, [{"name": "фильтр масляный", "article": "WIX-1", "qty": 2, "unit": "шт", "price": "1000,50"},
                         {"name": "Масло моторное 5W-40", "qty": 10, "unit": "л", "price": "250"},
                         {"name": "Замена фильтра", "qty": 1, "unit": "усл.", "price": "500"}], supplier_id=sup)
    pre = boss.get(f"/stock/bill/{b['id']}").json()
    assert pre["can_post"] and [x["action"] for x in pre["lines"]] == ["existing", "new", "skip"] and pre["lines"][0]["item"]["id"] == known
    res = ok(boss.post(f"/stock/receive-bill/{b['id']}", {"warehouse_id": w}))
    assert res == {"posted": 2, "created_items": 1, "skipped": []}
    assert one(boss, "Фильтр масляный")["total_text"] == "2" and one(boss, "Масло моторное 5W-40")["total_text"] == "10"
    assert one(boss, "Масло моторное 5W-40")["avg_price"] == 250.0
    j = boss.get("/stock/moves").json()["items"]
    assert {m["bill_number"] for m in j} == {b["number"]} and {m["counterparty"] for m in j} == {"ООО Запчасти"} and j[0]["doc_number"].startswith("Счёт ")
    again = err(boss.post(f"/stock/receive-bill/{b['id']}", {"warehouse_id": w}), 409)
    assert again["code"] == "bill_posted" and boss.get(f"/stock/bill/{b['id']}").json()["can_post"] is False
    for m in j:                                                                                         # убрали движения — можно принять заново
        ok(boss.post(f"/stock/moves/{m['id']}/void", {"reason": "Приняли по ошибке"}), 200)
    assert boss.get(f"/stock/bill/{b['id']}").json()["can_post"] is True
    ok(boss.post(f"/stock/receive-bill/{b['id']}", {"warehouse_id": w}))
    assert one(boss, "Фильтр масляный")["total_text"] == "2"
    assert "stock.receive_bill" in audit_actions()


def test_receive_bill_edge_cases(make_client, world):
    store, boss, boss_b, mech = make_client("store_a"), make_client("dir_a"), make_client("dir_b"), make_client("mech_a")
    w = wh(store, world)
    only_service = bill_with(boss, [{"name": "Работа", "qty": 1, "unit": "ч", "price": "900"}])
    pre = boss.get(f"/stock/bill/{only_service['id']}").json()
    assert pre["can_post"] is False and "нет строк с товаром" in pre["reason"]
    assert err(boss.post(f"/stock/receive-bill/{only_service['id']}", {"warehouse_id": w}))["code"] == "no_goods"
    item(store, world, "Масло", unit="шт")                                                              # на складе «шт», в счёте «л»
    clash = bill_with(boss, [{"name": "Масло", "qty": 5, "unit": "л", "price": "100"}], number="К-1") if False else bill_with(
        boss, [{"name": "Масло", "qty": 5, "unit": "л", "price": "100"}])
    e = err(boss.post(f"/stock/receive-bill/{clash['id']}", {"warehouse_id": w}))
    assert e["code"] == "nothing_posted" and "на складе «шт», в счёте «л»" in e["message"]
    good = bill_with(boss, [{"name": "Свеча", "qty": 4, "unit": "шт", "price": "70"}])
    assert boss_b.get(f"/stock/bill/{good['id']}").status_code == 404 and boss_b.post(f"/stock/receive-bill/{good['id']}", {"warehouse_id": w}).status_code == 404
    assert err(boss.post(f"/stock/receive-bill/{good['id']}", {"warehouse_id": 99999}))["field"] == "warehouse_id"
    # механик: может принимать, но завести новый товар не может
    assert err(mech.post(f"/stock/receive-bill/{good['id']}", {"warehouse_id": w}), 403)["code"] == "forbidden"
    item(store, world, "Свеча")
    assert ok(mech.post(f"/stock/receive-bill/{good['id']}", {"warehouse_id": w}))["posted"] == 1
    assert make_client("store_a").get(f"/stock/bill/{good['id']}").status_code == 404                   # кладовщик счетов не видит
    assert make_client("drv1").get(f"/stock/bill/{good['id']}").status_code in (403, 404)
    # счёт в архиве и отменённый
    arch = bill_with(boss, [{"name": "Ремень", "qty": 1, "unit": "шт", "price": "10"}])
    ok(boss.post(f"/bills/{arch['id']}/void", {"reason": "Ошибочный счёт"}), 200)
    assert err(boss.post(f"/stock/receive-bill/{arch['id']}", {"warehouse_id": w}), 409)["code"] == "bill_closed"
    assert boss.get(f"/stock/bill/{arch['id']}").json()["reason"] == "Счёт в архиве"


def test_same_part_names_in_one_bill_become_one_item(make_client, world):
    boss = make_client("dir_a")
    w = wh(boss, world)
    b = bill_with(boss, [{"name": "Ремень", "qty": 1, "unit": "шт", "price": "10"}, {"name": "ремень", "qty": 2, "unit": "шт", "price": "20"}])
    assert ok(boss.post(f"/stock/receive-bill/{b['id']}", {"warehouse_id": w}))["created_items"] == 1
    row = one(boss, "Ремень")
    assert row["total_text"] == "3" and row["avg_price"] == round((10 + 40) / 3, 2)


def test_registry_sections_show_totals(make_client, world):
    store = make_client("store_a")
    w, f = wh(store, world), item(store, world, "Свеча", min_qty=5)
    ok(move(store, "receipt", f, w, 2))
    row = store.get("/stock_items").json()["items"][0]
    assert row["total"] == 2 and row["state"]["text"] == "Заканчивается"
    assert store.get("/warehouses").json()["items"][0]["positions"] == 1
    ok(move(store, "receipt", f, w, 8))
    assert store.get("/stock_items").json()["items"][0]["state"]["text"] == "В наличии"
    d = Decimal(str(store.get("/stock_items").json()["items"][0]["min_qty"]))
    assert d == 5
