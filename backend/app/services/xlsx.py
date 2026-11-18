# -*- coding: utf-8 -*-
"""Запись книги Excel (.xlsx) без внешних библиотек.

Что получается в файле: цветная шапка (у каждого столбца свой цвет), под ней данные с лёгким оттенком того же цвета и тонкими рамками,
настоящие даты, суммы и числа (по ним работают сортировка и формулы), ширина столбцов по содержимому, закреплённая шапка, фильтр,
жирная итоговая строка («Итого…»), печать на ширину страницы. Только запись: файлы Excel сайт не читает (кроме загрузки показаний: xlsx_read).

Текст пишется как строка, а не как формула, поэтому значение вида «=1+1» в ячейке остаётся текстом.

Лист: (название, строки) или (название, строки, виды столбцов) или (название, строки, виды столбцов, параметры).
  строки: первая — заголовок; значения: текст, числа, Decimal, date, datetime (по местному времени), bool, None.
  виды столбцов: None | "text" | "money" | "int" | "num" | "dec" | "dec1" | "dec2" | "dec3" | "ton" | "date" | "datetime".
  параметры: {"colors": [номер цвета по столбцам], "freeze": сколько столбцов слева закрепить}.
"""
import datetime as dt
import io
import math
import re
import zipfile
from decimal import Decimal
from xml.sax.saxutils import escape

_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")

# цвета столбцов: (заливка шапки, лёгкий оттенок данных). Шапка с белым жирным текстом, поэтому заливки тёмные и насыщенные.
PALETTE = [
    ("2F6DB5", "EAF1FA"),   # синий
    ("2E8B57", "E8F5EE"),   # зелёный
    ("D9822B", "FCF1E3"),   # оранжевый
    ("7B4FA6", "F1EAF8"),   # фиолетовый
    ("1F8A8A", "E2F3F3"),   # бирюзовый
    ("C0485E", "FAE8EC"),   # малиновый
    ("A8820C", "FAF4DB"),   # золотой
    ("55667A", "ECEFF3"),   # графитовый
]

# форматы чисел: код формата Excel; None — «Общий». Деньги — с пробелом между тысячами и двумя знаками.
FORMATS = {
    None: None, "text": None, "num": None,
    "money": "#,##0.00", "int": "#,##0", "dec": "#,##0.0", "dec1": "#,##0.0", "dec2": "#,##0.00", "dec3": "#,##0.000", "ton": "#,##0.00",
    "date": "DD.MM.YYYY", "datetime": "DD.MM.YYYY HH:MM",
}
_BUILTIN_FMT = {"#,##0.00": 4, "#,##0": 3, "0.00": 2}

_EPOCH = dt.date(1899, 12, 30)
_INK = "1F2937"
_GRID = "CBD2DC"
_TOTAL_FILL = "E3E7ED"
MAX_WIDTH = 48


def _col(i: int) -> str:
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


def _text(v) -> str:
    return escape(_BAD_XML.sub("", str(v)))


def serial(v) -> float | int:
    """Дата или время как число Excel (дни от 30.12.1899)."""
    if isinstance(v, dt.datetime):
        return (v.date() - _EPOCH).days + (v.hour * 3600 + v.minute * 60 + v.second) / 86400
    return (v - _EPOCH).days


def _is_total(row) -> bool:
    return any(isinstance(v, str) and v.startswith("Итого") for v in row[:2])


