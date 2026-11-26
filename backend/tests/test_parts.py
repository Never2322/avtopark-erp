# -*- coding: utf-8 -*-
"""Детали в обращениях: список, права, юрлица, каталог с подсказками, «эту деталь уже меняли»."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, PartCatalog, RequestPart, ServiceRequest, User
from app.services.readings import add_reading


def new_request(c, vid, title="Течёт масло", parts=None):
    body = {"vehicle_id": vid, "title": title}
    if parts is not None:
        body["parts"] = parts
    r = c.post("/requests", body)
    assert r.status_code == 201, r.text
    return r.json()


def go(c, rid, to, comment="", **extra):
    return c.post(f"/requests/{rid}/transition", {"to_status": to, "comment": comment, **extra})


def part(name="Фильтр масляный", qty=1, unit="шт", article=""):
    return {"name": name, "qty": qty, "unit": unit, "article": article}


def add(c, rid, *items):
    return c.post(f"/requests/{rid}/parts", {"items": list(items)})


def catalog():
    with session_factory()() as db:
        return {c.name_key: c for c in db.scalars(select(PartCatalog))}


def test_mechanic_adds_parts_and_event_is_written(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1)
    r = add(mech, req["id"], part("Фильтр масляный", 2, "шт", "WIX 51085"), part("Масло моторное 10W-40", 24.5, "л"))
    assert r.status_code == 201, r.text
    block = r.json()
    assert block["total"] == 2 and block["to_buy"] == 2 and block["bought"] == 0 and block["can_add"] is True
    assert [i["name"] for i in block["items"]] == ["Фильтр масляный", "Масло моторное 10W-40"]
    assert block["items"][1]["qty"] == 24.5 and block["items"][1]["qty_text"] == "24,5"
    detail = mech.get(f"/requests/{req['id']}").json()
    assert detail["parts"]["total"] == 2
    assert any(e["kind"] == "edit" and "Добавлены детали" in e["text"] and "Фильтр масляный" in e["text"] for e in detail["events"])


def test_parts_can_be_given_when_request_is_created(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1, parts=[part("Ремень генератора", 1), part("Лампа H7", 2)])
    assert req["parts"]["total"] == 2
    bad = mech.post("/requests", {"vehicle_id": world.va1, "title": "Дубль деталей", "parts": [part("Лампа H7"), part("лампа  h7")]})
    assert bad.status_code == 409 and bad.json()["code"] == "duplicate_part"
    bad = mech.post("/requests", {"vehicle_id": world.va1, "title": "Пустая деталь", "parts": [{"name": " ", "qty": 1}]})
    assert bad.status_code == 422 and bad.json()["field"] == "name"
    with session_factory()() as db:                   # ничего лишнего не создано: ни обращения, ни деталей
        assert db.query(ServiceRequest).count() == 1 and db.query(RequestPart).count() == 2


def test_line_validation(make_client, world):
    mech = make_client("mech_a")
    req = new_request(mech, world.va1)
    rid = req["id"]
    cases = [(part(qty=0), "part_qty"), (part(qty=-3), "part_qty"), (part(qty=1.2345), "part_qty"), (part(qty="много"), "part_qty"),
             (part(qty=10_000_000), "part_qty"), (part(unit="штучек"), "part_unit"), (part(name=""), "part_name"),
             (part(name="---"), "part_name"), (part(name="Я" * 201), None)]
    for body, code in cases:
        r = add(mech, rid, body)
        assert r.status_code == 422, (body, r.text)
        if code:
            assert r.json()["code"] == code
    assert add(mech, rid, part(qty="2,5", unit="л")).status_code == 201          # запятая как у нас принято
    assert mech.get(f"/requests/{rid}").json()["parts"]["items"][0]["qty"] == 2.5
    assert mech.post(f"/requests/{rid}/parts", {"items": []}).status_code == 422


def test_duplicates_and_limit(make_client, world):
    mech = make_client("mech_a")
    rid = new_request(mech, world.va1)["id"]
    assert add(mech, rid, part("Фара левая")).status_code == 201
    r = add(mech, rid, part("ФАРА  левая!"))
    assert r.status_code == 409 and r.json()["code"] == "duplicate_part"
    assert add(mech, rid, part("Фара левая", unit="комплект")).status_code == 201     # другая единица — другая строка
    r = add(mech, rid, part("Болт М8"), part("Болт м8"))
    assert r.status_code == 409
    many = [part(f"Деталь номер {i}") for i in range(58)]
    assert add(mech, rid, *many).status_code == 201                                   # 2 + 58 = 60
    r = add(mech, rid, part("Лишняя деталь"))
    assert r.status_code == 422 and r.json()["code"] == "too_many_parts"
    assert mech.get(f"/requests/{rid}").json()["parts"]["can_add"] is False


def test_who_may_change_the_list(make_client, world):
    driver, driver2, mech, acc, boss_b = (make_client(x) for x in ("drv1", "drv2", "mech_a", "acc_a", "dir_b"))
    req = new_request(driver, world.va1)
    rid = req["id"]
    assert add(driver, rid, part("Колодки тормозные", 1, "комплект")).status_code == 201         # автор заявки дописал
    assert add(driver2, rid, part("Чужая деталь")).status_code == 404                              # чужой водитель заявку даже не видит
    assert add(boss_b, rid, part("Деталь из другой фирмы")).status_code == 404                     # другое юрлицо
    assert add(acc, rid, part("Деталь бухгалтера")).status_code == 403                             # видит, но не ведёт обращения
    go(mech, rid, "diagnostics")
    assert add(driver, rid, part("Ещё одна")).status_code == 201                                   # диагностика — ещё можно
    go(mech, rid, "approval")
    r = add(driver, rid, part("После согласования"))
    assert r.status_code == 403                                                                    # дальше только ведущий
    assert add(mech, rid, part("После согласования")).status_code == 201
    assert driver.get(f"/requests/{rid}/parts").json()["can_add"] is False
    assert acc.get(f"/requests/{rid}/parts").json()["total"] == 3


def test_remove_part_is_soft_and_bought_part_is_locked(make_client, world):
    mech = make_client("mech_a")
    rid = new_request(mech, world.va1)["id"]
    items = add(mech, rid, part("Свеча"), part("Прокладка")).json()["items"]
    r = mech.call("DELETE", f"/requests/{rid}/parts/{items[0]['id']}")
    assert r.status_code == 200 and [i["name"] for i in r.json()["items"]] == ["Прокладка"]
    assert mech.call("DELETE", f"/requests/{rid}/parts/{items[0]['id']}").status_code == 404      # второй раз — уже нет
    assert mech.call("DELETE", f"/requests/{rid}/parts/99999").status_code == 404
    with session_factory()() as db:                                                                   # строка осталась с отметкой кто и когда убрал
        row = db.get(RequestPart, items[0]["id"])
        assert row.removed_at is not None and row.removed_by_id is not None
    mech.post("/purchases/bought", {"ids": [items[1]["id"]]})
    r = mech.call("DELETE", f"/requests/{rid}/parts/{items[1]['id']}")
    assert r.status_code == 409 and r.json()["code"] == "part_bought"
    # убрав деталь, можно добавить такую же заново
    assert add(mech, rid, part("Свеча")).status_code == 201


def test_finished_request_keeps_its_list(make_client, world):
    boss, mech = make_client("dir_a"), make_client("mech_a")
    rid = new_request(mech, world.va1)["id"]
    pid = add(mech, rid, part("Сальник")).json()["items"][0]["id"]
    go(mech, rid, "diagnostics")
    go(boss, rid, "in_progress")
    go(mech, rid, "done", "Готово")
    assert add(mech, rid, part("Опоздавшая")).status_code == 409
    assert mech.call("DELETE", f"/requests/{rid}/parts/{pid}").status_code == 409
    go(boss, rid, "closed")
    assert add(boss, rid, part("Закрытая")).status_code == 409


def test_names_are_remembered_in_catalog(make_client, world):
    mech = make_client("mech_a")
    rid = new_request(mech, world.va1)["id"]
    add(mech, rid, part("Фильтр топливный", 1, "шт"))
    cat = catalog()
    assert cat["фильтр топливный"].uses == 1 and cat["фильтр топливный"].article == ""
    rid2 = new_request(mech, world.va2)["id"]
    add(mech, rid2, part("фильтр  топливный", 1, "шт", "PL 270"))
    cat = catalog()
    assert len(cat) == 1 and cat["фильтр топливный"].uses == 2
    assert cat["фильтр топливный"].article == "PL 270"                      # артикул подтянулся, название осталось как первое
    assert cat["фильтр топливный"].name == "Фильтр топливный"
    add(mech, new_request(mech, world.va3)["id"], part("Фильтр топливный", 1, "шт", "Другой артикул"))
    assert catalog()["фильтр топливный"].article == "PL 270"                # уже известный артикул не затирается


def test_part_options_autocomplete(make_client, world):
    mech, driver, store = make_client("mech_a"), make_client("drv1"), make_client("store_a")
    rid = new_request(mech, world.va1)["id"]
    add(mech, rid, part("Фильтр воздушный"), part("Фильтр масляный", article="WIX 1"), part("Шланг радиатора"))
    rid2 = new_request(mech, world.va2)["id"]
    add(mech, rid2, part("Фильтр масляный"))
    r = mech.get("/part-options?q=фильтр").json()["items"]
    assert [i["name"] for i in r] == ["Фильтр масляный", "Фильтр воздушный"]      # частые выше
    assert r[0]["uses"] == 2 and r[0]["article"] == "WIX 1"
    assert [i["name"] for i in mech.get("/part-options?q=wix").json()["items"]] == ["Фильтр масляный"]    # поиск и по артикулу
    assert mech.get("/part-options?q=%25").json()["items"] == []                    # знак % не шаблон
    assert driver.get("/part-options?q=фил").status_code == 200                      # водитель вносит детали в заявку
    assert store.get("/part-options").status_code == 200                              # кладовщик видит каталог
    assert make_client().get("/part-options").status_code == 401


def test_catalog_section_permissions_and_unique_name(make_client, world):
    boss, mech, acc, store, driver = (make_client(x) for x in ("dir_a", "mech_a", "acc_a", "store_a", "drv1"))
    assert driver.get("/parts").status_code == 403
    assert store.get("/parts").status_code == 200 and acc.get("/parts").status_code == 200
    assert store.post("/parts", {"name": "Подшипник", "unit": "шт"}).status_code == 403
    assert acc.post("/parts", {"name": "Подшипник", "unit": "шт"}).status_code == 403
    r = mech.post("/parts", {"name": "Подшипник ступицы", "unit": "шт", "article": "SKF 1", "category": "Ходовая"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    dup = boss.post("/parts", {"name": "подшипник  ступицы", "unit": "шт"})
    assert dup.status_code == 422 and "уже есть" in dup.json()["fields"]["name"]
    assert "name" in boss.post("/parts", {"name": "!!!", "unit": "шт"}).json()["fields"]
    assert mech.patch(f"/parts/{pid}", {"name": "Подшипник ступицы задний"}).status_code == 200
    assert "подшипник ступицы задний" in catalog() and "подшипник ступицы" not in catalog()
    assert mech.post(f"/parts/{pid}/void", {"reason": "Дубль"}).status_code == 200            # убрали — имя снова свободно
    assert boss.post("/parts", {"name": "Подшипник ступицы задний", "unit": "шт"}).status_code == 201
    labels = {a.action for a in _audit()}
    assert {"parts.create", "parts.update", "parts.void"} <= labels


def test_catalog_is_shared_between_legal_entities_but_requests_are_not(make_client, world):
    boss_b, mech = make_client("dir_b"), make_client("mech_a")
    assert boss_b.get("/parts").status_code == 200                                  # справочник общий, как каталог товаров
    rid = new_request(mech, world.va1)["id"]
    add(mech, rid, part("Секретная деталь"))
    assert boss_b.get(f"/requests/{rid}/parts").status_code == 404                   # а список деталей обращения — только своего юрлица


def _audit():
    with session_factory()() as db:
        return list(db.scalars(select(AuditLog)))


def test_audit_log_has_parts_actions_with_labels(make_client, world):
    from app.routers.audit_log import ACTIONS
    mech = make_client("mech_a")
    rid = new_request(mech, world.va1, parts=[part("Диск тормозной", 2)])["id"]
    pid = add(mech, rid, part("Суппорт")).json()["items"][1]["id"]
    mech.call("DELETE", f"/requests/{rid}/parts/{pid}")
    rows = {a.action: a for a in _audit()}
    assert rows["request_part.add"].details["parts"] == ["Суппорт"] and rows["request_part.remove"].details["part"] == "Суппорт"
    assert rows["request.create"].details["parts"] == ["Диск тормозной"]
    assert all(a in ACTIONS for a in ("request_part.add", "request_part.remove", "purchase.bought", "purchase.restore", "purchases.export"))


# ---------------------------------------------------------------- «эту деталь уже меняли»

def _age_request(rid, days):
    with session_factory()() as db:
        r = db.get(ServiceRequest, rid)
        r.created_at = r.created_at - dt.timedelta(days=days)
        db.commit()


def _reading(vid, value, days_ago):
    with session_factory()() as db:
        who = SimpleNamespace(user=db.get(User, 1), ip="test")
        from app.models import Vehicle
        add_reading(db, who, db.get(Vehicle, vid), "km", Decimal(value), recorded_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago))
        db.commit()


def test_hint_shows_open_request_with_same_part(make_client, world):
    mech = make_client("mech_a")
    first = new_request(mech, world.va1, "Первая")
    add(mech, first["id"], part("Фильтр масляный", article="WIX 1"))
    second = new_request(mech, world.va1, "Вторая")
    r = add(mech, second["id"], part("фильтр масляный"))
    item = r.json()["items"][0]
    assert item["hint"] == f"Уже есть в открытом обращении № {first['number']}" and item["hint_open"] is True
    other_car = new_request(mech, world.va2, "Другая машина")
    assert add(mech, other_car["id"], part("Фильтр масляный")).json()["items"][0]["hint"] == ""     # у другой машины подсказки нет
    by_article = add(mech, second["id"], part("Масляный фильтр WIX", article="wix-1")).json()["items"][1]
    assert by_article["hint"].startswith("Уже есть в открытом обращении")                              # нашли и по артикулу


def test_hint_after_previous_repair_counts_distance(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    _reading(world.va1, 500, 45)
    old = new_request(mech, world.va1, "Старый ремонт")
    add(mech, old["id"], part("Сцепление в сборе", 1, "комплект"))
    go(mech, old["id"], "diagnostics")
    go(boss, old["id"], "in_progress")
    go(mech, old["id"], "done")
    go(boss, old["id"], "closed")
    _age_request(old["id"], 40)
    _reading(world.va1, 1500, 0)
    new = new_request(mech, world.va1, "Снова сцепление")
    item = add(mech, new["id"], part("Сцепление в сборе", 1, "комплект")).json()["items"][0]
    day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).astimezone().strftime("%d.%m.%Y")
    assert item["hint"].startswith(f"Уже меняли: ") and f"обращение № {old['number']}" in item["hint"]
    assert item["hint"].endswith(f"(обращение № {old['number']}), с тех пор 1" + chr(160) + "000 км")                    # километры с тех пор
    assert item["hint_open"] is False and day[:2].isdigit()


def test_cancelled_requests_and_removed_parts_do_not_count(make_client, world):
    mech = make_client("mech_a")
    gone = new_request(mech, world.va1, "Отменённое")
    add(mech, gone["id"], part("Ремкомплект"))
    go(mech, gone["id"], "cancelled", "Не нужно")
    removed = new_request(mech, world.va1, "С убранной деталью")
    pid = add(mech, removed["id"], part("Ремкомплект")).json()["items"][0]["id"]
    mech.call("DELETE", f"/requests/{removed['id']}/parts/{pid}")
    cur = new_request(mech, world.va1, "Текущее")
    assert add(mech, cur["id"], part("Ремкомплект")).json()["items"][0]["hint"] == ""


def test_driver_does_not_get_hints_about_other_requests(make_client, world):
    mech, driver = make_client("mech_a"), make_client("drv1")
    old = new_request(mech, world.va1, "Чужое обращение")
    add(mech, old["id"], part("Стартер"))
    mine = new_request(driver, world.va1, "Не крутит стартер")
    block = add(driver, mine["id"], part("Стартер")).json()
    assert block["items"][0]["hint"] == ""            # водитель не видит других обращений — и номеров из них тоже
    assert make_client("dir_a").get(f"/requests/{mine['id']}/parts").json()["items"][0]["hint"].startswith("Уже есть")


def test_request_can_be_closed_at_any_stage(world, make_client):
    c = make_client("admin")
    r = new_request(c, world.va1)
    assert "closed" in [x["status"] for x in c.get(f"/requests/{r['id']}").json()["allowed_transitions"]]
    assert go(c, r["id"], "closed").status_code == 422                     # не выполнено — нужна причина
    x = go(c, r["id"], "closed", "Сделали сами, без ремонта")
    assert x.status_code == 200, x.text
    assert x.json()["status"] == "closed" and x.json()["closed_at"]
