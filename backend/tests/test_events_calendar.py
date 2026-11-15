# -*- coding: utf-8 -*-
"""События сотрудников, свои события календаря, календарь (день, неделя, месяц, год) и напоминания: права, юрлица, сроки."""
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db import session_factory
from app.models import AuditLog, DossierDoc, Employee, RepairBill
from app.services import reminders as rem
from app.common import org_today

TODAY = org_today()


def d(offset: int = 0) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def fields(r):
    assert r.status_code == 422, r.text
    j = r.json()
    return (j["detail"] if "detail" in j else j)["fields"]


def local_iso(day: dt.date, hour: int = 10, minute: int = 0) -> str:
    """Момент по часам организации, как его отправит сайт (в UTC)."""
    tz = ZoneInfo(get_settings().org_timezone)
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=tz).astimezone(dt.timezone.utc).isoformat()


def event(c, world, **kw):
    body = {"legal_entity_id": world.A, "employee_id": world.d1, "kind": "medical", "event_date": d(-10), "due_date": d(10), "description": "Периодический"}
    body.update(kw)
    return c.post("/employee_events", body)


def own(c, world, **kw):
    body = {"legal_entity_id": world.A, "title": "Встреча", "start_at": local_iso(TODAY + dt.timedelta(days=1))}
    body.update(kw)
    return c.post("/calendar_events", body)


def titles(rows):
    return [(r["source"], r["title"]) for r in rows]


# ---------------------------------------------------------------- чистые функции

def test_level_and_texts():
    t = dt.date(2026, 11, 15)
    assert rem.level_of(t - dt.timedelta(days=1), t, 30) == "due"
    assert rem.level_of(t, t, 30) == "due"
    assert rem.level_of(t + dt.timedelta(days=30), t, 30) == "soon"
    assert rem.level_of(t + dt.timedelta(days=31), t, 30) == "ok"
    assert rem.left_text(-3) == "просрочено на 3 дн." and rem.left_text(0) == "срок сегодня" and rem.left_text(5) == "5 дн."
    assert [rem.years_text(n) for n in (1, 2, 5, 11, 21, 22, 25, 112)] == ["1 год", "2 года", "5 лет", "11 лет", "21 год", "22 года", "25 лет", "112 лет"]


# ---------------------------------------------------------------- события сотрудников

def test_employee_events_crud_and_validation(world, make_client):
    c = make_client("dir_a")
    r = event(c, world)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["employee_id"] == world.d1 and row["kind"] == "medical" and row["controlled"] is True and row["amount"] == 0
    assert row["state"]["text"] == "Всё в порядке" or row["state"]["text"] == "Скоро срок"
    # нужна хотя бы одна дата
    assert "event_date" in fields(event(c, world, event_date=None, due_date=None))
    # срок не раньше события
    assert "due_date" in fields(event(c, world, event_date=d(-1), due_date=d(-5)))
    # событие в будущем не бывает
    assert "event_date" in fields(event(c, world, event_date=d(10), due_date=None))
    # сумма только у премии и штрафа
    assert "amount" in fields(event(c, world, amount="500"))
    ok = event(c, world, kind="bonus", amount="1500,50", due_date=None)
    assert ok.status_code == 201 and ok.json()["amount"] == 1500.5
    assert "amount" in fields(event(c, world, kind="bonus", amount="-1", due_date=None))
    # сотрудник из другого юрлица
    assert "employee_id" in fields(event(c, world, employee_id=world.d3))
    # правка и архив
    eid = row["id"]
    assert c.patch(f"/employee_events/{eid}", {"description": "  Ежегодный  "}).json()["description"] == "Ежегодный"
    assert c.post(f"/employee_events/{eid}/void", {"reason": "ошибка"}).status_code == 200
    assert c.get("/employee_events").json()["total"] == 1                      # остался только бонус
    assert c.post(f"/employee_events/{eid}/restore").status_code == 200


