# -*- coding: utf-8 -*-
"""Зарплатный табель: тарифы оплаты, часы работы и ремонта, сумма за день и месяц, права, снимок ставки, отмена."""
import datetime as dt

from sqlalchemy import select

from app.common import org_today
from app.db import session_factory
from app.models import AuditLog, WorkLog

TODAY = org_today()


def fields(r):
    j = r.json()
    return (j["detail"] if "detail" in j else j)["fields"]


def day(n=0):
    return (TODAY - dt.timedelta(days=n)).isoformat()


def mk_tariff(c, name, kind, unit, rate):
    r = c.post("/tariffs", {"name": name, "kind": kind, "unit": unit, "rate": str(rate)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def assign(c, eid, work=None, repair=None):
    r = c.put(f"/payroll/employees/{eid}/tariffs", {"work_tariff_id": work, "repair_tariff_id": repair})
    assert r.status_code == 200, r.text
    return r.json()


def mine_post(c, **kw):
    body = {"work_date": day(), "kind": "work", "hours": "8"}
    body.update(kw)
    return c.post("/payroll/mine", body)


def sheet_row(c, entity, eid, month=None):
    month = month or TODAY.strftime("%Y-%m")
    data = c.get("/timesheet", params={"month": month, "legal_entity_id": entity}).json()
    return data, next((r for r in data["rows"] if r["employee_id"] == eid), None)


# ---------------------------------------------------------------- тарифы

def test_pay_tariffs_rules(world, make_client):
    adm = make_client("dir_a")
    wid = mk_tariff(adm, "Водитель: работа, час", "work", "hour", 450)
    mk_tariff(adm, "Заказчик: самосвал, час", "customer", "hour", 2000)
    # оплату считают только за час или за сутки
    r = adm.post("/tariffs", {"name": "Не так", "kind": "work", "unit": "km", "rate": "10"})
    assert r.status_code == 422 and "unit" in fields(r)
    # вид после добавления не меняется
    r = adm.patch(f"/tariffs/{wid}", {"kind": "repair"})
    assert r.status_code == 422
    # в списке для заказов только тарифы заказчика
    labels = [x["label"] for x in adm.get("/options/tariff").json()["items"]]
    assert labels == ["Заказчик: самосвал, час"]
    # минимальная сумма у тарифа оплаты не хранится
    row = adm.get(f"/tariffs").json()["items"]
    assert all(float(x["min_amount"]) == 0 for x in row if x["kind"] == "work")
    # назначенный сотрудникам тариф в архив не уходит
    assign(adm, world.d1, work=wid)
    r = adm.post(f"/tariffs/{wid}/void", {"reason": "Устарел"})
    assert r.status_code == 409, r.text


def test_assign_tariffs_checks_kind_and_rights(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 400)
    rep = mk_tariff(d, "Ремонт", "repair", "hour", 300)
    cust = mk_tariff(d, "Заказчик", "customer", "hour", 1000)
    # тариф не того вида нельзя
    assert d.put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": rep}).status_code == 422
    assert d.put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": cust}).status_code == 422
    assert assign(d, world.d1, work=work, repair=rep)["work_tariff_id"] == work
    # чужое юрлицо выглядит как «не найдено»
    assert d.put(f"/payroll/employees/{world.d3}/tariffs", {"work_tariff_id": work}).status_code == 404
    # диспетчер и водитель тарифы не назначают
    for login in ("disp_a", "drv1"):
        assert make_client(login).put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": work}).status_code == 403
    # в карточке сотрудника видно, какие тарифы назначены
    card = d.get(f"/employees/{world.d1}").json()
    assert card["pay"]["work_tariff_id"] == work and card["pay"]["repair_tariff_id"] == rep and card["pay"]["can_edit"]
    assert any(a.action == "employee.pay_tariffs" for a in _audit())


def _audit():
    with session_factory()() as db:
        return list(db.scalars(select(AuditLog)))


# ---------------------------------------------------------------- водитель отмечает часы

def test_driver_marks_hours_and_sees_pay(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 450)
    rep = mk_tariff(d, "Ремонт", "repair", "hour", 300)
    assign(d, world.d1, work=work, repair=rep)
    drv = make_client("drv1")
    r = mine_post(drv, hours="8", vehicle_id=world.va1)
    assert r.status_code == 201, r.text
    it = r.json()["item"]
    assert it["amount"] == 3600 and it["kind"] == "work" and it["source"] == "driver" and it["no_rate"] is False
    r = mine_post(drv, kind="repair", hours="2,5", description="Менял гидравлический шланг")
    assert r.status_code == 201, r.text
    assert r.json()["item"]["amount"] == 750
    page = drv.get("/payroll/mine").json()
    assert page["totals"] == {"work_h": 8, "repair_h": 2.5, "work": 3600, "repair": 750, "total": 4350}
    assert page["tariffs"]["work"]["name"] == "Работа" and page["tariffs"]["repair"]["rate"] == 300
    assert [v["id"] for v in page["vehicles"]] == [world.va1]
    assert page["can_create"] is True


def test_driver_entry_rules(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 450), repair=mk_tariff(d, "Ремонт", "repair", "hour", 300))
    drv = make_client("drv1")
    # ремонт без описания, нулевые и дробные часы, будущее, слишком старое, лишнее за сутки
    assert "description" in fields(mine_post(drv, kind="repair"))
    for bad in ("0", "-1", "abc", "25", "2,55", "nan", ""):
        r = mine_post(drv, hours=bad)
        assert r.status_code == 422 and "hours" in fields(r), bad
    assert "work_date" in fields(mine_post(drv, work_date=(TODAY + dt.timedelta(days=1)).isoformat()))
    assert "work_date" in fields(mine_post(drv, work_date=day(30)))
    assert mine_post(drv, hours="10").status_code == 201
    r = mine_post(drv, hours="15")
    assert r.status_code == 422 and "hours" in fields(r)
    assert mine_post(drv, kind="repair", hours="14", description="Замена ремня").status_code == 201
    # чужая техника не выбирается
    assert mine_post(drv, work_date=day(1), vehicle_id=world.va2).status_code == 422
    assert mine_post(drv, work_date=day(1), vehicle_id=world.vb1).status_code == 422
    # неизвестный вид
    assert mine_post(drv, work_date=day(1), kind="rest").status_code == 422


def test_driver_without_tariff_gets_zero_then_rate_appears(world, make_client):
    drv = make_client("drv1")
    r = mine_post(drv, hours="6")
    assert r.status_code == 201 and r.json()["item"]["amount"] == 0 and r.json()["item"]["no_rate"] is True
    assert r.json()["warnings"]
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 500)
    res = assign(d, world.d1, work=work)
    assert res["recalculated"] == 1
    assert drv.get("/payroll/mine").json()["totals"]["work"] == 3000


