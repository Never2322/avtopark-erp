# -*- coding: utf-8 -*-
"""Первый запуск и командная строка: повторный запуск ничего не ломает, права ролей не затираются."""
import re

from sqlalchemy import func, select

from app import cli
from app.bootstrap import bootstrap
from app.config import get_settings
from app.db import session_factory
from app.models import Permission, Role, RolePermission, User


def test_bootstrap_is_idempotent_and_keeps_edited_roles(make_client, world):
    admin = make_client("admin")
    roles = {r["code"]: r for r in admin.get("/roles").json()["items"]}
    r = admin.put(f"/roles/{roles['driver']['id']}/permissions", {"permissions": ["dashboard.view", "vehicles.view_own"]})
    assert r.status_code == 200, r.text
    with session_factory()() as db:
        before = db.scalar(select(func.count()).select_from(Permission)), db.scalar(select(func.count()).select_from(Role))
        bootstrap(db)
        bootstrap(db)
        after = db.scalar(select(func.count()).select_from(Permission)), db.scalar(select(func.count()).select_from(Role))
    assert before == after
    again = {r["code"]: r for r in admin.get("/roles").json()["items"]}
    assert sorted(again["driver"]["permissions"]) == ["dashboard.view", "vehicles.view_own"]      # правка руководителя не откатилась


def test_admin_role_is_resynced_to_all_permissions(make_client, world):
    with session_factory()() as db:
        admin_role = db.scalar(select(Role).where(Role.code == "admin"))
        db.query(RolePermission).filter(RolePermission.role_id == admin_role.id).limit(1)
        row = db.scalar(select(RolePermission).where(RolePermission.role_id == admin_role.id).limit(1))
        db.delete(row)
        db.commit()
        assert db.scalar(select(func.count()).select_from(RolePermission).where(RolePermission.role_id == admin_role.id)) < db.scalar(select(func.count()).select_from(Permission))
        bootstrap(db)
        assert db.scalar(select(func.count()).select_from(RolePermission).where(RolePermission.role_id == admin_role.id)) == db.scalar(select(func.count()).select_from(Permission))


def test_first_admin_is_created_only_on_empty_base(monkeypatch):
    with session_factory()() as db:
        db.query(User).delete()
        db.commit()
    monkeypatch.setenv("ADMIN_LOGIN", "boss")
    monkeypatch.setenv("ADMIN_PASSWORD", "Strong-First-Pass-5")
    get_settings.cache_clear()
    try:
        with session_factory()() as db:
            assert bootstrap(db)["admin_created"] is True
            assert bootstrap(db)["admin_created"] is False
            assert db.scalar(select(func.count()).select_from(User)) == 1
    finally:
        monkeypatch.delenv("ADMIN_LOGIN")
        monkeypatch.delenv("ADMIN_PASSWORD")
        get_settings.cache_clear()


def test_create_admin_command_rejects_weak_password(capsys):
    assert cli.main(["create-admin", "--login", "boss", "--password", "12345678"]) == 1
    assert cli.main(["create-admin", "--login", "boss", "--password", "Strong-First-Pass-5"]) == 0
    assert "создан" in capsys.readouterr().out


def test_seed_demo_needs_confirmation_and_makes_a_working_system(make_client, capsys):
    cli.main(["create-admin", "--login", "boss", "--password", "Strong-First-Pass-5"])
    assert cli.main(["seed-demo"]) == 1
    assert cli.main(["seed-demo", "--yes"]) == 0
    out = capsys.readouterr().out
    passwords = dict(re.findall(r"^\s+(\w+)\s+(\S+)\s+\(", out, flags=re.M))
    assert set(passwords) == {"director", "dispatcher", "mechanic", "driver"}
    assert cli.main(["seed-demo", "--yes"]) == 1                      # второй раз не добавляет
    c = make_client()
    assert c.login("driver", passwords["driver"]).json()["must_change_password"] is True
    assert c.post("/auth/password", {"current_password": passwords["driver"], "new_password": "Driver-New-Pass-3"}).status_code == 200
    mine = c.get("/vehicles").json()["items"]
    assert len(mine) == 1                                              # у водителя одна закреплённая машина
    boss = make_client()
    boss.login("boss", "Strong-First-Pass-5")
    assert boss.get("/vehicles").json()["total"] == 8
    assert boss.get("/dashboard").json()["requests"]["open"] == 1
    assert boss.get("/vehicles", params={"status": "in_repair"}).json()["total"] == 1
