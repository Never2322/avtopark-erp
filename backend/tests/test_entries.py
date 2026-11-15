# -*- coding: utf-8 -*-
"""Записи водителей: рейсы и заправки — проверки, расход, пробег в показания, фото, права, изоляция юрлиц, повторная отправка, журнал."""
import datetime as dt
import json

from sqlalchemy import func, select

from app.db import session_factory
from app.models import AuditLog, EntryPhoto, FuelFill, Trip, Vehicle, VehicleReading
from tests.test_dossier import PDF, png

TODAY = dt.date.today()


def day(n: int = 0) -> str:
    return (TODAY - dt.timedelta(days=n)).isoformat()


def trip_body(w, vehicle=None, **kw):
    body = {"trip_date": day(), "vehicle_id": vehicle or w.va1, "route": "Склад — карьер Северный", "customer": "ООО Север",
            "cargo": "Щебень", "cargo_kg": "20000", "doc_number": "ТН-17", "km": 42}
    body.update(kw)
    return body


def fuel_body(w, vehicle=None, **kw):
    body = {"fill_date": day(), "vehicle_id": vehicle or w.va1, "liters": "80", "amount": "4400", "odometer": 1000, "full_tank": True,
            "station": "Лукойл"}
    body.update(kw)
    return body


def drv_post(c, kind, body, files=None, client_id=None):
    data = dict(body)
    if client_id:
        data["client_id"] = client_id
    return c.call("POST", f"/entries/mine/{kind}", data={"data": json.dumps(data)},
                  files=[("files", f) for f in (files or [])] or None)


def set_specs(w, c, vehicle, **kw):
    return c.put(f"/dossier/vehicles/{vehicle}/specs", kw)


def actions(*names):
    with session_factory()() as db:
        return db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(names)))


# ---------------------------------------------------------------- рейсы: сотрудник

def test_staff_trip_validation_and_defaults(world, make_client):
    d = make_client("disp_a")
    r = d.post("/trips", trip_body(world))
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["legal_entity_id"] == world.A
    assert t["driver_id"] == world.d1            # водитель подставился по закреплению машины
    assert t["driver_id_label"].startswith("Первый")

    for bad, field in ((dict(route="  "), "route"), (dict(trip_date=day(-5)), "trip_date"), (dict(trip_date=day(500)), "trip_date"),
                       (dict(km=99999), "km"), (dict(km=-1), "km"), (dict(cargo_kg="-5"), "cargo_kg")):
        r = d.post("/trips", trip_body(world, **bad))
        assert r.status_code == 422 and field in r.json()["fields"], (bad, r.text)
    # техника другого юрлица
    assert d.post("/trips", trip_body(world, vehicle=world.vb1)).status_code == 422
    # лишние пробелы
    r = d.post("/trips", trip_body(world, route="  Склад   —   объект ", customer=" ООО   Юг "))
    assert r.json()["route"] == "Склад — объект" and r.json()["customer"] == "ООО Юг"


def test_staff_can_enter_old_trip_but_driver_cannot(world, make_client):
    d = make_client("disp_a")
    assert d.post("/trips", trip_body(world, trip_date=day(90))).status_code == 201
    drv = make_client("drv1")
    r = drv_post(drv, "trip", trip_body(world, trip_date=day(90)))
    assert r.status_code == 422 and "trip_date" in r.json()["fields"]
    assert drv_post(drv, "trip", trip_body(world, trip_date=day(5))).status_code == 201


# ---------------------------------------------------------------- заправки: пробег и расход

def test_fuel_odometer_goes_to_readings_and_cannot_decrease(world, make_client):
    d = make_client("disp_a")
    r = d.post("/fuel_fills", fuel_body(world, odometer=1000))
    assert r.status_code == 201, r.text
    with session_factory()() as db:
        v = db.get(Vehicle, world.va1)
        assert v.current_km == 1000
        rd = db.scalars(select(VehicleReading).where(VehicleReading.vehicle_id == v.id)).all()
        assert sorted(x.source for x in rd) == ["fuel", "manual"]      # стартовое показание машины и показание с заправки
    # пробег меньше прошлого: заправка не сохраняется, ошибка на поле «пробег»
    before = actions("fuel_fills.create")
    r = d.post("/fuel_fills", fuel_body(world, odometer=900, fill_date=day()))
    assert r.status_code == 422 and r.json()["field"] == "odometer", r.text
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(FuelFill)) == 1
    assert actions("fuel_fills.create") == before


