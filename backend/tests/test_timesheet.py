# -*- coding: utf-8 -*-
"""Табель: сколько заработал каждый сотрудник в каждый день месяца. Кто в сетке, суммы по дням и за месяц, права по юрлицам, выгрузка.
Отметок (явка, отпуск, больничный) в табеле нет."""
import calendar
import datetime as dt

from sqlalchemy import func, select

from app.common import org_today
from app.db import session_factory
from app.models import AuditLog, Employee, WorkLog

TODAY = org_today()
PREV = (TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=1)       # прошлый месяц целиком: результат не зависит от «сегодня»
MONTH = PREV.strftime("%Y-%m")
DAYS = calendar.monthrange(PREV.year, PREV.month)[1]


def err(r):
    j = r.json()
    return j["detail"] if "detail" in j else j


def sheet(c, entity=None, month=MONTH):
    r = c.get("/timesheet", params={"month": month, **({"legal_entity_id": entity} if entity else {})})
    assert r.status_code == 200, r.text
    return r.json()


def row(data, eid):
    return next(r for r in data["rows"] if r["employee_id"] == eid)


def mk_tariff(c, name, kind, unit, rate):
    r = c.post("/tariffs", {"name": name, "kind": kind, "unit": unit, "rate": str(rate)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_hours(c, world, eid, day, hours, tariff, kind="work", **kw):
    body = {"legal_entity_id": world.A, "work_date": PREV.replace(day=day).isoformat(), "employee_id": eid, "kind": kind, "hours": str(hours), "tariff_id": tariff}
    if kind == "repair":
        body["description"] = "Ремонт узла"
    body.update(kw)
    r = c.post("/worklog", body)
    assert r.status_code == 201, r.text
    return r.json()


def test_rights_and_entities(world, make_client):
    assert make_client("mech_a").get("/timesheet", params={"month": MONTH}).status_code == 403
    assert make_client("drv1").get("/timesheet", params={"month": MONTH}).status_code == 403
    assert make_client("store_a").get("/timesheet", params={"month": MONTH}).status_code == 403
    d = make_client("disp_a")
    data = sheet(d)                                           # единственное доступное юрлицо подставляется само
    assert data["legal_entity"]["id"] == world.A and data["can_add"] and not data["can_export"]
    assert {r["employee_id"] for r in data["rows"]} == {world.d1, world.d2}
    assert d.get("/timesheet", params={"month": MONTH, "legal_entity_id": world.B}).status_code == 403
    assert make_client("dir_b").get("/timesheet", params={"month": MONTH, "legal_entity_id": world.A}).status_code == 403
    assert {r["employee_id"] for r in sheet(make_client("dir_b"))["rows"]} == {world.d3}
    adm = make_client("admin")
    r = adm.get("/timesheet", params={"month": MONTH})
    assert r.status_code == 422 and err(r)["field"] == "legal_entity_id"      # у администратора два юрлица: нужно выбрать
    assert {r["employee_id"] for r in sheet(adm, world.B)["rows"]} == {world.d3}


def test_sheet_has_no_marks_and_no_marking_routes(world, make_client):
    """Отметок кодами в табеле нет: ни в ответе, ни в маршрутах для их правки."""
    c = make_client("dir_a")
    data = sheet(c)
    assert "codes" not in data and "fill_modes" not in data and all("marks" not in r for r in data["rows"])
    assert c.put("/timesheet/cells", {"legal_entity_id": world.A, "month": MONTH, "cells": []}).status_code in (404, 405)
    assert c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekends"}).status_code in (404, 405)
    # право «ставить отметки» убрано из списка прав
    assert "timesheet.edit" not in {x["code"] for x in make_client("admin").get("/permissions").json()["items"]}


def test_money_per_day_and_month(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Экскаватор, час", "work", "hour", 1000)
    rep = mk_tariff(d, "Ремонт, час", "repair", "hour", 400)
    day_t = mk_tariff(d, "Сутки", "work", "day", 3000)
    add_hours(d, world, world.d1, 3, 8, work)
    add_hours(d, world, world.d1, 3, 2, rep, kind="repair")
    add_hours(d, world, world.d1, 5, "4,5", work)
    add_hours(d, world, world.d2, 3, 1, day_t)
    add_hours(d, world, world.d2, 3, 6, day_t)                      # суточный тариф: один раз в день и вид
    data = sheet(d)
    a, b = row(data, world.d1), row(data, world.d2)
    assert a["days"]["3"] == {"work_h": 8, "repair_h": 2, "work": 8000, "repair": 800, "total": 8800}
    assert a["days"]["5"]["total"] == 4500 and "4" not in a["days"]
    assert a["totals"]["total"] == 13300 and a["totals"]["work_h"] == 12.5 and a["totals"]["repair"] == 800 and a["worked_days"] == 2
    assert b["days"]["3"]["total"] == 3000 and b["totals"]["total"] == 3000
    assert data["day_totals"] == {"3": 11800.0, "5": 4500.0}
    assert data["totals"]["total"] == 16300 and data["totals"]["work"] == 15500 and data["totals"]["repair"] == 800
    # запись, убранная в архив, из табеля уходит
    items = d.get("/worklog", params={"date_from": PREV.replace(day=5).isoformat(), "date_to": PREV.replace(day=5).isoformat()}).json()["items"]
    assert d.post(f"/worklog/{items[0]['id']}/void", {"reason": "Ошибка"}).status_code == 200
    data = sheet(d)
    assert "5" not in row(data, world.d1)["days"] and data["totals"]["total"] == 11800
    # другой месяц — отдельно
    assert sheet(d, month=TODAY.strftime("%Y-%m"))["totals"]["total"] == 0


def test_who_is_in_the_sheet(world, make_client):
    """Уволенный до месяца и без часов не попадает в сетку; если в месяце у него есть часы, он остаётся."""
    d = make_client("dir_a")
    tariff = mk_tariff(d, "Час", "work", "hour", 500)
    with session_factory()() as db:
        e = db.get(Employee, world.d2)
        e.fired_on = PREV - dt.timedelta(days=5)
        db.commit()
    assert {r["employee_id"] for r in sheet(d)["rows"]} == {world.d1}
    with session_factory()() as db:                                 # у уволенного нашлись часы (внесли задним числом)
        db.add(WorkLog(legal_entity_id=world.A, employee_id=world.d2, work_date=PREV, kind="work", hours=2, tariff_id=tariff, tariff_name="Час", unit="hour", rate=500))
        db.commit()
    data = sheet(d)
    assert {r["employee_id"] for r in data["rows"]} == {world.d1, world.d2}
    assert row(data, world.d2)["days"][str(PREV.day)]["total"] == 1000


def test_month_validation(world, make_client):
    d = make_client("disp_a")
    for bad in ("2026-13", "26-01", "abc", "1999-05"):
        assert d.get("/timesheet", params={"month": bad}).status_code == 422
    assert sheet(d, month="")["month"] == TODAY.strftime("%Y-%m")


def test_money_needs_the_right_to_see_hours(world, make_client):
    """Табель показывает деньги: без права смотреть часы работы и ремонта он закрыт, даже если право «Табель» есть."""
    adm = make_client("admin")
    roles = {r["code"]: r for r in adm.get("/roles").json()["items"]}
    acc = roles["accountant"]
    assert make_client("acc_a").get("/timesheet", params={"month": MONTH}).status_code == 200
    keep = [c for c in acc["permissions"] if c != "worklog.view"]
    assert adm.put(f"/roles/{acc['id']}/permissions", {"permissions": keep}).status_code == 200
    r = make_client("acc_a").get("/timesheet", params={"month": MONTH})
    assert r.status_code == 403


def test_export_csv(world, make_client):
    c = make_client("dir_a")
    work = mk_tariff(c, "Час", "work", "hour", 400)
    rep = mk_tariff(c, "Ремонт", "repair", "hour", 350)
    add_hours(c, world, world.d1, 3, 8, work)
    add_hours(c, world, world.d1, 3, 2, rep, kind="repair")
    r = c.get("/timesheet/export.csv", params={"month": MONTH, "legal_entity_id": world.A})
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("№;ФИО;Должность;1;2;3") and lines[0].endswith("Часов работы;Часов ремонта;Работа, ₽;Ремонт, ₽;Итого, ₽")
    mine = next(ln for ln in lines if "Первый" in ln)
    assert ";3900,00;" in mine and mine.endswith(";8;2;3200,00;700,00;3900,00")
    last = lines[-1]
    assert last.startswith(";Итого за день;") and last.endswith(";8;2;3200,00;700,00;3900,00")
    assert make_client("disp_a").get("/timesheet/export.csv", params={"month": MONTH}).status_code == 403          # диспетчер выгружать не может
    assert make_client("dir_b").get("/timesheet/export.csv", params={"month": MONTH, "legal_entity_id": world.A}).status_code == 403
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "timesheet.export")) == 1
