# -*- coding: utf-8 -*-
"""Зарплатный табель: тарифы оплаты, часы работы и ремонта, сумма за день и месяц, права, снимок ставки, отмена."""
import datetime as dt

from sqlalchemy import select

from app.common import org_today
from app.db import session_factory
from app.models import AuditLog, Vehicle, WorkLog

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


def enter(c, eid, **kw):
    """Механик вносит сотруднику часы (страница «Внести часы»): денег в ответе нет."""
    body = {"employee_id": eid, "work_date": day(), "kind": "work", "hours": "8"}
    body.update(kw)
    return c.post("/payroll/enter", body)


def my_items(c):
    return c.get("/payroll/mine").json()["items"]


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


# ---------------------------------------------------------------- механик вносит часы, водитель только смотрит

def test_mechanic_enters_hours_driver_sees_pay(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 450)
    rep = mk_tariff(d, "Ремонт", "repair", "hour", 300)
    assign(d, world.d1, work=work, repair=rep)
    mech, drv = make_client("mech_a"), make_client("drv1")
    r = enter(mech, world.d1, hours="8", vehicle_id=world.va1)
    assert r.status_code == 201, r.text
    it = r.json()["item"]
    assert it["kind"] == "work" and it["hours"] == 8 and it["employee"] and it["can_delete"] is True
    assert not ({"amount", "rate_text", "no_rate"} & set(it))              # механик денег не видит
    r = enter(mech, world.d1, kind="repair", hours="2,5", description="Менял гидравлический шланг")
    assert r.status_code == 201, r.text
    page = drv.get("/payroll/mine").json()
    assert page["totals"] == {"work_h": 8, "repair_h": 2.5, "work": 3600, "repair": 750, "total": 4350}
    assert page["tariffs"]["work"]["name"] == "Работа" and page["tariffs"]["repair"]["rate"] == 300
    assert {x["source"] for x in page["items"]} == {"mechanic"}
    assert not ({"vehicles", "can_create", "min_date", "delete_hours"} & set(page))      # вносить водителю нечего
    assert not any("can_delete" in x for x in page["items"])
    # запись видна в общем разделе офиса с пометкой «Внёс механик»
    row = make_client("dir_a").get("/worklog").json()["items"]
    assert {x["src"] for x in row} == {"Внёс механик"} and sorted(float(x["amount"]) for x in row) == [750, 3600]


def test_driver_cannot_enter_hours(world, make_client):
    drv = make_client("drv1")
    d = make_client("dir_a")
    assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 100))
    # страница механика и ввод закрыты, старый адрес «отметить свои часы» убран, общий раздел недоступен
    assert drv.get("/payroll/enter").status_code == 403
    assert enter(drv, world.d1).status_code == 403
    assert enter(drv, world.d1, work_date=day(1)).status_code == 403
    assert drv.post("/payroll/mine", {"work_date": day(), "kind": "work", "hours": "8"}).status_code == 405
    assert drv.post("/payroll/mine/1/delete").status_code == 404
    assert drv.post("/worklog", {"legal_entity_id": world.A, "work_date": day(), "employee_id": world.d1, "kind": "work", "hours": "8"}).status_code == 403
    assert my_items(drv) == []
    # право «Отмечать свои часы» из программы убрано совсем
    with session_factory()() as db:
        from app.models import Permission
        assert db.scalar(select(Permission).where(Permission.code == "worklog.create_own")) is None
    # свои часы и сумму водитель по-прежнему видит
    assert drv.get("/payroll/mine").status_code == 200


def test_mechanic_entry_rules(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 450), repair=mk_tariff(d, "Ремонт", "repair", "hour", 300))
    mech = make_client("mech_a")
    # ремонт без описания, нулевые и дробные часы, будущее, слишком старое, лишнее за сутки
    assert "description" in fields(enter(mech, world.d1, kind="repair"))
    for bad in ("0", "-1", "abc", "25", "2,55", "nan", ""):
        r = enter(mech, world.d1, hours=bad)
        assert r.status_code == 422 and "hours" in fields(r), bad
    assert "work_date" in fields(enter(mech, world.d1, work_date=(TODAY + dt.timedelta(days=1)).isoformat()))
    assert "work_date" in fields(enter(mech, world.d1, work_date=day(30)))
    assert enter(mech, world.d1, hours="10").status_code == 201
    r = enter(mech, world.d1, hours="15")
    assert r.status_code == 422 and "hours" in fields(r)
    assert enter(mech, world.d1, kind="repair", hours="14", description="Замена ремня").status_code == 201
    # техника своего юрлица выбирается, чужого юрлица нет
    assert enter(mech, world.d1, work_date=day(1), vehicle_id=world.va2).status_code == 201
    assert enter(mech, world.d1, work_date=day(1), vehicle_id=world.vb1).status_code == 422
    # неизвестный вид и неизвестный сотрудник
    assert enter(mech, world.d1, work_date=day(1), kind="rest").status_code == 422
    assert enter(mech, 999999).json()["field"] == "employee_id"