def test_fuel_validation(world, make_client):
    d = make_client("disp_a")
    for bad, field in ((dict(liters="0"), "liters"), (dict(liters="-3"), "liters"), (dict(liters="5000"), "liters"),
                       (dict(amount="-1"), "amount"), (dict(fill_date=day(-3)), "fill_date"), (dict(odometer=-4), "odometer")):
        r = d.post("/fuel_fills", fuel_body(world, **bad))
        assert r.status_code == 422 and field in r.json()["fields"], (bad, r.text)
    r = d.post("/fuel_fills", fuel_body(world, odometer=None, amount="0"))
    assert r.status_code == 201
    row = d.get("/fuel_fills").json()["items"][0]
    assert row["per_l"] is None and row["consumption"] == ""


def test_consumption_between_full_tanks_and_remarks(world, make_client):
    adm = make_client("admin")
    d = make_client("disp_a")
    assert set_specs(world, adm, world.va1, tank_liters="100", fuel_norm="25").status_code == 200
    # первая полная заправка — точка отсчёта; потом частичная и полная
    assert d.post("/fuel_fills", fuel_body(world, fill_date=day(6), liters="90", odometer=1000)).status_code == 201
    assert d.post("/fuel_fills", fuel_body(world, fill_date=day(4), liters="40", odometer=1150, full_tank=False)).status_code == 201
    r = d.post("/fuel_fills", fuel_body(world, fill_date=day(2), liters="60", odometer=1400))
    assert r.status_code == 201
    # (40 + 60) л на 400 км = 25,0 л/100 км — ровно по норме
    rows = {x["id"]: x for x in d.get("/fuel_fills").json()["items"]}
    last = rows[r.json()["id"]]
    assert last["consumption"] == "25,0" and last["check"]["text"] == "В норме"
    assert r.json()["id"] in rows and not last["check_notes"]

    # перерасход: 80 л на 200 км = 40 л/100 км при норме 25 — на 60 % выше
    r = d.post("/fuel_fills", fuel_body(world, fill_date=day(1), liters="80", odometer=1600))
    assert r.status_code == 201
    row = next(x for x in d.get("/fuel_fills").json()["items"] if x["id"] == r.json()["id"])
    assert row["consumption"] == "40,0" and row["check"]["tone"] == "error" and row["check_notes"]
    # то же приходит предупреждением при сохранении, а запись остаётся
    r = d.post("/fuel_fills", fuel_body(world, fill_date=day(0), liters="150", odometer=1650, full_tank=False))
    assert r.status_code == 201
    assert any("бак вмещает" in x for x in d.get("/fuel_fills").json()["items"][0]["check_notes"])


def test_consumption_ignores_voided_and_missing_odometer(world, make_client):
    d = make_client("disp_a")
    a = d.post("/fuel_fills", fuel_body(world, fill_date=day(5), liters="50", odometer=1100)).json()
    nodo = d.post("/fuel_fills", fuel_body(world, fill_date=day(4), liters="50", odometer=None)).json()   # полный бак без пробега: отсчёт сбрасывается
    c = d.post("/fuel_fills", fuel_body(world, fill_date=day(2), liters="50", odometer=1400)).json()
    rows = {x["id"]: x for x in d.get("/fuel_fills").json()["items"]}
    assert rows[c["id"]]["consumption"] == "" and rows[a["id"]]["consumption"] == ""
    assert make_client("dir_a").post(f"/fuel_fills/{nodo['id']}/void", {"reason": "Ошибка ввода"}).status_code == 200
    rows = {x["id"]: x for x in d.get("/fuel_fills").json()["items"]}
    assert rows[c["id"]]["consumption"] == "16,7"           # 50 л на 300 км


# ---------------------------------------------------------------- водитель

def test_driver_adds_trip_with_photo(world, make_client):
    drv = make_client("drv1")
    me = drv.get("/entries/mine").json()
    assert [v["id"] for v in me["vehicles"]] == [world.va1] and me["can"] == {"trip": True, "fuel": True}
    r = drv_post(drv, "trip", trip_body(world, driver_id=world.d2, legal_entity_id=world.B), files=[("накладная.png", png(), "image/png")])
    assert r.status_code == 201, r.text
    item = r.json()["item"]
    assert item["driver_id"] == world.d1 and item["legal_entity_id"] == world.A     # водителя и юрлицо подменить нельзя
    assert len(item["pics"]) == 1 and item["can"]["delete"] and item["mine"]
    assert drv.get(f"/entries/photos/{item['pics'][0]['id']}").status_code == 200
    assert actions("trips.create", "entries.photo.add") == 2


