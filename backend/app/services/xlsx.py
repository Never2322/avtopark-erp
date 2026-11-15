# -*- coding: utf-8 -*-
"""Запись книги Excel (.xlsx) без внешних библиотек: несколько листов, жирный закреплённый заголовок, ширина столбцов,
форматы чисел (деньги, одна цифра после запятой, целые). Только запись: файлы Excel сайт не читает.

Текст пишется как строка, а не как формула, поэтому значение вида «=1+1» в ячейке остаётся текстом.
"""
import io
import math
import re
import zipfile
from xml.sax.saxutils import escape

_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")

# индексы стилей в cellXfs: обычные 0–4, жирные 5–9
_PLAIN, _HEAD = 0, 5
_KIND_STYLE = {None: 0, "text": 0, "money": 1, "dec": 2, "int": 3}
_BOLD_OFFSET = 5


def _col(i: int) -> str:
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


def _text(v) -> str:
    return escape(_BAD_XML.sub("", str(v)))


def _is_total(row) -> bool:
    return bool(row) and isinstance(row[0], str) and row[0].startswith("Итого")


def _sheet_xml(rows, kinds=None) -> str:
    """Лист: первая строка — заголовок (жирная, закреплена), строка «Итого…» жирная. kinds: вид данных по столбцам."""
    kinds = kinds or []
    widths: list[int] = []
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        bold = r == 1 or _is_total(row)
        for c, v in enumerate(row):
            while len(widths) <= c:
                widths.append(8)
            if v is None or v == "":
                continue
            ref = "%s%d" % (_col(c), r)
            if isinstance(v, bool):
                v = "Да" if v else "Нет"
            if isinstance(v, (int, float)):
                if isinstance(v, float) and not math.isfinite(v):
                    continue
                style = _KIND_STYLE.get(kinds[c] if c < len(kinds) else None, 0)
                if bold:
                    style += _BOLD_OFFSET
                cells.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, repr(v) if isinstance(v, float) else v))
                widths[c] = max(widths[c], len(("%.2f" % v) if isinstance(v, float) else str(v)) + 3)
            else:
                style = _HEAD if bold else _PLAIN
                text = str(v)
                cells.append('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, _text(text)))
                widths[c] = max(widths[c], min(len(text) + 2, 60))
        body.append('<row r="%d">%s</row>' % (r, "".join(cells)))
    cols = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w) for i, w in enumerate(widths))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            + ("<cols>%s</cols>" % cols if cols else "") +
            "<sheetData>%s</sheetData></worksheet>" % "".join(body))


def sheet_names(names) -> list[str]:
    """Имена листов: без запрещённых знаков, не длиннее 31, без повторов (Excel их не терпит)."""
    out, seen = [], set()
    for i, name in enumerate(names, start=1):
        base = _BAD_SHEET.sub(" ", str(name))[:31].strip() or "Лист%d" % i
        cand, n = base, 2
        while cand.lower() in seen:
            tail = " %d" % n
            cand = base[:31 - len(tail)] + tail
            n += 1
        seen.add(cand.lower())
        out.append(cand)
    return out


def xlsx_book(sheets) -> bytes:
    """Книга из нескольких листов. sheets: список (название, строки) или (название, строки, виды столбцов);
    в строках первая — заголовок; вид столбца: "money", "dec", "int" или None."""
    if not sheets:
        sheets = [("Лист1", [])]
    names = sheet_names([s[0] for s in sheets])
    count = len(sheets)
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    overrides = "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="%s.worksheet+xml"/>' % (i + 1, ct) for i in range(count))
    entries = "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n, {'"': "&quot;"}), i + 1, i + 1) for i, n in enumerate(names))
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rels = "".join('<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, rel, i + 1) for i in range(count))
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="%s.sheet.main+xml"/>' % ct
            + overrides +
            '<Override PartName="/xl/styles.xml" ContentType="%s.styles+xml"/></Types>' % ct),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % rel),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="%s">'
            "<sheets>%s</sheets></workbook>" % (rel, entries)),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + rels +
            '<Relationship Id="rId%d" Type="%s/styles" Target="styles.xml"/></Relationships>' % (count + 1, rel)),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0"/></numFmts>'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="10">'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            '<xf numFmtId="4" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>'
            '<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>'
            '<xf numFmtId="3" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            "</cellXfs></styleSheet>"),
    }
    for i, sheet in enumerate(sheets):
        files["xl/worksheets/sheet%d.xml" % (i + 1)] = _sheet_xml(sheet[1], sheet[2] if len(sheet) > 2 else None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
