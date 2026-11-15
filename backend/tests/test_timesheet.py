# -*- coding: utf-8 -*-
"""Табель: кто в сетке, разбор клеток, права по юрлицам, заполнение месяца, итоги, журнал, выгрузка."""
import calendar
import datetime as dt

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.models import AuditLog, Employee, TimesheetMark, Waybill
from app.services import timesheet as ts
from app.common import org_today

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


def put(c, entity, cells, month=MONTH):
    return c.put("/timesheet/cells", {"legal_entity_id": entity, "month": month, "cells": cells})


def cell(eid, day, text):
    return {"employee_id": eid, "day": day, "text": text}


def row(data, eid):
    return next(r for r in data["rows"] if r["employee_id"] == eid)


def weekend_days():
    return [d for d in range(1, DAYS + 1) if dt.date(PREV.year, PREV.month, d).weekday() >= 5]


def weekdays_():
    return [d for d in range(1, DAYS + 1) if dt.date(PREV.year, PREV.month, d).weekday() < 5]


# ---------------------------------------------------------------- разбор клетки

@pytest.mark.parametrize("text,code,hours", [
    ("Я", "Я", None), ("я", "Я", None), ("Я 6", "Я", 6), ("Я6", "Я", 6), ("Я 7,5", "Я", 7.5), ("B", "В", None), ("OT", "ОТ", None),
    ("с 2", "С", 2), ("  Б ", "Б", None), ("", None, None),
])
def test_parse_cell_ok(text, code, hours):
    got_code, got_hours = ts.parse_cell(text)
    assert got_code == code and (got_hours is None and hours is None or float(got_hours) == hours)


@pytest.mark.parametrize("text", ["Ю", "Я 25", "С", "ОТ 5", "В 8", "Я 7,25", "12", "Я Я", "ЯЯЯ"])
def test_parse_cell_errors(text):
    with pytest.raises(ts.CellError):
        ts.parse_cell(text)


def test_sums():
    cells = [("Я", None), ("Я", 6), ("РВ", None), ("С", 2), ("Б", None), ("ОТ", None), ("В", None), ("К", 4)]
    days, hours, absent = ts.sums(cells)
    assert (days, hours, absent) == (4, 8 + 6 + 8 + 2 + 4, 2)


# ---------------------------------------------------------------- кто что видит

def test_rights_and_entities(world, make_client):
    assert make_client("mech_a").get("/timesheet", params={"month": MONTH}).status_code == 403
    assert make_client("drv1").get("/timesheet", params={"month": MONTH}).status_code == 403
    assert make_client("store_a").get("/timesheet", params={"month": MONTH}).status_code == 403
    d = make_client("disp_a")
    data = sheet(d)                                           # единственное доступное юрлицо подставляется само
    assert data["legal_entity"]["id"] == world.A and data["can_edit"] and not data["can_export"]
    assert {r["employee_id"] for r in data["rows"]} == {world.d1, world.d2}
    assert d.get("/timesheet", params={"month": MONTH, "legal_entity_id": world.B}).status_code == 403
    assert make_client("dir_b").get("/timesheet", params={"month": MONTH, "legal_entity_id": world.A}).status_code == 403
    assert {r["employee_id"] for r in sheet(make_client("dir_b"))["rows"]} == {world.d3}
    adm = make_client("admin")
    r = adm.get("/timesheet", params={"month": MONTH})
    assert r.status_code == 422 and err(r)["field"] == "legal_entity_id"      # у администратора два юрлица: нужно выбрать
    assert {r["employee_id"] for r in sheet(adm, world.B)["rows"]} == {world.d3}


def test_month_validation(world, make_client):
    d = make_client("disp_a")
    for bad in ("2026-13", "26-01", "abc", "1999-05"):
        assert d.get("/timesheet", params={"month": bad}).status_code == 422
    assert sheet(d, month="")["month"] == TODAY.strftime("%Y-%m")


