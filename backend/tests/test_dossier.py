# -*- coding: utf-8 -*-
"""Личные дела: загрузка и выдача сканов, проверка файлов, паспорт как отдельное право, изоляция юрлиц,
неизменность файлов, сроки действия, данные техники, выдача новых прав существующим ролям."""
import datetime as dt
import io
import os

import pytest
from PIL import Image
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from app.bootstrap import bootstrap
from app.db import session_factory
from app.models import AuditLog, DossierDoc, DossierDocFile, Permission, Role, RolePermission, StoredFile
from app.common import org_today


def png(color=(200, 30, 30), size=(64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def jpeg(size=(1200, 900)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (20, 120, 200)).save(out, "JPEG")
    return out.getvalue()


PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def upload(client, url, kind, files, **fields):
    data = {"kind": kind, **fields}
    return client.call("POST", url, data=data, files=[("files", f) for f in files])


def add_employee_doc(client, eid, kind="driver_license", files=None, **fields):
    files = files if files is not None else [("права.png", png(), "image/png")]
    return upload(client, f"/dossier/employees/{eid}/docs", kind, files, **fields)


def add_vehicle_doc(client, vid, kind="sts", files=None, **fields):
    files = files if files is not None else [("стс.pdf", PDF, "application/pdf")]
    return upload(client, f"/dossier/vehicles/{vid}/docs", kind, files, **fields)


# ---------------------------------------------------------------- основной сценарий

def test_driver_file_photo_and_documents(make_client, world):
    c = make_client("admin")
    r = add_employee_doc(c, world.d1, "photo", [("фото.jpg", jpeg(), "image/jpeg")])
    assert r.status_code == 201, r.text
    photo = r.json()
    assert photo["kind"] == "photo" and photo["files"][0]["is_image"] and photo["files"][0]["width"] == 1200
    file_id = photo["files"][0]["file_id"]

    r = add_employee_doc(c, world.d1, "driver_license", doc_number="99 01 123456", issued_on="2021-03-01",
                         valid_until="2031-03-01", issued_by="ГИБДД", title="Права категории B, C")
    assert r.status_code == 201 and r.json()["expiry"] == "ok"
    r = add_employee_doc(c, world.d1, "pts", [("птс.pdf", PDF, "application/pdf"), ("птс2.png", png(), "image/png")])
    assert r.status_code == 201 and len(r.json()["files"]) == 2

    card = c.get(f"/dossier/employees/{world.d1}").json()
    assert card["photo_file_id"] == file_id and card["can_edit"] and card["can_passport"]
    assert [d["kind"] for d in card["documents"]] == ["pts", "driver_license", "photo"]
    assert {k["code"] for k in card["kinds"]} == {"photo", "passport", "driver_license", "sts", "pts", "other"}

    thumb = c.get(f"/dossier/files/{file_id}?thumb=true")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(thumb.content)).size[0] <= 480
    full = c.get(f"/dossier/files/{file_id}")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg" and full.content == jpeg()
    assert "inline" in full.headers["content-disposition"]
    assert c.get(f"/dossier/files/{file_id}?download=true").headers["content-disposition"].startswith("attachment")

    listing = c.get("/dossier/employees").json()
    row = next(i for i in listing["items"] if i["id"] == world.d1)
    assert row["photo_file_id"] == file_id and row["license_until"] == "2031-03-01" and row["summary"]["documents"] == 2


def test_full_view_is_audited_but_thumbnail_is_not(make_client, world):
    c = make_client("admin")
    file_id = add_employee_doc(c, world.d1, "photo", [("a.png", png(), "image/png")]).json()["files"][0]["file_id"]
    c.get(f"/dossier/files/{file_id}?thumb=true")
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "dossier.file.view")) == 0
    c.get(f"/dossier/files/{file_id}")
    c.get(f"/dossier/files/{file_id}?download=true")
    with session_factory()() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.action == "dossier.file.view").order_by(AuditLog.id)).all()
        assert len(rows) == 2 and rows[0].user_login == "admin" and rows[0].entity_type == "employee"
        assert rows[0].details["file_id"] == file_id and rows[1].details["mode"] == "download"
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "dossier.doc.create")) == 1