def test_mechanic_without_tariff_writes_hours_then_rate_appears(world, make_client):
    mech, drv = make_client("mech_a"), make_client("drv1")
    r = enter(mech, world.d1, hours="6")
    assert r.status_code == 201 and r.json()["warnings"]
    it = my_items(drv)[0]
    assert it["amount"] == 0 and it["no_rate"] is True
    d = make_client("dir_a")
    res = assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 500))
    assert res["recalculated"] == 1
    assert drv.get("/payroll/mine").json()["totals"]["work"] == 3000


def test_rate_is_snapshotted_old_entries_do_not_change(world, make_client):
    d = make_client("dir_a")
    work = mk_tariff(d, "Работа", "work", "hour", 400)
    assign(d, world.d1, work=work)
    mech, drv = make_client("mech_a"), make_client("drv1")
    assert enter(mech, world.d1, work_date=day(2), hours="5").status_code == 201
    # тариф подорожал: прошлый день тот же, новый день по новой ставке
    assert d.patch(f"/tariffs/{work}", {"rate": "600"}).status_code == 200
    assert enter(mech, world.d1, work_date=day(1), hours="5").status_code == 201
    items = {x["work_date"]: x["amount"] for x in my_items(drv) if x["work_date"] in (day(1), day(2))}
    assert items == {day(2): 2000, day(1): 3000}


def test_day_tariff_pays_once_per_day_and_kind(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, repair=mk_tariff(d, "Ремонт, сутки", "repair", "day", 2500))
    mech, drv = make_client("mech_a"), make_client("drv1")
    a = enter(mech, world.d1, kind="repair", hours="3", description="Диагностика").json()["item"]
    enter(mech, world.d1, kind="repair", hours="4", description="Замена колодок")
    assert sorted(x["amount"] for x in my_items(drv)) == [0, 2500]
    assert drv.get("/payroll/mine").json()["totals"]["repair"] == 2500
    # убрали первую запись: сутки засчитываются второй
    assert mech.post(f"/payroll/enter/{a['id']}/delete").status_code == 200
    assert drv.get("/payroll/mine").json()["totals"]["repair"] == 2500
    items = my_items(drv)
    assert len(items) == 1 and items[0]["amount"] == 2500


def test_mechanic_can_remove_only_own_fresh_entry(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 100))
    mech, disp = make_client("mech_a"), make_client("disp_a")
    it = enter(mech, world.d1).json()["item"]
    assert disp.post(f"/payroll/enter/{it['id']}/delete").status_code == 404           # чужую запись убирать нельзя
    with session_factory()() as db:                                                    # запись старше двух суток
        l = db.get(WorkLog, it["id"])
        l.created_at = l.created_at - dt.timedelta(hours=60)
        db.commit()
    assert mech.post(f"/payroll/enter/{it['id']}/delete").status_code == 403
    assert [x["can_delete"] for x in mech.get("/payroll/enter").json()["recent"]] == [False]
    it2 = enter(mech, world.d1, work_date=day(1)).json()["item"]
    assert mech.post(f"/payroll/enter/{it2['id']}/delete").status_code == 200
    assert all(x["id"] != it2["id"] for x in mech.get("/payroll/enter").json()["recent"])
    assert any(a.action == "worklog.mechanic_void" for a in _audit())
    # запись, внесённую в офисе, механик убрать не может
    r = office_post(d, world, work_date=day(2))
    assert r.status_code == 201, r.text
    assert mech.post(f"/payroll/enter/{r.json()['id']}/delete").status_code == 404


def test_repeat_send_does_not_duplicate(world, make_client):
    mech, drv = make_client("mech_a"), make_client("drv1")
    a = enter(mech, world.d1, client_id="abc-1")
    b = enter(mech, world.d1, client_id="abc-1")
    assert a.status_code == 201 and b.status_code == 201 and b.json()["duplicate"] is True
    assert len(my_items(drv)) == 1