def test_employee_event_states_and_filter(world, make_client):
    c = make_client("dir_a")
    soon = event(c, world, due_date=d(5)).json()
    overdue = event(c, world, due_date=d(-3), description="Просрочен").json()
    far = event(c, world, due_date=d(200), description="Далеко").json()
    nodue = event(c, world, due_date=None, description="Без срока").json()
    off = event(c, world, due_date=d(2), controlled=False, description="Не следим").json()
    assert soon["state"]["text"] == "Скоро срок" and soon["left"] == "5 дн."
    assert overdue["state"]["text"] == "Просрочено" and overdue["left"] == "просрочено на 3 дн."
    assert far["state"]["text"] == "Всё в порядке"
    assert nodue["state"]["text"] == "Без срока" and off["state"]["text"] == "Не контролируется"
    got = {r["id"] for r in c.get("/employee_events", params={"state": "attention"}).json()["items"]}
    assert got == {soon["id"], overdue["id"]}
    assert off["id"] not in {r["id"] for r in c.get("/employee_events", params={"state": "controlled"}).json()["items"]}
    assert len(c.get("/employee_events", params={"employee_id": world.d1}).json()["items"]) == 5
    assert c.get("/employee_events", params={"employee_id": world.d2}).json()["items"] == []
    assert c.get("/employee_events", params={"kind": "bonus"}).json()["items"] == []


def test_employee_events_rights_and_isolation(world, make_client):
    d_a, d_b = make_client("dir_a"), make_client("dir_b")
    eid = event(d_a, world).json()["id"]
    assert d_b.get("/employee_events").json()["items"] == []                    # чужое юрлицо не видно
    assert d_b.patch(f"/employee_events/{eid}", {"description": "x"}).status_code == 404
    assert event(d_b, world).status_code in (403, 422)                          # в чужое юрлицо не записать
    acc = make_client("acc_a")
    assert acc.get("/employee_events").status_code == 200 and event(acc, world, description="Бухгалтер").status_code == 201
    assert acc.post(f"/employee_events/{eid}/void", {"reason": "нет прав"}).status_code in (403, 404)
    disp = make_client("disp_a")
    assert disp.get("/employee_events").json()["total"] == 2
    assert event(disp, world).status_code == 403
    assert make_client("mech_a").get("/employee_events").status_code == 403
    assert make_client("drv1").get("/employee_events").status_code in (403, 404)
    # изменения пишутся в журнал
    with session_factory()() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "employee_events.create")) is not None


# ---------------------------------------------------------------- свои события

def test_calendar_events_crud_and_validation(world, make_client):
    c = make_client("dir_a")
    start = TODAY + dt.timedelta(days=3)
    r = own(c, world, title="  Техосмотр  ", start_at=local_iso(start, 9), end_at=local_iso(start, 11), remind_min=60, color="blue")
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Техосмотр" and r.json()["remind_min"] == 60 and r.json()["done"] is False
    assert "end_at" in fields(own(c, world, start_at=local_iso(start, 9), end_at=local_iso(start, 8)))
    assert "title" in fields(own(c, world, title="   "))
    assert "start_at" in fields(own(c, world, start_at=None))
    assert "remind_min" in fields(own(c, world, remind_min=-5))
    assert "remind_min" in fields(own(c, world, remind_min=60 * 24 * 31))
    assert "color" in fields(own(c, world, color="pink"))
    assert "vehicle_id" in fields(own(c, world, vehicle_id=world.vb1))        # чужая техника
    assert "employee_id" in fields(own(c, world, employee_id=world.d3))
    ok = own(c, world, vehicle_id=world.va1, employee_id=world.d1)
    assert ok.status_code == 201 and ok.json()["vehicle_id"] == world.va1
    # выполнено отмечается правкой
    eid = r.json()["id"]
    assert c.patch(f"/calendar_events/{eid}", {"done": True}).json()["done"] is True
    assert {x["id"] for x in c.get("/calendar_events", params={"state": "done"}).json()["items"]} == {eid}
    assert c.post(f"/calendar_events/{eid}/void", {"reason": "отмена"}).status_code == 200