# ---------------------------------------------------------------- паспорт и права

def test_passport_is_a_separate_right(make_client, world):
    admin = make_client("admin")
    passport = add_employee_doc(admin, world.d1, "passport", [("паспорт.png", png((1, 2, 3)), "image/png")], doc_number="5000 111222").json()
    add_employee_doc(admin, world.d1, "driver_license", valid_until="2030-01-01")
    pfile = passport["files"][0]["file_id"]

    disp = make_client("disp_a")                      # диспетчер видит личные дела, но не паспорта
    card = disp.get(f"/dossier/employees/{world.d1}").json()
    assert card["can_passport"] is False and not card["can_edit"]
    assert [d["kind"] for d in card["documents"]] == ["driver_license"]
    assert "passport" not in {k["code"] for k in card["kinds"]}
    assert disp.get(f"/dossier/files/{pfile}").status_code == 404
    assert disp.get(f"/dossier/files/{pfile}?thumb=true").status_code == 404
    assert "5000 111222" not in disp.get(f"/dossier/employees/{world.d1}").text
    row = next(i for i in disp.get("/dossier/employees").json()["items"] if i["id"] == world.d1)
    assert row["summary"]["documents"] == 1

    director = make_client("dir_a")                   # у директора по умолчанию есть право на паспорта
    assert [d["kind"] for d in director.get(f"/dossier/employees/{world.d1}").json()["documents"]] == ["driver_license", "passport"] \
        or {d["kind"] for d in director.get(f"/dossier/employees/{world.d1}").json()["documents"]} == {"passport", "driver_license"}
    assert director.get(f"/dossier/files/{pfile}").status_code == 200


def test_editing_requires_edit_right_and_passport_needs_both(make_client, world):
    disp = make_client("disp_a")
    assert add_employee_doc(disp, world.d1).status_code == 403            # только просмотр
    with session_factory()() as db:                                        # дадим диспетчеру правку, но без права на паспорта
        perm = db.scalar(select(Permission).where(Permission.code == "employee_files.edit"))
        role = db.scalar(select(Role).where(Role.code == "dispatcher"))
        db.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.commit()
    disp = make_client("disp_a")
    assert add_employee_doc(disp, world.d1).status_code == 201
    r = add_employee_doc(disp, world.d1, "passport")
    assert r.status_code == 403
    admin = make_client("admin")
    pdoc = add_employee_doc(admin, world.d1, "passport").json()
    assert disp.patch(f"/dossier/docs/{pdoc['id']}", {"note": "правка"}).status_code == 404     # паспорт для него невидим


def test_drivers_and_staff_without_rights_see_nothing(make_client, world):
    admin = make_client("admin")
    doc = add_employee_doc(admin, world.d1).json()
    for login in ("drv1", "emp_a", "store_a"):
        c = make_client(login)
        assert c.get("/dossier/employees").status_code == 403
        assert c.get(f"/dossier/employees/{world.d1}").status_code == 403
        assert c.get("/dossier/vehicles").status_code == 403
        assert c.get(f"/dossier/files/{doc['files'][0]['file_id']}").status_code == 404
        assert c.patch(f"/dossier/docs/{doc['id']}", {"note": "x"}).status_code == 404


