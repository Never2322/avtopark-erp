# -*- coding: utf-8 -*-
"""Чтение первого листа книги Excel (.xlsx) без внешних библиотек.

Нужно для загрузки показаний из файла. Формулы не вычисляются (берётся значение, которое Excel сохранил), макросы и
внешние ссылки игнорируются. Ячейки с датой превращаются в datetime.date / datetime.datetime. Старый формат .xls не
читается: файл надо пересохранить как .xlsx или CSV.

Защита: общий размер распакованных частей ограничен, документы с объявлениями DOCTYPE/ENTITY отклоняются (защита от
«бомб» в XML).
"""
import datetime as dt
import io
import re
import zipfile
from xml.etree import ElementTree as ET

MAX_UNPACKED = 40 * 1024 * 1024
MAX_ROWS = 20000
MAX_COLS = 60

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_BUILTIN_DATE = set(range(14, 23)) | {27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 45, 46, 47, 50, 51, 52, 53, 54, 55, 56, 57, 58}
_BUILTIN_DATETIME = {22, 46, 47}
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


class XlsxError(Exception):
    """Файл нельзя прочитать; текст можно показывать человеку."""


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _xml(zf: zipfile.ZipFile, name: str):
    try:
        raw = zf.read(name)
    except KeyError:
        return None
    head = raw[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw[:65536].upper():
        raise XlsxError("Файл Excel выглядит повреждённым или небезопасным. Сохраните его заново как .xlsx или CSV.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise XlsxError("Файл Excel не читается. Сохраните его заново как .xlsx или CSV.") from None


def _text_of(si) -> str:
    return "".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"]))


def _date_styles(styles) -> dict[int, str]:
    """Номера стилей (индексы cellXfs), в которых число показывается как дата или дата со временем."""
    result: dict[int, str] = {}
    if styles is None:
        return result
    custom: dict[int, str] = {}
    fmts = styles.find("m:numFmts", _NS)
    if fmts is not None:
        for f in fmts.findall("m:numFmt", _NS):
            custom[int(f.get("numFmtId", "0"))] = f.get("formatCode", "")
    xfs = styles.find("m:cellXfs", _NS)
    if xfs is None:
        return result
    for i, xf in enumerate(xfs.findall("m:xf", _NS)):
        fid = int(xf.get("numFmtId", "0"))
        code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", custom.get(fid, "")).lower()
        if fid in custom:
            if re.search(r"[dy]", code) and not re.search(r"[#0?]", code):
                result[i] = "datetime" if "h" in code else "date"
        elif fid in _BUILTIN_DATE:
            result[i] = "datetime" if fid in _BUILTIN_DATETIME else "date"
    return result


def serial_to_date(serial: float) -> dt.date | dt.datetime:
    """Номер дня Excel (система 1900) в дату; дробная часть — время суток."""
    base = dt.datetime(1899, 12, 30)
    moment = base + dt.timedelta(days=serial)
    if abs(serial - round(serial)) < 1e-9:
        return moment.date()
    return moment.replace(microsecond=0)


def read_first_sheet(data: bytes) -> list[list]:
    """Строки первого листа: списки значений (текст, число, дата или None). Пустые строки в конце отбрасываются."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise XlsxError("Это не файл .xlsx. Старый формат .xls не поддерживается: сохраните файл как .xlsx или CSV.") from None
    with zf:
        if sum(i.file_size for i in zf.infolist()) > MAX_UNPACKED:
            raise XlsxError("Файл Excel слишком большой после распаковки.")
        wb = _xml(zf, "xl/workbook.xml")
        if wb is None:
            raise XlsxError("В файле нет книги Excel. Сохраните его как .xlsx или CSV.")
        sheets = wb.find("m:sheets", _NS)
        first = sheets.find("m:sheet", _NS) if sheets is not None else None
        if first is None:
            raise XlsxError("В книге Excel нет листов.")
        rid = first.get("{%s}id" % _NS["r"])
        target = "worksheets/sheet1.xml"
        rels = _xml(zf, "xl/_rels/workbook.xml.rels")
        if rels is not None:
            for rel in rels.findall("pr:Relationship", _NS):
                if rel.get("Id") == rid:
                    target = rel.get("Target", target)
                    break
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        sheet = _xml(zf, path)
        if sheet is None:
            raise XlsxError("Не удалось найти первый лист в книге Excel.")
        shared: list[str] = []
        sst = _xml(zf, "xl/sharedStrings.xml")
        if sst is not None:
            shared = [_text_of(si) for si in sst.findall("m:si", _NS)]
        date_style = _date_styles(_xml(zf, "xl/styles.xml"))

    rows: dict[int, dict[int, object]] = {}
    data_el = sheet.find("m:sheetData", _NS)
    if data_el is None:
        return []
    for row in data_el.findall("m:row", _NS):
        rn = int(row.get("r", "0")) - 1 if row.get("r") else len(rows)
        if rn >= MAX_ROWS:
            raise XlsxError(f"В листе больше {MAX_ROWS} строк. Разбейте файл на части.")
        for pos, c in enumerate(row.findall("m:c", _NS)):
            ref = _CELL_REF.match(c.get("r", ""))
            ci = _col_index(ref.group(1)) if ref else pos
            if ci >= MAX_COLS:
                continue
            kind = c.get("t", "n")
            v = c.find("m:v", _NS)
            value: object = None
            if kind == "s" and v is not None and v.text is not None:
                idx = int(v.text)
                value = shared[idx] if 0 <= idx < len(shared) else None
            elif kind == "inlineStr":
                is_ = c.find("m:is", _NS)
                value = _text_of(is_) if is_ is not None else None
            elif kind in ("str", "e"):
                value = v.text if v is not None else None
            elif kind == "b":
                value = bool(int(v.text)) if v is not None and v.text else None
            elif v is not None and v.text not in (None, ""):
                try:
                    num = float(v.text)
                except ValueError:
                    num = None
                if num is not None:
                    style = date_style.get(int(c.get("s", "0") or 0))
                    if style and 0 < num < 2958466:
                        value = serial_to_date(num)
                        if style == "date" and isinstance(value, dt.datetime):
                            value = value.date() if value.time() == dt.time.min else value
                    else:
                        value = int(num) if num == int(num) and abs(num) < 1e15 else num
            if value is not None and not (isinstance(value, str) and not value.strip()):
                rows.setdefault(rn, {})[ci] = value
    if not rows:
        return []
    out: list[list] = []
    for rn in range(max(rows) + 1):
        cells = rows.get(rn, {})
        width = (max(cells) + 1) if cells else 0
        out.append([cells.get(i) for i in range(width)])
    while out and not any(x is not None for x in out[-1]):
        out.pop()
    return out
