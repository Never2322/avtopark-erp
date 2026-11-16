# -*- coding: utf-8 -*-
"""Журнал действий: записывается, не переписывается и не хранит секреты. Пользователи и временные пароли."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import session_factory
from app.models import AuditLog, User
from tests.helpers import PASSWORD


def actions(c, **params):
    return [r["action"] for r in c.get("/audit", params={"page_size": 200, **params}).json()["items"]]


def test_audit_is_append_only_in_database(make_client, world):
    make_client("dir_a").post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Поломка"})
    with session_factory()() as db:
        assert db.scalar(select(AuditLog.id).limit(1))
        for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "UPDATE audit_log SET user_login = 'кто-то другой'"):
            with pytest.raises(DBAPIError):
                db.execute(text(sql))
            db.rollback()


def test_main_actions_are_logged_with_who_and_where(make_client, world):
    boss = make_client("dir_a")
    boss.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Поломка"})
    boss.post(f"/vehicles/{world.va1}/readings", {"kind": "km", "value": 1100})
    req = boss.post("/requests", {"vehicle_id": world.va1, "title": "Проверка журнала"}).json()
    boss.post(f"/requests/{req['id']}/transition", {"to_status": "diagnostics"})
    wb = boss.post("/waybills", {"vehicle_id": world.va2, "driver_id": world.d2}).json()
    boss.post(f"/waybills/{wb['id']}/open", {})
    boss.post(f"/waybills/{wb['id']}/close", {"end_km": 1200})
    got = set(actions(make_client("admin")))
    assert {"login.ok", "vehicle.status", "reading.create", "request.create", "request.transition", "waybill.create", "waybill.open",
            "waybill.close"} <= got
    row = make_client("admin").get("/audit", params={"action": "vehicle.status"}).json()["items"][0]
    assert row["user_login"] == "dir_a" and row["entity_type"] == "vehicle" and row["entity_id"] == world.va1
    assert row["legal_entity_id"] == world.A and row["ip"] and row["details"]["to_status"] == "in_repair"
    assert row["action_label"] == "Сменён статус техники"


def test_secrets_never_reach_the_log(make_client, world):
    admin = make_client("admin")
    created = admin.post("/users", {"login": "newbie", "display_name": "Новичок", "roles": [{"role_id": world.role_ids["employee"], "legal_entity_id": world.A}]})
    assert created.status_code == 201, created.text
    temp = created.json()["temporary_password"]
    assert temp and created.json()["must_change_password"]
    admin.post(f"/users/{created.json()['id']}/reset-password")
    make_client().login("newbie", "wrong-password-1")
    with session_factory()() as db:
        dump = " ".join(f"{r.user_login} {r.details}" for r in db.scalars(select(AuditLog)))
        hashes = " ".join(u.password_hash for u in db.scalars(select(User)))
    assert temp not in dump and "wrong-password-1" not in dump and PASSWORD not in dump
    assert "argon2" not in dump and hashes.split()[0] not in dump


def test_temporary_password_shown_once_and_works_once_changed(make_client, world):
    admin = make_client("admin")
    uid = admin.post("/users", {"login": "second", "roles": [{"role_id": world.role_ids["mechanic"], "legal_entity_id": world.A}]}).json()
    temp = uid["temporary_password"]
    again = admin.get(f"/users/{uid['id']}").json()
    assert "temporary_password" not in again and "password_hash" not in again
    c = make_client()
    assert c.login("second", temp).status_code == 200
    assert c.get("/vehicles").status_code == 403
    assert c.post("/auth/password", {"current_password": temp, "new_password": "Fresh-Pass-4471"}).status_code == 200
    assert c.get("/vehicles").status_code == 200


def test_login_is_unique_ignoring_case_and_validated(make_client, world):
    admin = make_client("admin")
    assert admin.post("/users", {"login": "Dir_A", "roles": []}).status_code in (409, 422)
    assert admin.post("/users", {"login": "bad login!", "roles": []}).status_code == 422
    assert admin.post("/users", {"login": "x", "password": "123", "roles": []}).status_code == 422


def test_user_with_link_to_employee_and_deactivation_kills_sessions(make_client, world):
    admin = make_client("admin")
    c = make_client("mech_a")
    uid = [u["id"] for u in admin.get("/users").json()["items"] if u["login"] == "mech_a"][0]
    u = admin.get(f"/users/{uid}").json()
    r = admin.patch(f"/users/{uid}", {"display_name": u["display_name"], "is_active": False, "roles": [{"role_id": world.role_ids["mechanic"], "legal_entity_id": world.A}]})
    assert r.status_code == 200, r.text
    assert c.get("/auth/me").status_code == 401                         # отключённого человека выбрасывает сразу
    assert make_client().login("mech_a").status_code == 401


def test_audit_requires_permission_and_filters_work(make_client, world):
    assert make_client("mech_a").get("/audit").status_code == 403
    boss = make_client("dir_a")
    boss.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Поломка"})
    boss.post(f"/vehicles/{world.va2}/status", {"status": "in_repair", "reason": "Поломка"})
    assert boss.get("/audit", params={"entity_type": "vehicle", "entity_id": world.va1, "action": "vehicle.status"}).json()["total"] == 1
    assert boss.get("/audit", params={"user": "dir_a"}).json()["total"] >= 2
    assert boss.get("/audit", params={"action": "vehicle.status", "date_from": "2000-01-01", "date_to": "2999-01-01"}).json()["total"] == 2
    assert boss.get("/audit", params={"date_to": "2000-01-01"}).json()["total"] == 0
    assert len(boss.get("/audit", params={"page_size": 1}).json()["items"]) == 1
    assert boss.get("/audit/actions").json()["items"]


def test_dashboard_shows_only_what_role_may_see(make_client, world):
    boss, emp, drv = make_client("dir_a"), make_client("emp_a"), make_client("drv1")
    d = boss.get("/dashboard").json()
    assert d["vehicles"]["total"] == 3 and d["audit"] is not None and d["requests"] is not None and d["readings"] is not None
    e = emp.get("/dashboard").json()
    assert e["vehicles"] is None and e["waybills"] is None and e["audit"] is None and e["readings"] is None and e["requests"] is not None
    assert drv.get("/dashboard").json()["vehicles"]["total"] == 1
    assert drv.get("/dashboard").json()["audit"] is None
