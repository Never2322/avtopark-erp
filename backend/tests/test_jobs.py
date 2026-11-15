# -*- coding: utf-8 -*-
"""Наряды на день: создание и проверки, доска дня, отметки водителя, фото до и после, права, изоляция юрлиц, журнал."""
import datetime as dt

from sqlalchemy import func, select

from app.db import session_factory
from app.models import AuditLog, JobPhoto, StoredFile
from tests.test_dossier import PDF, png

TODAY = dt.date.today()
TODAY_ISO = TODAY.isoformat()


def new_job(w, c, vehicle=None, **kw):
    body = {"job_date": TODAY_ISO, "vehicle_id": vehicle or w.va1, "customer": "ООО Заказчик", "address": "Новосибирск, ул. Ленина, 1",
            "task": "Вывоз снега", "time_from": "08:30"}
    body.update(kw)
    return c.post("/jobs", body)


def photos(c, jid, stage="before", files=None):
    files = files if files is not None else [("до.png", png(), "image/png")]
    return c.call("POST", f"/jobs/{jid}/photos", data={"stage": stage}, files=[("files", f) for f in files])


def actions(*names):
    with session_factory()() as db:
        return db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(names)))


# ---------------------------------------------------------------- создание и проверки

def test_create_job_validation_and_entity_from_vehicle(world, make_client):
    d = make_client("disp_a")
    r = new_job(world, d)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["state"] == "planned" and j["legal_entity_id"] == world.A and j["time_from"] == "08:30"
    assert j["status"]["text"] == "Запланирован"

    # адрес обязателен, время только ЧЧ:ММ, дата не дальше года, машина из своего юрлица
    r = new_job(world, d, address="  ")
    assert r.status_code == 422 and "address" in r.json()["fields"]
    r = new_job(world, d, time_from="25:61")
    assert r.status_code == 422 and "time_from" in r.json()["fields"]
    r = new_job(world, d, job_date=(TODAY + dt.timedelta(days=400)).isoformat())
    assert r.status_code == 422 and "job_date" in r.json()["fields"]
    assert new_job(world, d, vehicle=world.vb1).status_code == 422

    # лишние пробелы в заказчике и адресе убираются
    r = new_job(world, d, customer="  ООО   Север ", address=" ул.  Мира ,  5 ")
    assert r.json()["customer"] == "ООО Север" and r.json()["address"] == "ул. Мира , 5"

    # списанную технику в наряд не ставят
    adm = make_client("admin")
    assert adm.post(f"/vehicles/{world.va3}/status", {"status": "written_off", "reason": "Списана по акту"}).status_code in (200, 201)
    r = new_job(world, d, vehicle=world.va3)
    assert r.status_code == 422 and "vehicle_id" in r.json()["fields"]


def test_state_cannot_be_set_through_registry_patch(world, make_client):
    d = make_client("disp_a")
    jid = new_job(world, d).json()["id"]
    r = d.patch(f"/jobs/{jid}", {"state": "done", "address": "Новый адрес"})
    assert r.status_code == 200
    assert r.json()["state"] == "planned" and r.json()["address"] == "Новый адрес"


# ---------------------------------------------------------------- отметки водителя

def test_driver_flow_arrive_finish(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    mine = new_job(world, d, vehicle=world.va1).json()["id"]
    other = new_job(world, d, vehicle=world.va2).json()["id"]

    r = drv.get("/jobs/mine")
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()["items"]]
    assert ids == [mine]                                   # чужую машину водитель не видит
    assert drv.post(f"/jobs/{other}/arrive").status_code == 404

    r = drv.post(f"/jobs/{mine}/arrive")
    assert r.status_code == 200 and r.json()["changed"] is True
    job = r.json()["job"]
    assert job["state"] == "on_site" and job["arrived_by"] == "Первый Тест" and job["can"]["finish"] is True
    # повторное нажатие ничего не ломает
    again = drv.post(f"/jobs/{mine}/arrive").json()
    assert again["changed"] is False and again["job"]["arrived_at"] == job["arrived_at"]

    r = drv.post(f"/jobs/{mine}/finish", {"note": "Вывезли 3 рейса"})
    assert r.status_code == 200 and r.json()["job"]["state"] == "done" and r.json()["job"]["result_note"] == "Вывезли 3 рейса"
    assert drv.post(f"/jobs/{mine}/finish", {"note": "ещё раз"}).json()["changed"] is False
    assert drv.post(f"/jobs/{mine}/arrive").status_code == 409
    assert actions("jobs.arrive", "jobs.finish") == 2

    # водитель не правит сам наряд и не меняет состояние
    assert drv.patch(f"/jobs/{mine}", {"address": "Другой"}).status_code in (403, 404)
    assert drv.post(f"/jobs/{mine}/state", {"state": "planned"}).status_code == 403
    assert drv.post("/jobs", {"job_date": TODAY_ISO, "vehicle_id": world.va1, "address": "х"}).status_code == 403