def test_all_day_event_is_normalised(world, make_client):
    c = make_client("dir_a")
    day = TODAY + dt.timedelta(days=2)
    r = own(c, world, all_day=True, start_at=local_iso(day, 15, 30), end_at=local_iso(day + dt.timedelta(days=2), 8))
    assert r.status_code == 201, r.text
    tz = ZoneInfo(get_settings().org_timezone)
    start = dt.datetime.fromisoformat(r.json()["start_at"]).astimezone(tz)
    end = dt.datetime.fromisoformat(r.json()["end_at"]).astimezone(tz)
    assert (start.date(), start.time()) == (day, dt.time.min)
    assert (end.date(), end.time()) == (day + dt.timedelta(days=2), dt.time.min)
    assert "end_at" in fields(own(c, world, all_day=True, start_at=local_iso(day, 10), end_at=local_iso(day - dt.timedelta(days=1), 10)))


def test_calendar_events_rights_and_isolation(world, make_client):
    d_a, d_b = make_client("dir_a"), make_client("dir_b")
    eid = own(d_a, world).json()["id"]
    assert d_b.get("/calendar_events").json()["items"] == []
    assert d_b.patch(f"/calendar_events/{eid}", {"title": "x"}).status_code == 404
    assert make_client("mech_a").get("/calendar_events").json()["total"] == 1
    assert own(make_client("mech_a"), world, title="Механик").status_code == 201
    store = make_client("store_a")
    assert store.get("/calendar_events").status_code == 200 and own(store, world).status_code == 403   # кладовщик только смотрит
    assert make_client("drv1").get("/calendar_events").status_code in (403, 404)


# ---------------------------------------------------------------- календарь

def test_calendar_views_and_grids(world, make_client):
    c = make_client("dir_a")
    for view, days in (("day", 1), ("week", 7)):
        j = c.get("/calendar", params={"view": view, "d": "2026-11-18"}).json()
        assert j["view"] == view and len(j["grid"]["days"]) == days
    wk = c.get("/calendar", params={"view": "week", "d": "2026-11-18"}).json()
    assert (wk["from"], wk["to"]) == ("2026-11-16", "2026-11-22") and wk["grid"]["days"][0]["wd"] == "пн"
    assert wk["title"] == "16 ноября – 22 ноября 2026" and wk["prev"] == "2026-11-11" and wk["next"] == "2026-11-25"
    mo = c.get("/calendar", params={"view": "month", "d": "2026-11-18"}).json()
    assert (mo["from"], mo["to"]) == ("2026-10-26", "2026-12-06") and len(mo["grid"]["weeks"]) == 6
    assert mo["title"] == "Ноябрь 2026" and mo["prev"] == "2026-10-01" and mo["next"] == "2026-12-01"
    first = mo["grid"]["weeks"][0][0]
    assert first["d"] == "2026-10-26" and first["in_month"] is False
    yr = c.get("/calendar", params={"view": "year", "d": "2026-11-18"}).json()
    assert len(yr["grid"]["months"]) == 12 and (yr["from"], yr["to"]) == ("2026-01-01", "2026-12-31") and yr["title"] == "2026"
    assert yr["prev"] == "2025-11-18" and yr["next"] == "2027-11-18"
    bad = c.get("/calendar", params={"view": "bogus", "d": "не дата"}).json()
    assert bad["view"] == "month" and bad["anchor"] == TODAY.isoformat()
    leap = c.get("/calendar", params={"view": "year", "d": "2028-02-29"}).json()
    assert leap["next"] == "2029-02-28"


