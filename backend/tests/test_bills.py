# -*- coding: utf-8 -*-
"""Счета на ремонт: выписка и загрузка, суммы и НДС, номера, оплата, файлы, чтение PDF, права, юрлица, архив, печать, выгрузка, журнал."""
import datetime as dt
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, RepairBill, RepairBillFile, Vehicle
from app.registry import org_tz

SAMPLE_PDF = (Path(__file__).parent / "fixtures" / "bill_sample.pdf").read_bytes()
EMPTY_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def today() -> dt.date:
    return dt.datetime.now(org_tz()).date()


def png(color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(out, "PNG")
    return out.getvalue()


def create(c, data, files=None):
    return c.call("POST", "/bills", data={"data": json.dumps(data, default=str)}, files=files)


def issued(c, **kw):
    body = {"kind": "issued", "supplier_name": "ООО «Склад»", "bill_date": today().isoformat(),
            "items": [{"name": "Фильтр масляный", "qty": 2, "unit": "шт", "price": "1000,50"}], **kw}
    r = create(c, body)
    assert r.status_code == 201, r.text
    return r.json()


def uploaded(c, files=None, **kw):
    body = {"kind": "uploaded", "number": kw.pop("number", "С-1"), "supplier_name": "ООО «Поставщик»", "bill_date": today().isoformat(),
            "total": "12000,00", **kw}
    r = create(c, body, files)
    assert r.status_code == 201, r.text
    return r.json()


def pdf_file(name="счёт.pdf", data=SAMPLE_PDF):
    return [("files", (name, data, "application/pdf"))]


def err(r, status=422):
    assert r.status_code == status, r.text
    return r.json()


def audit_actions():
    with session_factory()() as db:
        return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]


# ---------------------------------------------------------------- выписка

def test_issue_bill_computes_sums_vat_and_number(make_client, world):
    acc = make_client("acc_a")
    b = issued(acc, vehicle_id=world.va1, basis="Заказ-наряд 7", items=[
        {"name": "Фильтр масляный", "article": "WIX 1", "qty": 2, "unit": "шт", "price": "1000,50"},
        {"name": "Работа по замене", "qty": "1,5", "unit": "ч", "price": 1200}])
    assert b["kind"] == "issued" and b["number"] == "1" and b["legal_entity_id"] == world.A
    assert [i["amount"] for i in b["items"]] == [2001.0, 1800.0] and [i["pos"] for i in b["items"]] == [1, 2]
    assert [i["vat"] for i in b["items"]] == [360.84, 324.59]                       # НДС внутри суммы, копейки по правилу 0,5 вверх
    assert b["total"] == 3801.0 and b["vat"] == 685.43 and b["vat_pct"] == 22 and b["status"] == "unpaid"
    assert b["words"].startswith("Три тысячи восемьсот один рубль")
    assert b["items"][1]["qty_text"] == "1,5" and b["vehicle"].startswith("А001АА54") and b["can"]["print"] is True
    assert b["created_by"] == "Acc_a" and b["files"] == []


def test_vat_rate_changes_totals_and_zero_vat(make_client, world):
    acc = make_client("acc_a")
    a = issued(acc, vat_pct=10, items=[{"name": "Масло", "qty": 1, "price": "110"}])
    assert a["vat"] == 10.0 and a["total"] == 110.0
    z = issued(acc, vat_pct=0, items=[{"name": "Масло", "qty": 1, "price": "110"}])
    assert z["vat"] == 0.0 and z["vat_label"] == "Без НДС"
    changed = acc.patch(f"/bills/{a['id']}", {"vat_pct": 20}).json()               # смена ставки пересчитывает сохранённые строки
    assert changed["vat"] == 18.33 and changed["total"] == 110.0


def test_numbers_are_per_entity_and_year_and_unique(make_client, world):
    acc, boss_b = make_client("acc_a"), make_client("dir_b")
    assert [issued(acc)["number"] for _ in range(2)] == ["1", "2"]
    assert issued(acc, bill_date="2025-03-01")["number"] == "1"                     # в другом году счёт снова с первого номера
    other = issued(boss_b, vehicle_id=world.vb1)
    assert other["number"] == "1" and other["legal_entity_id"] == world.B            # у другого юрлица свои номера
    taken = err(create(acc, {"kind": "issued", "number": "2", "supplier_name": "ООО", "bill_date": today().isoformat(),
                             "items": [{"name": "Х", "qty": 1, "price": 5}]}))
    assert taken["code"] == "number_taken" and taken["field"] == "number"
    arch = issued(acc)
    assert acc.post(f"/bills/{arch['id']}/void", {"reason": "Ошибочный счёт"}).status_code == 200
    again = create(acc, {"kind": "issued", "number": arch["number"], "supplier_name": "ООО", "bill_date": today().isoformat(),
                         "items": [{"name": "Х", "qty": 1, "price": 5}]})
    assert again.status_code == 201                                                  # номер из архива освободился
    assert acc.post(f"/bills/{arch['id']}/restore").status_code == 409              # вернуть уже нельзя: номер занят