class _Styles:
    """Таблица стилей книги: каждое сочетание (заливка, шрифт, формат, рамка, выравнивание) получает свой номер."""

    def __init__(self):
        self.fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
        self.fonts = [self._font(False, _INK), self._font(True, _INK), self._font(True, "FFFFFF")]
        self.borders = [
            "<border><left/><right/><top/><bottom/><diagonal/></border>",
            self._border(_GRID, _GRID, "thin"),
            self._border("FFFFFF", "FFFFFF", "thin"),
            self._border(_GRID, "44546A", "medium"),
        ]
        self.formats: dict[str, int] = {}
        self.xfs: list[str] = []
        self._xf_ids: dict[tuple, int] = {}
        self.xf()                                   # номер 0 — обычная ячейка

    @staticmethod
    def _font(bold: bool, color: str) -> str:
        return '<font>%s<sz val="11"/><color rgb="FF%s"/><name val="Calibri"/><family val="2"/></font>' % ("<b/>" if bold else "", color)

    @staticmethod
    def _border(side: str, top: str, top_style: str) -> str:
        def one(tag, color, style):
            return '<%s style="%s"><color rgb="FF%s"/></%s>' % (tag, style, color, tag)
        return "<border>%s%s%s%s<diagonal/></border>" % (one("left", side, "thin"), one("right", side, "thin"), one("top", top, top_style), one("bottom", side, "thin"))

    def fill(self, rgb: str | None) -> int:
        if rgb is None:
            return 0
        xml = '<fill><patternFill patternType="solid"><fgColor rgb="FF%s"/><bgColor indexed="64"/></patternFill></fill>' % rgb
        if xml not in self.fills:
            self.fills.append(xml)
        return self.fills.index(xml)

    def fmt_id(self, code: str | None) -> int:
        if code is None:
            return 0
        if code in _BUILTIN_FMT:
            return _BUILTIN_FMT[code]
        return self.formats.setdefault(code, 164 + len(self.formats))

    def xf(self, fill: str | None = None, font: int = 0, fmt: str | None = None, border: int = 0, h: str | None = None, v: str = "center", wrap: bool = False) -> int:
        key = (fill, font, fmt, border, h, v, wrap)
        if key not in self._xf_ids:
            align = '<alignment%s vertical="%s"%s%s/>' % (' horizontal="%s"' % h if h else "", v, ' wrapText="1"' if wrap else "", ' indent="1"' if h == "left" else "")
            xml = ('<xf numFmtId="%d" fontId="%d" fillId="%d" borderId="%d" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">%s</xf>'
                   % (self.fmt_id(fmt), font, self.fill(fill), border, align))
            self._xf_ids[key] = len(self.xfs)
            self.xfs.append(xml)
        return self._xf_ids[key]

    def xml(self) -> str:
        nf = "".join('<numFmt numFmtId="%d" formatCode="%s"/>' % (i, escape(code, {'"': "&quot;"})) for code, i in self.formats.items())
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                + ('<numFmts count="%d">%s</numFmts>' % (len(self.formats), nf) if nf else "") +
                '<fonts count="%d">%s</fonts>' % (len(self.fonts), "".join(self.fonts)) +
                '<fills count="%d">%s</fills>' % (len(self.fills), "".join(self.fills)) +
                '<borders count="%d">%s</borders>' % (len(self.borders), "".join(self.borders)) +
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="%d">%s</cellXfs>' % (len(self.xfs), "".join(self.xfs)) +
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def _clean(v, head: bool = False):
    """Значение ячейки: (вид, значение): "n" число, "d" дата или время, "s" текст, None — пусто. Шапка всегда текст."""
    if v is None or v == "":
        return None, None
    if isinstance(v, bool):
        return "s", "да" if v else "нет"
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return None, None
        return ("s", str(v)) if head else ("n", v)
    if isinstance(v, dt.date):                       # и datetime тоже
        return "d", v
    return "s", str(v)


def _shown_len(kind: str, v, fmt_kind) -> int:
    if kind == "n":
        if fmt_kind in ("money", "ton", "dec2"):
            return len("{:,.2f}".format(v))
        return len(("%.3f" % v).rstrip("0").rstrip(".")) + (1 if abs(v) >= 1000 and fmt_kind in ("int", "dec", "dec1", "dec3") else 0)
    if kind == "d":
        return 16 if isinstance(v, dt.datetime) else 10
    return max((len(line) for line in v.split("\n")), default=0)


def _number_code(kind, v, fmt_kind) -> str | None:
    if kind == "n":
        return FORMATS.get(fmt_kind)
    if kind == "d":
        return FORMATS["datetime"] if isinstance(v, dt.datetime) else FORMATS["date"]
    return None


