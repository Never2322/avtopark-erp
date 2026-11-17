# -*- coding: utf-8 -*-
"""Водитель сам отмечает «на линии» или «в ремонте» у своей машины; сумма заработанного видна водителю на главной и директору в карточке."""
import datetime as dt

from sqlalchemy import delete, select

from app.common import org_today
from app.db import session_factory
from app.models import Notification, Permission, Role, RolePermission, User

TODAY = org_today()


def day(n=0):
    return (TODAY - dt.timedelta(days=n)).isoformat()


def inbox(login):
    with session_factory()() as db:
        uid = db.scalar(select(User.id).where(User.login == login))
        return list(db.scalars(select(Notification).where(Notification.user_id == uid)))


def history(c, vid):
    return c.get(f"/vehicles/{vid}/status-history").json()["items"]


# ---------------------------------------------------------------- статус

def test_driver_changes_status_of_own_vehicle_with_history(make_client, world):
    d = make_client("drv1")
    card = d.get(f"/vehicles/{world.va1}").json()
    assert card["actions"]["change_status"] is True and card["status"] == "in_work"
    assert d.post(f"/vehicles/{world.va1}/status", {"status": "in_repair"}).status_code == 422             # причина ремонта обязательна
    r = d.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Течёт масло"})
    assert r.status_code == 200 and r.json()["status"] == "in_repair" and r.json()["changed"] is True
    assert d.post(f"/vehicles/{world.va1}/status", {"status": "in_work"}).status_code == 200                 # обратно на линию без причины
    h = history(d, world.va1)
    assert [i["status"] for i in h][:2] == ["in_work", "in_repair"]
    repair = h[1]
    assert repair["reason"] == "Течёт масло" and repair["from_status"] == "in_work"
    assert repair["by"]                                                                                    # кто отметил, виден в истории
    # то же видит и офис
    assert make_client("dir_a").get(f"/vehicles/{world.va1}").json()["status"] == "in_work"
    assert d.post(f"/vehicles/{world.va1}/status", {"status": "flying"}).status_code == 422


def test_driver_cannot_touch_foreign_vehicle_status(make_client, world):
    d = make_client("drv1")
    for vid in (world.va2, world.va3):
        r = d.post(f"/vehicles/{vid}/status", {"status": "in_repair", "reason": "чужая"})
        assert r.status_code in (403, 404), (vid, r.status_code)
    assert make_client("dir_a").get(f"/vehicles/{world.va2}").json()["status"] == "in_work"
    # у чужой машины кнопки нет, и сама машина «не существует» для него
    assert d.get(f"/vehicles/{world.va2}").status_code == 404


def test_roles_without_the_right_cannot_change_status(make_client, world):
    for login in ("emp_a", "store_a", "acc_a"):
        r = make_client(login).post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "нельзя"})
        assert r.status_code in (403, 404), (login, r.status_code)
    assert make_client("dir_a").get(f"/vehicles/{world.va1}").json()["status"] == "in_work"


def test_driver_loses_button_when_right_is_taken_away(make_client, world):
    with session_factory()() as db:
        role = db.scalar(select(Role).where(Role.code == "driver"))
        perm = db.scalar(select(Permission).where(Permission.code == "vehicle_status.change_own"))
        db.execute(delete(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == perm.id))
        db.commit()
    d = make_client("drv1")
    assert d.get(f"/vehicles/{world.va1}").json()["actions"]["change_status"] is False
    assert d.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "нельзя"}).status_code == 403
    assert d.get("/driver/home").json()["can"]["status"] is False


def test_home_says_driver_can_change_status(make_client, world):
    home = make_client("drv1").get("/driver/home").json()
    assert home["can"]["status"] is True
    assert home["vehicles"][0]["status"] == "in_work"


def test_office_is_told_when_driver_marks_repair_but_not_the_other_way(make_client, world):
    d = make_client("drv1")
    d.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Не заводится"})
    for login in ("mech_a", "disp_a", "dir_a"):
        titles = [n.title for n in inbox(login)]
        assert any("водитель отметил" in t and "в ремонте" in t for t in titles), (login, titles)
        n = [n for n in inbox(login) if "водитель отметил" in n.title][0]
        assert "Не заводится" in n.text and n.url == f"/vehicles/{world.va1}"
    assert [n.title for n in inbox("drv1") if "водитель отметил" in n.title] == []                          # самому водителю не нужно
    assert [n.title for n in inbox("drv2") if "водитель отметил" in n.title] == []                          # соседу тоже
    # офис сам поменял статус: никому не шумим
    before = len(inbox("mech_a"))
    make_client("mech_a").post(f"/vehicles/{world.va1}/status", {"status": "in_work"})
    assert len(inbox("mech_a")) == before and len(inbox("dir_a")) == 1
    # повтор того же статуса ничего не создаёт
    d.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Не заводится"})
    d.post(f"/vehicles/{world.va1}/status", {"status": "in_repair", "reason": "Не заводится"})
    assert len([n for n in inbox("dir_a") if "водитель отметил" in n.title]) == 2


# ---------------------------------------------------------------- заработанное

