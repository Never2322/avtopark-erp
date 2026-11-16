# -*- coding: utf-8 -*-
"""Вход, выход, блокировка, CSRF, временный пароль."""
from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, User, UserSession
from tests.helpers import PASSWORD


def test_login_ok_and_me(make_client, world):
    c = make_client()
    r = c.login("dir_a")
    assert r.status_code == 200
    body = r.json()
    assert body["login"] == "dir_a" and body["csrf_token"]
    assert "erp_session" in c.http.cookies
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert c.get("/auth/me").json()["permissions"]["vehicles.view"]["entities"] == [world.A]


def test_same_error_for_bad_password_unknown_and_inactive(make_client, world):
    with session_factory()() as db:
        db.scalar(select(User).where(User.login == "emp_a")).is_active = False
        db.commit()
    answers = []
    for login, pw in (("dir_a", "wrong-password"), ("nobody", "wrong-password"), ("emp_a", PASSWORD)):
        r = make_client().login(login, pw)
        answers.append((r.status_code, r.json()["code"], r.json()["message"]))
    assert len(set(answers)) == 1 and answers[0][0] == 401


def test_lockout_after_failures_even_with_right_password(make_client, world):
    c = make_client()
    for _ in range(5):
        assert c.login("dir_a", "bad-password-1").status_code == 401
    r = c.login("dir_a", PASSWORD)
    assert r.status_code == 429 and "Подождите" in r.json()["message"]
    # неизвестный логин блокируется так же, поэтому по ответу нельзя понять, есть ли такой пользователь
    for _ in range(5):
        c.login("ghost", "bad-password-1")
    assert c.login("ghost", "bad-password-1").status_code == 429


def test_logout_really_revokes_the_session(make_client, world):
    c = make_client("drv1")
    token = c.http.cookies.get("erp_session")
    assert c.post("/auth/logout").status_code == 200
    stolen = make_client()
    stolen.http.cookies.set("erp_session", token)
    assert stolen.get("/auth/me").status_code == 401


def test_only_token_hash_is_stored(make_client, world):
    c = make_client("drv1")
    token = c.http.cookies.get("erp_session")
    with session_factory()() as db:
        hashes = [s.token_hash for s in db.scalars(select(UserSession))]
    assert token not in hashes and all(len(h) == 64 for h in hashes)


def test_csrf_is_required_for_changes(make_client, world):
    c = make_client("dir_a")
    r = c.call("POST", "/vehicles/%d/status" % world.va1, json={"status": "in_repair", "reason": "Поломка"}, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    r = c.http.post("/api/vehicles/%d/status" % world.va1, json={"status": "in_repair", "reason": "Поломка"})   # вообще без заголовка
    assert r.status_code == 403 and r.json()["code"] == "csrf"


def test_no_session_means_401_everywhere(make_client, world):
    c = make_client()
    for url in ("/vehicles", "/employees", "/requests", "/waybills", "/audit", "/users", "/backups", "/dashboard"):
        assert c.get(url).status_code == 401, url


def test_temporary_password_blocks_everything_until_changed(make_client, world):
    with session_factory()() as db:
        db.scalar(select(User).where(User.login == "mech_a")).must_change_password = True
        db.commit()
    c = make_client("mech_a")
    r = c.get("/vehicles")
    assert r.status_code == 403 and r.json()["code"] == "password_change_required"
    assert c.get("/auth/me").status_code == 200
    assert c.post("/auth/password", {"current_password": PASSWORD, "new_password": "short"}).status_code == 422
    assert c.post("/auth/password", {"current_password": PASSWORD, "new_password": "mech_a"}).status_code == 422
    assert c.post("/auth/password", {"current_password": PASSWORD, "new_password": "12345678"}).status_code == 422
    assert c.post("/auth/password", {"current_password": PASSWORD, "new_password": PASSWORD}).status_code == 422
    assert c.post("/auth/password", {"current_password": "wrong-one-1", "new_password": "Brand-New-Pass-77"}).status_code == 422
    assert c.post("/auth/password", {"current_password": PASSWORD, "new_password": "Brand-New-Pass-77"}).status_code == 200
    assert c.get("/vehicles").status_code == 200
    assert make_client().login("mech_a", "Brand-New-Pass-77").status_code == 200


def test_password_change_closes_other_sessions(make_client, world):
    first, second = make_client("drv1"), make_client("drv1")
    assert second.post("/auth/password", {"current_password": PASSWORD, "new_password": "Brand-New-Pass-77"}).status_code == 200
    assert first.get("/auth/me").status_code == 401
    assert second.get("/auth/me").status_code == 200


def test_login_events_are_in_audit_without_secrets(make_client, world):
    make_client().login("dir_a", "bad-password-1")
    make_client("dir_a")
    with session_factory()() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.action.in_(("login.failed", "login.ok")))).all()
        assert {r.action for r in rows} == {"login.failed", "login.ok"}
        text = " ".join(str(r.details) + r.user_login for r in rows)
    assert "bad-password-1" not in text and PASSWORD not in text
