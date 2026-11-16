# -*- coding: utf-8 -*-
"""Выход на работу: водитель отвечает «выйду / не выйду», директор и диспетчер видят ответы и могут отметить за человека."""
import datetime as dt

from sqlalchemy import func, select

from app.common import org_today
from app.db import session_factory
from app.models import AuditLog, Employee, ShiftPlan

TODAY = org_today()


def fields(r):
    j = r.json()
    j = j["detail"] if "detail" in j else j
    out = dict(j.get("fields") or {})
    if j.get("field"):
        out[j["field"]] = j["message"]
    return out


def day(n=0):
    return (TODAY + dt.timedelta(days=n)).isoformat()


def answer(c, n=1, will_come=True, reason=""):
    return c.post("/attendance/mine", {"date": day(n), "will_come": will_come, "reason": reason})


def listing(c, n=1, **params):
    return c.get("/attendance", params={"date": day(n), **params})


def test_driver_answers_and_changes_mind(world, make_client):
    drv = make_client("drv1")
    page = drv.get("/attendance/mine").json()
    assert len(page["days"]) == 7 and page["days"][0]["date"] == day(0) and page["days"][0]["is_today"]
    assert all(d["status"] == "none" for d in page["days"])
    r = answer(drv, 1, True)
    assert r.status_code == 200 and r.json()["status"] == "yes" and r.json()["source"] == "driver"
    # передумал: ответ заменяется, строка остаётся одна
    r = answer(drv, 1, False, "Болею")
    assert r.status_code == 200 and r.json()["status"] == "no" and r.json()["reason"] == "Болею"
    page = drv.get("/attendance/mine").json()
    assert page["days"][1]["status"] == "no" and page["days"][1]["reason"] == "Болею" and page["days"][2]["status"] == "none"
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ShiftPlan)) == 1
        acts = [a for a in db.scalars(select(AuditLog).where(AuditLog.action == "attendance.set").order_by(AuditLog.id))]
        assert [(a.details["before"], a.details["after"]) for a in acts] == [("none", "yes"), ("yes", "no")]
    # «выйду» снова: причина стирается
    r = answer(drv, 1, True, "лишний текст")
    assert r.json()["status"] == "yes" and r.json()["reason"] == ""


def test_driver_answer_rules(world, make_client):
    drv = make_client("drv1")
    # не выйду без причины
    for reason in ("", "  ", "ж"):
        r = answer(drv, 1, False, reason)
        assert r.status_code == 422 and "reason" in fields(r), reason
    # прошедший день и слишком далёкое будущее
    assert "date" in fields(answer(drv, -1))
    assert answer(drv, 14).status_code == 200
    assert "date" in fields(answer(drv, 15))
    # сегодня ещё можно
    assert answer(drv, 0).status_code == 200
    # без права и без входа
    assert make_client("dir_a").post("/attendance/mine", {"date": day(1), "will_come": True}).status_code == 403
    assert make_client("dir_a").get("/attendance/mine").status_code == 403
    assert make_client().get("/attendance/mine").status_code == 401


def test_office_sees_everyone_with_status(world, make_client):
    make_client("drv1")
    answer(make_client("drv1"), 1, True)
    answer(make_client("drv2"), 1, False, "Отпросился")
    d = make_client("dir_a")
    data = listing(d).json()
    by = {i["employee_id"]: i for i in data["items"]}
    assert set(by) == {world.d1, world.d2}                    # водитель из чужого юрлица не виден
    assert by[world.d1]["status"] == "yes" and by[world.d2]["status"] == "no" and by[world.d2]["reason"] == "Отпросился"
    assert by[world.d1]["vehicles"] == ["А001АА54"]
    assert data["counts"] == {"yes": 1, "no": 1, "none": 0, "total": 2}
    # другой день: никто не ответил
    other = listing(d, 3).json()
    assert other["counts"] == {"yes": 0, "no": 0, "none": 2, "total": 2}
    assert all(i["status"] == "none" for i in other["items"])
    # диспетчер видит так же; водитель и механик список не видят
    assert listing(make_client("disp_a")).json()["counts"]["total"] == 2
    for login in ("drv1", "mech_a", "emp_a"):
        assert listing(make_client(login)).status_code == 403
    # директор другого юрлица видит только своего водителя
    assert [i["employee_id"] for i in listing(make_client("dir_b")).json()["items"]] == [world.d3]
    # чужое юрлицо в отборе не раскрывается
    assert listing(d, legal_entity_id=world.B).status_code == 403


def test_office_marks_for_driver(world, make_client):
    d = make_client("dir_a")
    r = d.put(f"/attendance/{world.d1}", {"date": day(1), "will_come": False, "reason": "Позвонил: заболел"})
    assert r.status_code == 200 and r.json()["status"] == "no" and r.json()["source"] == "staff"
    assert r.json()["source_label"] == "Отметили в офисе"
    # водитель видит ответ, отмеченный за него
    assert make_client("drv1").get("/attendance/mine").json()["days"][1]["source"] == "staff"
    # и сам может его поправить
    r = answer(make_client("drv1"), 1, True)
    assert r.json()["status"] == "yes" and r.json()["source"] == "driver"
    # правила: причина для «не выйдет», срок
    assert "reason" in fields(d.put(f"/attendance/{world.d1}", {"date": day(1), "will_come": False}))
    assert d.put(f"/attendance/{world.d1}", {"date": day(-3), "will_come": True}).status_code == 200
    assert "date" in fields(d.put(f"/attendance/{world.d1}", {"date": day(-8), "will_come": True}))
    assert "date" in fields(d.put(f"/attendance/{world.d1}", {"date": day(61), "will_come": True}))
    # чужой сотрудник — «не найден»; водитель и бухгалтер отмечать за других не могут
    assert d.put(f"/attendance/{world.d3}", {"date": day(1), "will_come": True}).status_code == 404
    assert make_client("drv1").put(f"/attendance/{world.d2}", {"date": day(1), "will_come": True}).status_code == 403
    assert make_client("acc_a").put(f"/attendance/{world.d2}", {"date": day(1), "will_come": True}).status_code == 403


def test_dates_and_archived_people(world, make_client):
    d = make_client("dir_a")
    assert listing(d, 100).status_code == 422
    assert d.get("/attendance", params={"date": "вчера"}).status_code == 422
    assert d.get("/attendance").json()["date"] == day(0)           # без даты — сегодня
    # уволенный к этому дню в списке не нужен
    with session_factory()() as db:
        db.get(Employee, world.d2).fired_on = TODAY + dt.timedelta(days=2)
        db.commit()
    assert {i["employee_id"] for i in listing(d, 1).json()["items"]} == {world.d1, world.d2}
    assert {i["employee_id"] for i in listing(d, 3).json()["items"]} == {world.d1}
    assert d.put(f"/attendance/{world.d2}", {"date": day(3), "will_come": True}).status_code == 422
