# -*- coding: utf-8 -*-
"""Счётчик ходок: личная вкладка водителя самосвала. Кнопка «Ходка» пишет дату и время по часам и поясу телефона, случайную ходку можно
убрать. Записи видит только сам водитель: офису они недоступны, в журнал действий не пишутся."""
import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from app.common import org_today
from app.config import get_settings
from app.db import session_factory
from app.models import AuditLog, HaulMark, Permission, Role, RolePermission, Vehicle, VehicleType

TODAY = org_today()
UTC = dt.timezone.utc


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def press(c, **body):
    return c.post("/hauls", body)


def hauls(c, date=None):
    r = c.get("/hauls" + (f"?date={date}" if date else ""))
    assert r.status_code == 200, r.text
    return r.json()


def audit_rows():
    with session_factory()() as db:
        return db.scalar(select(func.count()).select_from(AuditLog))


# ---------------------------------------------------------------- кнопка, запись, итоги

def test_driver_of_dump_truck_presses_button_and_sees_records(make_client, world):
    d = make_client("drv1")
    assert d.me["features"]["hauls"] is True
    assert d.get("/driver/home").json()["can"]["hauls"] is True
    first = hauls(d)
    assert first["count"] == 0 and first["items"] == [] and first["date"] == first["today"]
    r = press(d, at=iso(dt.datetime.now(UTC)), tz_min=420)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1 and r.json()["items"][0]["n"] == 1
    press(d, at=iso(dt.datetime.now(UTC)), tz_min=420)
    p = press(d, tz_min=420).json()                      # телефон время не прислал: берётся время сервера
    assert [i["n"] for i in p["items"]] == [1, 2, 3] and p["count"] == 3
    assert hauls(d)["month_count"] >= 3
    assert any(x["count"] == 3 for x in hauls(d)["days"])
    for i in p["items"]:
        assert len(i["time"]) == 5 and i["time"][2] == ":"


def test_time_and_day_follow_the_phone_timezone(make_client, world):
    d = make_client("drv1")
    moment = dt.datetime.combine(TODAY - dt.timedelta(days=1), dt.time(20, 30), tzinfo=UTC)      # 20:30 UTC вчера
    r = press(d, at=iso(moment), tz_min=420).json()                                                # у водителя в Новосибирске уже 03:30 сегодня
    assert r["date"] == TODAY.isoformat() and r["items"][-1]["time"] == "03:30"
    r = press(d, at=iso(moment), tz_min=-300).json()                                               # а у того, кто в поясе минус пять, ещё 15:30 вчера
    assert r["date"] == (TODAY - dt.timedelta(days=1)).isoformat() and r["items"][-1]["time"] == "15:30"
    assert hauls(d, (TODAY - dt.timedelta(days=1)).isoformat())["count"] == 1
    assert hauls(d)["count"] == 1


def test_no_timezone_from_phone_uses_organization_timezone(make_client, world):
    d = make_client("drv1")
    moment = dt.datetime.combine(TODAY, dt.time(2, 5), tzinfo=UTC)
    r = press(d, at=iso(moment)).json()
    expected = moment.astimezone(ZoneInfo(get_settings().org_timezone))
    assert r["items"][-1]["time"] == expected.strftime("%H:%M") and r["date"] == expected.date().isoformat()
    assert press(d, at=iso(moment), tz_min=9999).status_code == 200                                 # невероятный пояс не ломает: берётся пояс организации


def test_phone_clock_in_the_future_or_far_past_is_replaced_by_server_time(make_client, world):
    d = make_client("drv1")
    now = dt.datetime.now(UTC)
    for bad in (now + dt.timedelta(hours=5), now - dt.timedelta(days=30)):
        r = press(d, at=iso(bad), tz_min=0)
        assert r.status_code == 200
    stamps = [dt.datetime.fromisoformat(i["marked_at"]) for i in hauls(d)["items"]]
    assert all(abs((s - now).total_seconds()) < 120 for s in stamps), stamps


# ---------------------------------------------------------------- убрать

def test_driver_removes_a_mistaken_haul(make_client, world):
    d = make_client("drv1")
    for _ in range(3):
        press(d, at=iso(dt.datetime.now(UTC)), tz_min=420)
    items = hauls(d)["items"]
    r = d.call("DELETE", f"/hauls/{items[1]['id']}")
    assert r.status_code == 200 and r.json()["count"] == 2
    assert [i["n"] for i in r.json()["items"]] == [1, 2]                                            # номера пересчитаны подряд
    assert d.call("DELETE", f"/hauls/{items[1]['id']}").status_code == 404                           # второй раз её уже нет
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(HaulMark)) == 2                           # убрана совсем, а не спрятана


def test_cannot_remove_someone_elses_haul(make_client, world):
    a, b = make_client("drv1"), make_client("drv2")
    mine = press(a, at=iso(dt.datetime.now(UTC)), tz_min=420).json()["items"][0]["id"]
    assert b.call("DELETE", f"/hauls/{mine}").status_code == 404
    assert hauls(a)["count"] == 1 and hauls(b)["count"] == 0                                         # у каждого свой счётчик