def test_mechanic_does_not_see_money(world, make_client):
    d = make_client("dir_a")
    assign(d, world.d1, work=mk_tariff(d, "Работа", "work", "hour", 450))
    mech = make_client("mech_a")
    assert enter(mech, world.d1).status_code == 201
    # общий раздел часов, табель, отчёт «Зарплата» и сумма в карточке сотрудника для механика закрыты
    assert mech.get("/worklog").status_code == 403
    assert mech.get("/timesheet").status_code == 403
    assert mech.get("/reports/salary", params={"month": TODAY.strftime("%Y-%m")}).status_code in (403, 404)
    card = mech.get(f"/employees/{world.d1}").json()
    assert "earned" not in (card.get("pay") or {}) and "work_tariff_id" not in (card.get("pay") or {})
    # и сам список для ввода денег не содержит
    page = mech.get("/payroll/enter").json()
    assert "amount" not in str(page) and "rate" not in str(page)


def test_enter_form_lists_only_own_entity(world, make_client):
    mech = make_client("mech_a")
    page = mech.get("/payroll/enter").json()
    ids = {x["id"] for x in page["people"]}
    assert world.d1 in ids and world.d2 in ids and world.d3 not in ids                     # d3 из юрлица «Бета»
    assert {x["id"] for x in page["vehicles"]} == {world.va1, world.va2, world.va3}
    assert page["today"] == day() and page["min_date"] == day(7)
    # сотрудник и техника другого юрлица не вносятся
    assert enter(mech, world.d3).json()["field"] == "employee_id"
    assert enter(mech, world.d1, vehicle_id=world.vb1).status_code == 422
    # архивная техника из списка уходит
    with session_factory()() as db:
        db.get(Vehicle, world.va3).archived_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    assert {x["id"] for x in mech.get("/payroll/enter").json()["vehicles"]} == {world.va1, world.va2}
    # в списке «последние» только то, что внёс этот механик
    enter(mech, world.d1)
    assert len(mech.get("/payroll/enter").json()["recent"]) == 1
    assert make_client("dir_b").get("/worklog").json()["total"] == 0


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


# ---------------------------------------------------------------- тариф выбирается вручную при внесении часов

def office_post(c, world, **kw):
    body = {"legal_entity_id": world.A, "work_date": day(3), "employee_id": world.d1, "kind": "work", "hours": "8"}
    body.update(kw)
    return c.post("/worklog", body)


def test_office_picks_tariff_by_hand_and_money_is_counted(world, make_client):
    d = make_client("dir_a")
    ekc = mk_tariff(d, "Экскаватор, час", "work", "hour", 1000)
    mk_tariff(d, "Заказчик: самосвал", "customer", "hour", 3000)
    # в карточке сотрудника тарифа нет, но офис выбирает тариф прямо в записи
    disp = make_client("disp_a")
    r = office_post(disp, world, tariff_id=ekc, hours="7,5")
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["amount"] == 7500 and j["tariff_id"] == ekc and j["tariff_id_label"] == "Экскаватор, час" and j["rate_text"].startswith("1 000")
    with session_factory()() as db:
        l = db.get(WorkLog, j["id"])
        assert l.tariff_manual is True and l.rate == 1000 and l.tariff_name == "Экскаватор, час"
    # без тарифа и без тарифа в карточке офис запись не сохраняет: сумма не должна тихо стать нулём
    r = office_post(disp, world, work_date=day(4))
    assert r.status_code == 422 and "tariff_id" in fields(r)
    # любой действующий тариф за час или за сутки подходит, в том числе «для заказчика»; за километр нельзя
    cust = mk_tariff(d, "Заказчик: самосвал, час", "customer", "hour", 2000)
    assert office_post(disp, world, work_date=day(5), tariff_id=cust).json()["amount"] == 16000
    km = mk_tariff(d, "За километр", "customer", "km", 40)
    r = office_post(disp, world, work_date=day(6), tariff_id=km)
    assert r.status_code == 422 and "tariff_id" in fields(r)
    # несуществующий тариф
    assert office_post(disp, world, work_date=day(6), tariff_id=999999).status_code == 422


def test_pay_tariff_options_list_and_rights(world, make_client):
    d = make_client("dir_a")
    w = mk_tariff(d, "Работа, час", "work", "hour", 400)
    r_ = mk_tariff(d, "Ремонт, сутки", "repair", "day", 2500)
    c = mk_tariff(d, "Для заказчика, час", "customer", "hour", 2000)
    mk_tariff(d, "За километр", "customer", "km", 40)
    items = make_client("disp_a").get("/options/pay_tariff").json()["items"]
    assert [x["id"] for x in items] == [w, r_, c]                         # сначала оплата работы и ремонта, потом остальные
    assert [x["group"] for x in items] == ["Оплата работы", "Оплата ремонта", "Другие тарифы"]
    assert items[0]["label"] == "Работа, час — 400 ₽/час" and items[1]["label"].endswith("2 500 ₽/день")
    # архивный тариф в списке не показывается
    assert d.post(f"/tariffs/{c}/void", {"reason": "Устарел"}).status_code == 200
    assert c not in [x["id"] for x in d.get("/options/pay_tariff").json()["items"]]
    # водителю и тем, кто часы не вносит, список закрыт
    assert make_client("drv1").get("/options/pay_tariff").status_code == 403
    assert make_client("mech_a").get("/options/pay_tariff").status_code == 403


