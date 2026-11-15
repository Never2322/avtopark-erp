# -*- coding: utf-8 -*-
"""Загрузка показаний из файла: просмотр не пишет в базу, загрузка идёт теми же правилами, что и ручной ввод, ошибки по строкам,
повтор файла, скачки на проверку, CSV (UTF-8 и Windows-1251) и Excel, права и юрлица."""
import datetime as dt
import io
import zipfile
from decimal import Decimal

from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog, Vehicle, VehicleReading
from app.services import readings_import as imp
from app.services.reminders import org_tz
from app.services.xlsx_read import read_first_sheet, serial_to_date
from app.services.xlsx import xlsx_book
from app.common import org_today

TODAY = org_today()


def d(days_ago: int) -> str:
    return (TODAY - dt.timedelta(days=days_ago)).strftime("%d.%m.%Y")


def csv(*lines, head="Госномер;Дата;Показание;Вид;Примечание") -> bytes:
    return ("\r\n".join([head, *lines]) + "\r\n").encode("utf-8")


def send(c, content: bytes, name="pokazaniya.csv", apply=False, kind="km", entity=None):
    data = {"kind": kind, "apply": "1" if apply else "0"}
    if entity is not None:
        data["legal_entity_id"] = str(entity)
    return c.call("POST", "/readings/import", files={"file": (name, content, "application/octet-stream")}, data=data)


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def readings(vid, kind="km"):
    with session_factory()() as db:
        return list(db.scalars(select(VehicleReading).where(VehicleReading.vehicle_id == vid, VehicleReading.kind == kind).order_by(VehicleReading.id)))


def current_km(vid):
    with session_factory()() as db:
        return db.get(Vehicle, vid).current_km


def by_n(res):
    return {r["n"]: r for r in res["rows"]}


# ---------------------------------------------------------------- просмотр и загрузка

def test_preview_writes_nothing_and_apply_adds(make_client, world):
    c = make_client("dir_a")
    f = csv(f"А001АА54;{d(5)};1100;км;", f"А001АА54;{d(2)};1250,5;км;бортовой компьютер", f"А002АА54;{d(3)};1040;;")
    res = ok(send(c, f))
    assert res["applied"] is False and res["summary"] == {"ok": 3, "review": 0, "duplicate": 0, "error": 0, "total": 3}
    assert len(readings(world.va1)) == 1 and len(readings(world.va2)) == 1                      # в базе ничего не появилось
    assert current_km(world.va1) == Decimal("1000.0")
    res = ok(send(c, f, apply=True))
    assert res["applied"] is True and res["summary"]["ok"] == 3
    rows1 = readings(world.va1)
    assert [float(r.value) for r in rows1] == [1000.0, 1100.0, 1250.5]
    assert all(r.source == "import" for r in rows1[1:]) and "pokazaniya.csv" in rows1[1].note and "бортовой компьютер" in rows1[2].note
    assert current_km(world.va1) == Decimal("1250.5") and current_km(world.va2) == Decimal("1040.0")
    with session_factory()() as db:
        entry = db.scalar(select(AuditLog).where(AuditLog.action == "readings.import"))
    assert entry and entry.details["added"] == 3 and entry.details["file"] == "pokazaniya.csv" and entry.user_login == "dir_a"


def test_preview_equals_apply(make_client, world):
    c = make_client("dir_a")
    f = csv(f"А001АА54;{d(5)};1100;;", f"А001АА54;{d(4)};900;;", "Я999ЯЯ99;01.01.2026;5;;", f"А002АА54;{d(2)};1900;;")
    before = ok(send(c, f))
    after = ok(send(c, f, apply=True))
    strip = lambda res: [(r["n"], r["status"], r["message"]) for r in res["rows"]]            # noqa: E731
    assert strip(before) == strip(after)


def test_repeat_of_the_same_file_is_duplicates(make_client, world):
    c = make_client("dir_a")
    f = csv(f"А001АА54;{d(5)};1100;;", f"А001АА54;{d(2)};1200;;")
    assert ok(send(c, f, apply=True))["summary"]["ok"] == 2
    again = ok(send(c, f, apply=True))
    assert again["summary"] == {"ok": 0, "review": 0, "duplicate": 2, "error": 0, "total": 2}
    assert len(readings(world.va1)) == 3                                                       # стартовое и два из файла


