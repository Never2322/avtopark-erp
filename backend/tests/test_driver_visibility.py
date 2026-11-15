# -*- coding: utf-8 -*-
"""Где виден водитель: в списке сотрудников и в выборе «Закрепить водителя» он появляется в том юрлице, где работает.

Это правило объясняет, почему водитель, добавленный в одно юрлицо, не виден при выбранном вверху другом.
Сайт после проверки подставляет в форму выбранное вверху юрлицо и подсказывает, что делать; здесь закреплено, как ведёт себя сервер.
"""


def new_employee(c, last, entity, driver=True):
    r = c.post("/employees", {"last_name": last, "first_name": "Тест", "is_driver": driver, "assignments": [{"legal_entity_id": entity}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def listed(c, last, entity=None):
    params = {"q": last}
    if entity is not None:
        params["legal_entity_id"] = entity
    return c.get("/employees", params=params).json()["total"]


def offered(c, eid, entity):
    items = c.get("/employees/lookup", params={"drivers": "true", "legal_entity_id": entity}).json()["items"]
    return any(i["id"] == eid for i in items)


def test_driver_is_visible_only_in_his_company(make_client, world):
    c = make_client("admin")
    eid = new_employee(c, "Новичков", world.B)
    assert listed(c, "Новичков", world.B) == 1
    assert listed(c, "Новичков", world.A) == 0                 # при выбранном вверху другом юрлице в списке его нет
    assert listed(c, "Новичков") == 1                          # «Все юрлица»: виден
    assert offered(c, eid, world.B) and not offered(c, eid, world.A)


def test_driver_can_be_assigned_only_to_vehicle_of_his_company(make_client, world):
    c = make_client("admin")
    eid = new_employee(c, "Новичков", world.B)
    assert c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": eid}).status_code == 422       # машина юрлица A
    assert c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": eid}).json()["code"] == "wrong_entity"
    assert c.post(f"/vehicles/{world.vb1}/assignments", {"employee_id": eid, "role": "backup"}).status_code == 201


def test_accepting_into_another_company_makes_driver_selectable_there(make_client, world):
    c = make_client("admin")
    eid = new_employee(c, "Совместитель", world.A)
    assert not offered(c, eid, world.B)
    assert c.post(f"/employees/{eid}/assignments", {"legal_entity_id": world.B}).status_code == 201     # «Принять в юрлицо»
    assert offered(c, eid, world.B) and offered(c, eid, world.A)
    assert listed(c, "Совместитель", world.B) == 1 and listed(c, "Совместитель", world.A) == 1
    assert c.post(f"/vehicles/{world.vb1}/assignments", {"employee_id": eid, "role": "backup"}).status_code == 201


def test_employee_without_driver_mark_is_listed_but_not_offered(make_client, world):
    c = make_client("admin")
    eid = new_employee(c, "Бухгалтеров", world.A, driver=False)
    assert listed(c, "Бухгалтеров", world.A) == 1               # в «Сотрудниках» виден
    assert not offered(c, eid, world.A)                         # а в «Закрепить водителя» нет: нет галочки «Водитель»
    assert c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": eid}).json()["code"] == "not_driver"
