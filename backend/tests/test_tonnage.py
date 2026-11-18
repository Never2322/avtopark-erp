# -*- coding: utf-8 -*-
"""Тоннаж: талоны весов с полигона. Водитель прикладывает только фото и заметку; тонны читаются с фото (уверенно или нет), иначе их вносит
сотрудник. Галочка «Вести тоннаж» на технике, вкладка водителя, права, журнал, отчёт за месяц, выгрузка."""
import datetime as dt
import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.common import org_today
from app.db import session_factory
from app.models import AuditLog, Vehicle, WeighTicket
from app.services import ocr, weigh
from tests.test_dossier import png

FIX = Path(__file__).parent / "fixtures"
TODAY = org_today()
TICKET = """ВЕСОВАЯ КВИТАНЦИЯ № 004512
Дата: 17.11.2026
Брутто: 21 640 кг
Тара: 9 820 кг
Нетто: 11 820 кг"""
needs_ocr = pytest.mark.skipif(not ocr.available(), reason="на этом компьютере нет tesseract: чтение фото не проверить")


@pytest.fixture()
def reads(monkeypatch):
    """Чтение фото подменено: что вернёт «распознавание», задаёт тест (проверка правил не зависит от tesseract)."""
    box = {"text": TICKET}
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "text_from_image", lambda data, scorer=None, enough=0: box["text"])
    return box


def flag(world, make_client, *vehicles, on=True):
    """Включить «Вести тоннаж» на технике, как это делает директор в карточке машины."""
    d = make_client("dir_a")
    for v in vehicles:
        r = d.patch(f"/vehicles/{v}", vehicle_body(d, v, tonnage=on))
        assert r.status_code == 200, r.text


def vehicle_body(c, vid, **kw):
    v = c.get(f"/vehicles/{vid}").json()
    body = {"type_id": v["type"]["id"], "plate": v["plate"], "internal_no": v["internal_no"], "make": v["make"], "model": v["model"], "vin": v["vin"] or "",
            "year": v["year"], "department_id": v["department_id"], "note": v.get("note") or ""}
    body.update(kw)
    return body


def put_ticket(c, vehicle, note="", files=None, client_id=None, **extra):
    body = {"vehicle_id": vehicle, "note": note, **extra}
    if client_id:
        body["client_id"] = client_id
    files = [png()] if files is None else files
    return c.call("POST", "/entries/mine/ticket", data={"data": json.dumps(body)}, files=[("files", ("talon.png", f, "image/png")) for f in files] or None)


def tickets():
    with session_factory()() as db:
        return list(db.scalars(select(WeighTicket).order_by(WeighTicket.id)))


# ---------------------------------------------------------------- разбор текста талона

def test_parse_confirmed_by_gross_minus_tare():
    r = weigh.parse(TICKET)
    assert r.tons == Decimal("11.820") and r.guess is None and r.number == "004512"
    assert "цифры сходятся" in r.note


def test_parse_units_and_layouts():
    cols = weigh.parse("Талон взвешивания №77\nМасса брутто, т 21,640\nМасса тары, т 9,820\nМасса нетто, т 11,820")      # единица стоит перед числом
    assert cols.tons == Decimal("11.820") and cols.number == "77"
    tons = weigh.parse("Брутто 21.64 т\nТара 9.82 т\nНетто 11.82 т")
    assert tons.tons == Decimal("11.820")
    twice = weigh.parse("Нетто: 11 820 кг\nИтого\nнетто 11820 кг")                                              # одно нетто дважды
    assert twice.tons == Decimal("11.820")


def test_parse_does_not_trust_what_it_cannot_check():
    only_net = weigh.parse("Нетто: 6 340 кг")
    assert only_net.tons is None and only_net.guess == Decimal("6.340") and "Сверьте с фото" in only_net.note
    broken = weigh.parse("Брутто 21640 кг\nТара 9820 кг\nНетто 11900 кг")                                       # не сходится на 80 кг
    assert broken.tons is None and broken.guess == Decimal("11.900")
    huge = weigh.parse("Брутто 300000 кг\nТара 9820 кг\nНетто 123456 кг")
    assert huge.tons is None and huge.guess is not None
    nothing = weigh.parse("")
    assert nothing.tons is None and nothing.guess is None and not nothing.seen
    gross_tare = weigh.parse("Брутто 21640 кг\nТара 9820 кг")
    assert gross_tare.tons is None and gross_tare.guess is None and "нетто» не нашла" in gross_tare.note