def test_calendar_shows_own_events_by_local_day(world, make_client):
    c = make_client("dir_a")
    day = TODAY + dt.timedelta(days=1)
    # 23:30 по часам организации: в UTC это уже может быть другой день, но в календаре — день организации
    late = own(c, world, title="Поздно", start_at=local_iso(day, 23, 30)).json()
    early = own(c, world, title="Рано", start_at=local_iso(day, 0, 15), remind_min=15).json()
    allday = own(c, world, title="Весь день", all_day=True, start_at=local_iso(day, 12)).json()
    j = c.get("/calendar", params={"view": "day", "d": day.isoformat()}).json()
    got = {i["title"]: i for i in j["items"]}
    assert set(got) == {"Поздно", "Рано", "Весь день"}
    assert got["Поздно"]["time"] == "23:30" and got["Рано"]["time"] == "00:15" and got["Весь день"]["time"] == "" and got["Весь день"]["all_day"] is True
    assert got["Поздно"]["row"]["id"] == late["id"] and got["Поздно"]["row"]["can_edit"] is True      # для формы изменения
    assert [i["title"] for i in j["items"]][:2] == ["Рано", "Поздно"]                                   # по времени, «весь день» в конце
    # соседний день пуст
    assert c.get("/calendar", params={"view": "day", "d": (day + dt.timedelta(days=1)).isoformat()}).json()["items"] == []
    # убранное в архив не показывается
    c.post(f"/calendar_events/{early['id']}/void", {"reason": "лишнее"})
    assert "Рано" not in [i["title"] for i in c.get("/calendar", params={"view": "day", "d": day.isoformat()}).json()["items"]]
    assert allday["id"]


def make_sources(world, make_client):
    """Один срок из каждого раздела, у каждого свой день."""
    c = make_client("dir_a")
    soon = TODAY + dt.timedelta(days=5)
    with session_factory()() as db:
        db.add(DossierDoc(employee_id=world.d1, kind="driver_license", valid_until=soon, doc_number="1"))
        db.add(DossierDoc(employee_id=world.d1, kind="passport", valid_until=soon, doc_number="2"))
        db.add(DossierDoc(vehicle_id=world.va1, kind="osago", valid_until=soon, doc_number="3"))
        db.add(DossierDoc(vehicle_id=world.vb1, kind="osago", valid_until=soon, doc_number="4"))          # другое юрлицо
        db.add(DossierDoc(employee_id=world.d1, kind="photo"))                                              # у фото срока нет
        db.add(RepairBill(legal_entity_id=world.A, number="77", bill_date=TODAY - dt.timedelta(days=10), supplier_name="Автозапчасть", total=Decimal(1000)))
        e = db.get(Employee, world.d2)
        e.birth_date = dt.date(1980, soon.month, soon.day)
        db.commit()
    assert event(c, world, due_date=d(5)).status_code == 201
    n = c.post("/ppe_norms", {"name": "Куртка", "wear_months": 12}).json()["id"]
    assert c.post("/ppe", {"legal_entity_id": world.A, "employee_id": world.d1, "ppe_id": n, "issue_date": d(-360)}).status_code == 201
    assert c.post("/fines", {"vehicle_id": world.va1, "fine_date": d(-20), "amount": "500", "due_date": d(4), "resolution_number": "123"}).status_code == 201
    assert c.post("/rentals", {"vehicle_id": world.va1, "start_date": d(-60), "end_date": d(6)}).status_code == 201
    assert c.post("/fuel_cards", {"legal_entity_id": world.A, "number": "777", "valid_until": d(7)}).status_code == 201
    assert own(c, world, title="Своё", start_at=local_iso(TODAY + dt.timedelta(days=1)), remind_min=0).status_code == 201
    return c


def test_calendar_collects_all_sources(world, make_client):
    c = make_sources(world, make_client)
    j = c.get("/calendar", params={"view": "month", "d": d(5)}).json()
    got = {(i["source"]) for i in j["items"]}
    wide = c.get("/calendar", params={"view": "year", "d": TODAY.isoformat()}).json()
    all_sources = {i["source"] for i in wide["items"]}
    assert all_sources == {"employee_doc", "vehicle_doc", "employee_event", "ppe", "fine", "rental", "fuel_card", "calendar", "birthday"}, all_sources
    assert "bill" not in all_sources                                            # у счёта нет даты, его нет в календаре
    docs = [i for i in wide["items"] if i["source"] == "employee_doc"]
    assert {i["title"] for i in docs} == {"Кончается срок: Водительское удостоверение", "Кончается срок: Паспорт"}
    assert [i["subject"] for i in wide["items"] if i["source"] == "vehicle_doc"] == ["А001АА54 · КАМАЗ 65115"]     # чужая техника не видна
    birthday = next(i for i in wide["items"] if i["source"] == "birthday")
    assert birthday["color"] == "violet" and birthday["subject"] == "Второй Тест" and birthday["title"].startswith("День рождения")
    fine = next(i for i in wide["items"] if i["source"] == "fine")
    assert fine["day"] == d(4) and fine["color"] == "amber" and "123" in fine["subject"]
    assert got  # месяц тоже не пуст