def test_repeat_with_same_code_does_not_double(make_client, world):
    d = make_client("drv1")
    at = iso(dt.datetime.now(UTC))
    first = press(d, at=at, tz_min=420, client_id="tap-1").json()
    again = press(d, at=at, tz_min=420, client_id="tap-1").json()
    assert first["id"] == again["id"] and again["count"] == 1
    assert press(d, at=at, tz_min=420, client_id="tap-2").json()["count"] == 2


def test_day_limit_protects_from_a_runaway_button(make_client, world, monkeypatch):
    from app.routers import hauls as router
    monkeypatch.setattr(router, "HAUL_DAY_LIMIT", 3)
    d = make_client("drv1")
    for _ in range(3):
        assert press(d, at=iso(dt.datetime.now(UTC)), tz_min=420).status_code == 200
    r = press(d, at=iso(dt.datetime.now(UTC)), tz_min=420)
    assert r.status_code == 422 and r.json()["code"] == "too_many"


def test_bad_date_is_rejected(make_client, world):
    d = make_client("drv1")
    assert d.get("/hauls?date=2999-01-01").status_code == 422
    assert d.get("/hauls?date=2001-01-01").status_code == 422
    assert d.get("/hauls?date=вчера").status_code == 422


# ---------------------------------------------------------------- кому доступно

def test_only_drivers_with_a_dump_truck_have_the_tab(make_client, world):
    with session_factory()() as db:                                    # машина водителя 2 теперь погрузчик
        loader = db.scalar(select(VehicleType.id).where(VehicleType.code == "loader"))
        db.get(Vehicle, world.va2).type_id = loader
        db.commit()
    d = make_client("drv2")
    assert d.me["features"]["hauls"] is False
    assert d.get("/driver/home").json()["can"]["hauls"] is False
    assert d.get("/hauls").status_code == 403
    assert press(d, tz_min=420).status_code == 403
    assert d.call("DELETE", "/hauls/1").status_code == 403
    assert make_client("drv1").get("/hauls").status_code == 200                                      # у водителя самосвала всё работает


def test_type_named_dump_truck_in_old_data_also_counts(make_client, world):
    with session_factory()() as db:
        loader = db.scalar(select(VehicleType).where(VehicleType.code == "loader"))
        loader.name = "Самосвал 25 тонн (из старой программы)"
        db.get(Vehicle, world.va2).type_id = loader.id
        db.commit()
    assert make_client("drv2").get("/hauls").status_code == 200


def test_driver_loses_the_tab_when_right_is_taken_away(make_client, world):
    with session_factory()() as db:
        role = db.scalar(select(Role).where(Role.code == "driver"))
        perm = db.scalar(select(Permission).where(Permission.code == "hauls.use_own"))
        db.execute(delete(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == perm.id))
        db.commit()
    d = make_client("drv1")
    assert d.me["features"]["hauls"] is False and d.get("/hauls").status_code == 403


def test_driver_role_has_the_right_by_default_and_others_do_not(make_client, world):
    with session_factory()() as db:
        has = {r.code for r in db.scalars(select(Role).join(RolePermission, RolePermission.role_id == Role.id).join(
            Permission, Permission.id == RolePermission.permission_id).where(Permission.code == "hauls.use_own"))}
    assert "driver" in has and not ({"director", "dispatcher", "accountant", "mechanic", "storekeeper"} & has)


# ---------------------------------------------------------------- офис не видит

def test_office_cannot_see_hauls_anywhere(make_client, world):
    d = make_client("drv1")
    for _ in range(2):
        press(d, at=iso(dt.datetime.now(UTC)), tz_min=420)
    for login in ("dir_a", "disp_a", "acc_a", "mech_a", "emp_a", "store_a", "admin"):
        c = make_client(login)
        assert c.get("/hauls").status_code == 403, login                                             # ни просмотра, ни кнопки
        assert press(c, tz_min=420).status_code == 403, login
        assert c.me["features"]["hauls"] is False, login
    paths = [r for r in make_client("admin").get("/audit?page_size=100").json().get("items", [])]
    assert not [x for x in paths if "haul" in str(x).lower()]


def test_hauls_are_not_written_to_the_audit_log(make_client, world):
    d = make_client("drv1")
    before = audit_rows()
    h = press(d, at=iso(dt.datetime.now(UTC)), tz_min=420).json()
    d.call("DELETE", f"/hauls/{h['id']}")
    assert audit_rows() == before


def test_there_is_no_office_route_for_hauls(app):
    """Единственные маршруты счётчика: свои ходки водителя. Ни списка по всем, ни выгрузки, ни отчёта."""
    spec = app.openapi()["paths"]
    hits = sorted(p for p in spec if "haul" in p.lower())
    assert hits == ["/api/hauls", "/api/hauls/{hid}"], hits
    assert not [p for p in spec if "haul" in str(spec[p]).lower() and not p.startswith("/api/hauls")]