def test_other_legal_entity_looks_like_not_found(make_client, world):
    admin = make_client("admin")
    own = add_employee_doc(admin, world.d1).json()
    foreign = add_employee_doc(admin, world.d3).json()
    foreign_vehicle = add_vehicle_doc(admin, world.vb1).json()

    a = make_client("dir_a")
    assert a.get(f"/dossier/employees/{world.d3}").status_code == 404
    assert add_employee_doc(a, world.d3).status_code == 404
    assert a.get(f"/dossier/files/{foreign['files'][0]['file_id']}").status_code == 404
    assert a.patch(f"/dossier/docs/{foreign['id']}", {"note": "x"}).status_code == 404
    assert a.post(f"/dossier/docs/{foreign['id']}/archive", {"reason": "проверка"}).status_code == 404
    assert a.get(f"/dossier/vehicles/{world.vb1}").status_code == 404
    assert add_vehicle_doc(a, world.vb1).status_code == 404
    assert a.put(f"/dossier/vehicles/{world.vb1}/specs", {"color": "красный"}).status_code == 404
    assert a.get(f"/dossier/files/{foreign_vehicle['files'][0]['file_id']}").status_code == 404
    assert {i["id"] for i in a.get("/dossier/employees").json()["items"]} == {world.d1, world.d2}
    assert {i["id"] for i in a.get("/dossier/vehicles").json()["items"]} == {world.va1, world.va2, world.va3}
    assert a.get("/dossier/employees?legal_entity_id=%d" % world.B).status_code == 403
    assert a.get(f"/dossier/files/{own['files'][0]['file_id']}").status_code == 200

    b = make_client("dir_b")
    assert b.get(f"/dossier/employees/{world.d3}").status_code == 200
    assert b.get(f"/dossier/employees/{world.d1}").status_code == 404


# ---------------------------------------------------------------- проверка файлов

@pytest.mark.parametrize("name,content,expected", [
    ("скан.jpg", "это не картинка, а текст, переименованный в jpg".encode(), "bad_file_type"),
    ("страница.html", b"<html><script>alert(1)</script></html>", "bad_file_type"),
    ("рисунок.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "bad_file_type"),
    ("пусто.png", b"", "empty_file"),
    ("битый.png", b"\x89PNG\r\n\x1a\n" + "мусор".encode() * 50, "bad_image"),
    ("битый.jpg", b"\xff\xd8\xff" + os.urandom(200), "bad_image"),
])
def test_bad_files_are_rejected(make_client, world, name, content, expected):
    c = make_client("admin")
    r = add_employee_doc(c, world.d1, "driver_license", [(name, content, "image/png")])
    assert r.status_code == 422 and r.json()["code"] == expected and r.json()["field"] == "files"
    with session_factory()() as db:                                          # ничего не записалось
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 0
        assert db.scalar(select(func.count()).select_from(DossierDoc)) == 0


def test_type_comes_from_content_not_from_name_or_browser(make_client, world):
    c = make_client("admin")
    r = add_employee_doc(c, world.d1, "driver_license", [("фото.pdf", png(), "application/pdf")])      # png под видом pdf
    assert r.status_code == 201
    assert r.json()["files"][0]["content_type"] == "image/png"
    fid = r.json()["files"][0]["file_id"]
    assert c.get(f"/dossier/files/{fid}").headers["content-type"] == "image/png"
    assert c.get(f"/dossier/files/{fid}").headers["x-content-type-options"] == "nosniff"


def test_size_limits_and_photo_rules(make_client, world):
    c = make_client("admin")
    big = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024 + 10)
    r = add_employee_doc(c, world.d1, "pts", [("огромный.pdf", big, "application/pdf")])
    assert r.status_code == 422 and r.json()["code"] == "file_too_big"
    r = add_employee_doc(c, world.d1, "photo", [("не-фото.pdf", PDF, "application/pdf")])
    assert r.status_code == 422 and r.json()["code"] == "photo_not_image"
    r = add_employee_doc(c, world.d1, "photo", [])
    assert r.status_code == 422 and r.json()["code"] == "no_file"
    r = add_employee_doc(c, world.d1, "pts", [(f"{i}.png", png((i, i, i)), "image/png") for i in range(11)])
    assert r.status_code == 422 and r.json()["code"] == "too_many_files"
    r = add_employee_doc(c, world.d1, "unknown_kind")
    assert r.status_code == 422 and r.json()["field"] == "kind"
    r = add_employee_doc(c, world.d1, "osago")                  # вид из списка техники сотруднику не подходит
    assert r.status_code == 422 and r.json()["field"] == "kind"
    r = add_vehicle_doc(c, world.va1, "passport")
    assert r.status_code == 422 and r.json()["field"] == "kind"


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_pixel_bomb_is_rejected(make_client, world):
    c = make_client("admin")
    out = io.BytesIO()
    Image.new("1", (9000, 9000)).save(out, "PNG")                # 81 млн пикселей при маленьком файле
    assert len(out.getvalue()) < 1_000_000
    r = add_employee_doc(c, world.d1, "driver_license", [("бомба.png", out.getvalue(), "image/png")])
    assert r.status_code == 422 and r.json()["code"] == "image_too_large"