def test_combine_photos_conflict_gives_no_tons():
    a, b = weigh.parse(TICKET), weigh.parse(TICKET.replace("11 820", "10 000").replace("9 820", "11 640"))
    assert a.tons and b.tons and a.tons != b.tons
    merged = weigh.combine([a, b])
    assert merged.tons is None and merged.guess is not None and "разный вес" in merged.note


# ---------------------------------------------------------------- галочка «Вести тоннаж»

def test_flag_defaults_by_type_and_can_be_changed(world, make_client):
    d = make_client("dir_a")
    with session_factory()() as db:
        from app.models import VehicleType
        types = {t.code: t.id for t in db.scalars(select(VehicleType))}
    base = {"legal_entity_id": world.A, "make": "Т", "model": "Т"}
    waste = d.post("/vehicles", {**base, "type_id": types["truck_waste"], "plate": "М111ММ54"}).json()
    dump = d.post("/vehicles", {**base, "type_id": types["truck_dump"], "plate": "М222ММ54"}).json()
    car = d.post("/vehicles", {**base, "type_id": types["car"], "plate": "М333ММ54"}).json()
    assert waste["tonnage"] is True and dump["tonnage"] is True and car["tonnage"] is False
    r = d.patch(f"/vehicles/{car['id']}", vehicle_body(d, car["id"], tonnage=True))
    assert r.status_code == 200 and r.json()["tonnage"] is True
    # правка без этого поля галочку не трогает
    body = vehicle_body(d, car["id"], note="новая заметка")
    assert d.patch(f"/vehicles/{car['id']}", body).json()["tonnage"] is True
    assert d.patch(f"/vehicles/{car['id']}", vehicle_body(d, car["id"], tonnage=False)).json()["tonnage"] is False


def test_tab_appears_only_for_a_driver_with_a_tonnage_vehicle(world, make_client):
    drv1, drv2 = make_client("drv1"), make_client("drv2")
    assert drv1.get("/auth/me").json()["features"]["tonnage"] is False                # у машины нет галочки
    flag(world, make_client, world.va1)
    assert drv1.get("/auth/me").json()["features"]["tonnage"] is True
    assert drv2.get("/auth/me").json()["features"]["tonnage"] is False                # у его машины галочки нет
    assert drv1.get("/driver/home").json()["can"]["tonnage"] is True
    assert make_client("disp_a").get("/auth/me").json()["features"]["tonnage"] is False  # у диспетчера нет закреплённой машины


# ---------------------------------------------------------------- водитель вносит талон

def test_driver_attaches_photo_and_note_and_tons_are_read(world, make_client, reads):
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    r = put_ticket(drv, world.va1, note="Полигон Северный, вторая ходка")
    assert r.status_code == 201, r.text
    item = r.json()["item"]
    assert item["note"] == "Полигон Северный, вторая ходка" and item["date"] == TODAY.isoformat()
    t = tickets()[0]
    assert t.tons == Decimal("11.820") and t.tons_source == "read" and t.doc_number == "004512" and t.read_at is not None
    assert t.driver_id == world.d1 and t.legal_entity_id == world.A
    mine = drv.get("/tonnage/mine").json()
    assert mine["totals"] == {"trips": 1, "tons": 11.82, "pending": 0}
    row = mine["items"][0]
    assert row["tons"] == 11.82 and row["tons_state"]["text"] == "Прочитан с фото" and "read_note" not in row


def test_driver_cannot_set_tons_date_or_other_fields(world, make_client, reads):
    reads["text"] = "Нетто: 6 340 кг"                                   # программа не уверена
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    r = put_ticket(drv, world.va1, tons="99", tons_source="manual", ticket_date=(TODAY - dt.timedelta(days=2)).isoformat(), doc_number="X", driver_id=world.d2)
    assert r.status_code == 201, r.text
    t = tickets()[0]
    assert t.tons is None and t.tons_source == "" and t.doc_number == "" and t.driver_id == world.d1       # лишнее отброшено, вес не взят из запроса
    assert t.tons_guess == Decimal("6.340") and "Сверьте с фото" in t.read_note
    assert t.ticket_date == TODAY - dt.timedelta(days=2)               # дату телефон ставит сам (при отправке без связи она нужна задним числом)


