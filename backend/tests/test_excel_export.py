# -*- coding: utf-8 -*-
"""Выгрузка в Excel: настоящий .xlsx с цветной шапкой и нормальными ячейками, в файле только данные и названия столбцов
(без значков, числа фото и повторов соседних колонок с экрана), CSV остаётся как был."""
import datetime as dt
import io
import re
import zipfile

import pytest

from app.common import org_today
from app.services.xlsx import PALETTE, serial, xlsx_book
from app.services.xlsx_read import read_first_sheet
from tests.test_registry import d, fine
from tests.test_trade import customer, order

TODAY = org_today()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def parts(data: bytes) -> tuple[str, str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    return z.read("xl/worksheets/sheet1.xml").decode(), z.read("xl/styles.xml").decode()


def cell_xf(sheet: str, styles: str, ref: str) -> str:
    idx = int(re.search(r'<c r="%s" s="(\d+)"' % ref, sheet).group(1))
    return re.findall(r"<xf [^>]*>.*?</xf>", re.search(r"<cellXfs.*?</cellXfs>", styles, re.S).group(0), re.S)[idx]


def fill_of(styles: str, xf: str) -> str:
    fills = re.findall(r"<fill>.*?</fill>", re.search(r"<fills.*?</fills>", styles, re.S).group(0), re.S)
    return fills[int(re.search(r'fillId="(\d+)"', xf).group(1))]


def test_writer_colours_formats_and_filter():
    rows = [["Дата", "Сумма, ₽", "Тонн", "Заметка"],
            [dt.date(2026, 11, 3), 1250.5, 1.25, "первая"],
            [dt.datetime(2026, 11, 4, 14, 30), 3000, None, None],
            ["Итого", 4250.5, 1.25, ""]]
    sheet, styles = parts(xlsx_book([("Тест", rows, ["date", "money", "ton", None])]))
    heads = [fill_of(styles, cell_xf(sheet, styles, f"{c}1")) for c in "ABCD"]
    assert len(set(heads)) == 4                                                                      # у каждого столбца своя заливка шапки
    assert all(PALETTE[i][0] in h for i, h in enumerate(heads))
    assert 'fontId="2"' in cell_xf(sheet, styles, "A1")                                              # белый жирный текст шапки
    assert 'formatCode="DD.MM.YYYY"' in styles and 'formatCode="DD.MM.YYYY HH:MM"' in styles
    assert f"<v>{serial(dt.date(2026, 11, 3))}</v>" in sheet                                         # дата — числом Excel, а не текстом
    assert 'numFmtId="4"' in cell_xf(sheet, styles, "B2")                                            # деньги: 1 250,50
    assert 'fontId="1"' in cell_xf(sheet, styles, "B4") and fill_of(styles, cell_xf(sheet, styles, "B4")) != fill_of(styles, cell_xf(sheet, styles, "B2"))
    assert '<autoFilter ref="A1:D3"/>' in sheet                                                      # итог в фильтр не входит: при сортировке не уедет
    assert 'state="frozen"' in sheet and 'ySplit="1"' in sheet
    assert re.search(r'<c r="D3" s="\d+"/>', sheet)                                                  # пустая ячейка тоже оформлена: таблица без дыр
    back = read_first_sheet(xlsx_book([("Тест", rows, ["date", "money", "ton", None])]))
    assert back[1][:3] == [dt.date(2026, 11, 3), 1250.5, 1.25] and back[2][0] == dt.datetime(2026, 11, 4, 14, 30)


def test_writer_header_numbers_are_text_and_wide_columns_wrap():
    sheet, _ = parts(xlsx_book([("Табель", [[1, 2, "Очень длинный заголовок столбца"], [5, 6, "я" * 120]], [None, None, None], {"freeze": 2})]))
    assert '<c r="A1" s="' in sheet and 't="inlineStr"><is><t xml:space="preserve">1</t>' in sheet        # цифры в шапке — подписи, не числа
    assert 'xSplit="2"' in sheet
    assert re.search(r'<col min="3" max="3" width="48"', sheet)                                      # ширина ограничена, длинный текст переносится


@pytest.mark.parametrize("path", ["/vehicles/export.xlsx", "/reminders/export.xlsx", "/purchases/export.xlsx", "/roster/export.xlsx",
                                  "/bills/export.xlsx?month=all", "/stock/balances/export.xlsx", "/stock/moves/export.xlsx", "/fines/export.xlsx",
                                  "/trips/export.xlsx", "/orders/export.xlsx"])
def test_every_excel_button_gives_a_real_workbook(world, make_client, path):
    c = make_client("dir_a")
    r = c.get(path)
    assert r.status_code == 200, (path, r.text)
    assert r.headers["content-type"].startswith(XLSX) and r.content[:2] == b"PK"
    assert '.xlsx"' in r.headers["content-disposition"]
    table = read_first_sheet(r.content)
    assert table and all(isinstance(h, str) and h for h in table[0])
    csv = c.get(path.replace(".xlsx", ".csv"))                                                       # старые закладки на CSV работают
    assert csv.status_code == 200 and csv.content.startswith("﻿".encode())
    assert c.get(path.replace(".xlsx", ".pdf")).status_code == 404


def test_timesheet_excel(world, make_client):
    c = make_client("dir_a")
    month = TODAY.strftime("%Y-%m")
    r = c.get("/timesheet/export.xlsx", params={"month": month, "legal_entity_id": world.A})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    table = read_first_sheet(r.content)
    assert table[0][:3] == ["№", "ФИО", "Должность"] and table[0][3] == "1" and table[0][-1] == "Итого, ₽"
    assert c.get("/timesheet/export.csv", params={"month": month, "legal_entity_id": world.A}).status_code == 200


def test_list_export_has_only_data_and_headers(world, make_client):
    w = world
    c = make_client("dir_a")
    c.post("/fines", fine(w, amount="1000", paid_at=d(-1), resolution_number="PAID-1"))
    c.post("/fines", fine(w, amount="2500,50", due_date=d(5), resolution_number="LATE-1"))
    r = c.get("/fines/export.xlsx")
    table = read_first_sheet(r.content)
    head = table[0]
    assert head[:3] == ["Юрлицо", "Техника", "Водитель"] and "Сумма, ₽" in head
    amount = head.index("Сумма, ₽")
    assert sorted(row[amount] for row in table[1:3]) == [1000.0, 2500.5]                             # деньги числами, а не текстом «2500,50»
    assert table[1][head.index("Дата нарушения")] == TODAY - dt.timedelta(days=10)                   # дата — датой
    assert table[-1][0] == "Итого" and table[-1][amount] == 3500.5                                   # итоговая строка есть только в Excel
    csv = c.get("/fines/export.csv").text.strip().split("\r\n")
    assert len(csv) == 3 and "Итого" not in csv[-1]                                                  # а в CSV — только строки данных


def test_screen_only_columns_stay_out_of_the_file(world, make_client):
    c = make_client("dir_a")
    cust = customer(c)
    assert order(world, c, cust, amount="100").status_code == 201
    head = read_first_sheet(c.get("/orders/export.xlsx").content)[0]
    assert head[:2] == ["Юрлицо", "№"]                                                              # номер заказа — первым, как на экране
    assert head.count("Статус") == 1 and "Маршрут" not in head and "Фото" not in head              # без повторов и значков с экрана
    assert "Откуда" in head and "Куда" in head and "С НДС, ₽" in head
    trips = read_first_sheet(c.get("/trips/export.xlsx").content)[0]
    assert "Фото" not in trips


def test_export_respects_rights(world, make_client):
    assert make_client("drv1").get("/fines/export.xlsx").status_code == 403
    assert make_client("drv1").get("/bills/export.xlsx").status_code == 403
    assert make_client("drv1").get("/stock/moves/export.xlsx").status_code == 403