def test_row_errors_are_listed_and_good_rows_still_load(make_client, world):
    c = make_client("dir_a")
    f = csv(
        f"А001АА54;{d(6)};1100;;",                     # 2: хорошая
        f"Я999ЯЯ99;{d(5)};1000;;",                      # 3: нет такой машины
        "А001АА54;31.02.2026;1200;;",                   # 4: неверная дата
        f"А001АА54;{d(4)};много;;",                     # 5: не число
        f"А001АА54;{d(4)};-5;;",                        # 6: отрицательное
        f"А001АА54;{(TODAY + dt.timedelta(days=3)).strftime('%d.%m.%Y')};1300;;",   # 7: в будущем
        f"А001АА54;{d(3)};1050;;",                      # 8: меньше предыдущего (1100)
        f"А001АА54;{d(3)};1200;фунты;",                 # 9: неизвестный вид
        f";{d(3)};1200;;",                              # 10: нет номера
        f"В001ВВ54;{d(3)};1200;;",                      # 11: машина другого юрлица
    )
    res = ok(send(c, f, apply=True))
    rows = by_n(res)
    assert res["summary"]["ok"] == 1 and res["summary"]["error"] == 9 and res["summary"]["total"] == 10
    assert rows[2]["status"] == "ok"
    assert "не найдена" in rows[3]["message"] and "дата" in rows[4]["message"] and "не число" in rows[5]["message"]
    assert "отрицательным" in rows[6]["message"] and "будущем" in rows[7]["message"] and "меньше предыдущего" in rows[8]["message"]
    assert "вид" in rows[9]["message"] and "госномер" in rows[10]["message"] and "не найдена" in rows[11]["message"]
    assert [float(r.value) for r in readings(world.va1)] == [1000.0, 1100.0] and len(readings(world.vb1)) == 1
    assert all(r["status"] != "ok" or r["vehicle_id"] for r in res["rows"])


def test_big_jump_goes_to_review(make_client, world):
    c = make_client("dir_a")
    res = ok(send(c, csv(f"А001АА54;{d(4)};1100;;", f"А001АА54;{d(2)};5000;;"), apply=True))
    assert res["summary"] == {"ok": 1, "review": 1, "duplicate": 0, "error": 0, "total": 2}
    assert "проверку" in by_n(res)[3]["message"]
    last = readings(world.va1)[-1]
    assert last.review_state == "pending" and last.is_anomaly
    assert current_km(world.va1) == Decimal("1100.0")                        # неподтверждённое не считается текущим


def test_repeat_does_not_duplicate_pending_or_skip_rejected(make_client, world):
    c = make_client("dir_a")
    f = csv(f"А001АА54;{d(2)};5000;;")                                                         # скачок: уйдёт на проверку
    assert ok(send(c, f, apply=True))["summary"]["review"] == 1
    again = ok(send(c, f, apply=True))
    assert again["summary"]["duplicate"] == 1 and len(readings(world.va1)) == 2                # вторая запись в очереди не появилась
    pending = readings(world.va1)[-1]
    reviewer = make_client("dir_a")
    assert reviewer.post(f"/readings/{pending.id}/review", {"decision": "reject", "comment": "ошибка в файле"}).status_code == 200
    third = ok(send(c, f, apply=True))                                                         # отклонённое можно загрузить заново
    assert third["summary"]["review"] == 1 and len(readings(world.va1)) == 3


def test_rows_in_any_order_and_plate_variants(make_client, world):
    c = make_client("dir_a")
    f = csv(f"a001 aa54;{d(2)};1300;;", f"А-001-АА-54;{d(6)};1100;;", f"А001АА54;{d(4)};1200;;")      # латиница, пробелы, тире, перемешано
    res = ok(send(c, f, apply=True))
    assert res["summary"]["ok"] == 3, res["rows"]
    assert [float(r.value) for r in readings(world.va1)] == [1000.0, 1100.0, 1200.0, 1300.0]


def test_date_only_is_local_noon(make_client, world):
    ok(send(make_client("dir_a"), csv(f"А001АА54;{d(5)};1100;;", f"А001АА54;{d(3)} 07:30;1150;;"), apply=True))
    first, second = readings(world.va1)[1:]
    assert first.recorded_at.astimezone(org_tz()).strftime("%H:%M") == "12:00"
    assert second.recorded_at.astimezone(org_tz()).strftime("%H:%M") == "07:30"


def test_engine_hours_by_column_and_by_default(make_client, world):
    c = make_client("dir_a")
    res = ok(send(c, csv(f"А001АА54;{d(5)};120;моточасы;", f"А001АА54;{d(4)};1120;км;"), apply=True, kind="km"))
    assert res["summary"]["ok"] == 2
    assert [float(r.value) for r in readings(world.va1, "engine_hours")] == [120.0]
    res = ok(send(c, csv(f"А002АА54;{d(5)};300;;"), apply=True, kind="engine_hours"))
    assert res["summary"]["ok"] == 1 and [float(r.value) for r in readings(world.va2, "engine_hours")] == [300.0]
    assert send(c, csv(f"А002АА54;{d(5)};300;;"), kind="parsecs").status_code == 422