def test_ticket_needs_a_photo_and_a_tonnage_vehicle(world, make_client, reads):
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    r = put_ticket(drv, world.va1, files=[])
    assert r.status_code == 422 and r.json()["field"] == "files"
    assert put_ticket(make_client("drv2"), world.va2).status_code == 422             # у его машины тоннаж не включён
    assert put_ticket(drv, world.va2).status_code == 422                              # чужая машина
    assert put_ticket(drv, world.vb1).status_code == 422                              # машина другого юрлица
    assert tickets() == []


def test_repeated_send_does_not_create_a_second_ticket(world, make_client, reads):
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    assert put_ticket(drv, world.va1, client_id="abc-1").status_code == 201
    again = put_ticket(drv, world.va1, client_id="abc-1")
    assert again.status_code == 201 and again.json()["duplicate"] is True
    assert len(tickets()) == 1


def test_unreadable_photo_leaves_tons_empty_for_staff(world, make_client, reads):
    reads["text"] = ""
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    assert put_ticket(drv, world.va1).status_code == 201
    t = tickets()[0]
    assert t.tons is None and t.read_at is not None and "не удалось" in t.read_note
    mine = drv.get("/tonnage/mine").json()
    assert mine["totals"] == {"trips": 1, "tons": 0.0, "pending": 1}
    assert mine["items"][0]["tons_state"]["text"] == "Нужно внести"
    # диспетчер видит подсказку и вписывает вес по фото
    d = make_client("disp_a")
    row = d.get("/weigh_tickets?state=none").json()["items"][0]
    assert row["tons_state"]["text"] == "Нужно внести" and "не удалось" in row["read_note"]
    r = d.patch(f"/weigh_tickets/{row['id']}", {"tons": "12,5"})
    assert r.status_code == 200, r.text
    t = tickets()[0]
    assert t.tons == Decimal("12.5") and t.tons_source == "manual" and t.tons_guess is None
    assert d.get("/weigh_tickets?state=none").json()["total"] == 0


def test_read_does_not_overwrite_a_value_typed_by_staff(world, make_client, reads):
    reads["text"] = ""
    flag(world, make_client, world.va1)
    drv, d = make_client("drv1"), make_client("disp_a")
    tid = put_ticket(drv, world.va1).json()["item"]["id"]
    assert d.patch(f"/weigh_tickets/{tid}", {"tons": "10"}).status_code == 200
    reads["text"] = TICKET
    drv.call("POST", f"/entries/ticket/{tid}/photos", files=[("files", ("b.png", png(), "image/png"))])
    t = tickets()[0]
    assert t.tons == Decimal("10") and t.tons_source == "manual"
    assert d.patch(f"/weigh_tickets/{tid}", {"tons": "500"}).status_code == 422          # больше предела за ходку


def test_added_photo_triggers_another_read_while_tons_are_empty(world, make_client, reads):
    reads["text"] = ""
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    tid = put_ticket(drv, world.va1).json()["item"]["id"]
    reads["text"] = TICKET
    r = drv.call("POST", f"/entries/ticket/{tid}/photos", files=[("files", ("b.png", png((10, 10, 10)), "image/png"))])
    assert r.status_code == 200, r.text
    assert tickets()[0].tons == Decimal("11.820")


def test_same_ticket_number_is_flagged_as_a_repeat(world, make_client, reads):
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    put_ticket(drv, world.va1)
    put_ticket(drv, world.va1, files=[png((9, 9, 9))])
    second = tickets()[1]
    assert "возможно, это повтор" in second.read_note
    d = make_client("dir_a")
    rep = d.get("/reports/tonnage").json()
    problems = [e for e in rep["extras"] if e["title"] == "Что проверить"][0]["rows"]
    assert len(problems) == 1 and "повтор" in problems[0][3]


def test_driver_can_remove_a_fresh_ticket_but_not_view_others(world, make_client, reads):
    flag(world, make_client, world.va1, world.va2)
    drv1, drv2 = make_client("drv1"), make_client("drv2")
    tid = put_ticket(drv1, world.va1).json()["item"]["id"]
    assert drv2.get(f"/entries/ticket/{tid}").status_code == 404                      # чужой талон
    assert drv2.get("/tonnage/mine").json()["items"] == []
    pid = drv1.get(f"/entries/ticket/{tid}").json()["item"]["pics"][0]["id"]
    assert drv1.get(f"/entries/photos/{pid}").status_code == 200
    assert drv2.get(f"/entries/photos/{pid}").status_code == 404
    assert make_client("disp_a").get(f"/entries/photos/{pid}").status_code == 200
    assert drv2.post(f"/entries/mine/ticket/{tid}/delete").status_code == 404
    assert drv1.post(f"/entries/mine/ticket/{tid}/delete").status_code == 200
    assert drv1.get("/tonnage/mine").json()["totals"]["trips"] == 0                   # убранный талон не считается


