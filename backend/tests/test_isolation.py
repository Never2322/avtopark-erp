# -*- coding: utf-8 -*-
"""Записи принадлежат своему юрлицу; чужое не видно ни в списках, ни по прямой ссылке."""
import datetime as dt


def test_director_of_a_does_not_see_entity_b_anywhere(make_client, world):
    c = make_client("dir_a")
    plates = {v["plate"] for v in c.get("/vehicles").json()["items"]}
    assert "А001АА54" in plates and "В001ВВ54" not in plates
    assert c.get(f"/vehicles/{world.vb1}").status_code == 404                     # чужое «не существует», а не «запрещено»
    assert c.get(f"/vehicles/{world.vb1}/readings").status_code == 404
    assert c.get(f"/vehicles/{world.vb1}/status-history").status_code == 404
    assert c.post(f"/vehicles/{world.vb1}/status", {"status": "parked"}).status_code == 404
    assert c.post(f"/vehicles/{world.vb1}/readings", {"kind": "km", "value": 5000}).status_code == 404
    assert c.get(f"/employees/{world.d3}").status_code == 404
    names = {e["last_name"] for e in c.get("/employees").json()["items"]}
    assert "Третий" not in names and "Первый" in names


def test_switcher_for_foreign_entity_is_forbidden_not_empty(make_client, world):
    c = make_client("dir_a")
    for url in ("/vehicles", "/requests", "/waybills", "/dashboard", "/audit"):
        r = c.get(url, params={"legal_entity_id": world.B})
        assert r.status_code == 403, url


def test_cannot_create_records_in_foreign_entity(make_client, world):
    c = make_client("dir_a")
    r = c.post("/vehicles", {"legal_entity_id": world.B, "type_id": world.type_id, "plate": "Е777ЕЕ54"})
    assert r.status_code == 403
    r = c.post("/employees", {"last_name": "Чужой", "assignments": [{"legal_entity_id": world.B}]})
    assert r.status_code in (403, 422)
    r = c.post("/requests", {"vehicle_id": world.vb1, "title": "Чужая машина"})
    assert r.status_code in (403, 422)
    r = c.post("/waybills", {"vehicle_id": world.vb1, "driver_id": world.d3})
    assert r.status_code in (403, 422)


def test_foreign_documents_are_invisible(make_client, world):
    b = make_client("dir_b")
    req = b.post("/requests", {"vehicle_id": world.vb1, "title": "Скрипит подвеска"})
    assert req.status_code == 201, req.text
    wb = b.post("/waybills", {"vehicle_id": world.vb1, "driver_id": world.d3})
    assert wb.status_code == 201, wb.text
    a = make_client("dir_a")
    assert a.get(f"/requests/{req.json()['id']}").status_code == 404
    assert a.get(f"/waybills/{wb.json()['id']}").status_code == 404
    assert a.get("/requests").json()["total"] == 0 and a.get("/waybills").json()["total"] == 0
    assert a.post(f"/requests/{req.json()['id']}/comments", {"text": "привет"}).status_code == 404
    assert a.post(f"/waybills/{wb.json()['id']}/cancel", {"reason": "чужой лист"}).status_code == 404


def test_numbers_are_separate_per_entity_and_gapless(make_client, world):
    a, b = make_client("dir_a"), make_client("dir_b")
    nums_a = [a.post("/requests", {"vehicle_id": world.va1, "title": f"Заявка {i}"}).json()["number"] for i in range(3)]
    nums_b = [b.post("/requests", {"vehicle_id": world.vb1, "title": f"Заявка {i}"}).json()["number"] for i in range(2)]
    assert nums_a == [1, 2, 3] and nums_b == [1, 2]


def test_entity_scoped_director_cannot_touch_global_admin_areas(make_client, world):
    c = make_client("dir_a")
    for method, url in (("get", "/users"), ("get", "/roles"), ("get", "/backups"), ("post", "/backups")):
        assert getattr(c, method)(url).status_code == 403, url
    assert c.post("/legal-entities", {"name": "ООО «Новое»"}).status_code == 403


def test_audit_is_limited_to_own_entity(make_client, world):
    make_client("dir_b").post("/requests", {"vehicle_id": world.vb1, "title": "Только для Беты"})
    make_client("dir_a").post("/requests", {"vehicle_id": world.va1, "title": "Только для Альфы"})
    seen = make_client("dir_a").get("/audit", params={"page_size": 200}).json()["items"]
    assert seen and all(row["legal_entity_id"] == world.A for row in seen)         # записи без юрлица (вход и др.) ему не видны
    everything = make_client("admin").get("/audit", params={"page_size": 200}).json()["items"]
    assert {row["legal_entity_id"] for row in everything} >= {world.A, world.B, None}


def test_admin_sees_both_entities_and_can_narrow(make_client, world):
    c = make_client("admin")
    assert {v["plate"] for v in c.get("/vehicles").json()["items"]} >= {"А001АА54", "В001ВВ54"}
    only_b = c.get("/vehicles", params={"legal_entity_id": world.B}).json()["items"]
    assert [v["plate"] for v in only_b] == ["В001ВВ54"]


def test_driver_cannot_be_assigned_across_entities(make_client, world):
    c = make_client("admin")
    r = c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": world.d3})
    assert r.status_code in (409, 422), r.text
    r = c.post("/waybills", {"vehicle_id": world.va3, "driver_id": world.d3, "work_date": dt.date.today().isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "driver_other_entity"