def test_calendar_colors_overdue_and_both_fine_dates(world, make_client):
    c = make_client("dir_a")
    assert c.post("/fines", {"vehicle_id": world.va1, "fine_date": d(-30), "amount": "500", "discount_until": d(-2), "due_date": d(3)}).status_code == 201
    items = c.get("/calendar", params={"view": "month", "d": TODAY.isoformat()}).json()["items"]
    fines = sorted((i["day"], i["title"], i["color"]) for i in items if i["source"] == "fine")
    # в календаре обе даты; прошедшая красная, будущая жёлтая
    got = [f for f in fines if f[0] in (d(-2), d(3))]
    assert len(got) == 2 or TODAY.month != (TODAY + dt.timedelta(days=3)).month or TODAY.month != (TODAY - dt.timedelta(days=2)).month
    for day, title, color in fines:
        if day == d(-2):
            assert title == "Скидка на штраф кончается" and color == "red"
        if day == d(3):
            assert title == "Оплатить штраф" and color == "amber"


def test_calendar_hides_what_you_may_not_see(world, make_client):
    make_sources(world, make_client)
    year = {"view": "year", "d": TODAY.isoformat()}
    # диспетчер: документы (без паспорта), события сотрудников (только смотрит), СИЗ, штрафы, аренда, карты; дни рождения — нет права на личные данные
    disp = {i["source"] for i in make_client("disp_a").get("/calendar", params=year).json()["items"]}
    assert "birthday" not in disp and {"employee_doc", "vehicle_doc", "ppe", "fine", "rental", "fuel_card", "employee_event"} <= disp
    docs = [i["title"] for i in make_client("disp_a").get("/calendar", params=year).json()["items"] if i["source"] == "employee_doc"]
    assert docs == ["Кончается срок: Водительское удостоверение"]                    # паспорт диспетчеру не показывается
    # механик: документы техники, штрафы, аренда, карты и свои события; ни событий сотрудников, ни СИЗ, ни личных дел водителей
    mech = {i["source"] for i in make_client("mech_a").get("/calendar", params=year).json()["items"]}
    assert mech == {"vehicle_doc", "fine", "rental", "fuel_card", "calendar"}, mech
    # кладовщик: свои события и СИЗ (он их выдаёт), больше ничего
    store = {i["source"] for i in make_client("store_a").get("/calendar", params=year).json()["items"]}
    assert store == {"calendar", "ppe"}
    # директор другого юрлица ничего чужого не видит
    assert make_client("dir_b").get("/calendar", params=year).json()["items"] == [] or {
        i["subject"] for i in make_client("dir_b").get("/calendar", params=year).json()["items"]} <= {"В001ВВ54 · КАМАЗ 65115", "Третий Тест"}
    # водителю календарь не положен
    assert make_client("drv1").get("/calendar").status_code == 403
    assert make_client("drv1").get("/reminders").status_code == 403
    assert make_client().get("/calendar").status_code == 401


def test_calendar_entity_switch(world, make_client):
    c = make_sources(world, make_client)
    admin = make_client("admin")
    year = {"view": "year", "d": TODAY.isoformat()}
    both = {i["subject"] for i in admin.get("/calendar", params=year).json()["items"] if i["source"] == "vehicle_doc"}
    assert both == {"А001АА54 · КАМАЗ 65115", "В001ВВ54 · КАМАЗ 65115"}
    only_b = {i["subject"] for i in admin.get("/calendar", params={**year, "legal_entity_id": world.B}).json()["items"] if i["source"] == "vehicle_doc"}
    assert only_b == {"В001ВВ54 · КАМАЗ 65115"}
    # чужое юрлицо в переключателе даёт пустой календарь, а не чужие данные
    assert c.get("/calendar", params={**year, "legal_entity_id": world.B}).json()["items"] == []