def test_dates_validation(make_client, world):
    c = make_client("admin")
    r = add_employee_doc(c, world.d1, "driver_license", issued_on="2025-01-01", valid_until="2024-01-01")
    assert r.status_code == 422 and r.json()["field"] == "valid_until"
    r = add_employee_doc(c, world.d1, "driver_license", valid_until="31.12.2030")
    assert r.status_code == 422 and r.json()["field"] == "valid_until"
    r = add_employee_doc(c, world.d1, "photo", [("a.png", png(), "image/png")], valid_until="2030-01-01")
    assert r.status_code == 201 and r.json()["valid_until"] is None            # у фото срока нет


# ---------------------------------------------------------------- сканы не стираются

def test_files_cannot_be_rewritten_or_deleted_even_in_the_database(make_client, world):
    c = make_client("admin")
    doc = add_employee_doc(c, world.d1).json()
    with session_factory()() as db:
        for sql in ("UPDATE stored_files SET data = 'x'::bytea", "DELETE FROM stored_files",
                    "DELETE FROM dossier_doc_files", "UPDATE dossier_doc_files SET file_id = file_id + 1",
                    "UPDATE dossier_doc_files SET doc_id = doc_id + 1"):
            with pytest.raises(DBAPIError):
                db.execute(text(sql))
            db.rollback()
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 1
        assert db.scalar(select(func.count()).select_from(DossierDocFile)) == 1


def test_removing_a_scan_hides_it_but_keeps_the_file(make_client, world):
    c = make_client("admin")
    doc = add_employee_doc(c, world.d1, "pts", [("1.png", png(), "image/png"), ("2.png", png((9, 9, 9)), "image/png")]).json()
    first, second = doc["files"]
    r = c.post(f"/dossier/docs/{doc['id']}/files/{first['link_id']}/remove", {"reason": "не тот документ"})
    assert r.status_code == 200 and len(r.json()["files"]) == 1 and r.json()["removed_files"] == 1
    assert c.get(f"/dossier/files/{first['file_id']}").status_code == 404
    assert c.get(f"/dossier/files/{second['file_id']}").status_code == 200
    assert c.post(f"/dossier/docs/{doc['id']}/files/{first['link_id']}/remove", {"reason": "ещё раз"}).status_code == 404
    assert c.post(f"/dossier/docs/{doc['id']}/files/{second['link_id']}/remove", {"reason": "x"}).status_code == 422   # причина нужна
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(StoredFile)) == 2            # сам файл остался
        link = db.get(DossierDocFile, first["link_id"])
        assert link.removed_at is not None and link.remove_reason == "не тот документ" and link.removed_by_id
        with pytest.raises(DBAPIError):                                                  # и снова «убрать» нельзя
            db.execute(text("UPDATE dossier_doc_files SET removed_at = now(), remove_reason = 'подмена' WHERE id = :i"), {"i": first["link_id"]})
        db.rollback()
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "dossier.file.remove")) == 1


def test_adding_scans_to_an_existing_document(make_client, world):
    c = make_client("admin")
    doc = add_employee_doc(c, world.d1, "pts").json()
    r = c.call("POST", f"/dossier/docs/{doc['id']}/files", files=[("files", ("обратная.png", png((5, 5, 5)), "image/png"))])
    assert r.status_code == 201 and len(r.json()["files"]) == 2
    assert c.call("POST", f"/dossier/docs/{doc['id']}/files", files=[]).status_code == 422
    photo = add_employee_doc(c, world.d1, "photo", [("a.png", png(), "image/png")]).json()
    r = c.call("POST", f"/dossier/docs/{photo['id']}/files", files=[("files", ("b.pdf", PDF, "application/pdf"))])
    assert r.status_code == 422 and r.json()["code"] == "photo_not_image"


