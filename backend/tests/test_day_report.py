# -*- coding: utf-8 -*-
"""Отчёт за день: по водителям техника, путевой лист, спидометр, топливо, ставки, тип занятости, часы и заработок, итоги."""
import datetime as dt
from decimal import Decimal

from app.common import org_today
from app.db import session_factory
from sqlalchemy import select

from app.models import AuditLog, FuelFill, Waybill

TODAY = org_today()


def mk_tariff(c, name, kind, unit, rate):
    r = c.post("/tariffs", {"name": name, "kind": kind, "unit": unit, "rate": str(rate)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def prepare(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 450)
    rep = mk_tariff(d, "Ремонт", "repair", "hour", 300)
    assert d.put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": work, "repair_tariff_id": rep}).status_code == 200
    mech = make_client("mech_a")
    assert mech.post("/payroll/enter", {"employee_id": world.d1, "work_date": TODAY.isoformat(), "kind": "work", "hours": "8", "vehicle_id": world.va1}).status_code == 201
    assert mech.post("/payroll/enter", {"employee_id": world.d1, "work_date": TODAY.isoformat(), "kind": "repair", "hours": "2", "description": "Менял ремень"}).status_code == 201
    with session_factory()() as db:
        db.add(Waybill(legal_entity_id=world.A, year=TODAY.year, number=7, vehicle_id=world.va1, driver_id=world.d1, work_date=TODAY, status="closed",
                       start_km=Decimal(1000), end_km=Decimal(1100)))
        db.add(FuelFill(legal_entity_id=world.A, vehicle_id=world.va1, driver_id=world.d1, fill_date=TODAY, liters=Decimal("60.5")))
        db.add(FuelFill(legal_entity_id=world.A, vehicle_id=world.va2, driver_id=None, fill_date=TODAY, liters=Decimal("30")))
        db.add(FuelFill(legal_entity_id=world.A, vehicle_id=world.va1, driver_id=world.d1, fill_date=TODAY - dt.timedelta(days=1), liters=Decimal("99")))
        db.commit()
    return d


def table(r):
    t = r.json()["table"]
    labels = [c["label"] for c in t["columns"]]
    return labels, [dict(zip(labels, row)) for row in t["rows"]], t["totals"]


def test_day_report_rows_and_totals(world, make_client):
    d = prepare(world, make_client)
    r = d.get("/reports/day", params={"from": TODAY.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == f"Отчёт за {TODAY.strftime('%d.%m.%Y')}" and r.json()["period"]["kind"] == "day"
    labels, rows, totals = table(r)
    assert labels == ["Водитель", "Техника", "Путевой лист", "Спидометр", "Топливо, л", "Ставка", "Ставка ремонта", "Тип занятости", "Часы работы",
                      "Часы ремонта", "Работа, ₽", "Ремонт, ₽", "Всего, ₽"]
    by = {x["Водитель"]: x for x in rows}
    a = by["Первый Т."]
    assert a["Техника"] == "А001АА54" and a["Путевой лист"] == f"{TODAY.year}-0007" and a["Спидометр"] == "1 000 → 1 100 км"
    assert a["Топливо, л"] == 60.5                           # вчерашняя заправка не входит
    assert a["Ставка"] == "450 ₽/ч" and a["Ставка ремонта"] == "300 ₽/ч" and a["Тип занятости"] == "Работа и ремонт"
    assert (a["Часы работы"], a["Часы ремонта"], a["Работа, ₽"], a["Ремонт, ₽"], a["Всего, ₽"]) == (8, 2, 3600, 600, 4200)
    nobody = by["Без водителя"]                              # заправка без водителя и без путевого листа
    assert nobody["Техника"] == "А002АА54" and nobody["Топливо, л"] == 30 and nobody["Тип занятости"] == ""
    assert rows[-1]["Водитель"] == "Без водителя"            # «без водителя» всегда в конце
    assert totals[0] == "Итого" and totals[labels.index("Топливо, л")] == 90.5 and totals[labels.index("Всего, ₽")] == 4200


def test_day_report_type_and_empty_days(world, make_client):
    d = prepare(world, make_client)
    # на другой день никого нет
    _, rows, totals = table(d.get("/reports/day", params={"from": (TODAY - dt.timedelta(days=5)).isoformat()}))
    assert rows == [] and totals is None
    # только ремонт: тип «Ремонт», ставка работы показывается из тарифа сотрудника
    mech = make_client("mech_a")
    day = TODAY - dt.timedelta(days=2)
    assert mech.post("/payroll/enter", {"employee_id": world.d1, "work_date": day.isoformat(), "kind": "repair", "hours": "3", "description": "Замена гидравлики"}).status_code == 201
    _, rows, _ = table(d.get("/reports/day", params={"from": day.isoformat()}))
    assert len(rows) == 1 and rows[0]["Тип занятости"] == "Ремонт" and rows[0]["Ставка"] == "450 ₽/ч" and rows[0]["Ремонт, ₽"] == 900


def test_day_report_rights_and_entities(world, make_client):
    prepare(world, make_client)
    # без права на отчёты нет доступа
    assert make_client("drv1").get("/reports/day").status_code == 403
    # директор другого юрлица чужих данных не видит
    _, rows, _ = table(make_client("dir_b").get("/reports/day", params={"from": TODAY.isoformat()}))
    assert rows == []
    # диспетчер и бухгалтер видят отчёт с часами
    for login in ("disp_a", "acc_a"):
        r = make_client(login).get("/reports/day", params={"from": TODAY.isoformat()})
        assert r.status_code == 200, (login, r.text)
        assert any(x["Водитель"] == "Первый Т." for x in table(r)[1]), login
    # неверная дата
    r = make_client("dir_a").get("/reports/day", params={"from": "вчера"})
    assert r.status_code == 422 and r.json()["field"] == "from"
    # по умолчанию — сегодня
    assert make_client("dir_a").get("/reports/day").json()["period"]["from"] == TODAY.isoformat()
    # отчёт есть в общем списке
    names = [x["name"] for x in make_client("dir_a").get("/reports").json()["items"]]
    assert "day" in names


def test_day_report_export(world, make_client):
    d = prepare(world, make_client)
    r = d.get("/reports/day/export.csv", params={"from": TODAY.isoformat()})
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert "Водитель;Техника;Путевой лист;Спидометр" in text and "Первый Т." in text and "Итого" in text
    r = d.get("/reports/day/export.xlsx", params={"from": TODAY.isoformat()})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert TODAY.isoformat() in r.headers["content-disposition"]


# ---------------------------------------------------------------- ручной выбор типа занятости

def day_kind(c, world, kind, **extra):
    return c.post("/payroll/day-kind", {"employee_id": world.d1, "work_date": TODAY.isoformat(), "kind": kind, **extra})


def first_row(c):
    _, rows, _ = table(c.get("/reports/day", params={"from": TODAY.isoformat()}))
    return next(x for x in rows if x["Водитель"] == "Первый Т.")


def test_day_report_marks_editable_rows(world, make_client):
    prepare(world, make_client)
    r = make_client("dir_a").get("/reports/day", params={"from": TODAY.isoformat()})
    names = [row[0] for row in r.json()["table"]["rows"]]
    meta = r.json()["day_rows"]
    assert len(meta) == len(names) and names[-1] == "Без водителя"
    assert meta[0] == {"employee_id": world.d1, "can_edit": True, "kind": "both"}
    assert meta[-1] == {"employee_id": None, "can_edit": False, "kind": ""}
    # бухгалтер может менять часы, значит и тип: доступ к правке часов даёт отдельное право, у водителя его нет
    assert make_client("acc_a").get("/reports/day", params={"from": TODAY.isoformat()}).json()["day_rows"][0]["can_edit"] is True


def test_day_kind_preview_changes_nothing(world, make_client):
    d = prepare(world, make_client)
    r = day_kind(d, world, "work", dry_run=True)
    assert r.status_code == 200, r.text
    assert r.json() == {"changed": 1, "hours": 2.0, "before": 4200.0, "after": 4500.0, "no_rate": False, "need_description": False, "rate_text": "450 ₽/час", "kept_manual": 0}
    a = first_row(d)
    assert (a["Тип занятости"], a["Часы работы"], a["Часы ремонта"], a["Всего, ₽"]) == ("Работа и ремонт", 8, 2, 4200)


def test_day_kind_moves_hours_and_recalculates(world, make_client):
    d = prepare(world, make_client)
    # в работу: ремонтные часы переходят в работу по ставке работы
    r = day_kind(d, world, "work")
    assert r.status_code == 200 and r.json()["changed"] == 1 and r.json()["after"] == 4500, r.text
    a = first_row(d)
    assert (a["Тип занятости"], a["Часы работы"], a["Часы ремонта"], a["Работа, ₽"], a["Всего, ₽"]) == ("Работа", 10, 0, 4500, 4500)
    # повтор ничего не меняет
    assert day_kind(d, world, "work").json()["changed"] == 0
    # в ремонт: у записи работы нет описания, нужно написать, что делали
    pre = day_kind(d, world, "repair", dry_run=True).json()
    assert pre["need_description"] is True and pre["before"] == 4500 and pre["after"] == 3000
    bad = day_kind(d, world, "repair")
    assert bad.status_code == 422 and bad.json()["field"] == "description"
    assert first_row(d)["Тип занятости"] == "Работа"          # после отказа ничего не изменилось
    assert day_kind(d, world, "repair", description="Ремонт по заявке").status_code == 200
    a = first_row(d)
    assert (a["Тип занятости"], a["Часы ремонта"], a["Ремонт, ₽"], a["Всего, ₽"]) == ("Ремонт", 10, 3000, 3000)
    with session_factory()() as db:
        logs = list(db.scalars(select(AuditLog).where(AuditLog.action == "worklog.day_kind")))
        assert len(logs) == 2 + 1 and {l.details["kind_to"] for l in logs} == {"work", "repair"}


def test_day_kind_tells_the_driver(world, make_client):
    d = prepare(world, make_client)
    drv = make_client("drv1")
    assert drv.get("/notifications/summary").json()["unread"] == 0
    assert day_kind(d, world, "work", dry_run=True).status_code == 200
    assert drv.get("/notifications/summary").json()["unread"] == 0            # предпросмотр водителю не приходит
    assert day_kind(d, world, "work").status_code == 200
    items = drv.get("/notifications").json()["items"]
    assert len(items) == 1 and items[0]["url"] == "/my-time"
    assert TODAY.strftime("%d.%m.%Y") in items[0]["title"] and "работа" in items[0]["title"]
    assert "4 200,00 ₽ → 4 500,00 ₽" in items[0]["text"]
    # в «Моих часах» помечена только та запись, которую перевели
    mine = {i["kind_label"] + str(i["hours"]): i for i in drv.get("/payroll/mine").json()["items"] if i["work_date"] == TODAY.isoformat()}
    assert len(mine) == 2 and sorted(i["office_changed"] for i in mine.values()) == [False, True]
    assert mine[[k for k in mine if k.endswith("2.0")][0]]["kind"] == "work" and mine[[k for k in mine if k.endswith("2.0")][0]]["office_changed"] is True
    # повторная смена ничего не перевела, значит и нового уведомления нет
    assert day_kind(d, world, "work").json()["changed"] == 0
    assert len(drv.get("/notifications").json()["items"]) == 1


def test_day_kind_without_tariff_and_rights(world, make_client):
    d = prepare(world, make_client)
    # у сотрудника убрали тариф ремонта: часы переходят, сумма 0, об этом сказано
    work2 = mk_tariff(d, "Работа новая", "work", "hour", 450)
    assert d.put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": work2, "repair_tariff_id": None}).status_code == 200
    pre = day_kind(d, world, "repair", dry_run=True, description="Ремонт").json()
    assert pre["no_rate"] is True and pre["before"] == 4200 and pre["after"] == 600 and pre["rate_text"] == "нет тарифа"
    assert day_kind(d, world, "repair", description="Ремонт").status_code == 200
    assert first_row(d)["Ремонт, ₽"] == 600                  # у новой записи ремонта ставки нет, старая запись ремонта сохранила свою
    # права и ошибки
    assert day_kind(make_client("drv1"), world, "work").status_code == 403
    assert day_kind(make_client("dir_b"), world, "work").status_code == 403
    assert day_kind(d, world, "сон").json()["field"] == "kind"
    assert d.post("/payroll/day-kind", {"employee_id": world.d1, "work_date": (TODAY - dt.timedelta(days=9)).isoformat(), "kind": "work"}).status_code == 404
    assert d.post("/payroll/day-kind", {"employee_id": 999999, "work_date": TODAY.isoformat(), "kind": "work"}).status_code == 404