# ---------------------------------------------------------------- напоминания

def test_reminders_levels_sources_and_order(world, make_client):
    c = make_sources(world, make_client)
    j = c.get("/reminders").json()
    rows = j["items"]
    srcs = {r["source"] for r in rows}
    assert srcs == {"employee_doc", "vehicle_doc", "employee_event", "ppe", "fine", "rental", "fuel_card", "bill", "calendar"}, srcs
    assert "birthday" not in srcs                                                      # дни рождения только в календаре
    assert j["counts"]["total"] == len(rows) and j["counts"]["due"] + j["counts"]["soon"] == len(rows)
    # просроченное идёт первым
    levels = [r["level"] for r in rows]
    assert levels == sorted(levels, key=lambda x: 0 if x == "due" else 1)
    bill = next(r for r in rows if r["source"] == "bill")
    assert bill["level"] == "due" and bill["url"].startswith("/bills/") and "77" in bill["subject"] and bill["day"] == d(-3)
    ppe = next(r for r in rows if r["source"] == "ppe")
    assert ppe["level"] == "soon" and ppe["left"] in (4, 5) and ppe["title"] == "Замена СИЗ: Куртка"
    fine = next(r for r in rows if r["source"] == "fine")
    assert fine["level"] == "soon" and fine["left"] == 4
    own_row = next(r for r in rows if r["source"] == "calendar")
    assert own_row["title"] == "Своё" and own_row["level"] == "soon"               # завтра, напомнить в момент начала: ещё не время
    # фильтры
    assert {r["source"] for r in c.get("/reminders", params={"source": "fine"}).json()["items"]} == {"fine"}
    assert all(r["level"] == "due" for r in c.get("/reminders", params={"level": "due"}).json()["items"])
    assert sum(s["count"] for s in j["sources"]) == len(rows)


def test_reminders_exclusions(world, make_client):
    c = make_client("dir_a")
    far = TODAY + dt.timedelta(days=100)
    with session_factory()() as db:
        db.add(DossierDoc(employee_id=world.d1, kind="driver_license", valid_until=far))                     # далеко
        db.add(DossierDoc(employee_id=world.d1, kind="sts", valid_until=TODAY + dt.timedelta(days=3), archived_at=dt.datetime.now(dt.timezone.utc)))  # в архиве
        db.add(RepairBill(legal_entity_id=world.A, number="1", bill_date=TODAY - dt.timedelta(days=3), total=Decimal(10)))                   # меньше недели
        db.add(RepairBill(legal_entity_id=world.A, number="2", bill_date=TODAY - dt.timedelta(days=30), total=Decimal(10), status="paid", paid_date=TODAY))  # оплачен
        db.commit()
    paid = c.post("/fines", {"vehicle_id": world.va1, "fine_date": d(-20), "amount": "5", "due_date": d(1), "paid_at": d(0)})
    assert paid.status_code == 201
    far_fine = c.post("/fines", {"vehicle_id": world.va1, "fine_date": d(-2), "amount": "5", "due_date": d(20)})        # дальше 7 дней
    assert far_fine.status_code == 201
    finished = c.post("/rentals", {"vehicle_id": world.va2, "start_date": d(-60), "end_date": d(3), "finished": True})
    assert finished.status_code == 201
    n = c.post("/ppe_norms", {"name": "Каска", "wear_months": 12}).json()["id"]
    c.post("/ppe", {"legal_entity_id": world.A, "employee_id": world.d1, "ppe_id": n, "issue_date": d(-360), "returned_date": d(-1)})   # возвращено
    assert event(c, world, due_date=d(3), controlled=False).status_code == 201                                   # не контролируется
    assert event(c, world, due_date=d(3), description="Убрано").status_code == 201
    eid = [r for r in c.get("/employee_events").json()["items"] if r["description"] == "Убрано"][0]["id"]
    c.post(f"/employee_events/{eid}/void", {"reason": "ошибся"})
    assert c.get("/reminders").json()["items"] == []
    # а вот просроченный штраф со скидкой, которая ещё действует, считается по скидке
    c.post("/fines", {"vehicle_id": world.va1, "fine_date": d(-5), "amount": "5", "discount_until": d(2), "due_date": d(30)})
    rows = c.get("/reminders").json()["items"]
    assert [(r["title"], r["left"]) for r in rows] == [("Оплатить штраф со скидкой", 2)]


