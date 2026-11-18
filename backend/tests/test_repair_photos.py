# -*- coding: utf-8 -*-
"""Фото ремонта: механик фотографирует технику после ремонта и описывает, что сделано, директор нажимает «Подтвердить».
Подтверждённая запись не меняется, пока подтверждение не снято. Всё остаётся в истории, фото и записи не стираются."""
import datetime as dt

from sqlalchemy import select

from app.common import org_today
from app.db import session_factory
from app.models import AuditLog, Notification, RepairResultPhoto, ServiceRequest, User
from tests.test_dossier import PDF, png

TODAY = org_today()


def img(name="после.png", color=(10, 150, 60)):
    return ("files", (name, png(color=color), "image/png"))


def add(c, vehicle, what="Заменили тормозные колодки и прокачали тормоза", files=None, **extra):
    data = {"vehicle_id": str(vehicle), "what": what, **{k: str(v) for k, v in extra.items()}}
    return c.call("POST", "/repair-photos", data=data, files=files if files is not None else [img()])


def uid(login):
    with session_factory()() as db:
        return db.scalar(select(User.id).where(User.login == login))


def titles(login):
    with session_factory()() as db:
        return [n.title for n in db.scalars(select(Notification).where(Notification.user_id == uid(login)).order_by(Notification.id))]


def actions(*names):
    with session_factory()() as db:
        return [a for a in db.scalars(select(AuditLog.action).where(AuditLog.action.in_(names)).order_by(AuditLog.id))]


def request_of(vehicle, entity, title="Тормоза", number=1):
    with session_factory()() as db:
        r = ServiceRequest(legal_entity_id=entity, number=number, vehicle_id=vehicle, title=title, status="in_progress")
        db.add(r)
        db.commit()
        return r.id


# ---------------------------------------------------------------- механик добавляет, директор подтверждает

