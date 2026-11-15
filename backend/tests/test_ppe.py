# -*- coding: utf-8 -*-
"""СИЗ: нормы, выдача со сроком замены, состояния, замена одной кнопкой, права и юрлица."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, PpeIssue
from app.services.ppe import add_months, due_for

TODAY = dt.date.today()


def d(offset: int = 0) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def fields(r):
    assert r.status_code == 422, r.text
    j = r.json()
    return (j["detail"] if "detail" in j else j)["fields"]


def norm(c, name="Куртка зимняя", months=12, **kw):
    r = c.post("/ppe_norms", {"name": name, "wear_months": months, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def issue(c, world, ppe_id, employee=None, **kw):
    body = {"legal_entity_id": world.A, "employee_id": employee or world.d1, "ppe_id": ppe_id, "issue_date": d(-30), "qty": 1}
    body.update(kw)
    return c.post("/ppe", body)


def test_add_months_clamps_day():
    assert add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert add_months(dt.date(2024, 1, 31), 1) == dt.date(2024, 2, 29)
    assert add_months(dt.date(2026, 11, 15), 3) == dt.date(2027, 2, 15)
    assert add_months(dt.date(2026, 12, 1), 12) == dt.date(2027, 12, 1)
    assert due_for(dt.date(2026, 5, 1), None) is None and due_for(None, 12) is None


# ---------------------------------------------------------------- нормы

def test_norms_crud_unique_and_rights(world, make_client):
    s = make_client("store_a")
    r = s.post("/ppe_norms", {"name": "  Перчатки   рабочие ", "wear_months": 1})
    assert r.status_code == 201 and r.json()["name"] == "Перчатки рабочие"
    assert "name" in fields(s.post("/ppe_norms", {"name": "перчатки рабочие"}))
    assert "wear_months" in fields(s.post("/ppe_norms", {"name": "Каска", "wear_months": 0}))
    assert "wear_months" in fields(s.post("/ppe_norms", {"name": "Каска", "wear_months": 121}))
    assert s.post("/ppe_norms", {"name": "Каска"}).status_code == 201                      # без срока можно
    assert make_client("mech_a").get("/ppe_norms").status_code == 403
    assert make_client("disp_a").get("/ppe_norms").status_code == 200
    assert make_client("disp_a").post("/ppe_norms", {"name": "Х"}).status_code == 403
    assert make_client("dir_b").get("/ppe_norms").status_code == 200                       # нормы общие для всех юрлиц


def test_norm_usage_counter_and_archive(world, make_client):
    c = make_client("dir_a")
    nid = norm(c, "Ботинки", 24)
    assert issue(c, world, nid).status_code == 201
    assert c.get("/ppe_norms").json()["items"][0]["used"] == 1
    assert c.post(f"/ppe_norms/{nid}/void", {"reason": "Сняли с производства"}).status_code == 200
    r = issue(c, world, nid)
    assert "ppe_id" in fields(r)                                                            # убранную позицию больше не выдать
    opts = c.get("/options/ppe").json()["items"]
    assert nid not in [o["id"] for o in opts]


# ---------------------------------------------------------------- выдача

def test_issue_auto_due_and_manual_due(world, make_client):
    c = make_client("store_a")
    nid = norm(c, "Куртка", 6)
    r = issue(c, world, nid, issue_date="2026-01-31")
    assert r.status_code == 201, r.text
    assert r.json()["due_date"] == "2026-07-31" and r.json()["employee_id_label"].startswith("Первый")
    r = issue(c, world, nid, issue_date="2026-01-31", due_date="2026-04-01")
    assert r.json()["due_date"] == "2026-04-01"                                            # вручную указанный срок остаётся
    no_term = norm(c, "Очки", None)
    assert issue(c, world, no_term).json()["due_date"] is None


def test_issue_validation(world, make_client):
    c = make_client("dir_a")
    nid = norm(c, "Жилет", 12)
    assert "issue_date" in fields(issue(c, world, nid, issue_date=d(5)))
    assert "returned_date" in fields(issue(c, world, nid, issue_date=d(-10), returned_date=d(-20)))
    assert "due_date" in fields(issue(c, world, nid, issue_date=d(-10), due_date=d(-20)))
    assert "qty" in fields(issue(c, world, nid, qty=0))
    assert "employee_id" in fields(issue(c, world, nid, employee=world.d3))                 # сотрудник из другого юрлица
    assert "employee_id" in fields(issue(c, world, nid, employee=999999))
    assert "ppe_id" in fields(issue(c, world, 999999))
    assert "legal_entity_id" in fields(c.post("/ppe", {"employee_id": world.d1, "ppe_id": nid, "issue_date": d(-1), "qty": 1}))


def test_edit_recomputes_only_automatic_due(world, make_client):
    c = make_client("dir_a")
    nid = norm(c, "Перчатки", 3)
    a = issue(c, world, nid, issue_date="2026-02-10").json()
    assert a["due_date"] == "2026-05-10"
    r = c.patch(f"/ppe/{a['id']}", {"issue_date": "2026-03-10"})
    assert r.json()["due_date"] == "2026-06-10"                                             # срок был посчитан сам: пересчитался
    b = issue(c, world, nid, issue_date="2026-02-10", due_date="2026-12-01").json()
    r = c.patch(f"/ppe/{b['id']}", {"issue_date": "2026-03-10"})
    assert r.json()["due_date"] == "2026-12-01"                                             # срок указан вручную: не трогаем
    r = c.patch(f"/ppe/{a['id']}", {"due_date": ""})
    assert r.json()["due_date"] == "2026-06-10"                                             # пусто — снова считаем сами


def test_states_filters_and_left(world, make_client):
    c = make_client("dir_a")
    nid = norm(c, "Каска", 1)
    soon = issue(c, world, nid, issue_date=d(-20), due_date=d(10)).json()
    late = issue(c, world, nid, employee=world.d2, issue_date=d(-40), due_date=d(-3)).json()
    today_ = issue(c, world, nid, issue_date=d(-40), due_date=d(0)).json()
    ok = issue(c, world, nid, issue_date=d(-2), due_date=d(200)).json()
    closed = issue(c, world, nid, issue_date=d(-90), due_date=d(-60), returned_date=d(-60)).json()
    st = {x["id"]: x for x in c.get("/ppe").json()["items"]}
    assert st[soon["id"]]["state"]["text"] == "Скоро замена" and st[soon["id"]]["left"] == "10 дн."
    assert st[late["id"]]["state"]["text"] == "Просрочено" and st[late["id"]]["left"] == "просрочено на 3 дн."
    assert st[today_["id"]]["state"]["text"] == "Пора менять" and st[ok["id"]]["state"]["text"] == "Носит"
    assert st[closed["id"]]["state"]["text"] == "Возвращено"
    ids = lambda **q: {x["id"] for x in c.get("/ppe", params=q).json()["items"]}          # noqa: E731
    assert ids(state="active") == {soon["id"], late["id"], today_["id"], ok["id"]}
    assert ids(state="attention") == {soon["id"], late["id"], today_["id"]}
    assert ids(state="returned") == {closed["id"]}
    assert ids(employee_id=world.d2) == {late["id"]}
    assert ids(q="Второй") == {late["id"]} and ids(q="каска") == set(st)


# ---------------------------------------------------------------- замена

def test_replace_closes_old_and_issues_new(world, make_client):
    c = make_client("store_a")
    nid = norm(c, "Куртка", 12)
    old = issue(c, world, nid, issue_date=d(-300), size="52", qty=2, due_date=d(-5)).json()
    r = c.post(f"/ppe/{old['id']}/replace", {"note": "износилась"})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["issue_date"] == d(0) and new["size"] == "52" and new["qty"] == 2 and new["employee_id"] == world.d1
    assert new["due_date"] == add_months(TODAY, 12).isoformat() and new["note"] == "износилась"
    items = {x["id"]: x for x in c.get("/ppe").json()["items"]}
    assert items[old["id"]]["returned_date"] == d(0) and items[old["id"]]["state"]["text"] == "Возвращено"
    with session_factory()() as db:
        log = db.scalars(select(AuditLog).where(AuditLog.action == "ppe.replace")).one()
        assert log.details["replaced"] == old["id"] and log.legal_entity_id == world.A
    again = c.post(f"/ppe/{old['id']}/replace", {})
    assert again.status_code == 409                                                         # уже возвращена


def test_replace_validation_and_rights(world, make_client):
    c = make_client("dir_a")
    nid = norm(c, "Ботинки", 12)
    old = issue(c, world, nid, issue_date=d(-10)).json()
    assert c.post(f"/ppe/{old['id']}/replace", {"issue_date": d(-20)}).status_code == 422
    assert c.post(f"/ppe/{old['id']}/replace", {"issue_date": d(5)}).status_code == 422
    assert make_client("disp_a").post(f"/ppe/{old['id']}/replace", {}).status_code == 403   # диспетчер только смотрит
    assert make_client("dir_b").post(f"/ppe/{old['id']}/replace", {}).status_code == 404    # чужое юрлицо не раскрывается
    assert make_client("drv1").post(f"/ppe/{old['id']}/replace", {}).status_code == 404
    with session_factory()() as db:
        assert db.scalar(select(PpeIssue.returned_date).where(PpeIssue.id == old["id"])) is None


def test_isolation_between_entities_and_roles(world, make_client):
    c = make_client("dir_a")
    nid = norm(c, "Респиратор", 6)
    a = issue(c, world, nid).json()
    b = make_client("dir_b")
    assert b.get("/ppe").json()["items"] == [] and b.get("/ppe", params={"legal_entity_id": world.A}).status_code == 403
    assert b.patch(f"/ppe/{a['id']}", {"size": "L"}).status_code == 404
    assert b.post(f"/ppe/{a['id']}/void", {"reason": "проверка"}).status_code == 404
    assert make_client("mech_a").get("/ppe").status_code == 403
    assert make_client("acc_a").get("/ppe").status_code == 200 and make_client("acc_a").post("/ppe", {}).status_code == 403
    # кладовщик выбирает сотрудника в форме выдачи, хотя вкладки «Сотрудники» у него нет
    assert make_client("store_a").get("/options/employee").status_code == 200
    # администратор видит всё, но запись добавляет в явно выбранное юрлицо
    adm = make_client("admin")
    assert {x["legal_entity_id"] for x in adm.get("/ppe").json()["items"]} == {world.A}
    assert issue(adm, world, nid, employee=world.d3, legal_entity_id=world.B).status_code == 201


def test_void_restore_and_export(world, make_client):
    c = make_client("dir_a")
    nid = norm(c, "Фартук", 12)
    a = issue(c, world, nid).json()
    assert c.post(f"/ppe/{a['id']}/void", {"reason": "ошибочно внесли"}).status_code == 200
    assert c.get("/ppe").json()["items"] == [] and len(c.get("/ppe", params={"archived": "true"}).json()["items"]) == 1
    assert c.post(f"/ppe/{a['id']}/restore").status_code == 200
    r = c.get("/ppe/export.csv")
    assert r.status_code == 200 and "Фартук" in r.content.decode("utf-8-sig") and "Носит" in r.content.decode("utf-8-sig")
