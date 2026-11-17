# -*- coding: utf-8 -*-
"""Всё, что нужно телефону без связи на стороне сервера: время отметок из очереди, повтор фото, кабинет водителя."""
import datetime as dt

from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, Job, JobPhoto
from tests.test_dossier import png
from tests.test_entries import day, fuel_body, set_specs
from tests.test_jobs import new_job, photos
from app.common import org_today

UTC = dt.timezone.utc


def ago(**kw) -> str:
    return (dt.datetime.now(UTC) - dt.timedelta(**kw)).isoformat()


def job_row(jid: int) -> Job:
    with session_factory()() as db:
        j = db.get(Job, jid)
        db.expunge(j)
        return j


def seconds_from(a: dt.datetime, iso: str) -> float:
    return abs((a - dt.datetime.fromisoformat(iso)).total_seconds())


# ---------------------------------------------------------------- время отметок

def test_arrive_uses_phone_time_when_plausible(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    when = ago(hours=2)
    assert drv.post(f"/jobs/{jid}/arrive", {"at": when}).status_code == 200
    j = job_row(jid)
    assert j.state == "on_site" and seconds_from(j.arrived_at, when) < 1
    with session_factory()() as db:
        log = db.scalar(select(AuditLog).where(AuditLog.action == "jobs.arrive", AuditLog.entity_id == jid))
        assert "reported_at" in log.details                     # в журнале видно, что время пришло с телефона


def test_arrive_without_body_and_with_bad_time(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    j1, j2, j3 = (new_job(world, d, time_from=t).json()["id"] for t in ("07:00", "08:00", "09:00"))
    assert drv.call("POST", f"/jobs/{j1}/arrive").status_code == 200                           # без тела, как раньше
    assert drv.post(f"/jobs/{j2}/arrive", {"at": (dt.datetime.now(UTC) + dt.timedelta(hours=3)).isoformat()}).status_code == 200
    assert drv.post(f"/jobs/{j3}/arrive", {"at": ago(days=9)}).status_code == 200
    now = dt.datetime.now(UTC)
    for jid in (j1, j2, j3):                                # будущее, давнее и отсутствие времени: берётся время сервера
        assert abs((now - job_row(jid).arrived_at).total_seconds()) < 30, jid
    assert drv.post(f"/jobs/{j1}/arrive", {"at": "не время"}).status_code == 422


def test_finish_time_never_before_arrival_and_resend_is_harmless(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    arrived = ago(hours=3)
    assert drv.post(f"/jobs/{jid}/arrive", {"at": arrived}).status_code == 200
    assert drv.post(f"/jobs/{jid}/finish", {"at": ago(hours=5), "note": "Вывезли"}).status_code == 200    # раньше приезда
    j = job_row(jid)
    assert j.finished_at == j.arrived_at and j.result_note == "Вывезли"
    # повторная отправка из очереди (ответ потерялся): ничего не меняется и ошибки нет
    r = drv.post(f"/jobs/{jid}/finish", {"at": ago(hours=1)})
    assert r.status_code == 200 and r.json()["changed"] is False
    assert drv.post(f"/jobs/{jid}/arrive", {"at": arrived}).status_code == 409                   # «приехал» после «закончил» уже не нужен

    j2 = new_job(world, d, time_from="10:00").json()["id"]
    fin = ago(minutes=40)
    assert drv.post(f"/jobs/{j2}/finish", {"at": fin}).status_code == 200                       # без «приехал»: приезд = конец
    r2 = job_row(j2)
    assert seconds_from(r2.finished_at, fin) < 1 and r2.arrived_at == r2.finished_at


def test_other_driver_cannot_mark_with_time(world, make_client):
    d = make_client("disp_a")
    jid = new_job(world, d).json()["id"]
    assert make_client("drv2").post(f"/jobs/{jid}/arrive", {"at": ago(minutes=5)}).status_code == 404
    assert job_row(jid).state == "planned"


# ---------------------------------------------------------------- повтор фото

def test_same_photo_twice_is_not_added_again(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    shot = png()
    r = photos(drv, jid, files=[("до.png", shot, "image/png")])
    assert r.status_code == 200 and r.json()["added"] == 1 and r.json()["skipped"] == 0
    r = photos(drv, jid, files=[("до-копия.png", shot, "image/png")])               # тот же файл ушёл повторно
    assert r.status_code == 200 and r.json()["added"] == 0 and r.json()["skipped"] == 1
    assert len(r.json()["job"]["photos"]["before"]) == 1
    # то же фото на другом этапе — это уже другое фото; повтор внутри одной отправки тоже не плодится
    assert photos(drv, jid, stage="after", files=[("а.png", shot, "image/png"), ("б.png", shot, "image/png")]).json()["added"] == 1
    other = png(color=(10, 200, 10))                                                # другие байты: считается новым файлом
    r = photos(drv, jid, files=[("новое.png", other, "image/png")])
    assert r.status_code == 200 and r.json()["added"] == 1 and len(r.json()["job"]["photos"]["before"]) == 2
    with session_factory()() as db:
        assert len(db.scalars(select(JobPhoto).where(JobPhoto.job_id == jid)).all()) == 3


def test_removed_photo_can_be_added_again(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv1")
    jid = new_job(world, d).json()["id"]
    shot = png()
    pid = photos(drv, jid, files=[("до.png", shot, "image/png")]).json()["job"]["photos"]["before"][0]["id"]
    assert drv.post(f"/jobs/photos/{pid}/remove", {"reason": "ошибка"}).status_code == 200
    r = photos(drv, jid, files=[("до.png", shot, "image/png")])
    assert r.json()["added"] == 1 and len(r.json()["job"]["photos"]["before"]) == 1


# ---------------------------------------------------------------- кабинет водителя

def test_driver_home_basics_and_permissions(world, make_client):
    drv = make_client("drv1")
    r = drv.get("/driver/home")
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["has_vehicle"] and [v["plate"] for v in h["vehicles"]] == ["А001АА54"]            # чужая и незакреплённая техника не видна
    v = h["vehicles"][0]
    assert v["status"] == "in_work" and v["current_km"] == 1000.0 and v["requests"] == [] and v["fuel"] is None
    assert h["name"] == "Тест"
    assert h["can"] == {"requests": True, "waybills": True, "trip": True, "fuel": True, "jobs": True, "reading": True, "tonnage": False, "status": True, "hauls": True}
    for login in ("mech_a", "disp_a", "dir_a", "acc_a", "emp_a", "store_a"):               # сотрудникам кабинет водителя не нужен
        assert make_client(login).get("/driver/home").status_code == 403, login


def test_driver_home_requests_and_fuel(world, make_client):
    d, drv1, drv2, adm = make_client("disp_a"), make_client("drv1"), make_client("drv2"), make_client("admin")
    assert drv1.post("/requests", {"vehicle_id": world.va1, "title": "Не заводится", "priority": "high"}).status_code == 201
    assert drv2.post("/requests", {"vehicle_id": world.va2, "title": "Течь масла", "priority": "normal"}).status_code == 201
    assert set_specs(world, adm, world.va1, tank_liters="100", fuel_norm="25").status_code == 200
    assert d.post("/fuel_fills", fuel_body(world, fill_date=day(5), liters="90", odometer=1000)).status_code == 201
    assert d.post("/fuel_fills", fuel_body(world, fill_date=day(1), liters="50", odometer=1250)).status_code == 201     # 20 л/100 км
    v = drv1.get("/driver/home").json()["vehicles"][0]
    assert [x["title"] for x in v["requests"]] == ["Не заводится"] and v["requests"][0]["priority_label"] == "Высокий"
    assert v["fuel"]["per100"] == 20.0 and v["fuel"]["norm"] == 25.0 and v["fuel"]["over_pct"] is None
    assert [x["title"] for x in drv2.get("/driver/home").json()["vehicles"][0]["requests"]] == ["Течь масла"]


def test_driver_home_without_vehicle(world, make_client):
    d, drv = make_client("disp_a"), make_client("drv2")
    aid = d.get(f"/vehicles/{world.va2}/assignments").json()["items"][0]["id"]
    assert d.post(f"/vehicles/{world.va2}/assignments/{aid}/end", {"ended_on": (org_today() - dt.timedelta(days=1)).isoformat()}).status_code == 200
    h = drv.get("/driver/home").json()
    assert h["has_vehicle"] is False and h["vehicles"] == []