def test_other_names_of_columns_and_title_rows(make_client, world):
    c = make_client("dir_a")
    body = ("Журнал пробега за ноябрь;;\r\n;;\r\nНомер ТС;Дата показания;Пробег, км\r\n"
            f"А001АА54;{d(5)};1 100\r\nА001АА54;{d(3)};1 250,5 км\r\n").encode("utf-8")
    res = ok(send(c, body, apply=True))
    assert res["summary"]["ok"] == 2 and res["columns"]["plate"] == "Номер ТС" and res["columns"]["value"] == "Пробег, км"
    assert [float(r.value) for r in readings(world.va1)] == [1000.0, 1100.0, 1250.5]


def test_windows_1251_comma_and_tab_files(make_client, world):
    c = make_client("dir_a")
    f = f"Госномер,Дата,Показание\r\nА001АА54,{d(5)},1100\r\n".encode("cp1251")
    assert ok(send(c, f))["summary"]["ok"] == 1
    f = f"Госномер\tДата\tПоказание\r\nА001АА54\t{d(5)}\t1100\r\n".encode("cp1251")
    assert ok(send(c, f))["summary"]["ok"] == 1


def test_bad_files(make_client, world):
    c = make_client("dir_a")
    r = send(c, "Имя;Возраст\r\nИван;30\r\n".encode("utf-8"))
    assert r.status_code == 422 and r.json()["code"] == "bad_columns" and "Госномер" in r.json()["message"]
    assert send(c, b"").status_code == 422
    assert send(c, csv(), name="a.csv").status_code == 422                                      # только заголовок
    old = send(c, b"\xd0\xcf\x11\xe0 binary", name="old.xls")
    assert old.status_code == 422 and ".xlsx" in old.json()["message"]
    assert send(c, b"x" * (imp.MAX_BYTES + 1)).status_code == 413
    many = csv(*[f"А001АА54;{d(5)};1100;;"] * (imp.MAX_ROWS + 1))
    assert send(c, many).status_code == 413
    assert send(c, b"PK\x03\x04 not a zip", name="x.xlsx").status_code == 422


def test_archived_vehicle_is_an_error_row(make_client, world):
    with session_factory()() as db:
        v = db.get(Vehicle, world.va3)
        v.archived_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
    res = ok(send(make_client("dir_a"), csv(f"А003АА54;{d(2)};1100;;")))
    assert res["summary"]["error"] == 1 and "архив" in res["rows"][0]["message"]


# ---------------------------------------------------------------- права и юрлица

def test_rights(make_client, world):
    f = csv(f"А001АА54;{d(5)};1100;;")
    for who in ("mech_a", "drv1", "acc_a", "emp_a", "store_a"):
        assert send(make_client(who), f).status_code == 403, who
        assert make_client(who).get("/readings/import/template.csv").status_code == 403, who
    assert ok(send(make_client("disp_a"), f))["summary"]["ok"] == 1
    assert ok(send(make_client("admin", "Tester-2026-Pass"), f))["summary"]["ok"] == 1
    assert send(make_client(), f).status_code in (401, 403)


def test_company_data_stays_separate(make_client, world):
    f = csv(f"А001АА54;{d(5)};1100;;", f"В001ВВ54;{d(5)};1100;;")
    res = ok(send(make_client("dir_b"), f, apply=True))
    assert res["summary"]["ok"] == 1 and res["summary"]["error"] == 1
    assert by_n(res)[2]["status"] == "error" and "не найдена" in by_n(res)[2]["message"] and by_n(res)[3]["status"] == "ok"
    assert len(readings(world.va1)) == 1 and len(readings(world.vb1)) == 2
    r = send(make_client("disp_a"), f, entity=world.B)
    assert r.status_code == 403


def test_archived_twin_with_same_plate_is_ignored(make_client, world):
    with session_factory()() as db:
        db.add(Vehicle(legal_entity_id=world.A, type_id=world.type_id, plate="А001АА54", make="ЗИЛ", model="Старый", archived_at=dt.datetime.now(dt.timezone.utc)))
        db.add(Vehicle(legal_entity_id=world.B, type_id=world.type_id, plate="Е555ЕЕ54", make="ЗИЛ", model="Один"))
        db.add(Vehicle(legal_entity_id=world.B, type_id=world.type_id, plate="Е555ЕЕ54", make="ЗИЛ", model="Два", archived_at=dt.datetime.now(dt.timezone.utc)))
        db.add(Vehicle(legal_entity_id=world.B, type_id=world.type_id, plate="Ж777ЖЖ54", make="ЗИЛ", model="А", archived_at=dt.datetime.now(dt.timezone.utc)))
        db.add(Vehicle(legal_entity_id=world.B, type_id=world.type_id, plate="Ж777ЖЖ54", make="ЗИЛ", model="Б", archived_at=dt.datetime.now(dt.timezone.utc)))
        db.commit()
    boss = make_client("admin", "Tester-2026-Pass")
    res = ok(send(boss, csv(f"А001АА54;{d(5)};1100;;", f"Е555ЕЕ54;{d(5)};100;;", f"Ж777ЖЖ54;{d(5)};100;;"), apply=True))
    rows = by_n(res)
    assert rows[2]["status"] == "ok" and rows[2]["vehicle_id"] == world.va1                    # живая машина, а не архивная с тем же номером
    assert rows[3]["status"] == "ok" and rows[4]["status"] == "error" and "в архиве" in rows[4]["message"]
    assert len(readings(world.va1)) == 2