def test_who_is_in_the_sheet(world, make_client):
    """Уволенный до месяца и архивный не попадают в сетку; тот, у кого в месяце есть отметки, остаётся."""
    d = make_client("dir_a")
    with session_factory()() as db:
        e = db.get(Employee, world.d2)
        e.fired_on = PREV - dt.timedelta(days=5)
        db.commit()
    assert {r["employee_id"] for r in sheet(d)["rows"]} == {world.d1}
    with session_factory()() as db:
        db.add(TimesheetMark(legal_entity_id=world.A, employee_id=world.d2, work_date=PREV, code="Я"))
        db.commit()
    assert {r["employee_id"] for r in sheet(d)["rows"]} == {world.d1, world.d2}


# ---------------------------------------------------------------- отметки

def test_save_cells_and_totals(world, make_client):
    c = make_client("dir_a")
    r = put(c, world.A, [cell(world.d1, 1, "Я"), cell(world.d1, 2, "Я 6"), cell(world.d1, 3, "Б"), cell(world.d1, 4, "с 2"), cell(world.d2, 1, "OT")])
    assert r.status_code == 200, r.text
    data = r.json()
    a = row(data, world.d1)
    assert a["marks"]["2"] == {"code": "Я", "hours": 6.0, "text": "Я 6"} and a["marks"]["1"]["text"] == "Я"
    assert (a["days"], a["hours"], a["absent"]) == (2, 8 + 6 + 2, 1)
    assert row(data, world.d2)["marks"]["1"]["code"] == "ОТ"          # латинские «OT» превратились в русские
    assert data["changed"] == 5
    # правка и стирание
    r = put(c, world.A, [cell(world.d1, 1, "Н"), cell(world.d1, 3, ""), cell(world.d1, 2, "Я 6")])
    a = row(r.json(), world.d1)
    assert a["marks"]["1"]["code"] == "Н" and "3" not in a["marks"] and r.json()["changed"] == 2        # «Я 6» не менялась
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(TimesheetMark)) == 4


def test_save_is_all_or_nothing_and_reports_cells(world, make_client):
    c = make_client("dir_a")
    r = put(c, world.A, [cell(world.d1, 1, "Я"), cell(world.d1, 2, "Ю"), cell(world.d1, 3, "С"), cell(world.d2, 4, "Я 30"), cell(999999, 5, "Я"), cell(world.d1, 31 if DAYS < 31 else 1, "Я")])
    assert r.status_code == 422
    fields = err(r)["fields"]
    assert f"c_{world.d1}_2" in fields and f"c_{world.d1}_3" in fields and f"c_{world.d2}_4" in fields and "c_999999_5" in fields
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(TimesheetMark)) == 0          # ничего не сохранилось, в том числе верная «Я»


def test_cannot_mark_someone_from_another_entity(world, make_client):
    c = make_client("dir_a")
    r = put(c, world.A, [cell(world.d3, 1, "Я")])
    assert r.status_code == 422 and f"c_{world.d3}_1" in err(r)["fields"]
    assert put(make_client("dir_b"), world.A, [cell(world.d1, 1, "Я")]).status_code == 403
    assert put(make_client("dir_b"), world.B, [cell(world.d3, 1, "Я")]).status_code == 200


def test_edit_right_needed(world, make_client):
    assert put(make_client("acc_a"), world.A, [cell(world.d1, 1, "Я")]).status_code == 200      # бухгалтер ведёт табель
    assert put(make_client("mech_a"), world.A, [cell(world.d1, 1, "Я")]).status_code == 403
    assert put(make_client("drv1"), world.A, [cell(world.d1, 1, "Я")]).status_code == 403


def test_audit_records_cell_changes(world, make_client):
    c = make_client("dir_a")
    put(c, world.A, [cell(world.d1, 1, "Я"), cell(world.d1, 2, "Б")])
    put(c, world.A, [cell(world.d1, 1, "Я")])                      # ничего не изменилось: записи в журнале не будет
    put(c, world.A, [cell(world.d1, 2, "")])
    with session_factory()() as db:
        logs = list(db.scalars(select(AuditLog).where(AuditLog.action == "timesheet.save").order_by(AuditLog.id)))
    assert len(logs) == 2 and logs[0].legal_entity_id == world.A
    assert logs[0].details["changed"] == 2 and logs[0].details["month"] == MONTH
    assert logs[1].details["changes"] == [[world.d1, 2, "Б", ""]]


# ---------------------------------------------------------------- заполнение

