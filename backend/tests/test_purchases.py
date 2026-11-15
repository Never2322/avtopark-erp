# -*- coding: utf-8 -*-
"""«Что купить»: общий список, склейка одинаковых деталей, отметка «куплено», права и юрлица."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, RequestPart, ServiceRequest


def new_request(c, vid, title="Ремонт", parts=None):
    r = c.post("/requests", {"vehicle_id": vid, "title": title, "parts": parts or []})
    assert r.status_code == 201, r.text
    return r.json()


def part(name, qty=1, unit="шт", article=""):
    return {"name": name, "qty": qty, "unit": unit, "article": article}


def go(c, rid, to, comment=""):
    return c.post(f"/requests/{rid}/transition", {"to_status": to, "comment": comment})


def ids_of(c, rid):
    return {i["name"]: i["id"] for i in c.get(f"/requests/{rid}/parts").json()["items"]}


def test_same_parts_from_different_requests_are_summed(make_client, world):
    mech = make_client("mech_a")
    a = new_request(mech, world.va1, "Первая", [part("Фильтр масляный", 2), part("Масло 10W-40", 24, "л")])
    b = new_request(mech, world.va2, "Вторая", [part("фильтр  масляный", 1), part("Ремень", 1)])
    data = mech.get("/purchases").json()
    by_name = {g["name"]: g for g in data["items"]}
    assert list(by_name) == ["Масло 10W-40", "Ремень", "Фильтр масляный"]                 # по алфавиту
    f = by_name["Фильтр масляный"]
    assert f["qty"] == 3 and f["qty_text"] == "3" and f["requests"] == 2 and len(f["ids"]) == 2
    assert {ln["number"] for ln in f["lines"]} == {a["number"], b["number"]} and {ln["plate"] for ln in f["lines"]} == {"А001АА54", "А002АА54"}
    assert by_name["Масло 10W-40"]["qty_text"] == "24"
    assert data["totals"] == {"positions": 3, "lines": 4, "requests": 2}
    # разные артикулы или единицы — разные строки
    new_request(mech, world.va3, "Третья", [part("Фильтр масляный", 1, "комплект"), part("Фильтр масляный", 1, "шт", "WIX 1")])
    names = [(g["name"], g["unit"], g["article"]) for g in mech.get("/purchases").json()["items"] if g["name"] == "Фильтр масляный"]
    assert len(names) == 3


def test_search_and_vehicle_filter(make_client, world):
    mech = make_client("mech_a")
    a = new_request(mech, world.va1, "Первая", [part("Фильтр масляный"), part("Ремень")])
    new_request(mech, world.va2, "Вторая", [part("Свеча", 4)])
    assert [g["name"] for g in mech.get("/purchases?q=ремен").json()["items"]] == ["Ремень"]
    assert [g["name"] for g in mech.get("/purchases?q=а002").json()["items"]] == ["Свеча"]                # по госномеру
    assert len(mech.get(f"/purchases?q={a['number']}").json()["items"]) == 2                          # по номеру обращения
    assert {g["name"] for g in mech.get(f"/purchases?vehicle_id={world.va1}").json()["items"]} == {"Фильтр масляный", "Ремень"}


def test_only_open_unbought_parts_are_listed(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    live = new_request(mech, world.va1, "Живое", [part("Деталь А")])
    gone = new_request(mech, world.va1, "Отменённое", [part("Деталь Б")])
    go(mech, gone["id"], "cancelled", "Не нужно")
    done = new_request(mech, world.va2, "Выполненное", [part("Деталь В")])
    go(mech, done["id"], "diagnostics")
    go(boss, done["id"], "in_progress")
    go(mech, done["id"], "done")
    removed = new_request(mech, world.va3, "С убранной", [part("Деталь Г")])
    mech.call("DELETE", f"/requests/{removed['id']}/parts/{ids_of(mech, removed['id'])['Деталь Г']}")
    assert [g["name"] for g in mech.get("/purchases").json()["items"]] == ["Деталь А"]
    assert live["number"]


def test_mark_bought_moves_line_to_recent_and_writes_trail(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1, "Ремонт", [part("Фильтр", 2), part("Прокладка")])
    ids = ids_of(mech, req["id"])
    r = mech.post("/purchases/bought", {"ids": [ids["Фильтр"]]})
    assert r.status_code == 200 and r.json() == {"marked": 1, "advanced": []}
    data = mech.get("/purchases").json()
    assert [g["name"] for g in data["items"]] == ["Прокладка"]
    assert [x["name"] for x in data["recent"]] == ["Фильтр"] and data["recent"][0]["bought_by"] == "Mech_a" and data["recent"][0]["can_restore"] is True
    block = mech.get(f"/requests/{req['id']}").json()
    assert block["parts"]["bought"] == 1 and block["parts"]["to_buy"] == 1
    assert any("Куплено: Фильтр — 2 шт" in e["text"] for e in block["events"])
    again = mech.post("/purchases/bought", {"ids": [ids["Фильтр"]]})                          # повторная отметка не ломает и не дублирует
    assert again.status_code == 200 and again.json()["marked"] == 0
    assert sum("Куплено" in e["text"] for e in mech.get(f"/requests/{req['id']}").json()["events"]) == 1
    with session_factory()() as db:
        acts = [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]
        assert acts.count("purchase.bought") == 1


def test_all_bought_returns_waiting_request_to_work(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    req = new_request(mech, world.va1, "Ждём запчасти", [part("Сальник"), part("Подшипник")])
    go(mech, req["id"], "diagnostics")
    go(boss, req["id"], "in_progress")
    go(mech, req["id"], "waiting_parts", "Заказали")
    ids = ids_of(mech, req["id"])
    r = mech.post("/purchases/bought", {"ids": [ids["Сальник"]]})
    assert r.json()["advanced"] == []
    assert mech.get(f"/requests/{req['id']}").json()["status"] == "waiting_parts"                 # одной детали мало
    r = mech.post("/purchases/bought", {"ids": [ids["Подшипник"]]})
    assert r.json()["advanced"] == [req["number"]]
    detail = mech.get(f"/requests/{req['id']}").json()
    assert detail["status"] == "in_progress"
    assert [e["to_status"] for e in detail["events"] if e["kind"] == "status"][-1] == "in_progress"
    assert any(e["text"] == "Все детали куплены" for e in detail["events"])


def test_request_in_other_status_is_not_moved_by_purchase(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1, "Новое", [part("Лампа")])
    mech.post("/purchases/bought", {"ids": [ids_of(mech, req["id"])["Лампа"]]})
    assert mech.get(f"/requests/{req['id']}").json()["status"] == "new"


def test_removing_last_unbought_part_releases_waiting_request(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    req = new_request(mech, world.va1, "Ждём", [part("Сальник"), part("Лишняя")])
    go(mech, req["id"], "diagnostics")
    go(boss, req["id"], "in_progress")
    go(mech, req["id"], "waiting_parts")
    ids = ids_of(mech, req["id"])
    mech.post("/purchases/bought", {"ids": [ids["Сальник"]]})
    mech.call("DELETE", f"/requests/{req['id']}/parts/{ids['Лишняя']}")                       # ждать больше нечего
    assert mech.get(f"/requests/{req['id']}").json()["status"] == "in_progress"


def test_restore_returns_part_and_request_waits_again(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    req = new_request(mech, world.va1, "Ремонт", [part("Колодки", 1, "комплект")])
    go(mech, req["id"], "diagnostics")
    go(boss, req["id"], "in_progress")
    pid = ids_of(mech, req["id"])["Колодки"]
    mech.post("/purchases/bought", {"ids": [pid]})
    r = mech.post("/purchases/restore", {"id": pid})
    assert r.status_code == 200 and r.json()["moved_to_waiting"] is True
    assert mech.get(f"/requests/{req['id']}").json()["status"] == "waiting_parts"
    assert [g["name"] for g in mech.get("/purchases").json()["items"]] == ["Колодки"] and mech.get("/purchases").json()["recent"] == []
    assert mech.post("/purchases/restore", {"id": pid}).status_code == 409                       # и так не куплена
    assert mech.post("/purchases/restore", {"id": 99999}).status_code == 404


def test_closed_and_finished_requests_cannot_be_changed_by_marks(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    req = new_request(mech, world.va1, "Ремонт", [part("Хомут"), part("Патрубок")])
    ids = ids_of(mech, req["id"])
    mech.post("/purchases/bought", {"ids": [ids["Хомут"]]})
    go(mech, req["id"], "diagnostics")
    go(boss, req["id"], "in_progress")
    go(mech, req["id"], "done")
    r = mech.post("/purchases/bought", {"ids": [ids["Патрубок"]]})
    assert r.status_code == 409 and r.json()["code"] == "request_closed"
    assert mech.post("/purchases/restore", {"id": ids["Хомут"]}).status_code == 409
    recent = mech.get("/purchases").json()["recent"]
    assert recent and recent[0]["can_restore"] is False                                           # вернуть уже нельзя — кнопки нет


def test_marking_is_all_or_nothing(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1, "Ремонт", [part("Гайка"), part("Шайба")])
    ids = ids_of(mech, req["id"])
    r = mech.post("/purchases/bought", {"ids": [ids["Гайка"], 424242]})
    assert r.status_code == 404
    assert mech.get(f"/requests/{req['id']}").json()["parts"]["bought"] == 0                      # ничего не отмечено
    assert mech.post("/purchases/bought", {"ids": []}).status_code == 422
    assert mech.post("/purchases/bought", {"ids": list(range(1, 202))}).status_code == 422
    assert mech.post("/purchases/bought", {"ids": ["x"]}).status_code == 422


def test_permissions_by_role(make_client, world):
    mech, acc, store, driver, disp, emp = (make_client(x) for x in ("mech_a", "acc_a", "store_a", "drv1", "disp_a", "emp_a"))
    req = new_request(mech, world.va1, "Ремонт", [part("Втулка")])
    pid = ids_of(mech, req["id"])["Втулка"]
    for c in (acc, store):
        assert c.get("/purchases").status_code == 200                                              # видят, чтобы знать, что нужно
        assert c.post("/purchases/bought", {"ids": [pid]}).status_code == 403                    # но отмечать покупку не могут
        assert c.post("/purchases/restore", {"id": pid}).status_code == 403
    for c in (driver, disp, emp):
        assert c.get("/purchases").status_code == 403
        assert c.get("/purchases/export.csv").status_code == 403
    assert store.get("/purchases/export.csv").status_code == 403
    assert acc.get("/purchases/export.csv").status_code == 403
    assert make_client().get("/purchases").status_code == 401
    assert mech.post("/purchases/bought", {"ids": [pid]}).status_code == 200


def test_other_legal_entity_sees_and_changes_nothing(make_client, world):
    mech, boss_b = make_client("mech_a"), make_client("dir_b")
    req = new_request(mech, world.va1, "Ремонт", [part("Втулка")])
    pid = ids_of(mech, req["id"])["Втулка"]
    assert boss_b.get("/purchases").json()["items"] == [] and boss_b.get("/purchases").json()["recent"] == []
    assert boss_b.get(f"/purchases?legal_entity_id={world.A}").status_code == 403
    assert boss_b.post("/purchases/bought", {"ids": [pid]}).status_code == 403
    assert boss_b.post("/purchases/restore", {"id": pid}).status_code == 404 or boss_b.post("/purchases/restore", {"id": pid}).status_code == 403
    assert mech.get("/purchases").json()["totals"]["positions"] == 1
    own = new_request(boss_b, world.vb1, "Свой ремонт", [part("Чужая втулка")])
    assert [g["name"] for g in boss_b.get("/purchases").json()["items"]] == ["Чужая втулка"] and own["number"]
    assert [g["name"] for g in make_client("admin").get("/purchases").json()["items"]] == ["Втулка", "Чужая втулка"]       # админ видит всё


def test_recent_list_keeps_30_days(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1, "Ремонт", [part("Старая покупка"), part("Свежая покупка")])
    ids = ids_of(mech, req["id"])
    mech.post("/purchases/bought", {"ids": list(ids.values())})
    with session_factory()() as db:
        row = db.get(RequestPart, ids["Старая покупка"])
        row.bought_at = row.bought_at - dt.timedelta(days=40)
        db.commit()
    assert [x["name"] for x in mech.get("/purchases").json()["recent"]] == ["Свежая покупка"]


def test_export_csv_contents_and_audit(make_client, world):
    mech = make_client("mech_a")
    new_request(mech, world.va1, "Первая", [part("Фильтр, масляный", 2, "шт", "WIX 1"), part("=ЗЛОЙ", 1)])
    new_request(mech, world.va2, "Вторая", [part("Фильтр, масляный", 1, "шт", "wix  1")])
    r = mech.get("/purchases/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = [line.split(";") for line in r.text.lstrip("﻿").strip().split("\r\n")]
    assert rows[0][:4] == ["Деталь", "Артикул", "Единица", "Всего"]
    body = [x for x in rows[1:] if "масляный" in x[0]]
    assert len(body) == 2 and all(x[3] == "3" for x in body)
    assert any(x[0].startswith("'=") for x in rows[1:])                                              # формулы Excel обезврежены
    with session_factory()() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "purchases.export")).details["rows"] == 3
    assert make_client("dir_b").get(f"/purchases/export.csv?legal_entity_id={world.A}").status_code == 403


def test_request_card_shows_request_status_in_purchase_line(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1, "Новое", [part("Лампа")])
    line = mech.get("/purchases").json()["items"][0]["lines"][0]
    assert line["status"] == "new" and line["approved"] is False and line["request_id"] == req["id"]
    with session_factory()() as db:
        assert db.get(ServiceRequest, req["id"]).status == "new"