def test_mechanic_adds_director_confirms_and_both_see_history(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    r = add(mech, world.va1)
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["vehicle"].startswith("А001АА54") and item["photo_count"] == 1 and item["confirmed"] is False
    assert item["repair_date"] == TODAY.isoformat() and item["created_by"] == "Mech_a"
    assert item["can_edit"] is True and item["can_confirm"] is False            # механик правит, но не подтверждает

    assert titles("dir_a") == ["Фото ремонта ждёт подтверждения: А001АА54"]      # директору пришло уведомление
    assert titles("mech_a") == []                                               # автору о своём действии не пишем

    seen = boss.get(f"/repair-photos/{item['id']}").json()["item"]
    assert seen["can_confirm"] is True and seen["photos"][0]["id"] == item["preview"][0]
    assert boss.get(f"/repair-photos/photos/{item['preview'][0]}").status_code == 200
    assert boss.get(f"/repair-photos/photos/{item['preview'][0]}?thumb=1").headers["content-type"] == "image/jpeg"

    ok = boss.post(f"/repair-photos/{item['id']}/confirm")
    assert ok.status_code == 200, ok.text
    done = ok.json()["item"]
    assert done["confirmed"] is True and done["confirmed_by"] == "Dir_a" and done["confirmed_at"]
    assert done["can_confirm"] is False and done["can_edit"] is False and done["can_unconfirm"] is True
    assert titles("mech_a") == ["Ремонт подтверждён: А001АА54"]                   # механик узнаёт, что директор принял

    # в истории механика запись остаётся, и он видит, что подтверждено
    lst = mech.get("/repair-photos").json()
    assert lst["total"] == 1 and lst["items"][0]["confirmed"] is True and lst["waiting"] == 0 and lst["confirmed"] == 1
    assert actions("repair_photos.create", "repair_photos.confirm") == ["repair_photos.create", "repair_photos.confirm"]


def test_mechanic_cannot_confirm_or_archive(make_client, world):
    mech = make_client("mech_a")
    rid = add(mech, world.va1).json()["item"]["id"]
    r = mech.post(f"/repair-photos/{rid}/confirm")
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert mech.post(f"/repair-photos/{rid}/void", {"reason": "лишнее"}).status_code == 403
    assert mech.get(f"/repair-photos/{rid}").json()["item"]["confirmed"] is False


def test_nobody_else_sees_the_section(make_client, world):
    boss = make_client("dir_a")
    rid = add(boss, world.va1).json()["item"]["id"]
    pid = boss.get(f"/repair-photos/{rid}").json()["item"]["photos"][0]["id"]
    for login in ("drv1", "acc_a", "disp_a", "emp_a", "store_a"):
        c = make_client(login)
        assert c.get("/repair-photos").status_code == 403, login
        assert c.get(f"/repair-photos/{rid}").status_code == 404, login
        assert c.get(f"/repair-photos/photos/{pid}").status_code == 404, login
        assert add(c, world.va1).status_code == 403, login
    other = make_client("dir_b")                                                # директор другого юрлица
    assert other.get("/repair-photos").json()["total"] == 0
    assert other.get(f"/repair-photos/{rid}").status_code == 404
    assert other.get(f"/repair-photos/photos/{pid}").status_code == 404
    assert other.post(f"/repair-photos/{rid}/confirm").status_code == 404


def test_default_rights_of_roles(make_client, world):
    for login, expected in (("dir_a", {"view", "create", "edit", "void", "confirm"}), ("mech_a", {"view", "create", "edit"}),
                            ("admin", {"view", "create", "edit", "void", "confirm"}), ("drv1", set()), ("acc_a", set())):
        c = make_client(login)
        granted = {code.split(".")[1] for code in c.me["permissions"] if code.startswith("repair_photos.")}
        assert granted == expected, (login, granted)


# ---------------------------------------------------------------- проверки при добавлении

def test_create_checks(make_client, world):
    mech = make_client("mech_a")
    assert add(mech, world.va1, files=[]).json()["code"] == "no_file"
    assert add(mech, world.va1, files=[("files", ("акт.pdf", PDF, "application/pdf"))]).json()["code"] == "photo_not_image"
    assert add(mech, world.va1, what="   ").json()["code"] == "required"
    assert add(mech, world.va1, what="я" * 2001).json()["code"] == "too_long"
    assert add(mech, world.va1, repair_date=(TODAY + dt.timedelta(days=1)).isoformat()).json()["code"] == "bad_date"
    assert add(mech, world.va1, repair_date="31.02.2026").json()["code"] == "bad_date"
    bad = mech.call("POST", "/repair-photos", data={"vehicle_id": "", "what": "Что-то"}, files=[img()])
    assert bad.status_code == 422 and bad.json()["field"] == "vehicle_id"
    assert add(mech, world.vb1).json()["code"] == "bad_vehicle"                  # техника чужого юрлица
    assert add(mech, 999999).json()["code"] == "bad_vehicle"
    too_many = add(mech, world.va1, files=[img(f"{i}.png") for i in range(11)])
    assert too_many.status_code == 422 and too_many.json()["code"] == "too_many_files"
    assert mech.get("/repair-photos").json()["total"] == 0                        # ни одна неудачная попытка не оставила запись
    assert add(mech, world.va1, repair_date=(TODAY - dt.timedelta(days=3)).isoformat()).json()["item"]["repair_date"] == (TODAY - dt.timedelta(days=3)).isoformat()


def test_link_to_request_of_the_same_vehicle(make_client, world):
    mech = make_client("mech_a")
    own = request_of(world.va1, world.A, "Тормоза")
    foreign = request_of(world.va2, world.A, "Чужая машина", number=2)
    assert add(mech, world.va1, request_id=foreign).json()["code"] == "bad_request"
    assert add(mech, world.va1, request_id="abc").json()["code"] == "bad_request"
    item = add(mech, world.va1, request_id=own).json()["item"]
    assert item["request_id"] == own and item["request_title"] == "Тормоза"
    opts = mech.get(f"/repair-photos/options?vehicle_id={world.va1}").json()
    assert [r["title"] for r in opts["requests"]] == ["Тормоза"]
    assert {v["label"].split(" ")[0] for v in opts["vehicles"]} == {"А001АА54", "А002АА54", "А003АА54"}      # только своё юрлицо
    cleared = mech.patch(f"/repair-photos/{item['id']}", {"clear_request": True}).json()["item"]
    assert cleared["request_id"] is None


# ---------------------------------------------------------------- подтверждённое не меняется

def test_confirmed_record_is_locked_until_confirmation_is_withdrawn(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    item = add(mech, world.va1).json()["item"]
    rid = item["id"]
    assert boss.post(f"/repair-photos/{rid}/confirm").status_code == 200
    assert boss.post(f"/repair-photos/{rid}/confirm").json()["code"] == "already_confirmed"
    for r in (mech.patch(f"/repair-photos/{rid}", {"what": "Другое"}), boss.patch(f"/repair-photos/{rid}", {"what": "Другое"}),
              mech.call("POST", f"/repair-photos/{rid}/photos", files=[img("2.png")]),
              mech.post(f"/repair-photos/photos/{item['preview'][0]}/remove", {"reason": "лишнее"})):
        assert r.status_code == 409 and r.json()["code"] == "locked", r.text
    assert mech.post(f"/repair-photos/{rid}/unconfirm", {}).status_code == 403          # снять подтверждение может только тот, кто подтверждает

    un = boss.post(f"/repair-photos/{rid}/unconfirm", {"reason": "Не та машина на фото"})
    assert un.status_code == 200 and un.json()["item"]["confirmed"] is False
    fixed = mech.patch(f"/repair-photos/{rid}", {"what": "Заменили колодки"}).json()["item"]
    assert fixed["what"] == "Заменили колодки"
    assert boss.post(f"/repair-photos/{rid}/unconfirm", {}).json()["code"] == "not_confirmed"
    with session_factory()() as db:
        row = db.scalars(select(AuditLog).where(AuditLog.action == "repair_photos.unconfirm")).one()
        assert row.details["reason"] == "Не та машина на фото"
    assert actions("repair_photos.update") == ["repair_photos.update"]


# ---------------------------------------------------------------- фото

def test_photos_add_remove_and_never_erased(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    item = add(mech, world.va1).json()["item"]
    rid, first = item["id"], item["preview"][0]
    assert mech.post(f"/repair-photos/photos/{first}/remove", {"reason": "Нечётко"}).json()["code"] == "last_photo"        # без фото запись не остаётся

    more = mech.call("POST", f"/repair-photos/{rid}/photos", files=[img("2.png", (200, 0, 0)), img("3.png", (0, 0, 200))])
    assert more.status_code == 200 and more.json()["item"]["photo_count"] == 3
    removed = mech.post(f"/repair-photos/photos/{first}/remove", {"reason": "Нечётко"})
    assert removed.status_code == 200
    d = removed.json()["item"]
    assert d["photo_count"] == 2 and d["removed"][0]["reason"] == "Нечётко" and d["removed"][0]["removed_by"] == "Mech_a"
    assert boss.get(f"/repair-photos/photos/{first}").status_code == 404                  # убранное фото не открывается
    assert boss.post(f"/repair-photos/photos/{first}/remove", {}).status_code == 404       # и второй раз не убирается
    with session_factory()() as db:
        assert db.get(RepairResultPhoto, first) is not None                              # но в базе остаётся
    assert boss.post(f"/repair-photos/photos/{d['photos'][0]['id']}/remove", {}).json()["item"]["removed"][1]["reason"] == "Убрано"
    assert actions("repair_photos.photo.add", "repair_photos.photo.remove") == ["repair_photos.photo.add", "repair_photos.photo.remove", "repair_photos.photo.remove"]


def test_photo_limit(make_client, world):
    mech = make_client("mech_a")
    rid = add(mech, world.va1, files=[img(f"{i}.png") for i in range(10)]).json()["item"]["id"]
    assert mech.call("POST", f"/repair-photos/{rid}/photos", files=[img(f"b{i}.png") for i in range(10)]).json()["item"]["photo_count"] == 20
    full = mech.call("POST", f"/repair-photos/{rid}/photos", files=[img("лишнее.png")])
    assert full.status_code == 422 and full.json()["code"] == "too_many_photos"
    assert mech.get(f"/repair-photos/{rid}").json()["item"]["can_add_photos"] is False


# ---------------------------------------------------------------- история: список, поиск, архив

def test_history_filters_search_and_pages(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    a = add(mech, world.va1, what="Заменили тормозные колодки", repair_date=(TODAY - dt.timedelta(days=20)).isoformat()).json()["item"]["id"]
    b = add(mech, world.va2, what="Сварили раму кузова", repair_date=(TODAY - dt.timedelta(days=5)).isoformat()).json()["item"]["id"]
    c3 = add(mech, world.va2, what="Поменяли масло в двигателе").json()["item"]["id"]
    boss.post(f"/repair-photos/{b}/confirm")

    lst = boss.get("/repair-photos").json()
    assert [i["id"] for i in lst["items"]] == [c3, b, a]                                  # свежие сверху
    assert (lst["total"], lst["waiting"], lst["confirmed"]) == (3, 2, 1) and lst["can_confirm"] and lst["can_void"]
    assert [i["id"] for i in boss.get("/repair-photos?state=waiting").json()["items"]] == [c3, a]
    assert [i["id"] for i in boss.get("/repair-photos?state=confirmed").json()["items"]] == [b]
    waiting = boss.get("/repair-photos?state=confirmed").json()
    assert (waiting["waiting"], waiting["confirmed"]) == (2, 1)                           # счётчики не зависят от выбранной вкладки
    assert [i["id"] for i in boss.get(f"/repair-photos?vehicle_id={world.va2}").json()["items"]] == [c3, b]
    assert [i["id"] for i in boss.get("/repair-photos?q=сварили").json()["items"]] == [b]
    assert [i["id"] for i in boss.get("/repair-photos?q=А001").json()["items"]] == [a]
    assert [i["id"] for i in boss.get("/repair-photos?q=%25").json()["items"]] == []     # «%» не шаблон
    frm, to = (TODAY - dt.timedelta(days=10)).isoformat(), (TODAY - dt.timedelta(days=1)).isoformat()
    assert [i["id"] for i in boss.get(f"/repair-photos?date_from={frm}&date_to={to}").json()["items"]] == [b]
    p1 = boss.get("/repair-photos?page=1&page_size=2").json()
    assert [i["id"] for i in p1["items"]] == [c3, b] and p1["total"] == 3
    assert [i["id"] for i in boss.get("/repair-photos?page=2&page_size=2").json()["items"]] == [a]
    assert boss.get(f"/repair-photos?legal_entity_id={world.B}").status_code == 403


def test_archive_keeps_the_record_and_can_return_it(make_client, world):
    mech, boss = make_client("mech_a"), make_client("dir_a")
    rid = add(mech, world.va1).json()["item"]["id"]
    boss.post(f"/repair-photos/{rid}/confirm")
    assert boss.post(f"/repair-photos/{rid}/void", {}).json()["code"] == "reason_required"
    gone = boss.post(f"/repair-photos/{rid}/void", {"reason": "Дубль"})
    assert gone.status_code == 200 and gone.json()["item"]["archived"] is True and gone.json()["item"]["archived_by"] == "Dir_a"
    assert boss.get("/repair-photos").json()["total"] == 0
    arch = boss.get("/repair-photos?archived=1").json()
    assert arch["total"] == 1 and arch["items"][0]["archive_reason"] == "Дубль"
    assert boss.post(f"/repair-photos/{rid}/void", {"reason": "ещё"}).json()["code"] == "archived"
    assert mech.patch(f"/repair-photos/{rid}", {"what": "Другое"}).json()["code"] == "locked"      # из архива не правится
    assert boss.post(f"/repair-photos/{rid}/unconfirm", {}).json()["code"] == "locked"
    back = boss.post(f"/repair-photos/{rid}/restore")
    assert back.status_code == 200 and back.json()["item"]["archived"] is False and back.json()["item"]["confirmed"] is True
    assert boss.post(f"/repair-photos/{rid}/restore").json()["code"] == "not_archived"
    assert actions("repair_photos.void", "repair_photos.restore") == ["repair_photos.void", "repair_photos.restore"]


def test_confirm_needs_a_photo_and_a_live_record(make_client, world):
    boss = make_client("dir_a")
    rid = add(boss, world.va1).json()["item"]["id"]
    with session_factory()() as db:                                                    # на случай, если фото в базе всё же нет
        for ph in db.scalars(select(RepairResultPhoto).where(RepairResultPhoto.result_id == rid)):
            ph.removed_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    assert boss.post(f"/repair-photos/{rid}/confirm").json()["code"] == "no_photo"
    assert boss.post("/repair-photos/999999/confirm").status_code == 404
    assert boss.get("/repair-photos/999999").status_code == 404


def test_director_can_add_and_the_other_director_is_not_notified(make_client, world):
    boss = make_client("dir_a")
    add(boss, world.va1)
    assert titles("dir_a") == []                                                       # автор сам себе не пишет
    assert titles("dir_b") == []                                                       # другое юрлицо не видит