def test_driver_cannot_use_foreign_vehicle_or_entity(world, make_client):
    drv = make_client("drv1")
    for vid in (world.va2, world.va3, world.vb1):
        r = drv_post(drv, "trip", trip_body(world, vehicle=vid))
        assert r.status_code == 422 and ("vehicle_id" in (r.json().get("fields") or {}) or r.json().get("field") == "vehicle_id"), r.text
    r = drv_post(drv, "fuel", fuel_body(world, vehicle=world.va2))
    assert r.status_code == 422
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Trip)) == 0 and db.scalar(select(func.count()).select_from(FuelFill)) == 0


def test_driver_fuel_with_warning_and_reading(world, make_client):
    adm, drv = make_client("admin"), make_client("drv1")
    set_specs(world, adm, world.va1, tank_liters="60", fuel_norm="30")
    r = drv_post(drv, "fuel", fuel_body(world, liters="75", odometer=2400), files=[("чек.png", png(), "image/png")])
    assert r.status_code == 201, r.text
    assert any("бак вмещает" in x for x in r.json()["warnings"])
    with session_factory()() as db:
        assert db.get(Vehicle, world.va1).current_km == 2400
    # пробег меньше уже записанного: ошибка на поле пробега, запись не создаётся
    r = drv_post(drv, "fuel", fuel_body(world, liters="30", odometer=100))
    assert r.status_code == 422 and r.json()["field"] == "odometer"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(FuelFill)) == 1


def test_big_odometer_jump_goes_to_review_with_warning(world, make_client):
    drv = make_client("drv1")
    assert drv_post(drv, "fuel", fuel_body(world, odometer=1000, fill_date=day(3))).status_code == 201
    r = drv_post(drv, "fuel", fuel_body(world, odometer=9000))
    assert r.status_code == 201
    assert any("на проверку" in x for x in r.json()["warnings"])
    with session_factory()() as db:
        assert db.scalars(select(VehicleReading.review_state).where(VehicleReading.vehicle_id == world.va1).order_by(VehicleReading.id)).all() == ["ok", "ok", "pending"]


def test_repeat_send_does_not_duplicate(world, make_client):
    drv = make_client("drv1")
    r1 = drv_post(drv, "trip", trip_body(world), client_id="abc-123")
    assert r1.status_code == 201 and not r1.json()["duplicate"]
    r2 = drv_post(drv, "trip", trip_body(world), client_id="abc-123", files=[("накладная.png", png(), "image/png")])
    assert r2.status_code == 201 and r2.json()["duplicate"] and r2.json()["item"]["id"] == r1.json()["item"]["id"]
    assert len(r2.json()["item"]["pics"]) == 1          # фото, не дошедшее в первый раз, досылается
    r3 = drv_post(drv, "trip", trip_body(world), client_id="abc-123", files=[("накладная.png", png(), "image/png")])
    assert len(r3.json()["item"]["pics"]) == 1          # а второй раз не дублируется
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Trip)) == 1
    # тот же код у другого типа записи или другого человека — отдельная запись
    assert drv_post(drv, "fuel", fuel_body(world), client_id="abc-123").status_code == 201


def test_photo_limits_and_types(world, make_client):
    drv = make_client("drv1")
    four = [("а.png", png(), "image/png")] * 4
    r = drv_post(drv, "trip", trip_body(world), files=four)
    assert r.status_code == 422 and r.json()["code"] == "too_many_photos"
    r = drv_post(drv, "trip", trip_body(world), files=[("скан.pdf", PDF, "application/pdf")])
    assert r.status_code == 422
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Trip)) == 0       # из-за плохого файла запись не создаётся
    item = drv_post(drv, "trip", trip_body(world), files=[("1.png", png(), "image/png")] * 3).json()["item"]
    r = drv.call("POST", f"/entries/trip/{item['id']}/photos", files=[("files", ("4.png", png(), "image/png"))])
    assert r.status_code == 422 and r.json()["code"] == "too_many_photos"
    # убираем одно — добавить можно
    pid = item["pics"][0]["id"]
    r = drv.post(f"/entries/photos/{pid}/remove", {"reason": "Нечётко"})
    assert r.status_code == 200 and len(r.json()["item"]["pics"]) == 2
    assert drv.get(f"/entries/photos/{pid}").status_code == 404
    r = drv.call("POST", f"/entries/trip/{item['id']}/photos", files=[("files", ("5.png", png(), "image/png"))])
    assert r.status_code == 200 and len(r.json()["item"]["pics"]) == 3
    with session_factory()() as db:           # убранное фото остаётся в базе с отметкой
        ph = db.get(EntryPhoto, pid)
        assert ph.removed_at is not None and ph.remove_reason == "Нечётко"