def test_issued_validation(make_client, world):
    acc = make_client("acc_a")
    base = {"kind": "issued", "supplier_name": "ООО", "bill_date": today().isoformat()}
    item = {"name": "Фильтр", "qty": 1, "unit": "шт", "price": "10"}

    def bad(field=None, code=None, **kw):
        body = {**base, "items": [item], **kw}
        e = err(create(acc, body))
        if field:
            assert e["field"] == field or field in e.get("fields", {}), e
        if code:
            assert e["code"] == code, e
        return e

    bad("items", "no_items", items=[])
    bad("items", "no_items", items=[{"name": "", "qty": 1, "price": ""}])                                   # пустая строка не считается строкой
    bad("items", "item_name", items=[{"name": "", "qty": 2, "price": "5"}])
    bad("items", "item_name", items=[{"name": "***", "qty": 1, "price": "5"}])
    bad("items", "item_unit", items=[{"name": "Х", "qty": 1, "unit": "пачка", "price": "5"}])
    bad("items", "bad_qty", items=[{"name": "Х", "qty": 0, "price": "5"}])
    bad("items", "bad_qty", items=[{"name": "Х", "qty": "1,2345", "price": "5"}])
    bad("items", "bad_qty", items=[{"name": "Х", "qty": "много", "price": "5"}])
    bad("items", "bad_money", items=[{"name": "Х", "qty": 1, "price": "-5"}])
    bad("items", "bad_money", items=[{"name": "Х", "qty": 1, "price": "5,555"}])
    bad("items", "zero_total", items=[{"name": "Х", "qty": 1, "price": "0"}])
    bad("items", "too_many_items", items=[{"name": f"Деталь {i}", "qty": 1, "price": 1} for i in range(101)])
    bad("items", "item_long", items=[{"name": "Я" * 201, "qty": 1, "price": 5}])
    bad("supplier_name", "supplier_required", supplier_name=" ")
    bad("supplier_inn", "bad_digits", supplier_inn="12345")
    bad("supplier_inn", "bad_digits", supplier_inn="77000000AB")
    bad("supplier_kpp", "bad_digits", supplier_kpp="12")
    bad("bik", "bad_digits", bik="04452500")
    bad("account", "bad_digits", account="4070281000000000000")
    bad("bill_date", "bill_date", bill_date="1999-05-01")
    bad("vat_pct", "bad_vat", vat_pct=13)
    bad("kind", "bad_kind", kind="чужой")
    bad("number", "number", number="N" * 41)
    bad("basis", "too_long", basis="О" * 121)
    bad("request_id", "bad_request", request_id=9999)
    bad("vehicle_id", "bad_vehicle", vehicle_id=9999)
    e = err(create(acc, {**base, "items": [item], "лишнее": 1}))                                              # неизвестные поля не молчат
    assert "лишнее" in e["fields"]
    assert err(create(acc, {"kind": "issued", "supplier_name": "ООО", "items": [item]}))["code"] == "bill_date"
    assert err(create(acc, {**base, "items": [item]}, pdf_file()))["code"] == "files_not_allowed"              # у выписанного нет файла
    assert err(acc.call("POST", "/bills", data={"data": "не json"}))["code"] == "bad_form"
    with session_factory()() as db:
        assert db.query(RepairBill).count() == 0                                                              # ни один неудачный счёт не сохранился


def test_issue_from_request_prefill_and_link(make_client, world):
    mech, acc = make_client("mech_a"), make_client("acc_a")
    req = mech.post("/requests", {"vehicle_id": world.va2, "title": "Течёт радиатор", "parts": [
        {"name": "Радиатор", "qty": 1, "unit": "шт", "article": "R-1"}, {"name": "Патрубок", "qty": 2, "unit": "шт"}]}).json()
    pre = acc.get(f"/bills/prefill?request={req['id']}").json()
    assert pre["vehicle_id"] == world.va2 and pre["basis"] == f"Обращение № {req['number']}" and pre["request_id"] == req["id"]
    assert [(i["name"], i["qty"], i["unit"], i["price"]) for i in pre["items"]] == [("Радиатор", "1", "шт", ""), ("Патрубок", "2", "шт", "")]
    for it in pre["items"]:
        it["price"] = "500"
    b = issued(acc, request_id=req["id"], basis=pre["basis"], items=pre["items"])
    assert b["vehicle_id"] == world.va2 and b["request"]["number"] == req["number"]                             # машина взята из обращения
    detail = acc.get(f"/requests/{req['id']}").json()
    assert detail["bills"]["visible"] and detail["bills"]["total"] == 1500.0 and detail["bills"]["items"][0]["id"] == b["id"]
    assert detail["bills"]["can_create"] is True
    mine = make_client("drv2").get(f"/requests/{req['id']}")
    assert mine.status_code == 404                                                                               # чужое обращение водитель не видит
    own = make_client("drv1").post("/requests", {"vehicle_id": world.va1, "title": "Стук"}).json()
    assert make_client("drv1").get(f"/requests/{own['id']}").json()["bills"] == {"visible": False, "items": [], "total": 0.0, "can_create": False}
    # обращение и машина должны совпадать
    e = err(create(acc, {"kind": "issued", "supplier_name": "ООО", "bill_date": today().isoformat(), "vehicle_id": world.va1,
                         "request_id": req["id"], "items": [{"name": "Х", "qty": 1, "price": 5}]}))
    assert e["code"] == "request_vehicle"
    assert make_client("drv1").get(f"/bills/prefill?request={own['id']}").status_code == 403
    assert make_client("dir_b").get(f"/bills/prefill?request={req['id']}").status_code == 404


# ---------------------------------------------------------------- получен от поставщика

def test_upload_bill_with_pdf_and_default_vat(make_client, world):
    acc = make_client("acc_a")
    b = uploaded(acc, pdf_file(), total="90804,92", vat_pct=22, number="ТС-100")
    assert b["kind"] == "uploaded" and b["total"] == 90804.92 and b["vat"] == 16374.66 and b["items"] == []
    assert len(b["files"]) == 1 and b["files"][0]["name"] == "счёт.pdf" and b["files"][0]["is_image"] is False
    f = acc.get(f"/bills/files/{b['files'][0]['id']}")
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf" and f.content == SAMPLE_PDF
    own_vat = uploaded(acc, number="С-2", total="1000", vat="150,25")                                              # НДС из счёта поставщика
    assert own_vat["vat"] == 150.25


