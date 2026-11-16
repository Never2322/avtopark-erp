# -*- coding: utf-8 -*-
"""Перенос данных из старой программы (SQLite) в новую (PostgreSQL).

Как работает:
  * старая база открывается только на чтение, в ней ничего не меняется;
  * каждая запись переносится в своей вложенной транзакции: негодная запись пропускается и попадает в отчёт, остальные идут дальше;
  * соответствие «номер в старой базе → номер в новой» хранится в таблице legacy_ids, поэтому повторный запуск не плодит дубликаты
    и можно доносить записи, добавленные в старую программу позже;
  * без --apply всё выполняется целиком, но в конце откатывается: это «посмотреть, что получится»;
  * пользователи, пароли, файлы и фото не переносятся (см. SKIPPED): пароли старой программы устроены иначе.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.enums import (CALENDAR_COLORS, EMPLOYEE_EVENT_KINDS, READING_SOURCES, TIMESHEET_CODES)
from app.models import (CalendarEvent, CashAccount, CashOp, ControlEvent, Constant, Counterparty, DossierDoc, Document, Employee, EmployeeEvent,
                        EmployeePayment, EmploymentAssignment, Fine, FuelCard, FuelFill, Job, LegacyId, LegalEntity, MaintenanceItem,
                        MiscExpense, Order, PartCatalog, PpeIssue, PpeNorm, RepairBill, RepairBillItem, Rental, RequestPart,
                        ServiceRequest, ServiceRequestEvent, StockItem, StockMove, Tariff, TimesheetMark, Trip, Vehicle, VehicleAssignment,
                        VehicleFinance, VehicleReading, VehicleRepair, VehicleSpecs, VehicleStatusHistory, VehicleTire, VehicleType,
                        Waybill, Warehouse)
from app.services.readings import recompute_current
from app.common import org_today

# Что не переносится и почему (для отчёта). Ключ — таблица старой базы.
SKIPPED = {
    "users": "пользователи и пароли (пароли старой программы устроены иначе; создайте пользователей заново, список логинов есть в отчёте)",
    "revoked_sessions": "служебная таблица входов", "user_vehicles": "привязка пользователей к технике (создаётся при заведении пользователей)",
    "attachments": "файлы и фото (счета, заправки, рейсы, обращения): в старой базе хранятся именами файлов на диске",
    "job_photos": "фото нарядов (файлы на диске)", "audit_log": "журнал действий старой программы", "inbox": "старые уведомления",
    "tg_queue": "очередь Telegram", "push_subs": "подписки на push", "push_queue": "очередь push", "mail_rules": "правила почты",
    "mail_seen": "служебная таблица почты", "mail_log": "журнал почты", "announcements": "объявления", "announcement_targets": "адресаты объявлений",
    "announcement_replies": "ответы на объявления", "waybill_templates": "шаблоны путевых листов", "settings": "настройки старой программы",
}


def kop(v) -> Decimal:
    try:
        return (Decimal(int(v or 0)) / 100).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def dec(v, places: int = 1) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v)).quantize(Decimal(1).scaleb(-places))
    except (InvalidOperation, ValueError):
        return None


def s(v, n: int | None = None) -> str:
    out = "" if v is None else str(v).strip()
    return out[:n] if n else out


def day(v) -> dt.date | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s(v))
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def moment(v, tz: ZoneInfo) -> dt.datetime | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?", s(v))
    if not m:
        return None
    try:
        return dt.datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0), int(m[6] or 0), tzinfo=tz).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def digits(v) -> int | None:
    m = re.search(r"\d+", s(v))
    return int(m[0]) if m and int(m[0]) > 0 else None


def slug(v: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "-", v.lower()).strip("-")[:30] or "x"


class Report:
    def __init__(self) -> None:
        self.stats: dict[str, Counter] = defaultdict(Counter)
        self.warnings: list[tuple[str, int | None, str]] = []
        self.notes: list[str] = []

    def count(self, table: str, what: str, n: int = 1) -> None:
        self.stats[table][what] += n

    def warn(self, table: str, old_id: int | None, msg: str) -> None:
        self.warnings.append((table, old_id, msg))

    def text(self, limit: int = 60) -> str:
        lines = ["ОТЧЁТ О ПЕРЕНОСЕ", ""]
        lines.append(f"{'Таблица старой базы':<26}{'в старой':>9}{'перенесено':>12}{'уже было':>10}{'пропущено':>11}")
        for t, c in self.stats.items():
            lines.append(f"{t:<26}{c['read']:>9}{c['new']:>12}{c['linked']:>10}{c['skipped']:>11}")
        if self.notes:
            lines += ["", "ЗАМЕТКИ"] + [f"  - {n}" for n in self.notes]
        if self.warnings:
            lines += ["", f"ПРЕДУПРЕЖДЕНИЯ ({len(self.warnings)})"]
            for t, i, m in self.warnings[:limit]:
                lines.append(f"  {t}{'' if i is None else ' №' + str(i)}: {m}")
            if len(self.warnings) > limit:
                lines.append(f"  … и ещё {len(self.warnings) - limit} (все в файле отчёта)")
        return "\n".join(lines)

    def full_text(self) -> str:
        return self.text(limit=10 ** 9)


class Importer:
    def __init__(self, db: Session, old: sqlite3.Connection, *, default_entity: str = "", tz: str | None = None) -> None:
        self.db, self.old = db, old
        self.tz = ZoneInfo(tz or get_settings().org_timezone)
        self.report = Report()
        self.default_name = default_entity.strip()
        self.maps: dict[str, dict[int, int]] = defaultdict(dict)
        for src, old_id, new_id in db.execute(select(LegacyId.src_table, LegacyId.old_id, LegacyId.new_id)):
            self.maps[src][old_id] = new_id
        self.entity_of_org: dict[int | None, int] = {}
        self.default_entity_id = 0
        self.vehicle_entity: dict[int, int] = {}
        self.employee_entity: dict[int, int] = {}
        self.used_numbers: dict[tuple, set[int]] = {}
        self.old_tables = {r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.today = org_today()
        self._row_links: list[tuple[str, int, str]] = []

    # ------------------------------------------------------------ вспомогательное
    def rows(self, table: str, order: str = "id") -> list[dict]:
        if table not in self.old_tables:
            note = f"в старой базе нет таблицы «{table}» — пропущено"
            if note not in self.report.notes:
                self.report.notes.append(note)
            return []
        cur = self.old.execute(f"SELECT * FROM {table} ORDER BY {order}")
        cols = [c[0] for c in cur.description]
        out = [dict(zip(cols, r)) for r in cur.fetchall()]
        self.report.stats[table]["read"] = len(out)
        return out

    def link(self, table: str, old_id: int, new_id: int, *, existing: bool = False) -> None:
        self.maps[table][old_id] = new_id
        self.db.add(LegacyId(src_table=table, old_id=old_id, new_id=new_id))
        self.report.count(table, "linked" if existing else "new")
        self._row_links.append((table, old_id, "linked" if existing else "new"))

    def seen(self, table: str, old_id: int) -> bool:
        if old_id in self.maps[table]:
            self.report.count(table, "linked")
            return True
        return False

    def each(self, table: str, order: str = "id"):
        """Перебирает записи старой таблицы, пропуская уже перенесённые. Ошибка в записи не ломает остальные."""
        for r in self.rows(table, order):
            if self.seen(table, r["id"]):
                continue
            yield r

    def run_one(self, table: str, r: dict, fn) -> None:
        self._row_links = []
        try:
            with self.db.begin_nested():
                res = fn(r)
                if res is None:
                    self.report.count(table, "skipped")
        except Exception as exc:                                     # noqa: BLE001 — любая ошибка записи = предупреждение, не сбой
            for t, old_id, what in self._row_links:                  # откат записи откатывает и её соответствие
                self.maps[t].pop(old_id, None)
                self.report.stats[t][what] -= 1
            self.report.count(table, "skipped")
            msg = str(getattr(exc, "orig", exc)).splitlines()[0][:220]
            self.report.warn(table, r.get("id"), f"не перенесено: {msg}")

    def add(self, table: str, r: dict, obj, *, existing: bool = False):
        self.db.add(obj)
        self.db.flush()
        self.link(table, r["id"], obj.id, existing=existing)
        return obj

    def audit_times(self, obj, r: dict) -> None:
        created = moment(r.get("created_at"), ZoneInfo("UTC"))
        if created:
            obj.created_at = created

    def ref(self, table: str, old_id) -> int | None:
        return self.maps[table].get(old_id) if old_id is not None else None

    def entity_for(self, *, vehicle=None, employee=None, org=None) -> int:
        if vehicle is not None and self.ref("vehicles", vehicle):
            return self.vehicle_entity.get(self.ref("vehicles", vehicle), self.default_entity_id)
        if employee is not None and self.ref("employees", employee):
            return self.employee_entity.get(self.ref("employees", employee), self.default_entity_id)
        if org in self.entity_of_org:
            return self.entity_of_org[org]
        return self.default_entity_id

    def alloc_number(self, entity_id: int, counter: str, year: int, wanted, model, *, by_year: bool = True, extra: dict | None = None) -> tuple[int, bool]:
        """Номер документа: берём старый, если это число и он свободен, иначе следующий свободный. Второй результат — «номер изменён»."""
        key = (entity_id, counter, year)
        if key not in self.used_numbers:
            q = select(model.number).where(model.legal_entity_id == entity_id)
            if by_year:
                q = q.where(model.year == year)
            for k, v in (extra or {}).items():
                q = q.where(getattr(model, k) == v)
            self.used_numbers[key] = set(self.db.scalars(q))
        used = self.used_numbers[key]
        n = digits(wanted)
        changed = False
        if n is None or n in used:
            n = (max(used) if used else 0) + 1
            changed = True
        used.add(n)
        return n, changed

    def bump_counters(self) -> None:
        for (entity_id, kind, year), used in self.used_numbers.items():
            if not used:
                continue
            self.db.execute(text(
                "INSERT INTO number_counters (legal_entity_id, kind, year, last_value) VALUES (:e, :k, :y, :v) "
                "ON CONFLICT (legal_entity_id, kind, year) DO UPDATE SET last_value = GREATEST(number_counters.last_value, :v)"),
                {"e": entity_id, "k": kind, "y": year, "v": max(used)})

    # ------------------------------------------------------------ запуск
    def run(self) -> Report:
        steps = [self.organizations, self.employees, self.vehicles, self.readings, self.state_log, self.vehicle_cards,
                 self.directories, self.money_by_vehicle, self.cash, self.orders_documents, self.stock, self.people_records,
                 self.field_work, self.repairs]
        for step in steps:
            step()
        self.bump_counters()
        self.db.flush()
        self.users_note()
        return self.report

    # ------------------------------------------------------------ организации, сотрудники
    def organizations(self) -> None:
        existing = {e.name.lower(): e.id for e in self.db.scalars(select(LegalEntity).where(LegalEntity.archived_at.is_(None)))}
        for o in self.rows("organizations"):
            old_id = o["id"]
            if old_id in self.maps["organizations"]:
                self.entity_of_org[old_id] = self.maps["organizations"][old_id]
                self.report.count("organizations", "linked")
                continue
            form = s(o.get("legal_form"))
            name = s(o["name"])
            full = f"{form} {name}" if form and form.lower() not in name.lower() else name
            eid = existing.get(full.lower()) or existing.get(name.lower())
            if eid:
                self.link("organizations", old_id, eid, existing=True)
            else:
                e = LegalEntity(name=full[:200], short_name=name[:80], phone=s(o.get("phone"), 40), email=s(o.get("email"), 120))
                self.db.add(e)
                self.db.flush()
                existing[full.lower()] = e.id
                eid = e.id
                self.link("organizations", old_id, eid)
            self.entity_of_org[old_id] = eid
        # юрлицо по умолчанию: названное, иначе первая организация, иначе уже имеющееся, иначе новое
        pick = None
        if self.default_name:
            pick = existing.get(self.default_name.lower())
            if pick is None:
                self.report.notes.append(f"юрлицо по умолчанию «{self.default_name}» не найдено среди организаций, взято первое")
        if pick is None and self.entity_of_org:
            pick = next(iter(self.entity_of_org.values()))
        if pick is None:
            pick = self.db.scalar(select(LegalEntity.id).where(LegalEntity.archived_at.is_(None)).order_by(LegalEntity.id).limit(1))
        if pick is None:
            e = LegalEntity(name="Основная организация", short_name="Основная")
            self.db.add(e)
            self.db.flush()
            pick = e.id
            self.report.notes.append("в старой базе нет организаций: создано юрлицо «Основная организация»")
        self.default_entity_id = pick
        self.entity_of_org[None] = pick

    def employees(self) -> None:
        def one(r: dict):
            eid = self.entity_of_org.get(r.get("org_id"), self.default_entity_id)
            note = s(r.get("note"))
            extra = []
            if s(r.get("tab_number")):
                extra.append(f"Табельный номер: {s(r['tab_number'])}")
            if s(r.get("phone_home")) and s(r.get("phone_work")):
                extra.append(f"Домашний телефон: {s(r['phone_home'])}")
            if extra:
                note = (note + "\n" if note else "") + "; ".join(extra)
            phone = s(r.get("phone_work")) or s(r.get("phone_home"))
            emp = Employee(last_name=s(r["last_name"], 80), first_name=s(r.get("first_name"), 80), middle_name=s(r.get("middle_name"), 80),
                           position=s(r.get("position"), 120), is_driver=bool(r.get("is_driver")), phone=phone[:40], birth_date=day(r.get("birth_date")), note=note)
            self.audit_times(emp, r)
            self.add("employees", r, emp)
            self.db.add(EmploymentAssignment(employee_id=emp.id, legal_entity_id=eid, position=s(r.get("position"), 120)))
            self.employee_entity[emp.id] = eid
            return emp
        for r in self.each("employees"):
            self.run_one("employees", r, one)
        # сотрудники, уже перенесённые раньше, тоже нужны для выбора юрлица
        for e_id, in_entity in self.db.execute(select(EmploymentAssignment.employee_id, EmploymentAssignment.legal_entity_id)):
            self.employee_entity.setdefault(e_id, in_entity)
        if any(r.get("salary_kop") for r in self.rows("employees")):
            self.report.notes.append("оклады сотрудников не переносятся: в новой программе нет такого поля")

    # ------------------------------------------------------------ техника
    TYPE_DEFAULT = {"truck": "Грузовой автомобиль", "car": "Легковой автомобиль", "trailer": "Прицеп", "special": "Спецтехника"}

    def vehicle_type(self, group: str, name: str) -> int:
        name = s(name, 80) or self.TYPE_DEFAULT.get(group, "Прочая техника")
        cache = getattr(self, "_types", None)
        if cache is None:
            cache = self._types = {t.name.lower(): t.id for t in self.db.scalars(select(VehicleType))}
        if name.lower() in cache:
            return cache[name.lower()]
        code = f"legacy-{slug(name)}"
        taken = set(self.db.scalars(select(VehicleType.code)))
        base, n = code, 2
        while code in taken:
            code, n = f"{base}-{n}", n + 1
        t = VehicleType(code=code, name=name, category=group if group in ("truck", "car", "trailer", "special") else "other",
                        primary_reading="engine_hours" if group == "special" else "km")
        self.db.add(t)
        self.db.flush()
        cache[name.lower()] = t.id
        return t.id

    @staticmethod
    def vehicle_status(r: dict) -> str:
        # статусов теперь два: ремонт или линия; списанные машины уходят в архив (см. ниже)
        if r.get("status") == "repair" or r.get("work_state") == "repair":
            return "in_repair"
        return "in_work"

    @staticmethod
    def fuel_kind(v: str) -> str:
        t = v.lower()
        if not t:
            return ""
        if "диз" in t or t in ("дт", "dt"):
            return "diesel"
        if "бенз" in t or t.startswith("аи"):
            return "petrol"
        if "газ" in t:
            return "gas"
        if "элек" in t:
            return "electric"
        return "other"

    def vehicles(self) -> None:
        active = {v.plate.upper(): v.id for v in self.db.scalars(select(Vehicle).where(Vehicle.archived_at.is_(None)))}
        vins = {v.vin.upper() for v in self.db.scalars(select(Vehicle).where(Vehicle.archived_at.is_(None), Vehicle.vin.is_not(None)))}
        for v_id, ent in self.db.execute(select(Vehicle.id, Vehicle.legal_entity_id)):
            self.vehicle_entity[v_id] = ent

        def one(r: dict):
            eid = self.entity_of_org.get(r.get("org_id"), self.default_entity_id)
            plate = s(r.get("plate"), 20).upper() or f"БЕЗ-НОМЕРА-{r['id']}"
            if plate in active:
                self.link("vehicles", r["id"], active[plate], existing=True)
                self.report.warn("vehicles", r["id"], f"машина с номером {plate} уже есть (в новой программе или в старой дважды): записи привязаны к ней")
                return active[plate]
            vin = s(r.get("vin"), 17).upper() or None
            note = s(r.get("note"))
            if vin and vin in vins:
                note = (note + "\n" if note else "") + f"VIN {vin} уже занят другой машиной, поэтому здесь не записан"
                vin = None
            if r.get("limit_km_month"):
                note = (note + "\n" if note else "") + f"Лимит пробега в месяц: {r['limit_km_month']} км"
            if s(r.get("work_state_note")):
                note = (note + "\n" if note else "") + f"Заметка о состоянии: {s(r['work_state_note'])}"
            status = self.vehicle_status(r)
            v = Vehicle(legal_entity_id=eid, type_id=self.vehicle_type(r.get("group_kind") or "truck", r.get("vehicle_type")), plate=plate,
                        internal_no=s(r.get("garage_number"), 30), make=s(r.get("make"), 60), model=s(r.get("model"), 60), vin=vin,
                        year=r.get("made_year"), status=status, archived_at=dt.datetime.now(dt.timezone.utc) if r.get("status") == "written_off" else None,
                        status_changed_at=moment(r.get("work_state_at"), self.tz) or moment(r.get("created_at"), ZoneInfo("UTC")),
                        note=note)
            self.audit_times(v, r)
            self.add("vehicles", r, v)
            active[plate] = v.id
            if vin:
                vins.add(vin)
            self.vehicle_entity[v.id] = eid
            fk = self.fuel_kind(s(r.get("fuel_type")))
            if fk or r.get("tank_capacity") or r.get("fuel_norm") or s(r.get("owner")):
                self.db.add(VehicleSpecs(vehicle_id=v.id, fuel_type=fk, tank_liters=dec(r.get("tank_capacity")), fuel_norm=dec(r.get("fuel_norm")), owner_name=s(r.get("owner"), 200)))
            started = day(r.get("created_at")) or self.today
            for key, role in (("driver1_id", "primary"), ("driver2_id", "backup")):
                drv = self.ref("employees", r.get(key))
                if drv:
                    self.db.add(VehicleAssignment(vehicle_id=v.id, employee_id=drv, role=role, started_on=started))
            return v
        for r in self.each("vehicles"):
            self.run_one("vehicles", r, one)
        self._vehicle_rows = {r["id"]: r for r in self.rows("vehicles")}

    def readings(self) -> None:
        by_vehicle: dict[int, list[dict]] = defaultdict(list)
        for r in self.rows("odometer_readings", "read_at, id"):
            by_vehicle[r["vehicle_id"]].append(r)
        touched: set[int] = set()
        for old_vid, items in by_vehicle.items():
            vid = self.ref("vehicles", old_vid)
            if not vid:
                self.report.count("odometer_readings", "skipped", len(items))
                continue
            fresh = [r for r in items if r["id"] not in self.maps["odometer_readings"]]
            self.report.count("odometer_readings", "linked", len(items) - len(fresh))
            if not fresh:
                continue
            # продолжаем от уже записанного в новой базе
            top = {k: self.db.scalar(select(func.max(VehicleReading.value)).where(VehicleReading.vehicle_id == vid, VehicleReading.kind == k,
                                                                                  VehicleReading.review_state.in_(("ok", "confirmed")))) or Decimal(0)
                   for k in ("km", "engine_hours")}
            for r in fresh:
                def one(r=r):
                    at = moment(r.get("read_at"), self.tz)
                    if at is None:
                        return None
                    src = r.get("source") if r.get("source") in READING_SOURCES else "import"
                    first = None
                    for kind, raw in (("km", r.get("km")), ("engine_hours", r.get("engine_hours"))):
                        val = dec(raw)
                        if val is None or (kind == "km" and val == 0 and r.get("engine_hours") is not None):
                            continue                                   # у машин с моточасами пробег 0 значит «пробега нет»
                        bad = val < top[kind]
                        if not bad:
                            top[kind] = val
                        row = VehicleReading(vehicle_id=vid, kind=kind, value=val, recorded_at=at, source=src, note=s(r.get("user_name"), 300),
                                             is_anomaly=bad, review_state="pending" if bad else "ok",
                                             review_comment=(f"Перенос: значение меньше предыдущего ({top[kind]})" if bad else ""))
                        self.db.add(row)
                        self.db.flush()
                        first = first or row
                        if bad:
                            self.report.warn("odometer_readings", r["id"], f"{kind}={val} меньше предыдущего {top[kind]}: оставлено на проверку")
                    if first is None:
                        return None
                    self.link("odometer_readings", r["id"], first.id)
                    return first
                self.run_one("odometer_readings", r, one)
            touched.add(vid)
        # начальные показания машины, если журнала по ней нет вовсе
        for old_vid, r in getattr(self, "_vehicle_rows", {}).items():
            vid = self.ref("vehicles", old_vid)
            if not vid or old_vid in by_vehicle:
                continue
            if self.db.scalar(select(func.count()).select_from(VehicleReading).where(VehicleReading.vehicle_id == vid)):
                continue
            at = moment(r.get("created_at"), ZoneInfo("UTC")) or dt.datetime.now(dt.timezone.utc)
            for kind, raw in (("km", r.get("odometer_start")), ("engine_hours", r.get("engine_hours_start"))):
                val = dec(raw)
                if val and val > 0:
                    self.db.add(VehicleReading(vehicle_id=vid, kind=kind, value=val, recorded_at=at, source="import", note="Начальное значение из старой программы"))
                    touched.add(vid)
        self.db.flush()
        for vid in touched:
            recompute_current(self.db, self.db.get(Vehicle, vid))

    def state_log(self) -> None:
        names = {"work": "in_work", "repair": "in_repair", "vacation": "parked", "dayoff": "day_off"}
        last: dict[int, str] = {}
        for r in self.rows("vehicle_state_log", "vehicle_id, set_at, id"):
            vid = self.ref("vehicles", r["vehicle_id"])
            if not vid or self.seen("vehicle_state_log", r["id"]):
                continue
            def one(r=r, vid=vid):
                status = names.get(r.get("state"), "in_work")
                h = VehicleStatusHistory(vehicle_id=vid, from_status=last.get(vid), status=status, reason=s(r.get("note"), 300), changed_at=moment(r.get("set_at"), self.tz) or dt.datetime.now(dt.timezone.utc))
                self.add("vehicle_state_log", r, h)
                last[vid] = status
                return h
            self.run_one("vehicle_state_log", r, one)

    # ------------------------------------------------------------ вкладки карточки техники и документы
    def vehicle_cards(self) -> None:
        kinds = {"СО": "so", "ТО-1": "to1", "ТО-2": "to2", "Другое": "other"}

        def maint(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            if not vid:
                return None
            return self.add("maintenance", r, MaintenanceItem(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, kind=kinds.get(s(r.get("kind")), "other"), work=s(r.get("work"), 300), interval_km=r.get("interval_km"),
                            due_km=r.get("due_odometer"), due_date=day(r.get("due_date")), done_at=day(r.get("done_at")), controlled=bool(r.get("controlled")), note=s(r.get("note"))))
        for r in self.each("maintenance"):
            self.run_one("maintenance", r, maint)

        def event(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            if not vid:
                return None
            return self.add("events", r, ControlEvent(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, event_type=s(r.get("event_type"), 80) or "Другое", title=s(r["title"], 200), unit=s(r.get("unit"), 80),
                            interval_km=r.get("interval_km"), due_km=r.get("due_odometer"), due_date=day(r.get("due_date")), due_hours=dec(r.get("due_hours")),
                            controlled=bool(r.get("controlled")), note=s(r.get("note"))))
        for r in self.each("events"):
            self.run_one("events", r, event)

        def tire(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            if not vid:
                return None
            return self.add("tires", r, VehicleTire(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, brand=s(r.get("brand"), 80), model=s(r.get("model"), 80), size=s(r.get("size"), 40), season=r.get("season") or "all",
                            serial=s(r.get("serial"), 60), position=s(r.get("position"), 60), install_date=day(r.get("install_date")), install_km=r.get("install_km"),
                            remove_date=day(r.get("remove_date")), remove_km=r.get("remove_km"), price=kop(r.get("price_kop")), wear_pct=r.get("wear_pct"), note=s(r.get("note"))))
        for r in self.each("tires"):
            self.run_one("tires", r, tire)

        def repair(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            d = day(r.get("repair_date"))
            if not vid or not d:
                return None
            return self.add("repairs", r, VehicleRepair(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, repair_date=d, odometer=r.get("odometer"), kind=r.get("kind") or "repair", title=s(r["title"], 200),
                            part_number=s(r.get("part_number"), 60), qty=dec(r.get("qty"), 3) or Decimal(1), cost=kop(r.get("cost_kop")), executor=s(r.get("executor"), 120), note=s(r.get("note"))))
        for r in self.each("repairs"):
            self.run_one("repairs", r, repair)

        def fin(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            d = day(r.get("op_date"))
            if not vid or not d:
                return None
            return self.add("vehicle_finance", r, VehicleFinance(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, op_date=d, kind=r.get("kind") or "expense", category=s(r.get("category"), 80), amount=kop(r.get("amount_kop")),
                            description=s(r.get("description"), 300), note=s(r.get("note"))))
        for r in self.each("vehicle_finance"):
            self.run_one("vehicle_finance", r, fin)

        def insurance(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            end = day(r.get("end_date"))
            if not vid:
                return None
            kind_text = s(r.get("kind")) or "ОСАГО"
            low = kind_text.lower()
            kind = "osago" if "осаго" in low else "kasko" if "каско" in low else "other"
            start = day(r.get("start_date"))
            if start and end and end < start:
                start = None
            bits = []
            if r.get("amount_kop"):
                bits.append(f"Сумма: {kop(r['amount_kop'])} ₽")
            if day(r.get("paid_at")):
                bits.append(f"Оплачено: {day(r['paid_at']).strftime('%d.%m.%Y')}")
            if s(r.get("note")):
                bits.append(s(r["note"]))
            doc = DossierDoc(vehicle_id=vid, kind=kind, title=kind_text[:200] if kind == "other" else "", doc_number=s(r.get("policy_number"), 60), issued_on=start, valid_until=end,
                             issued_by=s(r.get("company"), 200), note="; ".join(bits))
            if not r.get("controlled"):
                doc.archived_at = dt.datetime.now(dt.timezone.utc)
                doc.archive_reason = "Перенос: в старой программе не стояла на контроле"
            return self.add("insurance", r, doc)
        for r in self.each("insurance"):
            self.run_one("insurance", r, insurance)

    # ------------------------------------------------------------ справочники
    def directories(self) -> None:
        names = {c.name.lower(): c.id for c in self.db.scalars(select(Counterparty).where(Counterparty.archived_at.is_(None)))}

        def cp(r):
            key = s(r["name"]).lower()
            if key in names:
                self.link("counterparties", r["id"], names[key], existing=True)
                return names[key]
            c = Counterparty(name=s(r["name"], 200), kind=r.get("kind") or "customer", inn=s(r.get("inn"), 12), kpp=s(r.get("kpp"), 9), address=s(r.get("address"), 300),
                             phone=s(r.get("phone"), 40), email=s(r.get("email"), 120), contact=s(r.get("contact"), 120), bank_name=s(r.get("bank_name"), 120), bik=s(r.get("bik"), 9),
                             account=s(r.get("account"), 20), corr_account=s(r.get("corr_account"), 20), note=s(r.get("note")))
            self.add("counterparties", r, c)
            names[key] = c.id
            return c
        for r in self.each("counterparties"):
            self.run_one("counterparties", r, cp)

        tariffs = {t.name.lower(): t.id for t in self.db.scalars(select(Tariff).where(Tariff.archived_at.is_(None)))}

        def tariff(r):
            key = s(r["name"]).lower()
            if key in tariffs:
                self.link("tariffs", r["id"], tariffs[key], existing=True)
                return tariffs[key]
            t = self.add("tariffs", r, Tariff(name=s(r["name"], 160), unit=r.get("unit") or "km", rate=kop(r.get("rate_kop")), min_amount=kop(r.get("min_kop")), note=s(r.get("note"))))
            tariffs[key] = t.id
            return t
        for r in self.each("tariffs"):
            self.run_one("tariffs", r, tariff)

        consts = {c.name.lower(): c.id for c in self.db.scalars(select(Constant).where(Constant.archived_at.is_(None)))}

        def const(r):
            key = s(r["name"]).lower()
            if key in consts:
                self.link("constants", r["id"], consts[key], existing=True)
                return consts[key]
            c = self.add("constants", r, Constant(name=s(r["name"], 120), value=s(r.get("value"), 300), note=""))
            consts[key] = c.id
            return c
        for r in self.each("constants"):
            self.run_one("constants", r, const)

        norms = {n.name.lower(): n.id for n in self.db.scalars(select(PpeNorm).where(PpeNorm.archived_at.is_(None)))}

        def norm(r):
            key = s(r["name"]).lower()
            if key in norms:
                self.link("ppe_norms", r["id"], norms[key], existing=True)
                return norms[key]
            n = self.add("ppe_norms", r, PpeNorm(name=s(r["name"], 160), wear_months=r.get("wear_months") or None, note=s(r.get("note"), 300)))
            norms[key] = n.id
            return n
        for r in self.each("ppe_norms"):
            self.run_one("ppe_norms", r, norm)

        parts = {p.name_key: p.id for p in self.db.scalars(select(PartCatalog))}

        def part(r):
            key = (s(r.get("name_key")) or s(r["name"]).lower())[:200]
            if key in parts:
                self.link("parts_catalog", r["id"], parts[key], existing=True)
                return parts[key]
            p = self.add("parts_catalog", r, PartCatalog(name=s(r["name"], 200), name_key=key, article=s(r.get("article"), 60), unit=s(r.get("unit"), 12) or "шт",
                                                         category=s(r.get("category"), 60), uses=r.get("uses") or 0, note=s(r.get("note"), 300)))
            parts[key] = p.id
            return p
        for r in self.each("parts_catalog"):
            self.run_one("parts_catalog", r, part)

    # ------------------------------------------------------------ деньги и расходы по технике
    def money_by_vehicle(self) -> None:
        cards = {c.number: c.id for c in self.db.scalars(select(FuelCard).where(FuelCard.archived_at.is_(None)))}

        def card(r):
            if s(r["number"], 40) in cards:
                self.link("fuel_cards", r["id"], cards[s(r["number"], 40)], existing=True)
                return cards[s(r["number"], 40)]
            eid = self.entity_for(vehicle=r.get("vehicle_id"))
            c = self.add("fuel_cards", r, FuelCard(legal_entity_id=eid, number=s(r["number"], 40), provider=s(r.get("provider"), 80), vehicle_id=self.ref("vehicles", r.get("vehicle_id")),
                                                    limit_month_l=dec(r.get("limit_month_l")), valid_until=day(r.get("valid_until")), note=s(r.get("note"))))
            cards[c.number] = c.id
            return c
        for r in self.each("fuel_cards"):
            self.run_one("fuel_cards", r, card)

        def fill(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            d = day(r.get("fill_date"))
            if not vid or not d:
                return None
            return self.add("fuel_fills", r, FuelFill(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, fill_date=d, liters=dec(r["liters"], 2), amount=kop(r.get("amount_kop")),
                            odometer=r.get("odometer"), full_tank=bool(r.get("full_tank")), station=s(r.get("station"), 80), note=s(r.get("note"), 300), client_id=s(r.get("client_id"), 64)))
        for r in self.each("fuel_fills"):
            self.run_one("fuel_fills", r, fill)

        def fine(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            d = day(r.get("fine_date"))
            if not vid or not d:
                return None
            note = s(r.get("note"))
            if r.get("driver_told"):
                note = (note + "\n" if note else "") + "Водителю сообщили"
            return self.add("vehicle_fines", r, Fine(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, fine_date=d, resolution_number=s(r.get("resolution_number"), 60),
                            article=s(r.get("article"), 120), amount=kop(r.get("amount_kop")), discount_until=day(r.get("discount_until")), due_date=day(r.get("due_date")),
                            payer=r.get("payer") or "company", paid_at=day(r.get("paid_at")), note=note))
        for r in self.each("vehicle_fines"):
            self.run_one("vehicle_fines", r, fine)

        def rental(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            d = day(r.get("start_date"))
            if not vid or not d:
                return None
            return self.add("vehicle_rentals", r, Rental(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, direction=r.get("direction") or "out",
                            counterparty_id=self.ref("counterparties", r.get("counterparty_id")), start_date=d, end_date=day(r.get("end_date")), rate=kop(r.get("rate_kop")),
                            finished=bool(r.get("finished")), note=s(r.get("note"))))
        for r in self.each("vehicle_rentals"):
            self.run_one("vehicle_rentals", r, rental)

        def misc(r):
            d = day(r.get("op_date"))
            if not d:
                return None
            return self.add("misc_expenses", r, MiscExpense(legal_entity_id=self.default_entity_id, op_date=d, category=s(r.get("category"), 80), amount=kop(r.get("amount_kop")),
                                                            description=s(r.get("description"), 300)))
        for r in self.each("misc_expenses"):
            self.run_one("misc_expenses", r, misc)

    # ------------------------------------------------------------ касса
    def cash(self) -> None:
        def account(r):
            return self.add("cash_accounts", r, CashAccount(legal_entity_id=self.default_entity_id, name=s(r["name"], 120), kind=r.get("kind") or "cash", opening=kop(r.get("opening_kop")), note=s(r.get("note"), 300)))
        for r in self.each("cash_accounts"):
            self.run_one("cash_accounts", r, account)

        counters = {"in": "cash_in", "out": "cash_out", "transfer": "cash_transfer"}

        def op(r):
            acc = self.ref("cash_accounts", r["account_id"])
            d = day(r.get("op_date"))
            if not acc or not d:
                return None
            eid = self.db.get(CashAccount, acc).legal_entity_id
            kind = r["kind"]
            n, changed = self.alloc_number(eid, counters[kind], d.year, r.get("number"), CashOp, extra={"kind": kind})
            note = s(r.get("note"), 300)
            if changed and s(r.get("number")):
                note = (f"Номер в старой программе: {s(r['number'])}. " + note)[:300]
            return self.add("cash_ops", r, CashOp(legal_entity_id=eid, op_date=d, kind=kind, account_id=acc, to_account_id=self.ref("cash_accounts", r.get("to_account_id")) if kind == "transfer" else None,
                            amount=kop(r.get("amount_kop")), year=d.year, number=n, category=s(r.get("category"), 80), counterparty_id=self.ref("counterparties", r.get("counterparty_id")),
                            employee_id=self.ref("employees", r.get("employee_id")), note=note))
        for r in self.each("cash_ops", "op_date, id"):
            self.run_one("cash_ops", r, op)

    # ------------------------------------------------------------ заказы и документы
    def orders_documents(self) -> None:
        def order(r):
            d = day(r.get("order_date"))
            cust = self.ref("counterparties", r["customer_id"])
            if not d or not cust:
                return None
            eid = self.entity_for(vehicle=r.get("vehicle_id"))
            n, changed = self.alloc_number(eid, "order", d.year, r.get("number"), Order)
            note = s(r.get("note"))
            if changed:
                note = (f"Номер в старой программе: {s(r.get('number'))}\n" + note).strip()
            vid = self.ref("vehicles", r.get("vehicle_id"))
            drv = self.ref("employees", r.get("driver_id"))
            if drv and self.employee_entity.get(drv) != eid:
                self.report.warn("orders", r["id"], "водитель из другого юрлица: в заказе не указан")
                drv = None
            return self.add("orders", r, Order(legal_entity_id=eid, year=d.year, number=n, order_date=d, status=r.get("status") or "new", customer_id=cust,
                            point_from=s(r.get("point_from"), 200), point_to=s(r.get("point_to"), 200), cargo=s(r.get("cargo"), 200), cargo_kg=dec(r.get("cargo_kg")), distance_km=r.get("distance_km"),
                            hours=dec(r.get("hours"), 2), depart_at=moment(r.get("depart_at"), self.tz), deliver_at=moment(r.get("deliver_at"), self.tz), vehicle_id=vid, driver_id=drv,
                            tariff_id=self.ref("tariffs", r.get("tariff_id")), qty=dec(r.get("qty"), 3), rate=kop(r.get("rate_kop")), amount=kop(r.get("amount_kop")),
                            vat_pct=r.get("vat_pct") or 0, note=note))
        for r in self.each("orders", "order_date, id"):
            self.run_one("orders", r, order)

        def document(r):
            d = day(r.get("doc_date"))
            cp = self.ref("counterparties", r["counterparty_id"])
            if not d or not cp:
                return None
            oid = self.ref("orders", r.get("order_id"))
            eid = self.db.get(Order, oid).legal_entity_id if oid else self.default_entity_id
            n, changed = self.alloc_number(eid, "doc_" + r["doc_type"], d.year, r.get("number"), Document, extra={"doc_type": r["doc_type"]})
            note = s(r.get("note"))
            if changed:
                note = (f"Номер в старой программе: {s(r.get('number'))}\n" + note).strip()
            return self.add("documents", r, Document(legal_entity_id=eid, doc_type=r["doc_type"], year=d.year, number=n, doc_date=d, order_id=oid, counterparty_id=cp,
                            subject=s(r.get("subject")), qty=dec(r.get("qty"), 3) or Decimal(1), unit=s(r.get("unit"), 20) or "усл.", price=kop(r.get("price_kop")),
                            vat_pct=r.get("vat_pct") or 0, paid_date=day(r.get("paid_date")), note=note))
        for r in self.each("documents", "doc_date, id"):
            self.run_one("documents", r, document)

    # ------------------------------------------------------------ склад
    def stock(self) -> None:
        wh = {w.name.lower(): w.id for w in self.db.scalars(select(Warehouse).where(Warehouse.archived_at.is_(None), Warehouse.legal_entity_id == self.default_entity_id))}

        def warehouse(r):
            if s(r["name"]).lower() in wh:
                self.link("warehouses", r["id"], wh[s(r["name"]).lower()], existing=True)
                return wh[s(r["name"]).lower()]
            w = self.add("warehouses", r, Warehouse(legal_entity_id=self.default_entity_id, name=s(r["name"], 120), note=s(r.get("note"))))
            wh[s(r["name"]).lower()] = w.id
            return w
        for r in self.each("warehouses"):
            self.run_one("warehouses", r, warehouse)

        items = {i.name_key: i.id for i in self.db.scalars(select(StockItem).where(StockItem.archived_at.is_(None), StockItem.legal_entity_id == self.default_entity_id))}

        def item(r):
            key = s(r["name"]).lower()[:200]
            if key in items:
                self.link("stock_items", r["id"], items[key], existing=True)
                return items[key]
            i = self.add("stock_items", r, StockItem(legal_entity_id=self.default_entity_id, name=s(r["name"], 200), name_key=key, sku=s(r.get("sku"), 60), unit=s(r.get("unit"), 12) or "шт.",
                                                      min_qty=dec(r.get("min_qty"), 3) or Decimal(0), note=s(r.get("note"))))
            items[key] = i.id
            return i
        for r in self.each("stock_items"):
            self.run_one("stock_items", r, item)

        def move(r):
            d = day(r.get("move_date"))
            it, w = self.ref("stock_items", r["item_id"]), self.ref("warehouses", r["warehouse_id"])
            if not d or not it or not w:
                return None
            kind = r["kind"]
            return self.add("stock_moves", r, StockMove(legal_entity_id=self.default_entity_id, move_date=d, kind=kind, item_id=it, warehouse_id=w,
                            to_warehouse_id=self.ref("warehouses", r.get("to_warehouse_id")) if kind == "transfer" else None, qty=dec(r["qty"], 3), price=kop(r.get("price_kop")),
                            counterparty_id=self.ref("counterparties", r.get("counterparty_id")), vehicle_id=self.ref("vehicles", r.get("vehicle_id")),
                            doc_number=s(r.get("doc_number"), 60), note=s(r.get("note"), 300)))
        for r in self.each("stock_moves", "move_date, id"):
            self.run_one("stock_moves", r, move)

    # ------------------------------------------------------------ записи о людях
    def people_records(self) -> None:
        def pay(r):
            emp = self.ref("employees", r["employee_id"])
            d = day(r.get("pay_date"))
            if not emp or not d:
                return None
            return self.add("employee_payments", r, EmployeePayment(legal_entity_id=self.employee_entity.get(emp, self.default_entity_id), employee_id=emp, pay_date=d,
                            kind=r.get("kind") or "other", amount=kop(r.get("amount_kop")), note=s(r.get("note"), 300)))
        for r in self.each("employee_payments"):
            self.run_one("employee_payments", r, pay)

        def issue(r):
            emp, ppe = self.ref("employees", r["employee_id"]), self.ref("ppe_norms", r["ppe_id"])
            d = day(r.get("issue_date"))
            if not emp or not ppe or not d:
                return None
            return self.add("ppe_issues", r, PpeIssue(legal_entity_id=self.employee_entity.get(emp, self.default_entity_id), employee_id=emp, ppe_id=ppe, issue_date=d,
                            qty=r.get("qty") or 1, size=s(r.get("size"), 20), due_date=day(r.get("due_date")), returned_date=day(r.get("returned_date")), note=s(r.get("note"), 300)))
        for r in self.each("ppe_issues"):
            self.run_one("ppe_issues", r, issue)

        labels = {v.lower(): k for k, v in EMPLOYEE_EVENT_KINDS.items()}

        def event(r):
            emp = self.ref("employees", r["employee_id"])
            if not emp:
                return None
            raw = s(r.get("kind"))
            kind = labels.get(raw.lower(), "other")
            desc = s(r.get("description"), 300)
            if kind == "other" and raw and raw.lower() != "другое":
                desc = (f"{raw}: {desc}" if desc else raw)[:300]
            return self.add("employee_events", r, EmployeeEvent(legal_entity_id=self.employee_entity.get(emp, self.default_entity_id), employee_id=emp, kind=kind,
                            event_date=day(r.get("event_date")), due_date=day(r.get("due_date")), amount=kop(r.get("amount_kop")), description=desc,
                            controlled=bool(r.get("controlled")), note=s(r.get("note"), 500)))
        for r in self.each("employee_events"):
            self.run_one("employee_events", r, event)

        def mark(r):
            emp = self.ref("employees", r["employee_id"])
            d = day(r.get("work_date"))
            code = s(r.get("code"))
            if not emp or not d:
                return None
            if code not in TIMESHEET_CODES:
                self.report.warn("timesheet_marks", r["id"], f"неизвестный код табеля «{code}»: отметка не перенесена")
                return None
            return self.add("timesheet_marks", r, TimesheetMark(legal_entity_id=self.employee_entity.get(emp, self.default_entity_id), employee_id=emp, work_date=d, code=code, hours=dec(r.get("hours"))))
        for r in self.each("timesheet_marks", "work_date, id"):
            self.run_one("timesheet_marks", r, mark)

        def cal(r):
            start = moment(r.get("start_at"), self.tz)
            if not start:
                return None
            eid = self.entity_for(vehicle=r.get("vehicle_id"), employee=r.get("employee_id"))
            return self.add("calendar_events", r, CalendarEvent(legal_entity_id=eid, title=s(r["title"], 160), start_at=start, end_at=moment(r.get("end_at"), self.tz), all_day=bool(r.get("all_day")),
                            color=r.get("color") if r.get("color") in CALENDAR_COLORS else "teal", vehicle_id=self.ref("vehicles", r.get("vehicle_id")),
                            employee_id=self.ref("employees", r.get("employee_id")), remind_min=r.get("remind_min"), done=bool(r.get("done")), note=s(r.get("note"), 500)))
        for r in self.each("calendar_events", "start_at, id"):
            self.run_one("calendar_events", r, cal)

    # ------------------------------------------------------------ рейсы, наряды, путевые листы
    def field_work(self) -> None:
        def trip(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            d = day(r.get("trip_date"))
            if not vid or not d:
                return None
            return self.add("trips", r, Trip(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, trip_date=d, route=s(r.get("route"), 200), customer=s(r.get("customer"), 120),
                            cargo=s(r.get("cargo"), 200), cargo_kg=dec(r.get("cargo_kg")), doc_number=s(r.get("doc_number"), 60), km=r.get("km"), note=s(r.get("note"), 500),
                            client_id=s(r.get("client_id"), 64)))
        for r in self.each("trips", "trip_date, id"):
            self.run_one("trips", r, trip)

        def job(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            d = day(r.get("job_date"))
            if not vid or not d:
                return None
            t = s(r.get("time_from"))
            return self.add("jobs", r, Job(legal_entity_id=self.vehicle_entity[vid], vehicle_id=vid, job_date=d, time_from=t[:5] if re.match(r"^\d{1,2}:\d{2}", t) else "", customer=s(r.get("customer"), 120),
                            address=s(r["address"], 200), task=s(r.get("task"), 1000), state=r.get("state") or "planned", arrived_at=moment(r.get("arrived_at"), self.tz),
                            finished_at=moment(r.get("finished_at"), self.tz), result_note=s(r.get("result_note"), 1000)))
        for r in self.each("jobs", "job_date, id"):
            self.run_one("jobs", r, job)

        open_vehicles: set[int] = set()

        def waybill(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            drv = self.ref("employees", r.get("driver_id"))
            d = day(r.get("issue_date"))
            if not vid or not d:
                return None
            if not drv:
                self.report.warn("waybills", r["id"], "в путевом листе нет водителя: в новой программе он обязателен, лист не перенесён")
                return None
            eid = self.vehicle_entity[vid]
            n, changed = self.alloc_number(eid, "waybill", d.year, r.get("number"), Waybill)
            status = {"draft": "draft", "closed": "closed", "issued": "open"}.get(r.get("status"), "draft")
            valid_to = day(r.get("valid_to"))
            if status == "open" and (vid in open_vehicles or (valid_to and valid_to < self.today)):
                status = "closed"
            if status == "open":
                open_vehicles.add(vid)
            km_out, km_in = dec(r.get("odo_out")), dec(r.get("odo_in"))
            if km_out is not None and km_in is not None and km_in < km_out:
                self.report.warn("waybills", r["id"], "пробег по возвращении меньше пробега при выезде: конечный пробег не перенесён")
                km_in = None
            h_out, h_in = dec(r.get("hours_out")), dec(r.get("hours_in"))
            if h_out is not None and h_in is not None and h_in < h_out:
                h_in = None
            bits = []
            if changed:
                bits.append(f"Номер в старой программе: {s(r.get('number'))}")
            for label, key in (("Заказчик", "customer"), ("Груз", "cargo"), ("Выдал медосмотр", "medic_by"), ("Техосмотр", "tech_by"), ("Заметка", "note")):
                if s(r.get(key)):
                    bits.append(f"{label}: {s(r[key])}")
            return self.add("waybills", r, Waybill(legal_entity_id=eid, year=d.year, number=n, vehicle_id=vid, driver_id=drv, shift="day", work_date=d, status=status, route=s(r.get("route"), 300),
                            start_km=km_out, end_km=km_in, start_hours=h_out, end_hours=h_in, fuel_start_l=dec(r.get("fuel_out")), fuel_added_l=dec(r.get("fuel_issued")),
                            fuel_end_l=dec(r.get("fuel_in")), note="\n".join(bits), opened_at=moment(r.get("depart_at"), self.tz) or moment(r.get("issue_date"), self.tz),
                            closed_at=(moment(r.get("return_at"), self.tz) or moment(r.get("valid_to"), self.tz)) if status == "closed" else None))
        for r in self.each("waybills", "issue_date, id"):
            self.run_one("waybills", r, waybill)

    # ------------------------------------------------------------ обращения и счета на ремонт
    def repairs(self) -> None:
        prio = {"normal": "normal", "urgent": "high", "stopped": "urgent"}
        state = {"new": "new", "in_work": "in_progress", "parts": "waiting_parts", "done": "done", "rejected": "cancelled"}

        def ticket(r):
            vid = self.ref("vehicles", r["vehicle_id"])
            if not vid:
                return None
            eid = self.vehicle_entity[vid]
            n, _ = self.alloc_number(eid, "request", 0, None, ServiceRequest, by_year=False)
            text_ = s(r["text"])
            title = text_.splitlines()[0][:200]
            note = ""
            if s(r.get("author_name")):
                note = f"Автор в старой программе: {s(r['author_name'])}"
            req = ServiceRequest(legal_entity_id=eid, number=n, vehicle_id=vid, title=title, description=(text_ + ("\n\n" + note if note else "")), priority=prio.get(r.get("priority"), "normal"),
                                 status=state.get(r.get("state"), "new"), due_date=day(r.get("due_date")), closed_at=moment(r.get("closed_at"), self.tz))
            created = moment(r.get("created_at"), self.tz)
            if created:
                req.created_at = created
            return self.add("tickets", r, req)
        for r in self.each("tickets"):
            self.run_one("tickets", r, ticket)

        def comment(r):
            rid = self.ref("tickets", r["ticket_id"])
            if not rid:
                return None
            who = s(r.get("author_name"))
            ev = ServiceRequestEvent(request_id=rid, kind="comment", text=(f"[{who}] " if who else "") + s(r["text"]))
            created = moment(r.get("created_at"), self.tz)
            if created:
                ev.created_at = created
            return self.add("ticket_comments", r, ev)
        for r in self.each("ticket_comments"):
            self.run_one("ticket_comments", r, comment)

        def part(r):
            rid = self.ref("tickets", r["ticket_id"])
            if not rid:
                return None
            bought = moment(r.get("bought_at"), self.tz)
            return self.add("ticket_parts", r, RequestPart(request_id=rid, name=s(r["name"], 200), article=s(r.get("article"), 60), qty=dec(r.get("qty"), 3) or Decimal(1),
                            unit=s(r.get("unit"), 12) or "шт", bought_at=bought))
        for r in self.each("ticket_parts"):
            self.run_one("ticket_parts", r, part)

        def bill(r):
            d = day(r.get("bill_date"))
            if not d:
                return None
            vid = self.ref("vehicles", r.get("vehicle_id"))
            eid = self.vehicle_entity[vid] if vid else self.default_entity_id
            b = RepairBill(legal_entity_id=eid, kind=r.get("kind") or "uploaded", number=s(r["number"], 40), bill_date=d, supplier_id=self.ref("counterparties", r.get("supplier_id")),
                           supplier_name=s(r.get("supplier_name"), 160), supplier_inn=s(r.get("supplier_inn"), 12), supplier_kpp=s(r.get("supplier_kpp"), 9), supplier_addr=s(r.get("supplier_addr"), 250),
                           supplier_phone=s(r.get("supplier_phone"), 40), bank_name=s(r.get("bank_name"), 160), bik=s(r.get("bik"), 9), corr_account=s(r.get("corr_account"), 20),
                           account=s(r.get("account"), 20), basis=s(r.get("basis"), 120), vehicle_id=vid, request_id=self.ref("tickets", r.get("ticket_id")), vat_pct=r.get("vat_pct") if r.get("vat_pct") is not None else 22,
                           total=kop(r.get("total_kop")), vat=kop(r.get("vat_kop")), status=r.get("status") or "unpaid", paid_date=day(r.get("paid_date")), note=s(r.get("note"), 500))
            return self.add("repair_bills", r, b)
        for r in self.each("repair_bills", "bill_date, id"):
            self.run_one("repair_bills", r, bill)

        def item(r):
            bid = self.ref("repair_bills", r["bill_id"])
            if not bid:
                return None
            return self.add("repair_bill_items", r, RepairBillItem(bill_id=bid, pos=r.get("pos") or 1, name=s(r["name"], 200), article=s(r.get("article"), 60), qty=dec(r.get("qty"), 3) or Decimal(1),
                            unit=s(r.get("unit"), 12) or "шт", price=kop(r.get("price_kop")), amount=kop(r.get("sum_kop")), vat=kop(r.get("vat_kop"))))
        for r in self.each("repair_bill_items", "bill_id, pos, id"):
            self.run_one("repair_bill_items", r, item)

    # ------------------------------------------------------------ что не перенесено
    def users_note(self) -> None:
        for table, why in SKIPPED.items():
            if table in self.old_tables:
                n = self.old.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if n:
                    self.report.stats[table]["read"] = n
                    self.report.stats[table]["skipped"] = n
                    self.report.notes.append(f"не переносится: {table} ({n} зап.) — {why}")
        if "users" in self.old_tables:
            roles = {"admin": "администратор", "manager": "руководитель", "viewer": "просмотр", "driver": "водитель", "mechanic": "механик"}
            rows = self.old.execute("SELECT login, display_name, role, active FROM users ORDER BY id").fetchall()
            if rows:
                self.report.notes.append("пользователи старой программы (создайте их заново в разделе «Пользователи»): " +
                                         "; ".join(f"{lg} ({dn or '—'}, {roles.get(rl, rl)}{'' if ac else ', отключён'})" for lg, dn, rl, ac in rows))


def open_old(path: str) -> tuple[sqlite3.Connection, str]:
    """Старая база открывается только на чтение, и не сам файл, а его временная копия (вместе с -wal и -shm, если они есть):
    так не важно, кто владелец файла и открыта ли старая программа. Возвращает соединение и папку копии (её нужно удалить)."""
    tmp = tempfile.mkdtemp(prefix="old-db-")
    try:
        for suffix in ("", "-wal", "-shm"):
            if os.path.isfile(path + suffix):
                shutil.copyfile(path + suffix, os.path.join(tmp, "old.db" + suffix))
        con = sqlite3.connect(f"file:{os.path.join(tmp, 'old.db')}?mode=ro", uri=True)
        con.row_factory = None
        con.execute("select count(*) from sqlite_master").fetchone()      # не SQLite или повреждён — ошибка здесь, а не посреди переноса
        return con, tmp
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def run_import(db: Session, path: str, *, apply: bool, default_entity: str = "", tz: str | None = None) -> Report:
    old, tmp = open_old(path)
    try:
        tables = {r[0] for r in old.execute("select name from sqlite_master where type='table'")}
        if not tables & {"vehicles", "employees", "organizations"}:
            raise ValueError("в этом файле нет таблиц старой программы (транспорт, сотрудники): проверьте, тот ли файл выбран")
        imp = Importer(db, old, default_entity=default_entity, tz=tz)
        report = imp.run()
    finally:
        old.close()
        shutil.rmtree(tmp, ignore_errors=True)
    if apply:
        db.commit()
    else:
        db.rollback()
    return report
