# -*- coding: utf-8 -*-
"""Маршруты мусоровозов: название и улицы, расписание по дням недели и на одну дату, «Мои маршруты» водителя
и «Маршруты по дням» директора. Запись на дату заменяет обычное расписание того же маршрута в этот день."""
import datetime as dt

from sqlalchemy import select

from app.common import org_today
from app.db import session_factory
from app.enums import WEEKDAY_FIELDS
from app.models import Vehicle, VehicleAssignment, VehicleType
from app.services import routes as svc

TODAY = org_today()
DAY = WEEKDAY_FIELDS[TODAY.weekday()]
NEXT_WEEK = TODAY + dt.timedelta(days=7)
WEEK_AGO = TODAY - dt.timedelta(days=7)


def waste(plate, entity, driver=None, status="in_work"):
    """Мусоровоз в юрлице; driver: сотрудник, которого закрепляем."""
    with session_factory()() as db:
        t = db.scalar(select(VehicleType.id).where(VehicleType.code == "truck_waste"))
        v = Vehicle(legal_entity_id=entity, type_id=t, plate=plate, make="КО", model="440", status=status)
        db.add(v)
        db.flush()
        if driver:
            db.add(VehicleAssignment(vehicle_id=v.id, employee_id=driver, started_on=TODAY - dt.timedelta(days=30)))
        db.commit()
        return v.id


def route(c, name, streets="", entity=None, **extra):
    body = {"name": name, "streets": streets, **extra}
    if entity is not None:
        body["legal_entity_id"] = entity
    return c.post("/garbage_routes", body)


def plan(c, route_id, vehicle_id, **extra):
    return c.post("/route_plan", {"route_id": route_id, "vehicle_id": vehicle_id, **extra})


def board(c, date=None, **q):
    params = ("?" + "&".join(f"{k}={v}" for k, v in {"date": date, **q}.items() if v)) if (date or q) else ""
    return c.get("/route-board/day" + params)


def mine(c):
    r = c.get("/route-board/mine")
    assert r.status_code == 200, r.text
    return r.json()


def day_of(m, date):
    return next(d for d in m["days"] if d["date"] == date.isoformat())


# ---------------------------------------------------------------- обычное расписание: директор и водитель