def test_template(make_client, world):
    r = make_client("dir_a").get("/readings/import/template.csv")
    assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf") and "Госномер;Дата;Показание".encode() in r.content
    res = ok(send(make_client("dir_a"), r.content))                       # образец сам читается без ошибок разбора колонок
    assert res["summary"]["total"] == 3


# ---------------------------------------------------------------- Excel

def make_xlsx(rows, date_cols=(), sst=True) -> bytes:
    """Минимальная книга: даты как числа со стилем даты (так их хранит Excel), текст через общие строки."""
    strings: list[str] = []

    def cell(r, c, v):
        ref = f"{'ABCDEFGH'[c]}{r}"
        if v is None:
            return ""
        if isinstance(v, str):
            strings.append(v)
            return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
        style = ' s="1"' if c in date_cols else ""
        return f'<c r="{ref}"{style}><v>{v}</v></c>'

    body = "".join(f'<row r="{i + 1}">' + "".join(cell(i + 1, j, v) for j, v in enumerate(row)) + "</row>" for i, row in enumerate(rows))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "xl/workbook.xml": f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Лист1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>',
        "xl/worksheets/sheet1.xml": f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>",
        "xl/styles.xml": f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
        "xl/sharedStrings.xml": f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


def serial(day: dt.date) -> int:
    return (day - dt.date(1899, 12, 30)).days


def test_excel_with_real_date_cells(make_client, world):
    rows = [["Госномер", "Дата", "Показание"],
            ["А001АА54", serial(TODAY - dt.timedelta(days=5)), 1100],
            ["А001АА54", serial(TODAY - dt.timedelta(days=3)), 1234.5],
            ["А002АА54", serial(TODAY - dt.timedelta(days=3)), 1010]]
    res = ok(send(make_client("dir_a"), make_xlsx(rows, date_cols=(1,)), name="пробег.xlsx", apply=True))
    assert res["summary"]["ok"] == 3, res["rows"]
    assert [float(r.value) for r in readings(world.va1)] == [1000.0, 1100.0, 1234.5]
    assert readings(world.va1)[1].recorded_at.astimezone(org_tz()).date() == TODAY - dt.timedelta(days=5)


def test_excel_written_by_our_own_exporter_can_be_read_back(make_client, world):
    book = xlsx_book([("Показания", [["Госномер", "Дата", "Показание"], ["А001АА54", d(5), 1100], ["А002АА54", d(5), 1020]])])
    assert ok(send(make_client("dir_a"), book, name="from-report.xlsx"))["summary"]["ok"] == 2


def test_xlsx_reader_details():
    assert serial_to_date(45000) == dt.date(2023, 3, 15)
    assert serial_to_date(45000.5) == dt.datetime(2023, 3, 15, 12, 0)
    rows = read_first_sheet(make_xlsx([["a", 1, None], [None, None, "z"]]))
    assert rows == [["a", 1], [None, None, "z"]]
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><workbook/>')
    import pytest
    from app.services.xlsx_read import XlsxError
    with pytest.raises(XlsxError):
        read_first_sheet(evil.getvalue())


def test_value_and_date_parsers():
    assert imp.parse_value("12 345,67") == Decimal("12345.7") and imp.parse_value("12.345,6") == Decimal("12345.6")
    assert imp.parse_value(152340) == Decimal("152340.0") and imp.parse_value(1.25) == Decimal("1.3")
    assert imp.parse_value("152 340 км") == Decimal("152340.0")
    for bad in ("", "abc", "-1", "1e400", "nan", "99999999"):
        try:
            imp.parse_value(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert imp.parse_date("05.11.2026") == (dt.date(2026, 11, 5), None)
    assert imp.parse_date("2026-11-05T14:30:00") == (dt.date(2026, 11, 5), dt.time(14, 30))
    assert imp.parse_date(dt.date(2026, 1, 2)) == (dt.date(2026, 1, 2), None)
    for bad in ("", "вчера", "31.02.2026", "05.11.1850"):
        try:
            imp.parse_date(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert imp.plate_key("a 001-ac 54") == "А001АС54"