def test_driver_deletes_only_own_fresh_entry(world, make_client):
    drv, other = make_client("drv1"), make_client("drv2")
    t = drv_post(drv, "trip", trip_body(world)).json()["item"]
    f = drv_post(drv, "fuel", fuel_body(world, odometer=1100)).json()["item"]
    # чужой водитель не видит и не убирает
    assert other.post(f"/entries/mine/trip/{t['id']}/delete").status_code == 404
    r = drv.post(f"/entries/mine/trip/{t['id']}/delete")
    assert r.status_code == 200 and r.json()["note"] == ""
    r = drv.post(f"/entries/mine/fuel/{f['id']}/delete")
    assert r.status_code == 200 and "показаниях" in r.json()["note"]
    assert drv.get("/entries/mine").json()["items"] == []
    assert drv.post(f"/entries/mine/trip/{t['id']}/delete").status_code == 403         # уже в архиве
    assert actions("entries.driver_void") == 2
    # старую запись (больше суток) водитель не убирает
    t2 = drv_post(drv, "trip", trip_body(world)).json()["item"]
    with session_factory()() as db:
        db.get(Trip, t2["id"]).created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)
        db.commit()
    assert drv.post(f"/entries/mine/trip/{t2['id']}/delete").status_code == 403
    item = next(x for x in drv.get("/entries/mine").json()["items"] if x["id"] == t2["id"])
    assert not item["can"]["delete"] and not item["can"]["photo"]
    # а директор убирает любую через архив раздела
    assert make_client("dir_a").post(f"/trips/{t2['id']}/void", {"reason": "Дубль"}).status_code == 200


def test_driver_sees_entries_of_own_vehicle_only(world, make_client):
    d = make_client("disp_a")
    d.post("/trips", trip_body(world, vehicle=world.va1, route="Маршрут 1"))
    d.post("/trips", trip_body(world, vehicle=world.va2, route="Маршрут 2"))
    d.post("/fuel_fills", fuel_body(world, vehicle=world.va2, odometer=None))
    one = make_client("drv1").get("/entries/mine").json()
    assert [x["route"] for x in one["items"]] == ["Маршрут 1"]
    two = make_client("drv2").get("/entries/mine").json()
    assert sorted(x["kind"] for x in two["items"]) == ["fuel", "trip"]
    # чужое юрлицо
    three = make_client("drv3").get("/entries/mine").json()
    assert three["items"] == [] and [v["id"] for v in three["vehicles"]] == [world.vb1]


def test_driver_cannot_use_registry_or_feed(world, make_client):
    drv = make_client("drv1")
    assert drv.get("/trips").status_code == 403
    assert drv.get("/fuel_fills").status_code == 403
    assert drv.post("/trips", trip_body(world)).status_code in (403, 422)
    assert drv.get("/entries/feed").status_code == 403


# ---------------------------------------------------------------- фото: доступ

def test_photo_access_by_role_and_entity(world, make_client):
    drv = make_client("drv1")
    item = drv_post(drv, "fuel", fuel_body(world), files=[("чек.png", png(), "image/png")]).json()["item"]
    pid = item["pics"][0]["id"]
    url = f"/entries/photos/{pid}"
    assert make_client("disp_a").get(url).status_code == 200
    assert make_client("acc_a").get(url).status_code == 200
    assert make_client("drv2").get(url).status_code == 404            # другой водитель той же организации
    assert make_client("dir_b").get(url).status_code == 404           # другое юрлицо
    assert make_client("emp_a").get(url).status_code == 404
    assert make_client("disp_a").get(url + "?thumb=true").headers["content-type"].startswith("image/")
    # механик заправки только смотрит: фото не убирает и не добавляет
    mech = make_client("mech_a")
    assert mech.get(url).status_code == 200
    assert mech.post(f"/entries/photos/{pid}/remove", {"reason": "x"}).status_code == 403
    assert mech.call("POST", f"/entries/fuel/{item['id']}/photos", files=[("files", ("1.png", png(), "image/png"))]).status_code == 403
    # диспетчер может
    assert make_client("disp_a").post(f"/entries/photos/{pid}/remove", {"reason": "Не тот чек"}).status_code == 200
    assert actions("entries.photo.remove") == 1