def test_weekly_route_is_visible_to_director_and_driver(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    dr = make_client("dir_a")
    r = route(dr, "Центр-1", "ул. Ленина\nул. Мира\nпр. Победы, 1-25", entity=world.A)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    p = plan(dr, rid, w1, kind="weekly", **{DAY: True})
    assert p.status_code == 201, p.text
    assert p.json()["when"] and p.json()["route_id_label"] == "Центр-1"

    b = board(dr).json()
    assert b["date"] == TODAY.isoformat() and b["totals"] == {"routes": 1, "trucks": 1, "routes_all": 1}
    g = b["routes"][0]
    assert g["route"] == "Центр-1" and g["streets"] == ["ул. Ленина", "ул. Мира", "пр. Победы, 1-25"]
    t = g["trucks"][0]
    assert t["plate"] == "М001КО54" and t["driver"].startswith("Первый") and t["source"] == "weekly" and t["status"] == "in_work"
    assert board(dr, (TODAY + dt.timedelta(days=1)).isoformat()).json()["routes"] == []         # завтра этого дня недели нет

    d = make_client("drv1")
    assert d.me["features"]["routes"] is True
    assert d.get("/driver/home").json()["can"]["routes"] is True
    m = mine(d)
    today = day_of(m, TODAY)
    assert today["is_today"] and [i["route"] for i in today["items"]] == ["Центр-1"]
    assert today["items"][0]["streets"] == ["ул. Ленина", "ул. Мира", "пр. Победы, 1-25"]
    assert [i["route"] for i in day_of(m, NEXT_WEEK)["items"]] == ["Центр-1"]                  # в этот же день недели через неделю
    wk = m["week"][TODAY.weekday()]
    assert [i["route"] for i in wk["items"]] == ["Центр-1"] and sum(len(x["items"]) for x in m["week"]) == 1


def test_a_truck_can_drive_two_routes_and_a_route_can_have_two_trucks(make_client, world):
    w1, w2 = waste("М001КО54", world.A, driver=world.d1), waste("М002КО54", world.A)
    dr = make_client("dir_a")
    r1 = route(dr, "Север", "ул. Северная", entity=world.A).json()["id"]
    r2 = route(dr, "Юг", "ул. Южная", entity=world.A).json()["id"]
    for rid, v in ((r1, w1), (r2, w1), (r1, w2)):
        assert plan(dr, rid, v, kind="weekly", **{DAY: True}).status_code == 201
    b = board(dr).json()
    assert b["totals"] == {"routes": 2, "trucks": 2, "routes_all": 2}
    by = {g["route"]: sorted(t["plate"] for t in g["trucks"]) for g in b["routes"]}
    assert by == {"Север": ["М001КО54", "М002КО54"], "Юг": ["М001КО54"]}
    assert sorted(i["route"] for i in day_of(mine(make_client("drv1")), TODAY)["items"]) == ["Север", "Юг"]


# ---------------------------------------------------------------- запись на дату заменяет обычное расписание маршрута

def test_dated_plan_replaces_regular_schedule_of_that_route_only(make_client, world):
    w1, w2 = waste("М001КО54", world.A, driver=world.d1), waste("М002КО54", world.A, driver=world.d2)
    dr = make_client("dir_a")
    a = route(dr, "А", "ул. А", entity=world.A).json()["id"]
    b_ = route(dr, "Б", "ул. Б", entity=world.A).json()["id"]
    assert plan(dr, a, w1, kind="weekly", **{DAY: True}).status_code == 201          # обычно маршрут А едет w1
    assert plan(dr, b_, w1, kind="weekly", **{DAY: True}).status_code == 201         # и маршрут Б тоже w1
    assert plan(dr, a, w2, kind="date", on_date=TODAY.isoformat(), note="подмена").status_code == 201   # сегодня А вместо w1 едет w2

    got = {g["route"]: [(t["plate"], t["source"], t["note"]) for t in g["trucks"]] for g in board(dr).json()["routes"]}
    assert got == {"А": [("М002КО54", "date", "подмена")], "Б": [("М001КО54", "weekly", "")]}

    d1 = mine(make_client("drv1"))
    assert [i["route"] for i in day_of(d1, TODAY)["items"]] == ["Б"]                 # w1 сегодня только на «Б»
    assert [i["route"] for i in day_of(d1, NEXT_WEEK)["items"]] == ["А", "Б"]        # через неделю всё как обычно
    d2 = day_of(mine(make_client("drv2")), TODAY)["items"]                          # водитель подменяющей машины тоже видит свою замену
    assert [(i["route"], i["source"]) for i in d2] == [("А", "date")]


def test_dated_plan_for_past_or_other_day_does_not_affect_today(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    dr = make_client("dir_a")
    a = route(dr, "А", "ул. А", entity=world.A).json()["id"]
    plan(dr, a, w1, kind="weekly", **{DAY: True})
    plan(dr, a, w1, kind="date", on_date=WEEK_AGO.isoformat())
    assert [g["route"] for g in board(dr).json()["routes"]] == ["А"]
    assert board(dr, WEEK_AGO.isoformat()).json()["routes"][0]["trucks"][0]["source"] == "date"


# ---------------------------------------------------------------- проверка ввода

def test_plan_validation(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    dr = make_client("dir_a")
    a = route(dr, "А", "ул. А", entity=world.A).json()["id"]
    assert plan(dr, a, w1, kind="weekly").json()["fields"]["mon"] == "Отметьте хотя бы один день недели"
    assert plan(dr, a, w1, kind="date").json()["fields"]["on_date"] == "Заполните поле"
    assert plan(dr, a, w1, kind="weekly", mon=True, tue=True).status_code == 201
    dup = plan(dr, a, w1, kind="weekly", tue=True, wed=True)
    assert dup.status_code == 422 and "вт" in dup.json()["fields"]["mon"]
    assert plan(dr, a, w1, kind="weekly", wed=True).status_code == 201               # другие дни — можно
    assert plan(dr, a, w1, kind="date", on_date=TODAY.isoformat()).status_code == 201
    again = plan(dr, a, w1, kind="date", on_date=TODAY.isoformat())
    assert again.status_code == 422 and "on_date" in again.json()["fields"]
    # у записи «на дату» дни недели не хранятся, у обычной нет даты
    row = dr.get("/route_plan").json()["items"]
    assert all(not any(r[k] for k in WEEKDAY_FIELDS) for r in row if r["kind"] == "date")
    assert all(r["on_date"] is None for r in row if r["kind"] == "weekly")
    # маршрут и машина из разных юрлиц
    wb = waste("В001КО54", world.B)
    assert plan(dr, a, wb, kind="weekly", mon=True).json()["fields"] == {"vehicle_id": "Выберите значение из списка"}     # директор А чужую машину не видит
    bad = plan(make_client("admin"), a, wb, kind="weekly", mon=True)                                               # а админ видит, но юрлица должны совпасть
    assert bad.status_code == 422 and "route_id" in bad.json()["fields"]
    # дата вне разумных пределов
    assert plan(dr, a, w1, kind="date", on_date="1999-01-01").status_code == 422


def test_route_name_is_unique_and_archive_is_blocked_while_it_is_in_schedule(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    dr = make_client("dir_a")
    a = route(dr, "Центр-1", "ул. Ленина", entity=world.A).json()["id"]
    dup = route(dr, "  центр-1 ", entity=world.A)
    assert dup.status_code == 422 and dup.json()["fields"]["name"] == "Маршрут с таким названием уже есть"
    assert route(dr, "Центр-2", entity=world.B).status_code in (403, 422)                # чужое юрлицо
    p = plan(dr, a, w1, kind="weekly", mon=True).json()["id"]
    blocked = dr.post(f"/garbage_routes/{a}/void", {"reason": "не нужен"})
    assert blocked.status_code == 409 and "стоит в расписании" in blocked.json()["message"], blocked.text
    assert dr.post(f"/route_plan/{p}/void", {"reason": "ошибка"}).status_code == 200
    assert dr.post(f"/garbage_routes/{a}/void", {"reason": "не нужен"}).status_code == 200
    ids = [x["id"] for x in dr.get("/options/route").json()["items"]]
    assert a not in ids
    assert route(dr, "Центр-1", "ул. Мира", entity=world.A).status_code == 201       # имя архивного маршрута освободилось


def test_archived_plan_and_archived_vehicle_are_ignored(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    dr = make_client("dir_a")
    a = route(dr, "А", "ул. А", entity=world.A).json()["id"]
    p = plan(dr, a, w1, kind="weekly", **{DAY: True}).json()["id"]
    assert len(board(dr).json()["routes"]) == 1
    dr.post(f"/route_plan/{p}/void", {"reason": "ошибка"})
    b = board(dr).json()
    assert b["routes"] == [] and [r["route"] for r in b["routes_without_truck"]] == ["А"]
    assert mine(make_client("drv1"))["has_any"] is False
    dr.post(f"/route_plan/{p}/restore", {})
    assert len(board(dr).json()["routes"]) == 1
    with session_factory()() as db:
        db.get(Vehicle, w1).archived_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    assert board(dr).json()["routes"] == []


# ---------------------------------------------------------------- «без маршрута», «без машины», ремонт

def test_board_lists_idle_trucks_unserved_routes_and_repair_flag(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1, status="in_repair")
    w2 = waste("М002КО54", world.A, driver=world.d2)
    dr = make_client("dir_a")
    a = route(dr, "А", "ул. А", entity=world.A).json()["id"]
    route(dr, "Б", "ул. Б", entity=world.A)
    plan(dr, a, w1, kind="weekly", **{DAY: True})
    b = board(dr).json()
    assert [r["route"] for r in b["routes_without_truck"]] == ["Б"]
    assert [t["plate"] for t in b["trucks_without_route"]] == ["М002КО54"] and b["trucks_without_route"][0]["driver"].startswith("Второй")
    t = b["routes"][0]["trucks"][0]
    assert t["plate"] == "М001КО54" and t["status"] == "in_repair" and t["status_name"] == "В ремонте"


# ---------------------------------------------------------------- водитель видит только своё

def test_driver_sees_only_routes_of_own_trucks(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    w2 = waste("М002КО54", world.A, driver=world.d2)
    dr = make_client("dir_a")
    a = route(dr, "Мой", "ул. Моя", entity=world.A).json()["id"]
    b_ = route(dr, "Чужой", "ул. Чужая", entity=world.A).json()["id"]
    plan(dr, a, w1, kind="weekly", **{DAY: True})
    plan(dr, b_, w2, kind="weekly", **{DAY: True})
    plan(dr, b_, w2, kind="date", on_date=NEXT_WEEK.isoformat())
    m = mine(make_client("drv1"))
    text = str(m)
    assert "Мой" in text and "Чужой" not in text and "ул. Чужая" not in text
    assert any(v["id"] == w1 for v in m["vehicles"]) and not any(v["id"] == w2 for v in m["vehicles"])
    m2 = mine(make_client("drv2"))
    assert "Чужой" in str(m2) and "Мой" not in str(m2)


def test_driver_without_routes_has_no_tab_and_no_access(make_client, world):
    d = make_client("drv2")                                                          # только самосвал, расписания нет
    assert d.me["features"]["routes"] is False
    assert d.get("/driver/home").json()["can"]["routes"] is False
    assert d.get("/route-board/mine").status_code == 403
    waste("М002КО54", world.A, driver=world.d2)                                      # закрепили мусоровоз: вкладка появилась
    d2 = make_client("drv2")
    assert d2.me["features"]["routes"] is True
    m = mine(d2)
    assert m["has_any"] is False and all(not x["items"] for x in m["days"])


def test_driver_cannot_open_office_screens_or_edit(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    dr = make_client("dir_a")
    a = route(dr, "А", "ул. А", entity=world.A).json()["id"]
    plan(dr, a, w1, kind="weekly", **{DAY: True})
    d = make_client("drv1")
    for url in ("/route-board/day", "/route_plan", "/garbage_routes"):
        assert d.get(url).status_code == 403, url
    assert d.get("/options/route").json()["items"] == []                              # выбирать маршруты водителю не из чего
    assert d.post("/route_plan", {"route_id": a, "vehicle_id": w1, "kind": "weekly", "mon": True}).status_code == 403
    assert d.post("/garbage_routes", {"name": "Х"}).status_code == 403


# ---------------------------------------------------------------- роли и юрлица

def test_roles_and_legal_entity_isolation(make_client, world):
    wa = waste("М001КО54", world.A, driver=world.d1)
    wb = waste("В001КО54", world.B, driver=world.d3)
    da, db_ = make_client("dir_a"), make_client("dir_b")
    ra = route(da, "А-маршрут", "ул. А", entity=world.A).json()["id"]
    rb = route(db_, "Б-маршрут", "ул. Б", entity=world.B).json()["id"]
    plan(da, ra, wa, kind="weekly", **{DAY: True})
    plan(db_, rb, wb, kind="weekly", **{DAY: True})
    assert [g["route"] for g in board(da).json()["routes"]] == ["А-маршрут"]
    assert [g["route"] for g in board(db_).json()["routes"]] == ["Б-маршрут"]
    assert "Б-маршрут" not in str(da.get("/garbage_routes").json()) and "А-маршрут" not in str(db_.get("/route_plan").json())
    assert db_.patch(f"/garbage_routes/{ra}", {"note": "чужое"}).status_code in (403, 404)
    assert db_.post(f"/garbage_routes/{ra}/void", {"reason": "чужое"}).status_code in (403, 404)
    assert da.post("/route_plan", {"route_id": rb, "vehicle_id": wb, "kind": "weekly", "mon": True}).status_code in (403, 422)
    assert board(da, legal_entity_id=world.B).status_code == 403
    admin = make_client("admin")
    assert sorted(g["route"] for g in board(admin).json()["routes"]) == ["А-маршрут", "Б-маршрут"]

    disp = make_client("disp_a")                                                      # диспетчер ведёт маршруты и расписание, но не архивирует
    assert board(disp).status_code == 200
    mk = route(disp, "Д", "ул. Д", entity=world.A)
    assert mk.status_code == 201
    assert plan(disp, mk.json()["id"], wa, kind="weekly", tue=True).status_code == 201
    assert disp.post(f"/garbage_routes/{mk.json()['id']}/void", {"reason": "не нужен"}).status_code == 403

    for login in ("mech_a", "acc_a", "emp_a", "store_a"):
        c = make_client(login)
        assert board(c).status_code == 403, login
        assert c.get("/route_plan").status_code == 403, login
        assert c.me["features"]["routes"] is False


def test_board_date_limits(make_client, world):
    dr = make_client("dir_a")
    assert board(dr, (TODAY + dt.timedelta(days=30)).isoformat()).status_code == 200
    assert board(dr, (TODAY + dt.timedelta(days=1000)).isoformat()).status_code == 422
    assert board(dr, (TODAY - dt.timedelta(days=1000)).isoformat()).status_code == 422
    assert board(dr, "не дата").status_code == 422
    empty = board(dr).json()
    assert empty["routes"] == [] and empty["totals"]["routes_all"] == 0


# ---------------------------------------------------------------- тексты

def test_streets_and_weekday_texts():
    assert svc.streets_list("ул. Ленина\r\n ул. Мира ;пр. Победы\n\n") == ["ул. Ленина", "ул. Мира", "пр. Победы"]
    assert svc.streets_list("ул. Ленина, ул. Мира, ул. Садовая") == ["ул. Ленина", "ул. Мира", "ул. Садовая"]
    assert svc.streets_list("пр. Победы, 1-25\nул. Мира") == ["пр. Победы, 1-25", "ул. Мира"]       # запятая внутри строки при нескольких строках не делит
    assert svc.streets_list("") == [] and svc.streets_list(None) == []
    assert svc.weekdays_text([0, 1, 2, 3, 4]) == "пн–пт"
    assert svc.weekdays_text([0, 2, 4]) == "пн, ср, пт"
    assert svc.weekdays_text([0, 1]) == "пн, вт"
    assert svc.weekdays_text([0, 1, 2, 4, 5]) == "пн–ср, пт, сб"
    assert svc.weekdays_text(list(range(7))) == "каждый день"
    assert svc.weekdays_text([6]) == "вс"


def test_registry_meta_and_list_columns(make_client, world):
    w1 = waste("М001КО54", world.A, driver=world.d1)
    dr = make_client("dir_a")
    a = route(dr, "Длинный", "\n".join(f"ул. {i}" for i in range(1, 8)), entity=world.A).json()
    assert a["streets_short"].endswith("и ещё 4")
    plan(dr, a["id"], w1, kind="weekly", mon=True, tue=True, wed=True, fri=True)
    row = dr.get("/route_plan").json()["items"][0]
    assert row["when"] == "пн–ср, пт" and row["route_id_label"] == "Длинный" and "М001КО54" in row["vehicle_id_label"]
    meta = dr.get("/route_plan/meta").json()
    names = [f["name"] for f in meta["fields"]]
    assert names[:3] == ["route_id", "vehicle_id", "kind"] and set(WEEKDAY_FIELDS) <= set(names)
    # смена вида записи с «по дням» на «на дату» убирает дни
    pid = row["id"]
    ch = dr.patch(f"/route_plan/{pid}", {"kind": "date", "on_date": TODAY.isoformat()})
    assert ch.status_code == 200, ch.text
    assert ch.json()["when"].startswith(TODAY.strftime("%d.%m.%Y")) and not any(ch.json()[k] for k in WEEKDAY_FIELDS)
