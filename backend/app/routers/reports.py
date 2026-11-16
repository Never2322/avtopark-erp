# -*- coding: utf-8 -*-
"""Отчёты: список доступных, сам отчёт (таблица, диаграмма, дополнительные таблицы) и выгрузка в Excel или CSV.

Видеть отчёт — право «reports.view», деньги в нём — «reports.money», выгрузка — «reports.export». Отчёт, по которому у человека нет ни
одного столбца с данными, не показывается и открывается как «не найдено». Выгрузка устроена так же, как экран: что скрыто на экране,
в файл не попадает.
"""
import datetime as dt
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import audit
from app.common import csv_text, fail, utcnow
from app.db import get_db
from app.deps import Principal, require
from app.models import LegalEntity
from app.services import reports as svc
from app.services.reminders import local_today, org_tz
from app.services.xlsx import xlsx_book

router = APIRouter(prefix="/reports", tags=["Отчёты"])
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
KIND_FORMAT = {"money": "money", "dec": "dec", "int": "int"}


def report_or_404(p: Principal, name: str) -> svc.Report:
    rep = svc.REPORTS.get(name)
    if rep is None or not svc.available(p, rep):
        raise fail(404, "not_found", "Такого отчёта нет")
    return rep


def check_entity(p: Principal, perm: str, legal_entity_id: int | None) -> None:
    if legal_entity_id is not None and not p.scope(perm).allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")


def build(db: Session, p: Principal, rep: svc.Report, legal_entity_id: int | None, month: str | None, frm: str | None, to: str | None) -> dict:
    res, _ = svc.run(db, p, rep, legal_entity_id=legal_entity_id, month=month, frm=frm, to=to, today=local_today(), now=utcnow())
    res["legal_entity_id"] = legal_entity_id
    return res


@router.get("")
def list_reports(p: Principal = Depends(require("reports.view"))):
    items = [{"name": r.name, "title": r.title, "period": r.period, "money": r.money, "desc": r.desc} for r in svc.REPORTS.values() if svc.available(p, r)]
    return {"items": items, "can": {"export": bool(p.scope("reports.export")), "money": bool(p.scope("reports.money"))}}


@router.get("/{name}")
def get_report(name: str, legal_entity_id: int | None = None, month: str | None = None, frm: str | None = Query(None, alias="from"), to: str | None = None,
               p: Principal = Depends(require("reports.view")), db: Session = Depends(get_db)):
    rep = report_or_404(p, name)
    check_entity(p, "reports.view", legal_entity_id)
    res = build(db, p, rep, legal_entity_id, month, frm, to)
    res.pop("sheets", None)
    res["can"] = {"export": p.scope("reports.export").allows(legal_entity_id) if legal_entity_id is not None else bool(p.scope("reports.export"))}
    return res


# ---------------------------------------------------------------- выгрузка

def show_value(kind: str, key: str, v):
    """Значение ячейки для файла: даты по-русски, пустые значения периодов ремонта словами."""
    if kind == "date" and v:
        return dt.date.fromisoformat(v[:10]).strftime("%d.%m.%Y")
    if kind == "datetime":
        if not v:
            return "до начала учёта" if key == "from" else "идёт сейчас"
        return dt.datetime.fromisoformat(v).astimezone(org_tz()).strftime("%d.%m.%Y %H:%M")
    return v


def sheet_of(tbl: dict) -> tuple[list[list], list]:
    cols = tbl["columns"]
    rows = [[c["label"] for c in cols]]
    rows += [[show_value(c["kind"], c["key"], v) for c, v in zip(cols, r)] for r in tbl["rows"]]
    if tbl.get("totals"):
        rows.append(list(tbl["totals"]))
    return rows, [KIND_FORMAT.get(c["kind"]) for c in cols]


def csv_number(v):
    return str(v).replace(".", ",") if isinstance(v, float) else v


def info_sheet(db: Session, res: dict, fmt_time: str) -> list[list]:
    entity = db.get(LegalEntity, res["legal_entity_id"]).name if res.get("legal_entity_id") else "Все юрлица, к которым есть доступ"
    pr = res["period"]
    if pr["kind"] == "month":
        period = pr["month"]
    elif pr["kind"] == "year12":
        period = f"12 месяцев по {pr['month']}"
    else:
        period = f"{dt.date.fromisoformat(pr['from']).strftime('%d.%m.%Y')} — {dt.date.fromisoformat(pr['to']).strftime('%d.%m.%Y')}"
    rows = [["Параметр", "Значение"], ["Отчёт", res["title"]], ["Период", period], ["Юрлицо", entity], ["Сформирован", fmt_time], ["Как считается", res["note"]]]
    rows += [["Не показано", h] for h in res.get("hidden", [])]
    return rows


def file_stem(name: str, res: dict) -> str:
    pr = res["period"]
    tail = f"{pr['from']}_{pr['to']}" if pr["kind"] == "range" else pr["from"] if pr["kind"] == "day" else pr["month"]
    return re.sub(r"[^A-Za-z0-9_.-]", "", f"report-{name}-{tail}")


@router.get("/{name}/export.xlsx")
def export_xlsx(name: str, legal_entity_id: int | None = None, month: str | None = None, frm: str | None = Query(None, alias="from"), to: str | None = None,
                p: Principal = Depends(require("reports.export")), db: Session = Depends(get_db)):
    rep = report_or_404(p, name)
    check_entity(p, "reports.export", legal_entity_id)
    check_entity(p, "reports.view", legal_entity_id)
    res = build(db, p, rep, legal_entity_id, month, frm, to)
    stamp = utcnow().astimezone(org_tz()).strftime("%d.%m.%Y %H:%M")
    sheets = []
    for title, tbl in (res.get("sheets") or [(res["title"], res["table"])] + [(e["title"], e) for e in res["extras"]]):
        rows, kinds = sheet_of(tbl)
        sheets.append((title, rows, kinds))
    sheets.append(("О отчёте", info_sheet(db, res, stamp)))
    audit.log(db, p, "reports.export", "report", None, legal_entity_id, report=name, period=res["period"], format="xlsx")
    db.commit()
    return Response(xlsx_book(sheets), media_type=XLSX_MIME, headers={"Content-Disposition": f'attachment; filename="{file_stem(name, res)}.xlsx"'})


@router.get("/{name}/export.csv")
def export_csv(name: str, legal_entity_id: int | None = None, month: str | None = None, frm: str | None = Query(None, alias="from"), to: str | None = None,
               p: Principal = Depends(require("reports.export")), db: Session = Depends(get_db)):
    rep = report_or_404(p, name)
    check_entity(p, "reports.export", legal_entity_id)
    check_entity(p, "reports.view", legal_entity_id)
    res = build(db, p, rep, legal_entity_id, month, frm, to)
    out: list[list] = []
    for i, (title, tbl) in enumerate([(res["title"], res["table"])] + [(e["title"], e) for e in res["extras"]]):
        rows, _ = sheet_of(tbl)
        if i:
            out += [[], [title]]
        out += [[csv_number(v) for v in r] for r in rows]
    audit.log(db, p, "reports.export", "report", None, legal_entity_id, report=name, period=res["period"], format="csv")
    db.commit()
    return Response(csv_text(out), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{file_stem(name, res)}.csv"'})