def test_changing_tariff_on_edit_and_old_rate_stays(world, make_client):
    d = make_client("dir_a")
    cheap = mk_tariff(d, "Дешёвый, час", "work", "hour", 300)
    dear = mk_tariff(d, "Дорогой, час", "work", "hour", 900)
    disp = make_client("disp_a")
    wid = office_post(disp, world, tariff_id=cheap).json()["id"]
    # правка часов не трогает ставку, даже если тариф подорожал
    assert d.patch(f"/tariffs/{cheap}", {"rate": "350"}).status_code == 200
    r = disp.patch(f"/worklog/{wid}", {"hours": "10", "tariff_id": cheap})
    assert r.status_code == 200 and r.json()["amount"] == 3000
    # смена тарифа даёт новую ставку
    r = disp.patch(f"/worklog/{wid}", {"tariff_id": dear})
    assert r.status_code == 200 and r.json()["amount"] == 9000 and r.json()["tariff_id_label"] == "Дорогой, час"
    # убрали тариф и в карточке его нет: нельзя
    r = disp.patch(f"/worklog/{wid}", {"tariff_id": ""})
    assert r.status_code == 422 and "tariff_id" in fields(r)
    # назначили тариф в карточку: теперь «пусто» значит «из карточки»
    assign(d, world.d1, work=cheap)
    r = disp.patch(f"/worklog/{wid}", {"tariff_id": ""})
    assert r.status_code == 200 and r.json()["tariff_id_label"] == "Дешёвый, час" and r.json()["amount"] == 3500


def test_preview_shows_entry_and_day_total(world, make_client):
    d = make_client("dir_a")
    hour = mk_tariff(d, "Час", "work", "hour", 500)
    sut = mk_tariff(d, "Сутки", "repair", "day", 2500)
    disp = make_client("disp_a")
    office_post(disp, world, tariff_id=hour, hours="3")
    body = {"employee_id": world.d1, "work_date": day(3), "kind": "work", "hours": "5", "tariff_id": hour}
    j = disp.post("/payroll/preview", body).json()
    assert j["ok"] and j["amount"] == 2500 and j["day_other"] == 1500 and j["day_total"] == 4000 and j["day_hours_other"] == 3
    assert j["rate_text"] == "500 ₽/час" and j["source"] == "chosen"
    # суточный тариф: вторая запись того же вида за день денег не добавляет
    office_post(disp, world, kind="repair", description="Ремонт моста", tariff_id=sut, hours="2")
    j = disp.post("/payroll/preview", {**body, "kind": "repair", "tariff_id": sut}).json()
    assert j["amount"] == 0 and j["day_total"] == 1500 + 2500
    # тариф не выбран: берётся из карточки; нет и там: сумма 0 и подсказка
    j = disp.post("/payroll/preview", {**body, "tariff_id": None}).json()
    assert j["source"] == "none" and j["amount"] == 0 and j["rate_text"] == "нет тарифа"
    assign(d, world.d1, work=hour)
    j = disp.post("/payroll/preview", {**body, "tariff_id": None}).json()
    assert j["source"] == "card" and j["amount"] == 2500
    # пока часы не введены, суммы нет
    assert disp.post("/payroll/preview", {**body, "hours": ""}).json()["amount"] is None
    # предварительный расчёт ничего не сохраняет
    assert disp.get("/worklog").json()["total"] == 2
    # редактируя запись, она не считается дважды
    wid = disp.get("/worklog").json()["items"][-1]["id"]
    j = disp.post("/payroll/preview", {**body, "exclude_id": wid, "hours": "1"}).json()
    assert j["ok"]
    # права: водитель считать за других не может; чужое юрлицо закрыто
    assert make_client("drv1").post("/payroll/preview", body).status_code == 403
    assert make_client("dir_b").post("/payroll/preview", body).status_code == 403