def mk_tariff(c, name, kind, unit, rate):
    r = c.post("/tariffs", {"name": name, "kind": kind, "unit": unit, "rate": str(rate)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def office_hours(c, world, eid, kind, hours, n=0, **kw):
    body = {"legal_entity_id": world.A, "work_date": day(n), "employee_id": eid, "kind": kind, "hours": str(hours)}
    body.update(kw)
    r = c.post("/worklog", body)
    assert r.status_code == 201, r.text
    return r.json()


def test_driver_sees_earned_after_office_enters_hours(make_client, world):
    boss, disp, drv = make_client("dir_a"), make_client("disp_a"), make_client("drv1")
    work = mk_tariff(boss, "Работа", "work", "hour", 450)
    rep = mk_tariff(boss, "Ремонт", "repair", "hour", 300)
    assert boss.put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": work, "repair_tariff_id": rep}).status_code == 200
    assert drv.get("/driver/home").json()["earned"]["total"] == 0                                           # пока ничего
    office_hours(disp, world, world.d1, "work", 8, n=1)
    office_hours(disp, world, world.d1, "repair", 2, n=1, description="Менял шланг")
    e = drv.get("/driver/home").json()["earned"]
    assert e["work_h"] == 8 and e["repair_h"] == 2 and e["work"] == 3600 and e["repair"] == 600 and e["total"] == 4200
    assert e["month"] == TODAY.strftime("%Y-%m") and e["title"]
    # на главной и на странице «Мои часы» одни и те же числа
    page = drv.get("/payroll/mine").json()["totals"]
    assert {k: e[k] for k in ("work_h", "repair_h", "work", "repair", "total")} == page
    # то, что внёс механик, тоже прибавляется
    assert make_client("mech_a").post("/payroll/enter", {"employee_id": world.d1, "work_date": day(), "kind": "work", "hours": "1"}).status_code == 201
    assert drv.get("/driver/home").json()["earned"]["total"] == 4650


def test_earned_ignores_cancelled_and_other_months_and_other_people(make_client, world):
    boss, disp, drv, drv2 = make_client("dir_a"), make_client("disp_a"), make_client("drv1"), make_client("drv2")
    work = mk_tariff(boss, "Работа", "work", "hour", 100)
    for eid in (world.d1, world.d2):
        boss.put(f"/payroll/employees/{eid}/tariffs", {"work_tariff_id": work})
    keep = office_hours(disp, world, world.d1, "work", 5, n=1)
    gone = office_hours(disp, world, world.d1, "work", 3, n=2)
    office_hours(disp, world, world.d1, "work", 10, n=40)                  # прошлый месяц
    office_hours(disp, world, world.d2, "work", 7, n=1)                    # другой водитель
    assert drv.get("/driver/home").json()["earned"]["total"] == 800
    assert drv2.get("/driver/home").json()["earned"]["total"] == 700
    r = disp.post(f"/worklog/{gone['id']}/void", {"reason": "ошибка"})
    assert r.status_code == 200, r.text
    assert drv.get("/driver/home").json()["earned"]["total"] == 500
    assert keep["id"] != gone["id"]


def test_earned_without_tariff_counts_zero_and_says_so(make_client, world):
    drv = make_client("drv1")
    assert make_client("mech_a").post("/payroll/enter", {"employee_id": world.d1, "work_date": day(), "kind": "work", "hours": "8"}).status_code == 201      # офис без тарифа часы не внесёт, механик может
    e = drv.get("/driver/home").json()["earned"]
    assert e["total"] == 0 and e["no_rate"] == 1 and e["work_h"] == 8


def test_employee_card_shows_earned_only_to_those_who_see_hours(make_client, world):
    boss, disp = make_client("dir_a"), make_client("disp_a")
    work = mk_tariff(boss, "Работа", "work", "hour", 450)
    boss.put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": work})
    office_hours(disp, world, world.d1, "work", 6, n=1)
    for login in ("dir_a", "disp_a", "acc_a"):
        card = make_client(login).get(f"/employees/{world.d1}").json()
        assert card["pay"]["earned"]["total"] == 2700, login
        assert card["pay"]["earned"]["work_h"] == 6
    # механик видит сотрудников, но не часы и не деньги
    card = make_client("mech_a").get(f"/employees/{world.d1}").json()
    assert "pay" not in card or "earned" not in card["pay"]
    # другой водитель чужую карточку вообще не открывает
    assert make_client("drv2").get(f"/employees/{world.d1}").status_code in (403, 404)


def test_earned_in_card_respects_legal_entity(make_client, world):
    """Директор юрлица B не видит заработок по часам юрлица A."""
    boss, disp = make_client("dir_a"), make_client("disp_a")
    work = mk_tariff(boss, "Работа", "work", "hour", 100)
    boss.put(f"/payroll/employees/{world.d1}/tariffs", {"work_tariff_id": work})
    office_hours(disp, world, world.d1, "work", 4, n=1)
    other = make_client("dir_b")
    r = other.get(f"/employees/{world.d1}")
    if r.status_code == 200:
        pay = r.json().get("pay") or {}
        assert (pay.get("earned") or {"total": 0})["total"] == 0