def test_finish_without_arrive_fills_arrival(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    job = drv.post(f"/jobs/{jid}/finish", {"note": ""}).json()["job"]
    assert job["state"] == "done" and job["arrived_at"] and job["arrived_by"] == "Первый Тест"


def test_driver_window_today_tomorrow_and_unfinished(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    day = lambda n: (TODAY + dt.timedelta(days=n)).isoformat()
    today = new_job(world, d, job_date=day(0)).json()["id"]
    tomorrow = new_job(world, d, job_date=day(1)).json()["id"]
    later = new_job(world, d, job_date=day(2)).json()["id"]
    old_open = new_job(world, d, job_date=day(-3)).json()["id"]
    old_done = new_job(world, d, job_date=day(-3)).json()["id"]
    too_old = new_job(world, d, job_date=day(-20)).json()["id"]
    d.post(f"/jobs/{old_done}/state", {"state": "done"})
    got = {x["id"] for x in drv.get("/jobs/mine").json()["items"]}
    assert got == {today, tomorrow, old_open}
    assert later not in got and old_done not in got and too_old not in got
    # не закрытый наряд за прошлые дни помечен красным
    late = next(x for x in drv.get("/jobs/mine").json()["items"] if x["id"] == old_open)
    assert late["late"] is True and late["status"]["tone"] == "error"


def test_driver_without_vehicle_sees_nothing(world, make_client):
    d = make_client("disp_a")
    new_job(world, d)
    # водитель 3 из другого юрлица
    r = make_client("drv3").get("/jobs/mine")
    assert r.status_code == 200 and r.json()["items"] == []
    # общая доска дня водителю недоступна
    assert make_client("drv1").get("/jobs/day").status_code == 403
    assert make_client("drv1").get("/jobs").status_code == 403


# ---------------------------------------------------------------- доска дня, состояния, повтор

def test_day_board_counts_and_stale(world, make_client):
    d = make_client("disp_a")
    a = new_job(world, d, time_from="09:00").json()["id"]
    b = new_job(world, d, vehicle=world.va2, time_from="").json()["id"]
    c_ = new_job(world, d, vehicle=world.va3, time_from="07:00").json()["id"]
    stale = new_job(world, d, job_date=(TODAY - dt.timedelta(days=2)).isoformat()).json()["id"]
    d.post(f"/jobs/{c_}/state", {"state": "cancelled"})
    d.post(f"/jobs/{a}/arrive")
    r = d.get(f"/jobs/day?date={TODAY_ISO}")
    assert r.status_code == 200
    body = r.json()
    assert [x["id"] for x in body["items"]] == [c_, a, b]          # по времени, без времени в конце
    assert body["counts"] == {"total": 3, "planned": 1, "on_site": 1, "done": 0, "cancelled": 1}
    assert body["stale_total"] == 1 and body["stale"][0]["id"] == stale
    assert body["can_create"] is True

    # бухгалтер видит, но ничего не отмечает и не создаёт
    acc = make_client("acc_a")
    j = acc.get(f"/jobs/day?date={TODAY_ISO}").json()
    assert j["can_create"] is False and not any(x["can"]["arrive"] or x["can"]["edit"] for x in j["items"])
    assert acc.post(f"/jobs/{a}/finish", {}).status_code == 403
    # механик и кладовщик наряды не видят
    assert make_client("mech_a").get("/jobs/day").status_code == 403
    assert make_client("store_a").get("/jobs/day").status_code == 403


def test_manual_state_rules(world, make_client):
    d = make_client("disp_a")
    jid = new_job(world, d).json()["id"]
    j = d.post(f"/jobs/{jid}/state", {"state": "on_site"}).json()["job"]
    assert j["state"] == "on_site" and j["arrived_at"]
    arrived = j["arrived_at"]
    j = d.post(f"/jobs/{jid}/state", {"state": "done"}).json()["job"]
    assert j["finished_at"] and j["arrived_at"] == arrived
    j = d.post(f"/jobs/{jid}/state", {"state": "planned"}).json()["job"]
    assert j["arrived_at"] is None and j["finished_at"] is None and j["state"] == "planned"
    assert d.post(f"/jobs/{jid}/state", {"state": "planned"}).json()["changed"] is False
    assert d.post(f"/jobs/{jid}/state", {"state": "bogus"}).status_code == 422
    assert actions("jobs.state") == 3


def test_copy_to_next_day(world, make_client):
    d = make_client("disp_a")
    jid = new_job(world, d, task="Погрузка", time_from="10:00").json()["id"]
    d.post(f"/jobs/{jid}/state", {"state": "done"})
    r = d.post(f"/jobs/{jid}/copy")
    assert r.status_code == 200, r.text
    n = r.json()["job"]
    assert n["id"] != jid and n["job_date"] == (TODAY + dt.timedelta(days=1)).isoformat()
    assert (n["task"], n["time_from"], n["address"], n["state"]) == ("Погрузка", "10:00", "Новосибирск, ул. Ленина, 1", "planned")
    assert make_client("acc_a").post(f"/jobs/{jid}/copy").status_code == 403
    assert actions("jobs.copy") == 1


def test_archive_and_restore_job(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    assert d.post(f"/jobs/{jid}/void", {"reason": "Дубль"}).status_code == 200
    assert drv.post(f"/jobs/{jid}/arrive").status_code in (404, 409)
    assert [x["id"] for x in drv.get("/jobs/mine").json()["items"]] == []
    assert d.post(f"/jobs/{jid}/state", {"state": "done"}).status_code == 409
    assert d.post(f"/jobs/{jid}/restore").status_code == 200
    assert drv.post(f"/jobs/{jid}/arrive").status_code == 200


# ---------------------------------------------------------------- фото

def test_photos_before_after_and_limits(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    r = photos(drv, jid, "before")
    assert r.status_code == 200, r.text
    ph = r.json()["job"]["photos"]
    assert len(ph["before"]) == 1 and ph["after"] == [] and ph["before"][0]["added_by"] == "Первый Тест"
    pid = ph["before"][0]["id"]

    # картинка отдаётся и видна диспетчеру; превью маленькое
    full = d.get(f"/jobs/photos/{pid}")
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    assert d.get(f"/jobs/photos/{pid}?thumb=true").headers["content-type"] == "image/jpeg"
    # водитель другой машины и бухгалтер из чужого юрлица фото не видят
    assert make_client("drv2").get(f"/jobs/photos/{pid}").status_code == 404
    assert make_client("dir_b").get(f"/jobs/photos/{pid}").status_code == 404

    # только картинки: PDF и чужой тип отклоняются, пустой набор тоже
    assert photos(drv, jid, "after", [("акт.pdf", PDF, "application/pdf")]).status_code == 422
    assert photos(drv, jid, "after", []).status_code == 422
    assert photos(drv, jid, "bogus").status_code == 422

    # не больше десяти на этап
    ten = [(f"{i}.png", png((i, 10, 10)), "image/png") for i in range(10)]
    assert photos(drv, jid, "after", ten).status_code == 200
    r = photos(drv, jid, "after")
    assert r.status_code == 422 and r.json()["code"] == "too_many_photos"
    assert actions("jobs.photo.add") == 2


def test_photo_rules_after_done_and_cancel(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    before = photos(drv, jid, "before").json()["job"]["photos"]["before"][0]["id"]
    drv.post(f"/jobs/{jid}/finish", {})
    # выполнен: фото «после» водитель добавить может, «до» уже нет, убрать ничего нельзя
    assert photos(drv, jid, "after").status_code == 200
    r = photos(drv, jid, "before")
    assert r.status_code == 409 and r.json()["code"] == "photos_closed"
    assert drv.post(f"/jobs/photos/{before}/remove", {"reason": "лишнее"}).status_code == 403
    # диспетчер может и то и другое
    assert photos(d, jid, "before").status_code == 200
    assert d.post(f"/jobs/photos/{before}/remove", {"reason": "Не то фото"}).status_code == 200
    # отменённому наряду фото не добавляют никому
    d.post(f"/jobs/{jid}/state", {"state": "cancelled"})
    assert photos(d, jid, "after").status_code == 409 and photos(drv, jid, "after").status_code == 409


def test_removed_photo_is_marked_not_erased(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    pid = photos(drv, jid, "before").json()["job"]["photos"]["before"][0]["id"]
    r = drv.post(f"/jobs/photos/{pid}/remove", {"reason": ""})
    assert r.status_code == 200 and r.json()["job"]["photos"]["before"] == []
    assert d.get(f"/jobs/photos/{pid}").status_code == 404
    assert drv.post(f"/jobs/photos/{pid}/remove", {}).status_code == 404
    with session_factory()() as db:
        ph = db.get(JobPhoto, pid)
        assert ph.removed_at is not None and ph.removed_by_id and ph.remove_reason == "Убрано"
        assert db.get(StoredFile, ph.file_id) is not None          # файл остался
    # после удаления можно снова добавить: считаются только живые фото
    assert photos(drv, jid, "before").status_code == 200


# ---------------------------------------------------------------- права и изоляция

def test_entity_isolation(world, make_client):
    a, b = make_client("disp_a"), make_client("dir_b")
    jid = new_job(world, a).json()["id"]
    assert b.get("/jobs").json()["items"] == []
    assert b.get(f"/jobs/day?date={TODAY_ISO}").json()["items"] == []
    for call in (b.post(f"/jobs/{jid}/state", {"state": "done"}), b.post(f"/jobs/{jid}/copy"), b.patch(f"/jobs/{jid}", {"address": "х"}),
                 b.post(f"/jobs/{jid}/void", {"reason": "чужой"}), b.post(f"/jobs/{jid}/arrive")):
        assert call.status_code == 404, call.text
    # свой наряд в чужой машине: нельзя ни создать, ни перенести
    assert new_job(world, b, vehicle=world.va1).status_code == 422
    own = new_job(world, b, vehicle=world.vb1).json()["id"]
    assert a.get(f"/jobs/day?date={TODAY_ISO}&legal_entity_id={world.B}").status_code == 403
    assert a.post(f"/jobs/{own}/arrive").status_code == 404


def test_driver_loses_access_when_unassigned(world, make_client):
    d, drv, adm = make_client("disp_a"), make_client("drv1"), make_client("admin")
    jid = new_job(world, d).json()["id"]
    assert drv.post(f"/jobs/{jid}/arrive").status_code == 200
    with session_factory()() as db:
        from app.models import VehicleAssignment
        for a in db.scalars(select(VehicleAssignment).where(VehicleAssignment.vehicle_id == world.va1)):
            a.ended_on = TODAY - dt.timedelta(days=1)
        db.commit()
    assert drv.post(f"/jobs/{jid}/finish", {}).status_code == 404
    assert drv.get("/jobs/mine").json()["items"] == []
    assert adm.get(f"/jobs/day?date={TODAY_ISO}").json()["items"][0]["id"] == jid


def test_registry_list_export_and_filters(world, make_client):
    d = make_client("disp_a")
    a = new_job(world, d, address="Склад на Станционной").json()["id"]
    b = new_job(world, d, vehicle=world.va2, address="Карьер").json()["id"]
    d.post(f"/jobs/{a}/state", {"state": "done"})
    r = d.get(f"/jobs?date_from={TODAY_ISO}&date_to={TODAY_ISO}")
    assert r.json()["total"] == 2
    assert {x["id"] for x in d.get("/jobs?state=open").json()["items"]} == {b}
    assert {x["id"] for x in d.get("/jobs?state=done").json()["items"]} == {a}
    assert {x["id"] for x in d.get("/jobs?q=Карьер").json()["items"]} == {b}
    assert {x["id"] for x in d.get("/jobs?q=А002").json()["items"]} == {b}          # поиск по номеру машины
    csv = d.get("/jobs/export.csv")
    assert csv.status_code == 200 and "Склад на Станционной" in csv.text and "Выполнен" in csv.text
    labels = {x["code"]: x["label"] for x in make_client("admin").get("/audit/actions").json()["items"]}
    assert labels["jobs.arrive"] and labels["jobs.photo.add"]


def test_roles_defaults(world, make_client):
    from app.permissions import ROLE_DEFAULTS
    assert "jobs.view_own" in ROLE_DEFAULTS["driver"] and "jobs.act_own" in ROLE_DEFAULTS["driver"] and "jobs.edit" not in ROLE_DEFAULTS["driver"]
    assert "jobs.edit" in ROLE_DEFAULTS["dispatcher"] and "jobs.view" in ROLE_DEFAULTS["accountant"] and "jobs.edit" not in ROLE_DEFAULTS["accountant"]
    assert not [c for c in ROLE_DEFAULTS["mechanic"] + ROLE_DEFAULTS["storekeeper"] if c.startswith("jobs.")]
    assert make_client("dir_a").get("/jobs").status_code == 200
    assert make_client("emp_a").get("/jobs").status_code == 403


def test_dashboard_jobs_block(world, make_client):
    d = make_client("disp_a")
    a = new_job(world, d).json()["id"]
    new_job(world, d, vehicle=world.va2)
    new_job(world, d, job_date=(TODAY - dt.timedelta(days=1)).isoformat())
    d.post(f"/jobs/{a}/state", {"state": "done"})
    assert d.get("/dashboard").json()["jobs"] == {"today": 2, "open": 1, "done": 1, "stale": 1, "own": False}
    # водитель считает только свою технику
    mine = make_client("drv1").get("/dashboard").json()["jobs"]
    assert mine == {"today": 1, "open": 0, "done": 1, "stale": 1, "own": True}
    assert make_client("mech_a").get("/dashboard").json()["jobs"] is None
    # выбранное в переключателе юрлицо сужает счётчики
    assert make_client("admin").get(f"/dashboard?legal_entity_id={world.B}").json()["jobs"]["today"] == 0