# ---------------------------------------------------------------- лента сотрудника

def test_feed_for_staff(world, make_client):
    drv = make_client("drv1")
    set_specs(world, make_client("admin"), world.va1, tank_liters="50")
    drv_post(drv, "trip", trip_body(world))
    drv_post(drv, "fuel", fuel_body(world, liters="40", amount="2200", odometer=1000, fill_date=day(1)))
    drv_post(drv, "fuel", fuel_body(world, liters="70", amount="3850", odometer=1300))      # больше бака
    make_client("dir_b").post("/trips", trip_body(world, vehicle=world.vb1, route="Чужой маршрут"))

    d = make_client("disp_a")
    feed = d.get("/entries/feed").json()
    assert feed["totals"] == {"trips": 1, "fills": 2, "liters": 110.0, "amount": 6050.0}
    assert {x["kind"] for x in feed["items"]} == {"trip", "fuel"} and all(x["legal_entity_id"] == world.A for x in feed["items"])
    assert d.get("/entries/feed?kind=trip").json()["totals"]["fills"] == 0
    bad = d.get("/entries/feed?problems=true").json()["items"]
    assert len(bad) == 1 and bad[0]["kind"] == "fuel" and bad[0]["check_notes"]
    assert d.get(f"/entries/feed?vehicle_id={world.va2}").json()["items"] == []
    assert d.get(f"/entries/feed?date_from={day(0)}&date_to={day(0)}").json()["totals"]["fills"] == 1
    # чужое юрлицо запрошено явно — отказ; другая организация видит своё
    assert d.get(f"/entries/feed?legal_entity_id={world.B}").status_code == 403
    other = make_client("dir_b").get("/entries/feed").json()
    assert [x["route"] for x in other["items"] if x["kind"] == "trip"] == ["Чужой маршрут"] and other["totals"]["fills"] == 0


def test_role_matrix_for_entries(world, make_client):
    body_t, body_f = trip_body(world), fuel_body(world, odometer=None)
    # бухгалтер видит и правит всё, механик смотрит заправки, кладовщик ничего
    acc, mech, store = make_client("acc_a"), make_client("mech_a"), make_client("store_a")
    assert acc.post("/trips", body_t).status_code == 201 and acc.post("/fuel_fills", body_f).status_code == 201
    assert mech.get("/fuel_fills").status_code == 200 and mech.get("/trips").status_code == 403
    assert mech.post("/fuel_fills", body_f).status_code in (403, 422)
    assert store.get("/fuel_fills").status_code == 403 and store.get("/trips").status_code == 403
    # диспетчер вносит и правит, но в архив не убирает
    d = make_client("disp_a")
    tid = d.post("/trips", body_t).json()["id"]
    assert d.patch(f"/trips/{tid}", {"route": "Новый маршрут"}).json()["route"] == "Новый маршрут"
    assert d.post(f"/trips/{tid}/void", {"reason": "Дубль записи"}).status_code in (403, 404)
    # директор убирает и возвращает
    dr = make_client("dir_a")
    assert dr.post(f"/trips/{tid}/void", {"reason": "Дубль записи"}).status_code == 200
    assert dr.post(f"/trips/{tid}/restore").status_code == 200
    # чужое юрлицо
    assert tid not in [x["id"] for x in make_client("dir_b").get("/trips").json()["items"]]
    assert make_client("dir_b").patch(f"/trips/{tid}", {"route": "Взлом"}).status_code in (403, 404)


def test_specs_tank_and_norm_validation(world, make_client):
    adm = make_client("admin")
    r = set_specs(world, adm, world.va1, tank_liters="120.5", fuel_norm="32")
    assert r.status_code == 200 and r.json()["tank_liters"] == 120.5 and r.json()["fuel_norm"] == 32
    assert set_specs(world, adm, world.va1, tank_liters="0").status_code == 422
    assert set_specs(world, adm, world.va1, fuel_norm="-1").status_code == 422
    r = set_specs(world, adm, world.va1, tank_liters=None, fuel_norm=None)
    assert r.status_code == 200 and r.json()["tank_liters"] is None
    assert make_client("mech_a").get(f"/dossier/vehicles/{world.va1}").json()["specs"]["fuel_norm"] is None
