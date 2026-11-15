# -*- coding: utf-8 -*-
"""Вкладки техники: шины, история ремонтов, финансы по машине, плановое ТО и контроль мероприятий.
Проверяются правила записей, расчёт «осталось и состояние», кнопка «Выполнено», напоминания и календарь, права и юрлица."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import User
from app.services import control, notify

TODAY = dt.date.today()


def d(offset: int = 0) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def items(r):
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ---------------------------------------------------------------- шины

def test_tires_rules_and_run(make_client, world):
    c = make_client("mech_a")
    r = c.post("/tires", {"vehicle_id": world.va1, "brand": "Michelin", "size": "315/80 R22.5", "season": "winter", "position": "передняя ось", "install_date": d(-30), "install_km": 400, "price": "25000"})
    assert r.status_code == 201, r.text
    row = items(c.get("/tires", params={"vehicle_id": world.va1}))[0]
    assert row["run_km"] == 600 and row["state"] == {"text": "На машине", "tone": "success"}          # пробег машины 1000, поставили на 400
    bad = c.post("/tires", {"vehicle_id": world.va1, "install_km": 500, "remove_km": 100})
    assert bad.status_code == 422 and bad.json()["fields"]["remove_km"]
    assert c.post("/tires", {"vehicle_id": world.va1, "install_date": d(-3), "remove_date": d(-9)}).status_code == 422
    assert c.post("/tires", {"vehicle_id": world.va1, "wear_pct": 150}).status_code == 422
    done = c.patch(f"/tires/{row['id']}", {"remove_date": d(-1), "remove_km": 900})
    assert done.status_code == 200, done.text
    row = items(c.get("/tires", params={"vehicle_id": world.va1}))[0]
    assert row["run_km"] == 500 and row["state"]["text"] == "Снята"
    assert len(items(c.get("/tires", params={"state": "mounted"}))) == 0 and len(items(c.get("/tires", params={"state": "removed"}))) == 1


def test_tires_filter_by_vehicle(make_client, world):
    c = make_client("mech_a")
    for v in (world.va1, world.va2, world.va2):
        assert c.post("/tires", {"vehicle_id": v, "brand": "Nokian"}).status_code == 201
    assert len(items(c.get("/tires", params={"vehicle_id": world.va2}))) == 2
    assert len(items(c.get("/tires", params={"vehicle_id": world.va1}))) == 1
    assert len(items(c.get("/tires", params={"q": "nokian"}))) == 3


# ---------------------------------------------------------------- ремонты и финансы

def test_repairs_and_totals(make_client, world):
    c = make_client("mech_a")
    assert c.post("/vehicle_repairs", {"vehicle_id": world.va1, "repair_date": d(-5), "title": "Замена масла", "cost": "4500.50", "executor": "СТО Север"}).status_code == 201
    assert c.post("/vehicle_repairs", {"vehicle_id": world.va1, "repair_date": d(-2), "kind": "part", "title": "Фильтр", "qty": "2", "cost": "1200"}).status_code == 201
    r = c.get("/vehicle_repairs", params={"vehicle_id": world.va1})
    assert r.status_code == 200 and r.json()["total"] == 2 and float(r.json()["totals"]["cost"]) == 5700.5
    assert c.post("/vehicle_repairs", {"vehicle_id": world.va1, "repair_date": d(10), "title": "Из будущего"}).status_code == 422
    assert c.post("/vehicle_repairs", {"vehicle_id": world.va1, "repair_date": d(-1), "title": ""}).status_code == 422
    assert len(items(c.get("/vehicle_repairs", params={"kind": "part"}))) == 1


def test_finance_balance(make_client, world):
    c = make_client("dir_a")
    assert c.post("/vehicle_finance", {"vehicle_id": world.va1, "op_date": d(-3), "kind": "income", "category": "Аренда", "amount": "30000"}).status_code == 201
    assert c.post("/vehicle_finance", {"vehicle_id": world.va1, "op_date": d(-2), "kind": "expense", "category": "Мойка", "amount": "1500"}).status_code == 201
    r = c.get("/vehicle_finance", params={"vehicle_id": world.va1})
    assert [x["balance"] for x in r.json()["items"]] == [-1500.0, 30000.0]
    assert float(r.json()["totals"]["balance"]) == 28500.0
    assert c.post("/vehicle_finance", {"vehicle_id": world.va1, "op_date": d(-1), "amount": "0"}).status_code == 422
    assert c.post("/vehicle_finance", {"vehicle_id": world.va1, "op_date": d(-1), "amount": "-5"}).status_code == 422


# ---------------------------------------------------------------- ТО и контроль: уровни

def test_control_evaluate_rules():
    ev = lambda **k: control.evaluate(**{"due_date": None, "due_km": None, "due_hours": None, "controlled": True, "closed": False, "today": TODAY, "cur_km": 1000, "cur_hours": 50, **k})  # noqa: E731
    assert ev(due_km=900)["level"] == "due" and ev(due_km=1000)["level"] == "due"
    assert ev(due_km=1400)["level"] == "soon" and ev(due_km=1501)["level"] == "ok"
    assert ev(due_date=TODAY - dt.timedelta(days=1))["level"] == "due"
    assert ev(due_date=TODAY + dt.timedelta(days=14))["level"] == "soon" and ev(due_date=TODAY + dt.timedelta(days=15))["level"] == "ok"
    assert ev(due_hours=55)["level"] == "soon" and ev(due_hours=70)["level"] == "ok" and ev(due_hours=49)["level"] == "due"
    assert ev(due_km=5000, due_date=TODAY - dt.timedelta(days=1))["level"] == "due"                     # любой порог наступил
    assert ev(due_km=900, controlled=False)["level"] == "closed" and ev(due_km=900, closed=True)["level"] == "closed"
    assert ev()["level"] == "ok"
    assert control.left_text({"days": -3, "km": 1250, "hours": 4.5}) == "−3 дн. · 1 250 км · 4.5 м·ч"


def test_maintenance_states_and_validation(make_client, world):
    c = make_client("mech_a")
    base = {"vehicle_id": world.va1, "kind": "to1", "work": "Замена масла"}
    bad = c.post("/maintenance", base)
    assert bad.status_code == 422 and "due_date" in bad.json()["fields"]
    ids = {}
    for name, extra in (("due", {"due_km": 900}), ("soon", {"due_km": 1300}), ("ok", {"due_km": 9000}), ("closed", {"due_km": 900, "controlled": False}),
                        ("by_date", {"due_date": d(5)})):
        r = c.post("/maintenance", {**base, **extra})
        assert r.status_code == 201, (name, r.text)
        ids[r.json()["id"]] = name
    got = {ids[x["id"]]: x for x in items(c.get("/maintenance"))}
    assert [got[k]["state"]["text"] for k in ("due", "soon", "ok", "closed", "by_date")] == ["Наступил", "Скоро", "В порядке", "Закрыто", "Скоро"]
    assert got["due"]["left"] == "−100 км" and got["by_date"]["left"] == "5 дн."
    assert len(items(c.get("/maintenance", params={"state": "open"}))) == 4 and len(items(c.get("/maintenance", params={"state": "closed"}))) == 1


def test_maintenance_done_creates_next(make_client, world):
    c = make_client("mech_a")
    mid = c.post("/maintenance", {"vehicle_id": world.va1, "kind": "to2", "work": "ТО-2", "due_km": 1200, "interval_km": 10000}).json()["id"]
    r = c.post(f"/maintenance/{mid}/done", {"note": "сделали в гараже"})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["done"]["state"]["text"] == "Закрыто" and j["done"]["done_at"] == d(0) and "сделали в гараже" in j["done"]["note"]
    assert j["next"]["due_km"] == 11000 and j["next"]["state"]["text"] == "В порядке" and j["next"]["kind"] == "to2"
    again = c.post(f"/maintenance/{mid}/done")
    assert again.status_code == 409 and again.json()["code"] == "already_done"
    no_interval = c.post("/maintenance", {"vehicle_id": world.va1, "due_km": 1500}).json()["id"]
    j = c.post(f"/maintenance/{no_interval}/done").json()
    assert j["next"] is None


def test_maintenance_done_needs_rights(make_client, world):
    mech = make_client("mech_a")
    mid = mech.post("/maintenance", {"vehicle_id": world.va1, "due_km": 1200, "interval_km": 5000}).json()["id"]
    assert make_client("disp_a").post(f"/maintenance/{mid}/done").status_code == 403
    assert make_client("dir_b").post(f"/maintenance/{mid}/done").status_code in (403, 404)
    assert items(mech.get("/maintenance"))[0]["done_at"] is None                                          # ничего не изменилось


def test_events_need_a_threshold(make_client, world):
    c = make_client("mech_a")
    bad = c.post("/vehicle_events", {"vehicle_id": world.va1, "title": "Поверка тахографа"})
    assert bad.status_code == 422 and "due_date" in bad.json()["fields"]
    ok = c.post("/vehicle_events", {"vehicle_id": world.va1, "event_type": "Тахограф", "title": "Поверка", "due_hours": "55"})
    assert ok.status_code == 201, ok.text
    row = items(c.get("/vehicle_events"))[0]
    assert row["state"]["text"] in ("Скоро", "Наступил", "В порядке") and row["event_type"] == "Тахограф"
    assert len(items(c.get("/vehicle_events", params={"state": "closed"}))) == 0


# ---------------------------------------------------------------- напоминания и календарь

def test_reminders_for_km_hours_and_dates(make_client, world):
    c = make_client("mech_a")
    c.post("/maintenance", {"vehicle_id": world.va1, "kind": "to1", "work": "Масло", "due_km": 900})                # наступило
    c.post("/maintenance", {"vehicle_id": world.va1, "kind": "to1", "work": "Тормоза", "due_km": 1300})             # скоро
    c.post("/maintenance", {"vehicle_id": world.va1, "kind": "to1", "work": "Далеко", "due_km": 9000})
    c.post("/maintenance", {"vehicle_id": world.va1, "kind": "to1", "work": "Снято", "due_km": 900, "controlled": False})
    c.post("/vehicle_events", {"vehicle_id": world.va2, "title": "Поверка", "due_date": d(3)})
    j = c.get("/reminders").json()
    mine = {r["title"]: r for r in j["items"] if r["source"] in ("maintenance", "vehicle_event")}
    assert set(mine) == {"Пора на ТО: ТО-1: Масло", "Пора на ТО: ТО-1: Тормоза", "Контроль: Поверка"}
    assert mine["Пора на ТО: ТО-1: Масло"]["level"] == "due" and mine["Пора на ТО: ТО-1: Масло"]["left_text"] == "−100 км"
    assert mine["Пора на ТО: ТО-1: Тормоза"]["level"] == "soon" and mine["Контроль: Поверка"]["left_text"] == "3 дн."
    assert mine["Контроль: Поверка"]["url"] == f"/vehicles/{world.va2}?tab=events" and mine["Пора на ТО: ТО-1: Масло"]["url"].endswith("?tab=maintenance")
    assert {x["code"] for x in j["sources"]} >= {"maintenance", "vehicle_event"}
    cal = c.get("/calendar", params={"view": "month", "d": d(0)}).json()
    titles = [e["title"] for day in cal["days"] for e in day["events"]] if "days" in cal else str(cal)
    assert "Контроль: Поверка" in str(titles) and "Пора на ТО: ТО-1: Масло" not in str(titles)                 # в календаре только срок по дате


def test_done_item_and_archived_vehicle_leave_reminders(make_client, world):
    c = make_client("mech_a")
    mid = c.post("/maintenance", {"vehicle_id": world.va1, "work": "Масло", "due_km": 900}).json()["id"]
    assert any(r["source"] == "maintenance" for r in c.get("/reminders").json()["items"])
    c.post(f"/maintenance/{mid}/done")
    assert not any(r["source"] == "maintenance" for r in c.get("/reminders").json()["items"])


def test_km_reminder_notifies_once(make_client, world):
    c = make_client("mech_a")
    c.post("/maintenance", {"vehicle_id": world.va1, "work": "Масло", "due_km": 900})
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.login == "mech_a"))
        assert notify.sweep_user(db, user) == 1
        db.commit()
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.login == "mech_a"))
        assert notify.sweep_user(db, user) == 0                              # на следующий обход день не поменялся: повторного уведомления нет
        db.commit()


# ---------------------------------------------------------------- права и юрлица

def test_rights_by_role(make_client, world):
    body = {"vehicle_id": world.va1, "due_km": 1500}
    assert make_client("mech_a").post("/maintenance", body).status_code == 201
    assert make_client("dir_a").post("/maintenance", body).status_code == 201
    disp = make_client("disp_a")
    assert disp.get("/maintenance").status_code == 200 and disp.post("/maintenance", body).status_code == 403
    assert disp.get("/vehicle_finance").status_code == 403                                                  # диспетчеру финансы по технике не видны
    acc = make_client("acc_a")
    assert acc.get("/maintenance").status_code == 403 and acc.get("/vehicle_finance").status_code == 200
    assert acc.post("/vehicle_finance", {"vehicle_id": world.va1, "op_date": d(-1), "amount": "100"}).status_code == 201
    for who in ("drv1", "store_a", "emp_a"):
        c = make_client(who)
        for res in ("tires", "vehicle_repairs", "vehicle_finance", "maintenance", "vehicle_events"):
            assert c.get(f"/{res}").status_code == 403, (who, res)
    mech = make_client("mech_a")
    tid = mech.post("/tires", {"vehicle_id": world.va1}).json()["id"]
    assert mech.post(f"/tires/{tid}/void", {"reason": "ошибка"}).status_code == 403                         # механик не убирает в архив
    assert make_client("dir_a").post(f"/tires/{tid}/void", {"reason": "ошибка"}).status_code == 200


def test_other_legal_entity_is_invisible(make_client, world):
    mech = make_client("mech_a")
    mid = mech.post("/maintenance", {"vehicle_id": world.va1, "work": "Масло", "due_km": 900}).json()["id"]
    boss_b = make_client("dir_b")
    assert items(boss_b.get("/maintenance")) == [] and boss_b.patch(f"/maintenance/{mid}", {"work": "чужое"}).status_code in (403, 404)
    other = boss_b.post("/maintenance", {"vehicle_id": world.va1, "due_km": 1500})
    assert other.status_code == 422 and other.json()["fields"]["vehicle_id"]                                 # чужая машина
    assert not any(r["source"] == "maintenance" for r in boss_b.get("/reminders").json()["items"])
    mine_b = boss_b.post("/maintenance", {"vehicle_id": world.vb1, "due_km": 900})
    assert mine_b.status_code == 201
    assert [r["subject"].split(" ")[0] for r in boss_b.get("/reminders").json()["items"] if r["source"] == "maintenance"] == ["В001ВВ54"]
