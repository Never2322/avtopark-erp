# -*- coding: utf-8 -*-
"""Критерий: показания нельзя незаметно переписать. Исправление — новая запись, старая остаётся; защита работает и на уровне базы."""
import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import session_factory


def readings(c, vid):
    return c.get(f"/vehicles/{vid}/readings", params={"page_size": 200}).json()["items"]


def current_km(c, vid):
    return c.get(f"/vehicles/{vid}").json()["current_km"]


def test_value_cannot_decrease(make_client, world):
    c = make_client("mech_a")
    assert current_km(c, world.va1) == 1000
    r = c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 900})
    assert r.status_code == 422 and r.json()["code"] == "reading_decreased"
    assert current_km(c, world.va1) == 1000


def test_normal_reading_updates_current_value(make_client, world):
    c = make_client("mech_a")
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1250.5}).status_code == 201
    assert current_km(c, world.va1) == 1250.5


def test_future_date_and_unknown_kind_rejected(make_client, world):
    c = make_client("mech_a")
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 2000, "recorded_at": future}).status_code == 422
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "liters", "value": 5}).status_code == 422
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": -5}).status_code == 422


def test_big_jump_waits_for_review_and_does_not_change_current(make_client, world):
    c, boss = make_client("mech_a"), make_client("dir_a")
    r = c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 9000})
    assert r.status_code == 201 and r.json()["review_state"] == "pending" and r.json()["is_anomaly"]
    assert current_km(c, world.va1) == 1000                                    # пока не проверено, в текущие не попадает
    pending = boss.get("/readings/pending").json()["items"]
    assert len(pending) == 1
    assert make_client("mech_a").get("/readings/pending").status_code == 403    # проверяет тот, у кого есть право
    assert boss.post(f"/readings/{r.json()['id']}/review", {"decision": "reject"}).status_code == 422   # отказ без причины нельзя
    ok = boss.post(f"/readings/{r.json()['id']}/review", {"decision": "confirm", "comment": "Был рейс в другой город"})
    assert ok.status_code == 200
    assert current_km(c, world.va1) == 9000
    assert boss.post(f"/readings/{r.json()['id']}/review", {"decision": "confirm"}).status_code == 409   # второй раз нельзя


def test_rejected_jump_never_counts(make_client, world):
    c, boss = make_client("mech_a"), make_client("dir_a")
    rid = c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 9000}).json()["id"]
    assert boss.post(f"/readings/{rid}/review", {"decision": "reject", "comment": "Опечатка, лишний ноль"}).status_code == 200
    assert current_km(c, world.va1) == 1000
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1090}).status_code == 201


def test_correction_is_a_new_row_and_old_one_stays(make_client, world):
    c, boss = make_client("mech_a"), make_client("dir_a")
    rid = c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1200}).json()["id"]
    assert boss.post(f"/readings/{rid}/correct", {"value": 1210, "reason": ""}).status_code == 422          # причина обязательна
    assert c.post(f"/readings/{rid}/correct", {"value": 1210, "reason": "ошибся"}).status_code == 403       # механик исправлять не может
    fixed = boss.post(f"/readings/{rid}/correct", {"value": 1210, "reason": "Ошибся при вводе"})
    assert fixed.status_code == 201 and fixed.json()["corrects_id"] == rid
    rows = {r["id"]: r for r in readings(boss, world.va1)}
    assert rows[rid]["value"] == 1200 and rows[rid]["superseded_by"] == fixed.json()["id"] and not rows[rid]["effective"]
    assert rows[fixed.json()["id"]]["effective"]
    assert current_km(boss, world.va1) == 1210
    # одну запись нельзя исправить дважды: исправлять нужно действующую
    assert boss.post(f"/readings/{rid}/correct", {"value": 1220, "reason": "ещё раз"}).status_code == 409


def test_correction_cannot_break_order(make_client, world):
    boss = make_client("dir_a")
    first = boss.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1100}).json()["id"]
    boss.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1200})
    assert boss.post(f"/readings/{first}/correct", {"value": 1500, "reason": "Хочу больше"}).status_code == 422   # больше следующего
    assert boss.post(f"/readings/{first}/correct", {"value": 500, "reason": "Хочу меньше"}).status_code == 422    # меньше предыдущего


def test_database_refuses_to_rewrite_or_delete_readings(make_client, world):
    make_client("mech_a").post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1100})
    with session_factory()() as db:
        for sql in ("UPDATE vehicle_readings SET value = 1", "UPDATE vehicle_readings SET recorded_at = now()",
                    "UPDATE vehicle_readings SET vehicle_id = 2", "DELETE FROM vehicle_readings"):
            with pytest.raises(DBAPIError):
                db.execute(text(sql))
            db.rollback()
        # разрешено менять только отметки проверки
        db.execute(text("UPDATE vehicle_readings SET review_comment = 'ok' WHERE id = 1"))
        db.commit()


def test_current_value_cannot_be_edited_by_hand(make_client, world):
    c = make_client("dir_a")
    c.patch(f"/vehicles/{world.va1}", {"type_id": world.type_id, "plate": "А001АА54", "note": "", "current_km": 5})
    assert current_km(c, world.va1) == 1000


def test_hours_and_km_are_tracked_separately(make_client, world):
    c = make_client("dir_a")
    c.post(f"/vehicles/{world.va1}/readings", {"kind": "engine_hours", "value": 500})
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "engine_hours", "value": 499}).status_code == 422
    assert c.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1100}).status_code == 201


def test_archived_vehicle_takes_no_readings(make_client, world):
    c = make_client("dir_a")
    assert c.post(f"/vehicles/{world.va3}/archive").status_code == 200
    assert c.post(f"/vehicles/{world.va3}/readings", {"kind": "km", "value": 1100}).status_code == 409