def test_day_kind_keeps_hand_picked_tariff(world, make_client):
    d = make_client("dir_a")
    ekc = mk_tariff(d, "Экскаватор, час", "work", "hour", 1000)
    rep = mk_tariff(d, "Ремонт, час", "repair", "hour", 300)
    assign(d, world.d1, repair=rep)
    disp = make_client("disp_a")
    wid = office_post(disp, world, tariff_id=ekc, hours="4").json()["id"]
    r = disp.post("/payroll/day-kind", {"employee_id": world.d1, "work_date": day(3), "kind": "repair", "description": "Ремонт ковша"})
    assert r.status_code == 200 and r.json()["kept_manual"] == 1 and r.json()["after"] == 4000
    row = disp.get("/worklog").json()["items"][0]
    assert row["id"] == wid and row["kind"] == "repair" and row["amount"] == 4000


# ---------------------------------------------------------------- отчёт «Зарплата за месяц»

def salary(c, month=None, **params):
    return c.get("/reports/salary", params={"month": month or TODAY.strftime("%Y-%m"), **params})


def test_salary_report_totals_by_employee_tariff_and_vehicle(world, make_client):
    d = make_client("dir_a")
    ekc = mk_tariff(d, "Экскаватор, час", "work", "hour", 1000)
    rep = mk_tariff(d, "Ремонт, сутки", "repair", "day", 2500)
    month = TODAY.strftime("%Y-%m")
    days = [x for x in (TODAY, TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=2)) if x.strftime("%Y-%m") == month]
    assert len(days) >= 1
    disp = make_client("disp_a")
    office_post(disp, world, work_date=days[0].isoformat(), tariff_id=ekc, hours="8", vehicle_id=world.va1)
    office_post(disp, world, work_date=days[0].isoformat(), kind="repair", description="Мост", tariff_id=rep, hours="2")
    office_post(disp, world, work_date=days[0].isoformat(), kind="repair", description="Ещё мост", tariff_id=rep, hours="1")   # сутки платят один раз
    if len(days) > 1:
        office_post(disp, world, work_date=days[1].isoformat(), tariff_id=ekc, hours="4,5", vehicle_id=world.va1)
    # запись другого месяца в отчёт не попадает
    first = TODAY.replace(day=1)
    prev = first - dt.timedelta(days=1)
    office_post(disp, world, work_date=prev.isoformat(), tariff_id=ekc, hours="3")
    r = salary(d)
    assert r.status_code == 200, r.text
    j = r.json()
    cols = [c["label"] for c in j["table"]["columns"]]
    row = dict(zip(cols, j["table"]["rows"][0]))
    work_h = 8 + (4.5 if len(days) > 1 else 0)
    assert len(j["table"]["rows"]) == 1
    assert row["Часы работы"] == work_h and row["Часы ремонта"] == 3 and row["Часов всего"] == work_h + 3
    assert row["Работа, ₽"] == work_h * 1000 and row["Ремонт, ₽"] == 2500 and row["К выплате, ₽"] == work_h * 1000 + 2500
    assert row["Дней с часами"] == len(days[:2])
    totals = dict(zip(cols, j["table"]["totals"]))
    assert totals["К выплате, ₽"] == row["К выплате, ₽"]
    tar = {x[0]: x for x in next(e for e in j["extras"] if e["title"] == "По тарифам")["rows"]}
    assert tar["Экскаватор, час"][2] == work_h and tar["Ремонт, сутки"][3] == 2500
    veh = next(e for e in j["extras"] if e["title"] == "По технике")["rows"]
    assert any(x[0] == "Без техники" for x in veh) and any(x[1] == work_h and x[2] == work_h * 1000 for x in veh if x[0] != "Без техники")
    assert "Зарплата за" in j["title"] and "Внимание" not in j["note"]
    # прошлый месяц считается отдельно
    pj = salary(d, prev.strftime("%Y-%m")).json()
    assert dict(zip(cols, pj["table"]["rows"][0]))["К выплате, ₽"] == 3000


def test_salary_report_warns_about_entries_without_tariff_and_rights(world, make_client):
    drv = make_client("drv1")
    assert enter(make_client("mech_a"), world.d1, hours="6").status_code == 201          # механик внёс часы, тарифа нет
    j = salary(make_client("dir_a")).json()
    assert "Внимание: у 1 записей нет тарифа" in j["note"]
    assert dict(zip([c["label"] for c in j["table"]["columns"]], j["table"]["rows"][0]))["К выплате, ₽"] == 0
    # механик, водитель и чужое юрлицо отчёта не видят
    assert salary(make_client("mech_a")).status_code == 404
    assert salary(drv).status_code in (403, 404)
    assert salary(make_client("dir_b"), legal_entity_id=world.A).status_code == 403
    # выгрузка в Excel работает
    x = make_client("dir_a").get("/reports/salary/export.xlsx", params={"month": TODAY.strftime("%Y-%m")})
    assert x.status_code == 200 and x.content[:2] == b"PK"
