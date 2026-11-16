# -*- coding: utf-8 -*-
"""Матрица прав: что каждая роль может и чего не может. Это стартовая модель, её подтверждают руководители."""
import pytest

GET = "GET"
ALLOW, DENY = "allow", "deny"


def status(c, method, url, body=None):
    return c.call(method, url, json=body) if method != GET else c.get(url)


# роль → (метод, адрес, ожидание). Для «allow» ждём 2xx, для «deny» ждём 403.
MATRIX = {
    "admin": [(GET, "/users", ALLOW), (GET, "/roles", ALLOW), (GET, "/audit", ALLOW), (GET, "/backups", ALLOW), (GET, "/vehicles", ALLOW)],
    "dir_a": [(GET, "/vehicles", ALLOW), (GET, "/employees", ALLOW), (GET, "/audit", ALLOW), (GET, "/requests", ALLOW), (GET, "/waybills", ALLOW),
              (GET, "/users", DENY), (GET, "/roles", DENY), (GET, "/backups", DENY)],
    "mech_a": [(GET, "/vehicles", ALLOW), (GET, "/requests", ALLOW), (GET, "/employees", ALLOW), (GET, "/waybills", ALLOW),
               (GET, "/audit", DENY), (GET, "/users", DENY), (GET, "/backups", DENY), (GET, "/readings/pending", DENY)],
    "disp_a": [(GET, "/vehicles", ALLOW), (GET, "/waybills", ALLOW), (GET, "/employees", ALLOW), (GET, "/audit", DENY), (GET, "/users", DENY)],
    "drv1": [(GET, "/vehicles", ALLOW), (GET, "/waybills", ALLOW), (GET, "/requests", ALLOW),
             (GET, "/employees", DENY), (GET, "/audit", DENY), (GET, "/users", DENY), (GET, "/backups", DENY), (GET, "/roles", DENY)],
    "acc_a": [(GET, "/vehicles", ALLOW), (GET, "/waybills", ALLOW), (GET, "/employees", ALLOW), (GET, "/requests", ALLOW),
              (GET, "/audit", DENY), (GET, "/users", DENY), (GET, "/backups", DENY)],
    "emp_a": [(GET, "/requests", ALLOW), (GET, "/vehicles", DENY), (GET, "/waybills", DENY), (GET, "/employees", DENY), (GET, "/audit", DENY)],
    "store_a": [(GET, "/vehicles", ALLOW), (GET, "/requests", ALLOW), (GET, "/waybills", DENY), (GET, "/employees", DENY), (GET, "/audit", DENY)],
}
CASES = [(role, m, url, want) for role, rows in MATRIX.items() for m, url, want in rows]


@pytest.mark.parametrize("role,method,url,want", CASES, ids=[f"{r}:{u}:{w}" for r, _, u, w in CASES])
def test_matrix(make_client, world, role, method, url, want):
    r = status(make_client(role), method, url)
    if want == ALLOW:
        assert 200 <= r.status_code < 300, (r.status_code, r.text)
    else:
        assert r.status_code == 403, (r.status_code, r.text)


def test_changes_need_their_own_permissions(make_client, world):
    body = {"status": "in_repair", "reason": "Поломка"}
    assert make_client("drv1").post(f"/vehicles/{world.va1}/status", body).status_code == 403
    assert make_client("acc_a").post(f"/vehicles/{world.va1}/status", body).status_code == 403
    assert make_client("emp_a").post(f"/vehicles/{world.va1}/status", body).status_code == 403
    assert make_client("mech_a").post(f"/vehicles/{world.va1}/status", body).status_code == 200
    new = {"legal_entity_id": world.A, "type_id": world.type_id, "plate": "К111КК54"}
    assert make_client("mech_a").post("/vehicles", new).status_code == 403
    assert make_client("disp_a").post("/vehicles", new).status_code == 403
    assert make_client("dir_a").post("/vehicles", new).status_code == 201


def test_archive_and_edit_are_separate(make_client, world):
    assert make_client("mech_a").post(f"/vehicles/{world.va3}/archive").status_code == 403
    assert make_client("mech_a").patch(f"/vehicles/{world.va3}", {"plate": "А003АА54", "type_id": world.type_id, "note": "x"}).status_code == 403
    assert make_client("dir_a").post(f"/vehicles/{world.va3}/archive").status_code == 200


def test_storekeeper_can_create_request_but_pick_only_allowed_vehicles(make_client, world):
    c = make_client("store_a")
    assert c.post("/requests", {"vehicle_id": world.va1, "title": "Нужна ветошь на складе"}).status_code == 201
    assert c.post("/requests", {"vehicle_id": world.vb1, "title": "Чужая машина"}).status_code in (403, 422)


def test_employee_role_creates_request_for_any_vehicle_of_entity_and_sees_only_own(make_client, world):
    emp, other = make_client("emp_a"), make_client("store_a")
    mine = emp.post("/requests", {"vehicle_id": world.va3, "title": "Сотрудник заметил течь"})
    assert mine.status_code == 201, mine.text
    other.post("/requests", {"vehicle_id": world.va3, "title": "Чужое обращение"})
    titles = [r["title"] for r in emp.get("/requests").json()["items"]]
    assert titles == ["Сотрудник заметил течь"]
    assert emp.get(f"/vehicles/{world.va3}").status_code in (403, 404)


def test_role_changes_need_roles_manage_and_admin_role_is_fixed(make_client, world):
    admin = make_client("admin")
    roles = {r["code"]: r for r in admin.get("/roles").json()["items"]}
    admin_role = roles["admin"]["id"]
    assert admin.put(f"/roles/{admin_role}/permissions", {"permissions": ["dashboard.view"]}).status_code in (403, 409, 422)
    assert make_client("dir_a").put(f"/roles/{roles['driver']['id']}/permissions", {"permissions": ["users.manage"]}).status_code == 403
    r = admin.put(f"/roles/{roles['driver']['id']}/permissions", {"permissions": ["dashboard.view", "no.such.permission"]})
    assert r.status_code == 422


def test_cannot_remove_last_admin(make_client, world):
    admin = make_client("admin")
    me = admin.me["id"]
    r = admin.patch(f"/users/{me}", {"display_name": "Admin", "is_active": False, "roles": [{"role_id": world.role_ids["admin"], "legal_entity_id": None}]})
    assert r.status_code == 409
    r = admin.patch(f"/users/{me}", {"display_name": "Admin", "is_active": True, "roles": [{"role_id": world.role_ids["driver"], "legal_entity_id": world.A}]})
    assert r.status_code == 409