def test_upload_validation(make_client, world):
    acc = make_client("acc_a")
    base = {"kind": "uploaded", "number": "С-9", "supplier_name": "ООО", "bill_date": today().isoformat(), "total": "100"}
    assert err(create(acc, {**base, "total": None}))["field"] == "total"
    assert err(create(acc, {**base, "total": "0"}))["code"] == "bad_money"
    assert err(create(acc, {**base, "total": "-5"}))["code"] == "bad_money"
    assert err(create(acc, {**base, "total": "abc"}))["code"] == "bad_money"
    assert err(create(acc, {**base, "total": "10,555"}))["code"] == "bad_money"
    assert err(create(acc, {**base, "total": "99999999999"}))["code"] == "bad_money"
    assert err(create(acc, {**base, "vat": "200"}))["code"] == "vat_over"
    assert err(create(acc, {**base, "number": ""}))["code"] == "number"
    for bad in (b"just text", b"\x00\x01\x02", b""):
        assert err(create(acc, base, [("files", ("x.txt", bad, "text/plain"))]))["code"] in ("bad_file_type", "empty_file")
    assert err(create(acc, base, [("files", ("x.pdf", b"%PDF-" + b"0" * (11 * 1024 * 1024), "application/pdf"))]))["code"] == "file_too_big"


def test_duplicate_bill_is_refused_but_other_amounts_pass(make_client, world):
    acc = make_client("acc_a")
    uploaded(acc, number="Сч-77", total="5000")
    e = err(create(acc, {"kind": "uploaded", "number": "сч-77", "supplier_name": "ООО", "bill_date": today().isoformat(), "total": "5000"}))
    assert e["code"] == "duplicate_bill" and e["field"] == "number"                                              # регистр не важен
    assert create(acc, {"kind": "uploaded", "number": "Сч-77", "supplier_name": "ООО", "bill_date": today().isoformat(), "total": "5001"}).status_code == 201
    assert create(acc, {"kind": "uploaded", "number": "Сч-77", "supplier_name": "ООО", "bill_date": (today() - dt.timedelta(days=1)).isoformat(),
                        "total": "5000"}).status_code == 201


def test_same_file_twice_and_file_limit(make_client, world):
    acc = make_client("acc_a")
    b = uploaded(acc, [("files", ("1.png", png(), "image/png")), ("files", ("копия.png", png(), "image/png"))])
    assert len(b["files"]) == 1                                                                                   # тот же снимок дважды не добавляется
    r = acc.call("POST", f"/bills/{b['id']}/files", files=[("files", ("1.png", png(), "image/png"))])
    assert r.status_code == 200 and r.json()["added"] == 0
    for i in range(9):
        assert acc.call("POST", f"/bills/{b['id']}/files", files=[("files", (f"{i}.png", png((i, 9, 9)), "image/png"))]).status_code == 200
    r = acc.call("POST", f"/bills/{b['id']}/files", files=[("files", ("лишний.png", png((250, 1, 1)), "image/png"))])
    assert r.status_code == 422 and r.json()["code"] == "too_many_files"
    assert acc.call("POST", f"/bills/{b['id']}/files", files=[]).status_code == 422
    assert acc.get(f"/bills/{b['id']}").json()["can"]["files"] is False                                           # кнопка «приложить» спрятана


def test_supplier_details_come_from_counterparty(make_client, world):
    boss, acc = make_client("dir_a"), make_client("acc_a")
    sup = boss.post("/counterparties", {"name": "ООО Автозапчасти", "kind": "supplier", "inn": "5400123456", "kpp": "540001001", "phone": "+7 913 000",
                                         "bank_name": "Банк", "bik": "044525000", "account": "40702810000000000002"}).json()
    b = uploaded(acc, supplier_id=sup["id"], supplier_name="", number="К-1")
    assert b["supplier_name"] == "ООО Автозапчасти" and b["supplier_inn"] == "5400123456" and b["bik"] == "044525000" and b["supplier_id"] == sup["id"]
    own = uploaded(acc, supplier_id=sup["id"], supplier_name="ООО Другое имя", number="К-2")
    assert own["supplier_name"] == "ООО Другое имя" and own["supplier_inn"] == "5400123456"                      # введённое вручную не затирается
    assert err(create(acc, {"kind": "uploaded", "number": "К-3", "supplier_id": 9999, "bill_date": today().isoformat(), "total": "5"}))["code"] == "bad_supplier"
    boss.post(f"/counterparties/{sup['id']}/void", {"reason": "Закрылись"})
    assert err(create(acc, {"kind": "uploaded", "number": "К-4", "supplier_id": sup["id"], "bill_date": today().isoformat(), "total": "5"}))["code"] == "bad_supplier"
    assert acc.get(f"/bills/{b['id']}").status_code == 200                                                        # старый счёт продолжает открываться


# ---------------------------------------------------------------- чтение счёта поставщика