def test_fill_weekends_and_weekdays(world, make_client):
    c = make_client("dir_a")
    r = c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekends"})
    assert r.status_code == 200 and r.json()["added"] == 2 * len(weekend_days())
    a = row(r.json(), world.d1)
    assert {int(k) for k, v in a["marks"].items() if v["code"] == "В"} == set(weekend_days())
    r = c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekdays"})
    assert r.json()["added"] == 2 * len(weekdays_())
    a = row(r.json(), world.d1)
    assert a["days"] == len(weekdays_()) and a["hours"] == 8 * len(weekdays_())
    again = c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekdays"})
    assert again.json()["added"] == 0                          # уже заполненное не трогаем


def test_fill_keeps_existing_marks(world, make_client):
    c = make_client("dir_a")
    wd = weekdays_()[0]
    put(c, world.A, [cell(world.d1, wd, "ОТ")])
    r = c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekdays"})
    assert row(r.json(), world.d1)["marks"][str(wd)]["code"] == "ОТ"


def test_fill_respects_hiring_date(world, make_client):
    c = make_client("dir_a")
    hired = dt.date(PREV.year, PREV.month, 15)
    with session_factory()() as db:
        db.get(Employee, world.d2).hired_on = hired
        db.commit()
    r = c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekdays"})
    days = sorted(int(k) for k in row(r.json(), world.d2)["marks"])
    assert days and days[0] >= 15 and all(dt.date(PREV.year, PREV.month, d).weekday() < 5 for d in days)
    assert len(row(r.json(), world.d1)["marks"]) == len(weekdays_())


def test_fill_from_waybills(world, make_client):
    c = make_client("dir_a")
    sat = weekend_days()[0]
    mon = weekdays_()[0]
    with session_factory()() as db:
        for n, (day, status) in enumerate([(mon, "closed"), (sat, "open"), (weekdays_()[1], "draft"), (weekdays_()[2], "cancelled")], 1):
            db.add(Waybill(legal_entity_id=world.A, year=PREV.year, number=n, vehicle_id=world.va1, driver_id=world.d1, status=status,
                           work_date=dt.date(PREV.year, PREV.month, day)))
        db.commit()
    c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekends"})       # в субботу уже стоит «В»
    r = c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "waybills"})
    assert r.status_code == 200, r.text
    a = row(r.json(), world.d1)
    assert a["marks"][str(mon)]["code"] == "Я" and a["marks"][str(sat)]["code"] == "РВ"                # «В» заменена: в выходной выезжал
    assert str(weekdays_()[1]) not in a["marks"] and str(weekdays_()[2]) not in a["marks"]              # черновик и аннулированный не считаются
    assert row(r.json(), world.d2)["marks"].get(str(mon)) is None


def test_fill_validation_and_rights(world, make_client):
    c = make_client("dir_a")
    assert c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "magic"}).status_code == 422
    assert make_client("mech_a").post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekends"}).status_code == 403
    assert make_client("dir_b").post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekends"}).status_code == 403
    c.post("/timesheet/fill", {"legal_entity_id": world.A, "month": MONTH, "mode": "weekends"})
    with session_factory()() as db:
        assert db.scalars(select(AuditLog.details).where(AuditLog.action == "timesheet.fill")).one()["added"] == 2 * len(weekend_days())


# ---------------------------------------------------------------- выгрузка

def test_export_csv(world, make_client):
    c = make_client("dir_a")
    put(c, world.A, [cell(world.d1, 1, "Я 6"), cell(world.d1, 2, "Б")])
    r = c.get("/timesheet/export.csv", params={"month": MONTH, "legal_entity_id": world.A})
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("№;ФИО;Должность;1;2;3") and lines[0].endswith("Дней;Часов;Неявок, дней")
    mine = next(ln for ln in lines if "Первый" in ln)
    assert ";Я 6;Б;" in mine and mine.endswith(";1;6;1")
    assert make_client("disp_a").get("/timesheet/export.csv", params={"month": MONTH}).status_code == 403          # диспетчер выгружать не может
    assert make_client("dir_b").get("/timesheet/export.csv", params={"month": MONTH, "legal_entity_id": world.A}).status_code == 403
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "timesheet.export")) == 1