# ---------------------------------------------------------------- сроки, архив, правка

def test_expiry_statuses_and_problem_filter(make_client, world):
    c = make_client("admin")
    today = org_today()
    add_employee_doc(c, world.d1, "driver_license", valid_until=(today - dt.timedelta(days=1)).isoformat())
    add_employee_doc(c, world.d2, "driver_license", valid_until=(today + dt.timedelta(days=10)).isoformat())
    ok = add_employee_doc(c, world.d2, "pts", valid_until=(today + dt.timedelta(days=400)).isoformat()).json()
    assert ok["expiry"] == "ok" and ok["days_left"] == 400
    items = {i["id"]: i for i in c.get("/dossier/employees").json()["items"]}
    assert items[world.d1]["summary"]["expired"] == 1 and items[world.d1]["license_expiry"] == "expired"
    assert items[world.d2]["summary"]["expiring"] == 1 and items[world.d2]["license_expiry"] == "expiring"
    flagged = {i["id"] for i in c.get("/dossier/employees?problems=true").json()["items"]}
    assert flagged == {world.d1, world.d2}
    only = {i["id"] for i in c.get(f"/dossier/vehicles?problems=true").json()["items"]}
    assert only == set()
    add_vehicle_doc(c, world.va1, "osago", valid_until=(today - dt.timedelta(days=3)).isoformat())
    assert {i["id"] for i in c.get("/dossier/vehicles?problems=true").json()["items"]} == {world.va1}


def test_archive_restore_and_edit(make_client, world):
    c = make_client("admin")
    doc = add_employee_doc(c, world.d1, "driver_license", valid_until="2020-01-01").json()
    assert c.post(f"/dossier/docs/{doc['id']}/archive", {"reason": "на"}).status_code == 422
    r = c.post(f"/dossier/docs/{doc['id']}/archive", {"reason": "заменено новым удостоверением"})
    assert r.status_code == 200 and r.json()["archived"] and r.json()["archive_reason"].startswith("заменено")
    card = c.get(f"/dossier/employees/{world.d1}").json()
    assert card["documents"] == [] and len(card["archived_documents"]) == 1 and card["summary"]["expired"] == 0   # архивный не считается
    assert c.patch(f"/dossier/docs/{doc['id']}", {"note": "правка"}).status_code == 409
    assert c.post(f"/dossier/docs/{doc['id']}/archive", {"reason": "ещё раз"}).status_code == 409
    assert c.post(f"/dossier/docs/{doc['id']}/restore").status_code == 200
    assert c.post(f"/dossier/docs/{doc['id']}/restore").status_code == 409
    r = c.patch(f"/dossier/docs/{doc['id']}", {"doc_number": "77 77 000111", "valid_until": "2032-05-05", "issued_on": "2022-05-05"})
    assert r.status_code == 200 and r.json()["doc_number"] == "77 77 000111" and r.json()["expiry"] == "ok"
    assert c.patch(f"/dossier/docs/{doc['id']}", {"issued_on": "2022-05-05", "valid_until": "2021-01-01"}).status_code == 422
    with session_factory()() as db:
        actions = [a for (a,) in db.execute(select(AuditLog.action).where(AuditLog.action.like("dossier.%")).order_by(AuditLog.id))]
        assert actions == ["dossier.doc.create", "dossier.doc.archive", "dossier.doc.restore", "dossier.doc.update"]


def test_document_needs_exactly_one_owner(world):
    with session_factory()() as db:
        db.add(DossierDoc(employee_id=world.d1, vehicle_id=world.va1, kind="other"))
        with pytest.raises(DBAPIError):
            db.flush()
        db.rollback()
        db.add(DossierDoc(kind="other"))
        with pytest.raises(DBAPIError):
            db.flush()
        db.rollback()


# ---------------------------------------------------------------- техника