def test_reminders_own_event_levels(world, make_client):
    c = make_client("dir_a")
    now = dt.datetime.now(dt.timezone.utc)
    soon_start = (now + dt.timedelta(minutes=20)).isoformat()
    assert own(c, world, title="Через 20 минут, за 30", start_at=soon_start, remind_min=30).status_code == 201     # пора напомнить
    assert own(c, world, title="Через 20 минут, за 10", start_at=soon_start, remind_min=10).status_code == 201     # ещё рано, но сегодня: «скоро»
    assert own(c, world, title="Без напоминания", start_at=soon_start).status_code == 201
    late = own(c, world, title="Прошло", start_at=(now - dt.timedelta(hours=2)).isoformat(), remind_min=0).json()
    done = own(c, world, title="Сделано", start_at=(now - dt.timedelta(hours=2)).isoformat(), remind_min=0, done=True)
    assert done.status_code == 201
    far = own(c, world, title="Далеко", start_at=(now + dt.timedelta(days=60)).isoformat(), remind_min=0)
    assert far.status_code == 201
    got = {r["title"]: r["level"] for r in c.get("/reminders").json()["items"]}
    assert got == {"Через 20 минут, за 30": "due", "Через 20 минут, за 10": "soon", "Прошло": "due"}, got
    # отметили выполненным — напоминание ушло
    c.patch(f"/calendar_events/{late['id']}", {"done": True})
    assert "Прошло" not in {r["title"] for r in c.get("/reminders").json()["items"]}


def test_reminders_rights_and_scope(world, make_client):
    make_sources(world, make_client)
    srcs = lambda login: {r["source"] for r in make_client(login).get("/reminders").json()["items"]}   # noqa: E731
    assert srcs("store_a") <= {"calendar", "ppe"}
    assert srcs("mech_a") <= {"vehicle_doc", "fine", "rental", "fuel_card", "calendar", "bill"}        # счета на ремонт механик видит
    assert not srcs("mech_a") & {"employee_event", "ppe", "employee_doc"}
    assert "employee_event" in srcs("disp_a") and "bill" not in srcs("disp_a")
    assert "bill" in srcs("acc_a") and "employee_event" in srcs("acc_a")
    assert srcs("dir_b") <= {"vehicle_doc"} or srcs("dir_b") == set()
    assert make_client("drv1").get("/reminders").status_code == 403
    assert make_client("emp_a").get("/reminders/summary").status_code == 403
    assert make_client().get("/reminders").status_code == 401


def test_reminders_summary_and_export(world, make_client):
    c = make_sources(world, make_client)
    full = c.get("/reminders").json()
    s = c.get("/reminders/summary").json()
    assert s == full["counts"] and s["total"] >= 8
    r = c.get("/reminders/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].split(";")[0] in ("Состояние", "﻿Состояние") or "Состояние" in lines[0]
    assert len(lines) == s["total"] + 1
    with session_factory()() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "reminders.export")) is not None
    assert make_client("drv1").get("/reminders/export.csv").status_code == 403


def test_role_defaults_for_new_permissions(world, make_client):
    can = lambda login, perm: perm in make_client(login).get("/auth/me").json()["permissions"]   # noqa: E731
    for login in ("dir_a", "acc_a", "disp_a", "mech_a", "store_a"):
        assert can(login, "calendar.view") and can(login, "reminders.view"), login
    for login in ("drv1", "emp_a"):
        assert not can(login, "calendar.view") and not can(login, "reminders.view"), login
    assert can("dir_a", "employee_events.void") and not can("acc_a", "employee_events.void")
    assert can("disp_a", "employee_events.view") and not can("disp_a", "employee_events.create")
    assert not can("mech_a", "employee_events.view") and can("mech_a", "calendar_events.create")
    assert can("store_a", "calendar_events.view") and not can("store_a", "calendar_events.create")