def test_rate_is_snapshotted_old_entries_do_not_change(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 400)
    assign(d, world.d1, work=work)
    drv = make_client("drv1")
    assert mine_post(drv, work_date=day(2), hours="5").json()["item"]["amount"] == 2000
    # тариф подорожал: прошлый день тот же, новый день по новой ставке
    assert d.patch(f"/tariffs/{work}", {"rate": "600"}).status_code == 200
    assert mine_post(drv, work_date=day(1), hours="5").json()["item"]["amount"] == 3000
    items = {x["work_date"]: x["amount"] for x in drv.get("/payroll/mine").json()["items"] if x["work_date"] in (day(1), day(2))}
    assert items == {day(2): 2000, day(1): 3000}


def test_day_tariff_pays_once_per_day_and_kind(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, repair=mk_tariff(d, "Ремонт, сутки", "repair", "day", 2500))
    drv = make_client("drv1")
    a = mine_post(drv, kind="repair", hours="3", description="Диагностика").json()["item"]
    b = mine_post(drv, kind="repair", hours="4", description="Замена колодок").json()["item"]
    assert a["amount"] == 2500 and b["amount"] == 0
    assert drv.get("/payroll/mine").json()["totals"]["repair"] == 2500
    # убрали первую запись: сутки засчитываются второй
    assert drv.post(f"/payroll/mine/{a['id']}/delete").status_code == 200
    assert drv.get("/payroll/mine").json()["totals"]["repair"] == 2500
    items = drv.get("/payroll/mine").json()["items"]
    assert len(items) == 1 and items[0]["amount"] == 2500


def test_driver_can_remove_only_own_fresh_entry(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 100))
    drv, other = make_client("drv1"), make_client("drv2")
    it = mine_post(drv).json()["item"]
    assert other.post(f"/payroll/mine/{it['id']}/delete").status_code == 404
    with session_factory()() as db:                              # запись старше двух суток
        l = db.get(WorkLog, it["id"])
        l.created_at = l.created_at - dt.timedelta(hours=60)
        db.commit()
    assert drv.post(f"/payroll/mine/{it['id']}/delete").status_code == 403
    it2 = mine_post(drv, work_date=day(1)).json()["item"]
    assert drv.post(f"/payroll/mine/{it2['id']}/delete").status_code == 200
    assert all(x["id"] != it2["id"] for x in drv.get("/payroll/mine").json()["items"])