def test_vehicle_dossier_specs_documents_and_history(make_client, world):
    mech = make_client("mech_a")
    r = mech.put(f"/dossier/vehicles/{world.va1}/specs", {"color": "оранжевый", "engine_no": "ДВ-123", "engine_power_hp": 240.5,
                                                          "max_weight_kg": 25000, "fuel_type": "diesel", "ownership": "leasing",
                                                          "owner_name": "ООО «Лизинг»", "registered_on": "2020-06-01"})
    assert r.status_code == 200 and r.json()["color"] == "оранжевый" and r.json()["engine_power_hp"] == 240.5
    assert mech.put(f"/dossier/vehicles/{world.va1}/specs", {"ownership": "подарок"}).status_code == 422
    assert mech.put(f"/dossier/vehicles/{world.va1}/specs", {"fuel_type": "вода"}).status_code == 422
    assert mech.put(f"/dossier/vehicles/{world.va1}/specs", {"max_weight_kg": -5}).status_code == 422

    r = add_vehicle_doc(mech, world.va1, "photo", [("машина.jpg", jpeg(), "image/jpeg")])
    assert r.status_code == 201
    r = add_vehicle_doc(mech, world.va1, "osago", doc_number="ХХХ 0123456789", valid_until="2027-01-01", issued_by="Страховая")
    assert r.status_code == 201

    card = mech.get(f"/dossier/vehicles/{world.va1}").json()
    assert card["can_edit"] and card["photo_file_id"] and card["specs"]["ownership"] == "leasing"
    assert card["vehicle"]["plate"] == "А001АА54" and {d["kind"] for d in card["documents"]} == {"photo", "osago"}
    assert {"drivers", "readings", "statuses"} <= set(card["history"])             # у механика есть право смотреть технику
    assert card["history"]["drivers"][0]["name"].startswith("Первый")
    assert card["history"]["readings"][0]["value"] == 1000.0
    assert {o["code"] for o in card["options"]["ownership"]} == {"own", "leasing", "rent"}
    assert mech.get(f"/dossier/files/{card['photo_file_id']}?thumb=true").status_code == 200

    items = {i["id"]: i for i in mech.get("/dossier/vehicles").json()["items"]}
    assert items[world.va1]["photo_file_id"] == card["photo_file_id"] and items[world.va1]["summary"]["documents"] == 1
    assert items[world.va2]["photo_file_id"] is None
    assert mech.get("/dossier/vehicles?q=А002").json()["total"] == 1


def test_vehicle_dossier_read_only_roles_and_history_visibility(make_client, world):
    admin = make_client("admin")
    add_vehicle_doc(admin, world.va1, "sts")
    acc = make_client("acc_a")                    # бухгалтер: может смотреть личные дела техники, менять нельзя
    card = acc.get(f"/dossier/vehicles/{world.va1}").json()
    assert card["can_edit"] is False and len(card["documents"]) == 1
    assert add_vehicle_doc(acc, world.va1).status_code == 403
    assert acc.put(f"/dossier/vehicles/{world.va1}/specs", {"color": "x"}).status_code == 403
    disp = make_client("disp_a")
    assert disp.patch(f"/dossier/docs/{card['documents'][0]['id']}", {"note": "x"}).status_code == 403
    # без права смотреть технику чужие истории не показываем: убираем у бухгалтера просмотр техники и обращений
    with session_factory()() as db:
        role = db.scalar(select(Role).where(Role.code == "accountant"))
        ids = db.scalars(select(Permission.id).where(Permission.code.in_(("vehicles.view", "requests.view")))).all()
        db.execute(delete(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id.in_(ids)))
        db.commit()
    acc = make_client("acc_a")
    assert acc.get(f"/dossier/vehicles/{world.va1}").json()["history"] == {}


def test_vehicle_without_specs_returns_empty_values(make_client, world):
    card = make_client("admin").get(f"/dossier/vehicles/{world.va3}").json()
    assert card["specs"]["color"] == "" and card["specs"]["engine_power_hp"] is None and card["documents"] == []


def test_archived_vehicles_are_listed_only_on_request(make_client, world):
    admin = make_client("admin")
    assert admin.post(f"/vehicles/{world.va3}/archive", {}).status_code in (200, 409, 422)
    with session_factory()() as db:
        from app.models import Vehicle
        v = db.get(Vehicle, world.va3)
        v.archived_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    assert world.va3 not in {i["id"] for i in admin.get("/dossier/vehicles").json()["items"]}
    assert world.va3 in {i["id"] for i in admin.get("/dossier/vehicles?archived=true").json()["items"]}


