# -*- coding: utf-8 -*-
"""Меню: порядок и названия задают директор и администратор, видят все."""

LAYOUT = {"groups": [
    {"key": "money", "title": "  Финансы  ", "items": [{"id": "/bills", "label": "Счета", "sub": ""}, {"id": "/expenses", "label": "", "sub": "Итоги"}]},
    {"key": "home", "title": "", "items": [{"id": "/", "label": "Сводка", "sub": ""}]},
]}


def test_director_and_admin_set_menu_for_everyone(make_client, world):
    assert make_client("drv1").get("/menu-layout").json() == {"layout": None, "can_edit": False}
    d = make_client("dir_a")
    assert d.get("/menu-layout").json()["can_edit"] is True
    r = d.call("PUT", "/menu-layout", json=LAYOUT)
    assert r.status_code == 200, r.text
    got = make_client("drv1").get("/menu-layout").json()["layout"]
    assert got["groups"][0]["title"] == "Финансы" and got["groups"][0]["items"][0] == {"id": "/bills", "label": "Счета", "sub": ""}
    assert make_client("admin").call("DELETE", "/menu-layout").status_code == 200
    assert make_client("mech_a").get("/menu-layout").json()["layout"] is None


def test_others_cannot_change_menu(make_client, world):
    for login in ("mech_a", "disp_a", "drv1", "acc_a"):
        c = make_client(login)
        assert c.call("PUT", "/menu-layout", json=LAYOUT).status_code == 403
        assert c.call("DELETE", "/menu-layout").status_code == 403


def test_bad_layout_is_refused_and_logged_when_saved(make_client, world):
    a = make_client("admin")
    dup = {"groups": [{"key": "a", "title": "", "items": [{"id": "/x"}]}, {"key": "b", "title": "", "items": [{"id": "/x"}]}]}
    assert a.call("PUT", "/menu-layout", json=dup).status_code == 422
    assert a.call("PUT", "/menu-layout", json={"groups": [{"key": "a", "title": "x" * 41, "items": []}]}).status_code == 422
    assert a.call("PUT", "/menu-layout", json=LAYOUT).status_code == 200
    actions = [x["action"] for x in a.get("/audit", params={"page_size": 50}).json()["items"]]
    assert "menu.save" in actions