def test_tickets_do_not_leak_into_the_trips_and_fuel_pages(world, make_client, reads):
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    put_ticket(drv, world.va1)
    assert drv.get("/entries/mine").json()["items"] == []
    assert make_client("disp_a").get("/entries/feed").json()["items"] == []


# ---------------------------------------------------------------- права и изоляция юрлиц

def test_staff_rights_and_entity_isolation(world, make_client, reads):
    flag(world, make_client, world.va1)
    put_ticket(make_client("drv1"), world.va1)
    assert make_client("dir_a").get("/weigh_tickets").json()["total"] == 1
    assert make_client("acc_a").get("/weigh_tickets").json()["total"] == 1
    assert make_client("acc_a").post("/weigh_tickets", {"ticket_date": TODAY.isoformat(), "vehicle_id": world.va1, "tons": "5"}).status_code == 403
    assert make_client("mech_a").get("/weigh_tickets").status_code == 403
    assert make_client("drv1").get("/weigh_tickets").status_code == 403
    assert make_client("dir_b").get("/weigh_tickets").json()["total"] == 0           # талон чужого юрлица не виден
    d = make_client("disp_a")
    r = d.post("/weigh_tickets", {"ticket_date": TODAY.isoformat(), "vehicle_id": world.va1, "tons": "7,25"})
    assert r.status_code == 201 and r.json()["tons"] == 7.25
    assert r.json()["driver_id"] == world.d1
    with session_factory()() as db:
        assert db.scalar(select(WeighTicket).where(WeighTicket.tons == Decimal("7.25"))).tons_source == "manual"
    # машина без галочки в журнал не попадёт
    assert d.post("/weigh_tickets", {"ticket_date": TODAY.isoformat(), "vehicle_id": world.va3, "tons": "5"}).status_code == 422


def test_tonnage_endpoint_needs_the_right(world, make_client):
    assert make_client("mech_a").get("/tonnage/mine").status_code == 403
    assert make_client("drv1").get("/tonnage/mine?month=2026-13").status_code == 422
    empty = make_client("drv1").get("/tonnage/mine").json()
    assert empty["vehicles"] == [] and empty["can"]["create"] is False and empty["totals"]["trips"] == 0


def test_every_ticket_read_is_in_the_audit_log(world, make_client, reads):
    flag(world, make_client, world.va1)
    put_ticket(make_client("drv1"), world.va1)
    with session_factory()() as db:
        actions = {a.action for a in db.scalars(select(AuditLog))}
    assert {"weigh_tickets.create", "weigh_tickets.read", "entries.photo.add"} <= actions


# ---------------------------------------------------------------- отчёт за месяц и выгрузка

def seed_month(world, make_client, reads):
    flag(world, make_client, world.va1, world.va2)
    drv1, drv2 = make_client("drv1"), make_client("drv2")
    reads["text"] = TICKET
    put_ticket(drv1, world.va1)                                                         # 11,82 т
    reads["text"] = TICKET.replace("21 640", "30 000").replace("9 820", "13 000").replace("11 820", "17 000").replace("004512", "004513")
    put_ticket(drv1, world.va1, files=[png((1, 2, 3))])                                  # 17 т
    reads["text"] = ""
    put_ticket(drv1, world.va1, files=[png((4, 5, 6))])                                  # без тоннажа
    reads["text"] = TICKET.replace("004512", "009999").replace("11 820", "5 000").replace("21 640", "14 820")
    put_ticket(drv2, world.va2)                                                         # 5 т
    return TODAY.strftime("%Y-%m")