# ---------------------------------------------------------------- права при обновлении программы

NEW_PERMS = ("employee_files.view", "employee_files.view_passport", "employee_files.edit", "vehicle_files.view", "vehicle_files.edit")


def role_codes(db, role_code):
    return set(db.scalars(select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
                          .join(Role, Role.id == RolePermission.role_id).where(Role.code == role_code)))


def test_new_permissions_reach_existing_roles_once(world):
    """Обновление программы: право появилось, а роли уже есть. По умолчанию его получают нужные роли, но только один раз."""
    with session_factory()() as db:
        ids = db.scalars(select(Permission.id).where(Permission.code.in_(NEW_PERMS))).all()
        db.execute(delete(RolePermission).where(RolePermission.permission_id.in_(ids)))
        db.execute(delete(Permission).where(Permission.id.in_(ids)))
        db.commit()
        assert not role_codes(db, "director") & set(NEW_PERMS)
        bootstrap(db)
        assert set(NEW_PERMS) <= role_codes(db, "director") and set(NEW_PERMS) <= role_codes(db, "admin")
        assert {"employee_files.view", "vehicle_files.view"} <= role_codes(db, "dispatcher")
        assert "employee_files.view_passport" not in role_codes(db, "dispatcher")
        assert "vehicle_files.edit" in role_codes(db, "mechanic") and "vehicle_files.view" in role_codes(db, "accountant")
        assert not role_codes(db, "driver") & set(NEW_PERMS) and not role_codes(db, "employee") & set(NEW_PERMS)
        # администратор снял право у бухгалтера: при следующем старте оно не возвращается
        role = db.scalar(select(Role).where(Role.code == "accountant"))
        perm = db.scalar(select(Permission).where(Permission.code == "vehicle_files.view"))
        db.execute(delete(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == perm.id))
        db.commit()
        bootstrap(db)
        assert "vehicle_files.view" not in role_codes(db, "accountant")


def test_audit_labels_exist_for_new_actions(make_client, world):
    admin = make_client("admin")
    labels = {a["code"] for a in admin.get("/audit/actions").json()["items"]}
    assert {"dossier.doc.create", "dossier.file.view", "dossier.file.remove", "dossier.doc.archive", "vehicle.specs"} <= labels


# ---------------------------------------------------------------- копии

def test_scans_survive_backup_and_restore_byte_for_byte(make_client, world):
    """Сканы лежат в базе, поэтому попадают в резервную копию. Разворачиваем настоящую копию и сверяем каждый байт."""
    from pathlib import Path
    from sqlalchemy import create_engine
    from app.services import backup
    from tests.conftest import ADMIN_URL, BACKUPS

    admin = make_client("admin")
    original = png((10, 200, 30), (80, 60))
    add_employee_doc(admin, world.d1, "pts", [("птс.png", original, "image/png")], doc_number="77 УК 123456")
    r = admin.post("/backups")
    assert r.status_code == 202
    info = admin.get(f"/backups/{r.json()['id']}").json()
    assert info["status"] == "ok" and info["restore_check"]["ok"] is True
    dump = next(Path(BACKUPS).glob("erp_*.dump"))

    temp, url = "erp_scan_check", backup._url()
    eng = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{temp}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{temp}"'))
        backup._run([backup._bin("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error", "-d", temp,
                     *backup._conn_args(url)[:6], str(dump)], backup._env(url))
        check = create_engine(ADMIN_URL.rsplit("/", 1)[0] + "/" + temp)
        with check.connect() as c:
            data, thumb, sha = c.execute(text("SELECT data, thumb, sha256 FROM stored_files")).one()
            number = c.execute(text("SELECT doc_number FROM dossier_docs")).scalar_one()
        check.dispose()
        assert bytes(data) == original and thumb and len(sha) == 64 and number == "77 УК 123456"
    finally:
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{temp}" WITH (FORCE)'))
        eng.dispose()