def _sheet_xml(rows, kinds, opts, st: _Styles) -> str:
    kinds = list(kinds or [])
    opts = opts or {}
    rows = [list(r) for r in rows]
    ncols = max((len(r) for r in rows), default=0)
    kinds += [None] * (ncols - len(kinds))
    colors = list(opts.get("colors") or [])
    palette = [PALETTE[(colors[c] if c < len(colors) else c) % len(PALETTE)] for c in range(ncols)]
    cleaned = [[_clean(r[c] if c < len(r) else None, head=i == 0) for c in range(ncols)] for i, r in enumerate(rows)]
    totals = {i for i, r in enumerate(rows) if i and _is_total(r)}

    # ширина столбца по самому длинному значению и самому длинному слову шапки; очень широкие текстовые столбцы переносятся по строкам
    widths, wrap_col = [], []
    for c in range(ncols):
        head = str(cleaned[0][c][1] or "") if cleaned else ""
        word = max((len(w) for w in head.replace("\n", " ").split(" ")), default=0)
        longest = max((_shown_len(k, v, kinds[c]) for line in cleaned[1:] for k, v in [line[c]] if k), default=0)
        low = 10 if kinds[c] == "money" else 17 if kinds[c] == "datetime" else 12 if kinds[c] == "date" else 6
        want = max(longest + 2, word + 3, low)
        widths.append(min(want, MAX_WIDTH))
        wrap_col.append(want > MAX_WIDTH)

    head_lines = max((min(4, math.ceil(len(str(cleaned[0][c][1] or "")) * 1.1 / max(widths[c] - 1, 1))) for c in range(ncols)), default=1) if cleaned else 1

    body = []
    last_data = 1
    for r, line in enumerate(cleaned):
        is_head, is_total = r == 0, r in totals
        cells = []
        for c, (k, v) in enumerate(line):
            ref = "%s%d" % (_col(c), r + 1)
            fill, tint = palette[c]
            if is_head:
                s = st.xf(fill, 2, None, 2, "center", "center", True)
            elif is_total:
                s = st.xf(_TOTAL_FILL, 1, _number_code(k, v, kinds[c]), 3, None if k in ("n", "d") else "left", "center", False)
            else:
                wrap = wrap_col[c] and k == "s"
                s = st.xf(tint, 0, _number_code(k, v, kinds[c]), 1, "center" if k == "d" else None if k == "n" else "left", "top" if wrap else "center", wrap)
            if k is None:
                cells.append('<c r="%s" s="%d"/>' % (ref, s))
            elif k == "n":
                cells.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, s, repr(v)))
            elif k == "d":
                cells.append('<c r="%s" s="%d"><v>%s</v></c>' % (ref, s, repr(serial(v))))
            else:
                cells.append('<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, s, _text(v)))
        ht = ' ht="%d" customHeight="1"' % max(26, 15 * head_lines + 8) if is_head else (' ht="20" customHeight="1"' if is_total else "")
        body.append('<row r="%d"%s>%s</row>' % (r + 1, ht, "".join(cells)))
        if not is_head and not is_total:
            last_data = r + 1

    freeze = int(opts.get("freeze") or 0)
    if freeze:
        pane = ('<pane xSplit="%d" ySplit="1" topLeftCell="%s2" activePane="bottomRight" state="frozen"/>'
                '<selection pane="topRight"/><selection pane="bottomLeft"/><selection pane="bottomRight"/>' % (freeze, _col(freeze)))
    else:
        pane = '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft"/>'
    cols = "".join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w) for i, w in enumerate(widths))
    auto = '<autoFilter ref="A1:%s%d"/>' % (_col(ncols - 1), max(last_data, 1)) if ncols else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
            '<sheetViews><sheetView workbookViewId="0" showGridLines="0">%s</sheetView></sheetViews>'
            '<sheetFormatPr defaultRowHeight="20" customHeight="1"/>' % pane
            + ("<cols>%s</cols>" % cols if cols else "") +
            "<sheetData>%s</sheetData>%s" % ("".join(body), auto) +
            '<pageMargins left="0.4" right="0.4" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
            '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>')


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
    """Книга из нескольких листов. sheets: список (название, строки[, виды столбцов[, параметры]]); в строках первая — заголовок."""
    if not sheets:
        sheets = [("Лист1", [])]
    names = sheet_names([s[0] for s in sheets])
    count = len(sheets)
    styles = _Styles()
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    sheet_xml = [_sheet_xml(s[1], s[2] if len(s) > 2 else None, s[3] if len(s) > 3 else None, styles) for s in sheets]
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
        "xl/styles.xml": styles.xml(),
    }
    for i, xml in enumerate(sheet_xml):
        files["xl/worksheets/sheet%d.xml" % (i + 1)] = xml
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