def test_recognize_real_pdf(make_client, world):
    with session_factory()() as db:
        v = db.get(Vehicle, world.va3)
        v.plate = "А315ВТ154"
        db.commit()
    boss, acc = make_client("dir_a"), make_client("acc_a")
    boss.post("/counterparties", {"name": "ООО Вымышленный склад", "kind": "supplier", "inn": "7700000001"})
    r = acc.call("POST", "/bills/recognize", files={"file": ("счёт.pdf", SAMPLE_PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    out = r.json()
    f = out["found"]
    assert out["readable"] is True and out["how"] == "pdf"
    assert (f["number"], f["bill_date"], f["total"], f["vat"], f["vat_pct"]) == ("ТС-100", "2026-11-05", 90804.92, 16374.66, 22)
    assert f["supplier_inn"] == "7700000001" and f["supplier_kpp"] == "770001001" and f["bik"] == "044525000"
    assert f["account"] == "40702810000000000002" and f["corr_account"] == "30101810000000000001" and f["basis"] == "Заказ-наряд № ЗН-7 от 05.11.2026"
    assert f["vehicle_id"] == world.va3 and f["supplier_id"]
    with session_factory()() as db:
        assert db.query(RepairBill).count() == 0 and db.query(RepairBillFile).count() == 0                        # распознавание ничего не сохраняет
    # подсказка не переходит границу юрлиц: чужие машины не сопоставляются
    other = make_client("dir_b").call("POST", "/bills/recognize", files={"file": ("счёт.pdf", SAMPLE_PDF, "application/pdf")}).json()
    assert "vehicle_id" not in other["found"] and other["found"]["number"] == "ТС-100"


def test_recognize_scan_photo_and_garbage(make_client, world):
    acc = make_client("acc_a")
    r = acc.call("POST", "/bills/recognize", files={"file": ("скан.pdf", EMPTY_PDF, "application/pdf")}).json()
    assert r["readable"] is False and "нет текста" in r["note"]
    r = acc.call("POST", "/bills/recognize", files={"file": ("фото.png", png(), "image/png")}).json()
    assert r["readable"] is False and r["how"] == "photo" and r["found"] == {}
    assert acc.call("POST", "/bills/recognize", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 422
    assert make_client("drv1").call("POST", "/bills/recognize", files={"file": ("a.pdf", SAMPLE_PDF, "application/pdf")}).status_code == 403
    assert make_client("store_a").call("POST", "/bills/recognize", files={"file": ("a.pdf", SAMPLE_PDF, "application/pdf")}).status_code == 403


# ---------------------------------------------------------------- права и юрлица

def test_roles_and_entities(make_client, world):
    acc, mech, boss_b, boss_a, admin = (make_client(x) for x in ("acc_a", "mech_a", "dir_b", "dir_a", "admin"))
    b = issued(acc)
    for login in ("drv1", "disp_a", "emp_a", "store_a"):
        c = make_client(login)
        assert c.get("/bills").status_code == 403 and c.get(f"/bills/{b['id']}").status_code == 403, login
        assert create(c, {"kind": "uploaded", "number": "1", "supplier_name": "Х", "bill_date": today().isoformat(), "total": "5"}).status_code == 403
    assert make_client().get("/bills").status_code == 401
    # механик: видит и вносит счета, но не правит, не оплачивает и не убирает
    assert mech.get(f"/bills/{b['id']}").status_code == 200
    assert uploaded(mech, number="М-1")["created_by"] == "Mech_a"
    assert mech.patch(f"/bills/{b['id']}", {"note": "х"}).status_code == 403
    assert mech.post(f"/bills/{b['id']}/status", {"status": "paid"}).status_code == 403
    assert mech.post(f"/bills/{b['id']}/void", {"reason": "Ошибка"}).status_code == 403
    assert mech.get("/bills/export.csv").status_code == 403
    # бухгалтер и директор — полный набор
    assert acc.patch(f"/bills/{b['id']}", {"note": "уточнено"}).status_code == 200
    assert boss_a.post(f"/bills/{b['id']}/status", {"status": "paid"}).status_code == 200
    # другое юрлицо ничего не видит и не меняет
    assert boss_b.get(f"/bills/{b['id']}").status_code == 404
    assert boss_b.get(f"/bills?legal_entity_id={world.A}").status_code == 403
    assert boss_b.get("/bills?month=all").json()["total"] == 0
    assert boss_b.patch(f"/bills/{b['id']}", {"note": "чужой"}).status_code == 404
    assert boss_b.post(f"/bills/{b['id']}/status", {"status": "unpaid"}).status_code == 404
    assert boss_b.post(f"/bills/{b['id']}/void", {"reason": "чужой счёт"}).status_code == 404
    assert boss_b.get(f"/print/bills/{b['id']}").status_code == 404
    assert err(create(boss_b, {"kind": "uploaded", "number": "Ч-1", "supplier_name": "Х", "bill_date": today().isoformat(), "total": "5",
                               "vehicle_id": world.va1}), 403)["code"] == "forbidden"                            # машина чужого юрлица
    assert admin.get("/bills?month=all").json()["total"] >= 2


def test_entity_must_be_chosen_when_it_cannot_be_guessed(make_client, world):
    admin, acc = make_client("admin"), make_client("acc_a")
    body = {"kind": "uploaded", "number": "А-1", "supplier_name": "Х", "bill_date": today().isoformat(), "total": "5"}
    assert err(create(admin, body))["code"] == "entity_required"
    r = create(admin, {**body, "legal_entity_id": world.B})
    assert r.status_code == 201 and r.json()["legal_entity_id"] == world.B
    assert create(acc, {**body, "number": "А-2"}).json()["legal_entity_id"] == world.A                              # у бухгалтера одно юрлицо
    assert err(create(acc, {**body, "number": "А-3", "legal_entity_id": world.B}), 403)["code"] == "forbidden"
    assert err(create(admin, {**body, "number": "А-4", "legal_entity_id": world.A, "vehicle_id": world.vb1}))["code"] == "other_entity"
    assert err(create(admin, {**body, "number": "А-5", "legal_entity_id": 999}))["code"] == "bad_entity"


# ---------------------------------------------------------------- оплата

def test_payment_status_flow(make_client, world):
    acc, mech = make_client("acc_a"), make_client("mech_a")
    b = uploaded(acc, bill_date=(today() - dt.timedelta(days=3)).isoformat())
    paid = acc.post(f"/bills/{b['id']}/status", {"status": "paid"}).json()
    assert paid["status"] == "paid" and paid["paid_date"] == today().isoformat()
    back = acc.post(f"/bills/{b['id']}/status", {"status": "unpaid"}).json()
    assert back["status"] == "unpaid" and back["paid_date"] is None                                                # дата оплаты не остаётся без оплаты
    when = (today() - dt.timedelta(days=1)).isoformat()
    assert acc.post(f"/bills/{b['id']}/status", {"status": "paid", "paid_date": when}).json()["paid_date"] == when
    assert err(acc.post(f"/bills/{b['id']}/status", {"status": "paid", "paid_date": (today() + dt.timedelta(days=3)).isoformat()}))["code"] == "paid_future"
    assert err(acc.post(f"/bills/{b['id']}/status", {"status": "paid", "paid_date": (today() - dt.timedelta(days=9)).isoformat()}))["code"] == "paid_before"
    assert err(acc.post(f"/bills/{b['id']}/status", {"status": "оплачен"}))["code"] == "bad_status"
    assert acc.post(f"/bills/{b['id']}/status", {"status": "cancelled"}).json()["paid_date"] is None
    assert mech.post(f"/bills/{b['id']}/status", {"status": "paid"}).status_code == 403
    same = acc.post(f"/bills/{b['id']}/status", {"status": "cancelled"})                                            # то же состояние второй раз — без дублей в журнале
    assert same.status_code == 200 and audit_actions().count("bill.status") == 4


def test_cannot_create_paid_without_pay_right_and_paid_needs_date_rules(make_client, world):
    acc, mech = make_client("acc_a"), make_client("mech_a")
    body = {"kind": "uploaded", "number": "О-1", "supplier_name": "Х", "bill_date": today().isoformat(), "total": "5"}
    assert create(mech, {**body, "status": "paid"}).status_code == 403
    ok = create(acc, {**body, "status": "paid"})
    assert ok.status_code == 201 and ok.json()["paid_date"] == today().isoformat()
    assert err(create(acc, {**body, "number": "О-2", "status": "paid", "paid_date": (today() - dt.timedelta(days=5)).isoformat()}))["code"] == "paid_before"
    assert err(create(acc, {**body, "number": "О-3", "status": "bogus"}))["code"] == "bad_status"


# ---------------------------------------------------------------- правка

def test_edit_uploaded_bill_recalculates_vat_only_when_needed(make_client, world):
    acc = make_client("acc_a")
    b = uploaded(acc, total="1000", vat="100")
    assert acc.patch(f"/bills/{b['id']}", {"note": "заметка"}).json()["vat"] == 100.0                               # НДС из счёта не затёрт
    r = acc.patch(f"/bills/{b['id']}", {"total": "2200"}).json()
    assert r["total"] == 2200.0 and r["vat"] == 396.72                                                              # сумма изменилась — НДС пересчитан
    r = acc.patch(f"/bills/{b['id']}", {"vat": "400"}).json()
    assert r["vat"] == 400.0
    assert err(acc.patch(f"/bills/{b['id']}", {"vat": "9999"}))["code"] == "vat_over"
    r = acc.patch(f"/bills/{b['id']}", {"number": "Новый-1", "supplier_name": "ООО Новое", "basis": "Договор 5", "vehicle_id": world.va2}).json()
    assert (r["number"], r["supplier_name"], r["basis"], r["vehicle_id"]) == ("Новый-1", "ООО Новое", "Договор 5", world.va2)
    assert acc.patch(f"/bills/{b['id']}", {"vehicle_id": None}).json()["vehicle_id"] is None                         # машину можно отвязать


def test_edit_rules(make_client, world):
    acc = make_client("acc_a")
    b = issued(acc)
    assert err(acc.patch(f"/bills/{b['id']}", {"kind": "uploaded"}))["code"] == "kind_fixed"
    assert err(acc.patch(f"/bills/{b['id']}", {"legal_entity_id": world.B}))["code"] == "entity_fixed"
    assert err(acc.patch(f"/bills/{b['id']}", {"status": "paid"}))["code"] == "use_status"
    assert err(acc.patch(f"/bills/{b['id']}", {"total": "5"}).__class__ and acc.patch(f"/bills/{b['id']}", {"items": []}))["code"] == "no_items"
    u = uploaded(acc)
    assert err(acc.patch(f"/bills/{u['id']}", {"items": [{"name": "Х", "qty": 1, "price": 5}]}))["code"] == "items_not_allowed"
    new = acc.patch(f"/bills/{b['id']}", {"items": [{"name": "Свеча", "qty": 4, "unit": "шт", "price": "250"}, {"name": "Ремень", "qty": 1, "price": "700"}]}).json()
    assert new["total"] == 1700.0 and [i["name"] for i in new["items"]] == ["Свеча", "Ремень"]                      # строки заменены целиком
    with session_factory()() as db:
        from app.models import RepairBillItem
        assert db.query(RepairBillItem).filter_by(bill_id=b["id"]).count() == 2
    before = len(audit_actions())
    acc.patch(f"/bills/{b['id']}", {"note": new["note"]})                                                           # ничего не изменилось — записи в журнале нет
    assert len(audit_actions()) == before
    upd = [a for a in _audit() if a.action == "bill.update"][-1]
    assert upd.details["changes"]["total"] == {"было": "2001.00", "стало": "1700.00"}
    # дубль при правке
    uploaded(acc, number="Д-1", total="700")
    clash = acc.patch(f"/bills/{u['id']}", {"number": "д-1", "total": "700"})
    assert err(clash)["code"] == "duplicate_bill"


def _audit():
    with session_factory()() as db:
        return list(db.scalars(select(AuditLog).order_by(AuditLog.id)))


# ---------------------------------------------------------------- список, поиск, итоги

def test_list_filters_search_and_totals(make_client, world):
    acc = make_client("acc_a")
    d = today()
    old = (d.replace(day=1) - dt.timedelta(days=2))
    a = uploaded(acc, number="Ф-1", total="1000", vat="100", vehicle_id=world.va1, supplier_name="ООО Альфа")
    b = uploaded(acc, number="Ф-2", total="2000", vehicle_id=world.va2, supplier_name="ИП Бета")
    c = uploaded(acc, number="Ф-3", total="4000", supplier_name="ООО Гамма", bill_date=old.isoformat())
    acc.post(f"/bills/{b['id']}/status", {"status": "paid"})
    cancel = uploaded(acc, number="Ф-4", total="8000", supplier_name="ООО Дельта")
    acc.post(f"/bills/{cancel['id']}/status", {"status": "cancelled"})
    r = acc.get("/bills").json()                                                                                    # по умолчанию текущий месяц
    assert {i["number"] for i in r["items"]} == {"Ф-1", "Ф-2", "Ф-4"} and r["month"] == f"{d.year:04d}-{d.month:02d}"
    assert r["totals"] == {"count": 2, "total": 3000.0, "paid": 2000.0, "unpaid": 1000.0, "vat": 100.0 + 360.66, "cancelled": 1}   # отменённый не в итогах
    assert {i["number"] for i in acc.get("/bills?month=all").json()["items"]} == {"Ф-1", "Ф-2", "Ф-3", "Ф-4"}
    assert [i["number"] for i in acc.get(f"/bills?month={old.year:04d}-{old.month:02d}").json()["items"]] == ["Ф-3"]
    assert [i["number"] for i in acc.get(f"/bills?month=all&vehicle_id={world.va1}").json()["items"]] == ["Ф-1"]
    assert [i["number"] for i in acc.get("/bills?month=all&status=paid").json()["items"]] == ["Ф-2"]
    assert {i["number"] for i in acc.get("/bills?month=all&q=альфа").json()["items"]} == {"Ф-1"}                      # без учёта регистра
    assert {i["number"] for i in acc.get("/bills?month=all&q=а002").json()["items"]} == {"Ф-2"}                       # по госномеру машины
    assert {i["number"] for i in acc.get("/bills?month=all&q=ф-3").json()["items"]} == {"Ф-3"}
    assert acc.get("/bills?month=all&q=%25").json()["total"] == 0                                                     # знак % не шаблон
    assert acc.get("/bills?month=all&kind=issued").json()["total"] == 0
    assert acc.get("/bills?month=all&page_size=2&page=2").json()["items"].__len__() == 2
    first = acc.get("/bills?month=all").json()["items"][0]
    assert first["vehicle"] in ("", "А001АА54 · КАМАЗ 65115", "А002АА54 · КАМАЗ 65115") and first["files"] == 0 and first["legal_entity"] == "ООО «Альфа»"
    for bad in ("month=2026-13", "month=ноябрь", "status=bogus", "kind=x"):
        assert acc.get("/bills?" + bad).status_code == 422, bad
    meta = acc.get("/bills/meta").json()
    assert meta["default_month"] == r["month"] and f"{old.year:04d}-{old.month:02d}" in meta["months"] and meta["can"]["create"] is True
    assert [e["id"] for e in meta["entities"]] == [world.A] and {s["code"] for s in meta["statuses"]} == {"unpaid", "paid", "cancelled"}
    assert make_client("mech_a").get("/bills/meta").json()["can"]["pay"] is False
    assert a["id"] and c["id"]


def test_summary_by_month_and_vehicle(make_client, world):
    acc = make_client("acc_a")
    d = today()
    prev = (d.replace(day=1) - dt.timedelta(days=5))
    uploaded(acc, number="И-1", total="1000", vehicle_id=world.va1, vat_pct=0)
    uploaded(acc, number="И-2", total="3000", vehicle_id=world.va1, bill_date=prev.isoformat())
    x = uploaded(acc, number="И-3", total="500", vehicle_id=world.va2)
    acc.post(f"/bills/{x['id']}/status", {"status": "paid"})
    gone = uploaded(acc, number="И-4", total="9000", vehicle_id=world.va2)
    acc.post(f"/bills/{gone['id']}/status", {"status": "cancelled"})
    arch = uploaded(acc, number="И-5", total="7000")
    acc.post(f"/bills/{arch['id']}/void", {"reason": "Ошибочно внесён"})
    uploaded(acc, number="И-6", total="100")
    by_month = acc.get(f"/bills/summary?group=month&date_from={prev.replace(day=1).isoformat()}&date_to={d.isoformat()}").json()
    assert [(i["key"], i["count"], i["total"]) for i in by_month["items"]] == [(f"{prev.year:04d}-{prev.month:02d}", 1, 3000.0), (f"{d.year:04d}-{d.month:02d}", 3, 1600.0)]
    assert by_month["totals"]["total"] == 4600.0 and by_month["totals"]["paid"] == 500.0
    by_car = acc.get(f"/bills/summary?group=vehicle&date_from={prev.replace(day=1).isoformat()}").json()
    assert [(i["label"].split(" ")[0], i["total"]) for i in by_car["items"]] == [("А001АА54", 4000.0), ("А002АА54", 500.0), ("Без", 100.0)]
    assert acc.get(f"/bills/summary?group=vehicle&vehicle_id={world.va2}&date_from=2000-01-01").json()["totals"]["count"] == 1
    assert acc.get("/bills/summary?group=год").status_code == 422
    assert acc.get("/bills/summary?date_from=вчера").status_code == 422
    assert make_client("dir_b").get("/bills/summary?group=month").json()["items"] == []


# ---------------------------------------------------------------- архив

def test_void_and_restore(make_client, world):
    acc, mech = make_client("acc_a"), make_client("mech_a")
    b = uploaded(acc)
    assert err(acc.post(f"/bills/{b['id']}/void", {"reason": "аб"}))["code"] == "reason_required"
    assert mech.post(f"/bills/{b['id']}/void", {"reason": "Ошибка ввода"}).status_code == 403
    r = acc.post(f"/bills/{b['id']}/void", {"reason": "Внесён дважды"}).json()
    assert r["archived"] is True and r["archive_reason"] == "Внесён дважды" and r["can"]["restore"] and not r["can"]["edit"]
    assert acc.post(f"/bills/{b['id']}/void", {"reason": "Ещё раз"}).status_code == 409
    assert acc.get("/bills?month=all").json()["total"] == 0 and acc.get("/bills?month=all&archived=true").json()["total"] == 1
    assert acc.get("/bills?month=all").json()["totals"]["total"] == 0                                              # архив не входит в итоги
    assert acc.patch(f"/bills/{b['id']}", {"note": "х"}).status_code == 409
    assert acc.post(f"/bills/{b['id']}/status", {"status": "paid"}).status_code == 409
    assert acc.call("POST", f"/bills/{b['id']}/files", files=pdf_file()).status_code == 409
    twin = uploaded(acc, number=b["number"], total="12000,00")                                                    # такой же счёт внесли заново
    assert err(acc.post(f"/bills/{b['id']}/restore"), 409)["code"] == "duplicate_bill"
    acc.post(f"/bills/{twin['id']}/void", {"reason": "Удаляю дубль"})
    ok = acc.post(f"/bills/{b['id']}/restore").json()
    assert ok["archived"] is False and ok["archive_reason"] == ""
    assert acc.post(f"/bills/{b['id']}/restore").status_code == 409
    with session_factory()() as db:
        assert db.get(RepairBill, b["id"]) is not None                                                            # запись никогда не стирается


# ---------------------------------------------------------------- файлы

def test_file_download_remove_and_access(make_client, world):
    acc, boss_b, mech = make_client("acc_a"), make_client("dir_b"), make_client("mech_a")
    b = uploaded(acc, [("files", ("фото счёта.png", png(), "image/png"))])
    fid = b["files"][0]["id"]
    thumb = acc.get(f"/bills/files/{fid}?thumb=true")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert acc.get(f"/bills/files/{fid}").headers["content-type"] == "image/png" and "no-store" in acc.get(f"/bills/files/{fid}").headers["cache-control"]
    assert mech.get(f"/bills/files/{fid}").status_code == 200                                                      # кому виден счёт, тому виден файл
    assert boss_b.get(f"/bills/files/{fid}").status_code == 404
    assert make_client("drv1").get(f"/bills/files/{fid}").status_code in (403, 404)
    assert make_client().get(f"/bills/files/{fid}").status_code == 401
    assert mech.post(f"/bills/files/{fid}/remove", {"reason": "х"}).status_code == 403
    assert boss_b.post(f"/bills/files/{fid}/remove", {"reason": "х"}).status_code == 404
    r = acc.post(f"/bills/files/{fid}/remove", {"reason": "Не тот файл"}).json()
    assert r["files"] == [] and acc.get(f"/bills/files/{fid}").status_code == 404
    with session_factory()() as db:
        row = db.get(RepairBillFile, fid)
        assert row.removed_at and row.remove_reason == "Не тот файл"                                                # сам файл остался в базе
    assert acc.post(f"/bills/files/{fid}/remove", {}).status_code == 404
    assert acc.call("POST", f"/bills/{b['id']}/files", files=[("files", ("новый.png", png((1, 2, 3)), "image/png"))]).json()["files"][0]["name"] == "новый.png"
    i = issued(acc)
    assert acc.call("POST", f"/bills/{i['id']}/files", files=pdf_file()).status_code == 422                       # к выписанному файл не прикладывают


def test_supplier_options_for_bill_entry(make_client, world):
    """Механик вносит счета, но справочник контрагентов ему закрыт: поставщиков для счёта он выбирает отдельным списком (только поставщики)."""
    boss, mech, drv = make_client("dir_a"), make_client("mech_a"), make_client("drv1")
    boss.post("/counterparties", {"name": "ООО Поставщик-1", "kind": "supplier"})
    boss.post("/counterparties", {"name": "ООО Заказчик-1", "kind": "customer"})
    boss.post("/counterparties", {"name": "ООО Оба-1", "kind": "both"})
    assert mech.get("/counterparties").status_code == 403
    names = {x["label"] for x in mech.get("/options/supplier").json()["items"]}
    assert names == {"ООО Поставщик-1", "ООО Оба-1"}
    assert mech.get("/options/counterparty").status_code == 403                                                    # общий справочник по-прежнему закрыт
    assert drv.get("/options/supplier").status_code == 403
    assert make_client().get("/options/supplier").status_code == 401
    assert "ООО Заказчик-1" in {x["label"] for x in boss.get("/options/counterparty").json()["items"]}


# ---------------------------------------------------------------- печать и выгрузка

def test_print_data(make_client, world):
    with session_factory()() as db:
        from app.models import LegalEntity
        ent = db.get(LegalEntity, world.A)
        ent.inn, ent.address = "5400000000", "г. Новосибирск"
        db.commit()
    acc = make_client("acc_a")
    b = issued(acc, vehicle_id=world.va1, supplier_name="ООО Склад", supplier_inn="7700000001", bank_name="Банк", bik="044525000",
               account="40702810000000000002", basis="Заказ-наряд 7")
    d = acc.get(f"/print/bills/{b['id']}").json()
    assert d["kind"] == "repair_bill" and d["number"] == "1" and d["total"] == 2001.0 and d["vat"] == 360.84 and d["buyer"]["inn"] == "5400000000"
    assert d["supplier"]["bik"] == "044525000" and d["items"][0]["qty"] == "2" and d["vehicle"].startswith("А001АА54") and d["missing"] == []
    assert d["words"].startswith("Две тысячи один рубль")
    bare = issued(acc, supplier_name="ИП Без реквизитов")
    assert set(acc.get(f"/print/bills/{bare['id']}").json()["missing"]) == {"банк поставщика", "БИК поставщика", "расчётный счёт поставщика"}
    u = uploaded(acc)
    assert acc.get(f"/print/bills/{u['id']}").status_code == 404
    assert make_client("drv1").get(f"/print/bills/{b['id']}").status_code == 403


def test_export_csv(make_client, world):
    acc, mech = make_client("acc_a"), make_client("mech_a")
    uploaded(acc, number="Э-1", total="1000", vat="100", supplier_name="=ЗЛОЙ", vehicle_id=world.va1)
    gone = uploaded(acc, number="Э-2", total="500")
    acc.post(f"/bills/{gone['id']}/status", {"status": "cancelled"})
    r = acc.get("/bills/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "bills-" in r.headers["content-disposition"]
    rows = [line.split(";") for line in r.text.lstrip("﻿").strip().split("\r\n")]
    assert rows[0][:3] == ["Юрлицо", "№ счёта", "Дата"] and len(rows) == 4
    first = rows[1]
    assert first[1] == "Э-1" and first[3].startswith("'=") and first[8] == "1000,00" and first[9] == "100,00" and first[12] == "получен от поставщика"
    assert rows[3][0].startswith("Итого") and rows[3][8] == "1000,00"                                                # отменённый в итог не входит
    assert mech.get("/bills/export.csv").status_code == 403 and make_client("dir_b").get("/bills/export.csv?month=all").text.count("\r\n") == 2
    assert make_client("dir_b").get(f"/bills/export.csv?legal_entity_id={world.A}").status_code == 403
    assert "bill.export" in audit_actions()


# ---------------------------------------------------------------- журнал

def test_audit_trail_is_complete(make_client, world):
    from app.routers.audit_log import ACTIONS
    acc = make_client("acc_a")
    b = uploaded(acc, pdf_file())
    acc.patch(f"/bills/{b['id']}", {"note": "правка"})
    acc.post(f"/bills/{b['id']}/status", {"status": "paid"})
    acc.call("POST", f"/bills/{b['id']}/files", files=[("files", ("ещё.png", png(), "image/png"))])
    fid = acc.get(f"/bills/{b['id']}").json()["files"][-1]["id"]
    acc.post(f"/bills/files/{fid}/remove", {"reason": "лишний"})
    acc.post(f"/bills/{b['id']}/void", {"reason": "тест журнала"})
    acc.post(f"/bills/{b['id']}/restore")
    acc.get("/bills/export.csv?month=all")
    seen = [a for a in audit_actions() if a.startswith("bill.")]
    assert seen == ["bill.create", "bill.update", "bill.status", "bill.file.add", "bill.file.remove", "bill.void", "bill.restore", "bill.export"]
    assert all(a in ACTIONS for a in seen)
    rows = {a.action: a for a in _audit()}
    assert rows["bill.create"].legal_entity_id == world.A and rows["bill.create"].details["number"] == "С-1" and rows["bill.create"].details["files"] == ["счёт.pdf"]
    assert rows["bill.status"].details["to_status"] == "paid" and rows["bill.void"].details["reason"] == "тест журнала"
    log = make_client("admin").get("/audit?entity_type=bill").json()
    assert any(i["action_label"] == "Создан счёт на ремонт" for i in log["items"])


def test_money_never_loses_kopecks(make_client, world):
    acc = make_client("acc_a")
    b = issued(acc, vat_pct=22, items=[{"name": f"Деталь {i}", "qty": "0,333", "unit": "кг", "price": "10,01"} for i in range(30)])
    assert Decimal(str(b["total"])) == sum((Decimal(str(i["amount"])) for i in b["items"]), Decimal(0))              # итог равен сумме строк
    assert Decimal(str(b["vat"])) == sum((Decimal(str(i["vat"])) for i in b["items"]), Decimal(0))
    assert b["items"][0]["amount"] == 3.33 and b["items"][0]["qty_text"] == "0,333"
    with session_factory()() as db:
        row = db.get(RepairBill, b["id"])
        assert row.total == Decimal("99.90") and isinstance(row.total, Decimal)


@pytest.mark.parametrize("raw,expected", [("1 234,56", "1234.56"), ("1234.5", "1234.50"), ("90804,92 ₽", "90804.92"), ("5 руб.", "5.00"), (10, "10.00")])
def test_money_parser(raw, expected):
    from app.services.bills import to_money
    assert str(to_money(raw)) == expected