def test_month_report_by_vehicle(world, make_client, reads):
    month = seed_month(world, make_client, reads)
    d = make_client("dir_a")
    rep = d.get(f"/reports/tonnage?month={month}").json()
    assert rep["title"].startswith("Тоннаж за ")
    cols = [c["key"] for c in rep["table"]["columns"]]
    rows = {r[0].split(" · ")[0]: dict(zip(cols, r)) for r in rep["table"]["rows"]}
    a1, a2 = rows["А001АА54"], rows["А002АА54"]
    assert (a1["trips"], a1["tons"], a1["pending"]) == (3, 28.82, 1) and a1["avg"] == 14.41
    assert (a2["trips"], a2["tons"], a2["pending"]) == (1, 5.0, 0)
    assert rep["table"]["totals"][cols.index("tons")] == 33.82 and rep["table"]["totals"][cols.index("trips")] == 4
    assert [c["kind"] for c in rep["table"]["columns"] if c["key"] == "tons"] == ["ton"]
    assert "Внимание: у 1 талонов тоннаж ещё не внесён" in rep["note"]
    titles = [e["title"] for e in rep["extras"]]
    assert titles[:2] == ["По дням", "По водителям"] and "Что проверить" in titles
    by_driver = {r[0]: r for r in [e for e in rep["extras"] if e["title"] == "По водителям"][0]["rows"]}
    assert by_driver["Первый Тест"][1:] == [3, 28.82]
    assert rep["chart"]["values"][0] == 28.82
    # другой месяц пуст, но машины с галочкой в таблице остаются
    old = d.get("/reports/tonnage?month=2020-01").json()
    assert [r[cols.index("trips")] for r in old["table"]["rows"]] == [0, 0]


def test_month_report_ignores_archived_tickets_and_other_entities(world, make_client, reads):
    month = seed_month(world, make_client, reads)
    d = make_client("disp_a")
    first = d.get("/weigh_tickets").json()["items"][-1]
    assert d.post(f"/weigh_tickets/{first['id']}/void", {"reason": "ошибочный талон"}).status_code == 200
    rep = make_client("dir_a").get(f"/reports/tonnage?month={month}").json()
    cols = [c["key"] for c in rep["table"]["columns"]]
    assert rep["table"]["totals"][cols.index("trips")] == 3
    assert make_client("dir_b").get(f"/reports/tonnage?month={month}").json()["table"]["rows"] == []


def test_report_visibility(world, make_client):
    assert make_client("drv1").get("/reports/tonnage").status_code in (403, 404)
    assert make_client("mech_a").get("/reports/tonnage").status_code == 404           # у механика нет права на тоннаж
    assert make_client("acc_a").get("/reports/tonnage").status_code == 200
    assert any(x["name"] == "tonnage" for x in make_client("dir_a").get("/reports").json()["items"])
    assert make_client("dir_a").get("/reports/tonnage?month=bad").status_code == 422


def test_report_exports_to_excel_and_csv(world, make_client, reads):
    month = seed_month(world, make_client, reads)
    d = make_client("dir_a")
    x = d.get(f"/reports/tonnage/export.xlsx?month={month}")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(x.content)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        styles = z.read("xl/styles.xml").decode()
    assert "28.82" in sheet and 'numFmtId="4"' in styles                                # тонны с двумя знаками
    c = d.get(f"/reports/tonnage/export.csv?month={month}")
    assert c.status_code == 200 and "28,82" in c.content.decode("utf-8-sig")


# ---------------------------------------------------------------- настоящее чтение фото (нужен tesseract)

@needs_ocr
@pytest.mark.parametrize("name", ["ticket_sample.png", "ticket_sample_photo.jpg", "ticket_sample_photo_rot90.jpg"])
def test_real_photo_of_a_ticket_is_read(world, make_client, name):
    flag(world, make_client, world.va1)
    drv = make_client("drv1")
    r = drv.call("POST", "/entries/mine/ticket", data={"data": json.dumps({"vehicle_id": world.va1, "note": "ходка"})},
                 files=[("files", (name, (FIX / name).read_bytes(), "image/jpeg" if name.endswith("jpg") else "image/png"))])
    assert r.status_code == 201, r.text
    t = tickets()[0]
    assert t.tons == Decimal("11.820") and t.tons_source == "read" and t.doc_number == "004512"
    assert drv.get("/tonnage/mine").json()["totals"]["tons"] == 11.82


@needs_ocr
def test_a_blank_photo_is_not_guessed(world, make_client):
    flag(world, make_client, world.va1)
    assert put_ticket(make_client("drv1"), world.va1, files=[png((200, 200, 200), size=(600, 400))]).status_code == 201
    t = tickets()[0]
    assert t.tons is None and t.tons_guess is None and t.read_at is not None


def test_without_tesseract_the_ticket_is_saved_and_waits_for_staff(world, make_client, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    flag(world, make_client, world.va1)
    assert put_ticket(make_client("drv1"), world.va1).status_code == 201
    t = tickets()[0]
    assert t.tons is None and "не включено" in t.read_note and t.read_at is not None