def test_repeat_send_does_not_duplicate(world, make_client):
    drv = make_client("drv1")
    a = mine_post(drv, client_id="abc-1")
    b = mine_post(drv, client_id="abc-1")
    assert a.status_code == 201 and b.status_code == 201 and b.json()["duplicate"] is True
    assert len(drv.get("/payroll/mine").json()["items"]) == 1


# ---------------------------------------------------------------- офис: общий раздел и табель

def test_office_adds_edits_cancels_and_totals(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 400)
    rep = mk_tariff(d, "Ремонт", "repair", "hour", 350)
    assign(d, world.d1, work=work, repair=rep)
    disp = make_client("disp_a")
    r = disp.post("/worklog", {"legal_entity_id": world.A, "work_date": day(3), "employee_id": world.d1, "kind": "work", "hours": "8"})
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert r.json()["amount"] == 3200 and r.json()["src"] == "Внесли в офисе" and r.json()["rate_text"].startswith("400")
    r = disp.post("/worklog", {"legal_entity_id": world.A, "work_date": day(3), "employee_id": world.d1, "kind": "repair", "hours": "2", "description": "Масло и фильтры"})
    assert r.status_code == 201 and r.json()["amount"] == 700
    # ремонт без описания нельзя
    r = disp.post("/worklog", {"legal_entity_id": world.A, "work_date": day(4), "employee_id": world.d1, "kind": "repair", "hours": "2"})
    assert r.status_code == 422
    # сотрудник из другого юрлица
    r = disp.post("/worklog", {"legal_entity_id": world.A, "work_date": day(3), "employee_id": world.d3, "kind": "work", "hours": "1"})
    assert r.status_code == 422
    # правка часов пересчитывает сумму; дату, сотрудника и вид менять нельзя
    r = disp.patch(f"/worklog/{wid}", {"hours": "9"})
    assert r.status_code == 200 and r.json()["amount"] == 3600
    assert disp.patch(f"/worklog/{wid}", {"work_date": day(2)}).status_code == 422
    lst = disp.get("/worklog").json()
    assert lst["totals"]["amount"] == 3600 + 700 and lst["total"] == 2
    # отмена убирает запись из суммы, возврат возвращает
    assert disp.post(f"/worklog/{wid}/void", {"reason": "Ошибся в часах"}).status_code == 200
    assert disp.get("/worklog").json()["totals"]["amount"] == 700
    assert disp.post(f"/worklog/{wid}/restore").status_code == 200
    assert disp.get("/worklog").json()["totals"]["amount"] == 4300
    # водитель общий раздел не видит
    assert make_client("drv1").get("/worklog").status_code == 403


def test_timesheet_shows_daily_and_monthly_money(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 400), repair=mk_tariff(d, "Ремонт", "repair", "hour", 350))
    drv = make_client("drv1")
    month = TODAY.strftime("%Y-%m")
    dates = [x for x in (TODAY, TODAY - dt.timedelta(days=1)) if x.strftime("%Y-%m") == month]
    assert dates, "тест нужен хотя бы с одним днём текущего месяца"
    mine_post(drv, work_date=dates[0].isoformat(), hours="8")
    mine_post(drv, work_date=dates[0].isoformat(), kind="repair", hours="2", description="Замена шланга")
    data, row = sheet_row(d, world.A, world.d1)
    assert data["pay"]["enabled"] is True
    cell = row["pay"]["days"][str(dates[0].day)]
    assert cell == {"work_h": 8, "repair_h": 2, "work": 3200, "repair": 700, "total": 3900}
    assert row["pay"]["totals"]["total"] == 3900 and data["pay"]["totals"]["total"] == 3900 and data["pay"]["day_totals"][str(dates[0].day)] == 3900
    # у того, кто видит табель, но не часы, сумм нет
    assert make_client("mech_a").get("/timesheet", params={"month": month, "legal_entity_id": world.A}).status_code == 403
    csv = d.get("/timesheet/export.csv", params={"month": month, "legal_entity_id": world.A}).content.decode("utf-8-sig")
    assert "Итого, ₽" in csv and "3900,00" in csv
    # чужое юрлицо суммы не показывает
    other = make_client("dir_b")
    assert other.get("/timesheet", params={"month": month, "legal_entity_id": world.A}).status_code == 403


def test_entries_of_another_entity_stay_apart(world, make_client):
    d3 = make_client("drv3")
    r = mine_post(d3, vehicle_id=world.vb1)
    assert r.status_code == 201
    a = make_client("dir_a").get("/worklog").json()
    assert a["total"] == 0                                  # запись юрлица «Бета» директору «Альфы» не видна
    assert make_client("dir_b").get("/worklog").json()["total"] == 1
