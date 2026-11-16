# -*- coding: utf-8 -*-
"""Перенос из старой программы: проверяется на вымышленной копии старой базы (tests/legacy_fixture.py)."""
import datetime as dt
import sqlite3
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.legacy_import import run_import
from app.models import (CalendarEvent, CashAccount, CashOp, ControlEvent, Counterparty, Document, DossierDoc, Employee, EmployeeEvent, EmploymentAssignment, Fine,
                        FuelFill, Job, LegacyId, LegalEntity, MaintenanceItem, MiscExpense, NumberCounter, Order, PpeIssue, RepairBill, RepairBillItem, Rental, RequestPart,
                        ServiceRequest, ServiceRequestEvent, StockMove, TimesheetMark, Trip, Vehicle, VehicleAssignment, VehicleFinance, VehicleReading, VehicleRepair,
                        VehicleSpecs, VehicleStatusHistory, VehicleTire, VehicleType, Waybill)
from tests.legacy_fixture import make_old_db


@pytest.fixture()
def old_db(tmp_path):
    return make_old_db(str(tmp_path / "old.db"))


def do_import(path, apply=True, **kw):
    with session_factory()() as db:
        return run_import(db, path, apply=apply, **kw)


def count(model):
    with session_factory()() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_preview_writes_nothing(old_db):
    before = count(LegalEntity)
    report = do_import(old_db, apply=False)
    assert count(Vehicle) == 0 and count(Employee) == 0 and count(LegacyId) == 0 and count(LegalEntity) == before
    assert report.stats["vehicles"]["new"] == 4                       # а показано то, что получилось бы


def test_organizations_employees_vehicles(old_db):
    do_import(old_db)
    with session_factory()() as db:
        names = {e.name for e in db.scalars(select(LegalEntity))}
        assert {"ООО Север", "ИП Юг"} <= names
        north = db.scalar(select(LegalEntity).where(LegalEntity.name == "ООО Север"))
        emp = db.scalar(select(Employee).where(Employee.last_name == "Ершов"))
        assert emp.is_driver and emp.phone == "+7 900 000-00-01" and "Табельный номер: 12" in emp.note and "Домашний телефон" in emp.note
        assert db.scalar(select(EmploymentAssignment.legal_entity_id).where(EmploymentAssignment.employee_id == emp.id)) == north.id
        v1 = db.scalar(select(Vehicle).where(Vehicle.plate == "А101АА154"))
        assert v1.legal_entity_id == north.id and v1.status == "in_work" and v1.vin == "XTC651150A0000001" and v1.year == 2015 and v1.internal_no == "101"
        assert "Лимит пробега в месяц: 6000 км" in v1.note
        specs = db.get(VehicleSpecs, v1.id)
        assert specs.fuel_type == "diesel" and specs.tank_liters == Decimal("350.0") and specs.fuel_norm == Decimal("32.5") and specs.owner_name == "ООО Север"
        t1 = db.get(VehicleType, v1.type_id)
        assert (t1.name, t1.category, t1.primary_reading) == ("Самосвал", "truck", "km")
        v2 = db.scalar(select(Vehicle).where(Vehicle.plate == "Т202ТТ54"))
        assert v2.status == "in_repair" and db.get(VehicleType, v2.type_id).primary_reading == "engine_hours"
        v3 = db.scalar(select(Vehicle).where(Vehicle.plate == "БЕЗ-НОМЕРА-3"))          # у прицепа не было номера
        assert db.get(VehicleType, v3.type_id).name == "Прицеп" and db.get(LegalEntity, v3.legal_entity_id).name == "ИП Юг"
        assert db.scalar(select(Vehicle.status).where(Vehicle.plate == "В303ВВ54")) == "in_work"
        drv = db.scalars(select(VehicleAssignment).where(VehicleAssignment.vehicle_id == v1.id)).all()
        assert len(drv) == 1 and drv[0].role == "primary" and drv[0].employee_id == emp.id


def test_readings_keep_journal_and_current_values(old_db):
    report = do_import(old_db)
    with session_factory()() as db:
        v1 = db.scalar(select(Vehicle).where(Vehicle.plate == "А101АА154"))
        rows = db.scalars(select(VehicleReading).where(VehicleReading.vehicle_id == v1.id).order_by(VehicleReading.recorded_at)).all()
        assert [(r.value, r.review_state) for r in rows] == [(Decimal("1000.0"), "ok"), (Decimal("1500.0"), "ok"), (Decimal("1400.0"), "pending"), (Decimal("2100.0"), "ok")]
        assert rows[2].is_anomaly and "меньше предыдущего" in rows[2].review_comment
        assert v1.current_km == Decimal("2100.0")                                # меньшее значение на проверке и в пробег не попало
        assert rows[0].recorded_at == dt.datetime(2026, 1, 10, 1, 0, tzinfo=dt.timezone.utc)   # 08:00 по Новосибирску
        v2 = db.scalar(select(Vehicle).where(Vehicle.plate == "Т202ТТ54"))
        assert v2.current_hours == Decimal("1650.5") and v2.current_km is None   # пробег 0 у машины с моточасами не записан
        v4 = db.scalar(select(Vehicle).where(Vehicle.plate == "В303ВВ54"))
        assert v4.current_km == Decimal("5000.0")                                # начальный пробег, потому что журнала не было
        hist = db.scalars(select(VehicleStatusHistory).where(VehicleStatusHistory.vehicle_id == v2.id).order_by(VehicleStatusHistory.changed_at)).all()
        assert [(h.from_status, h.status, h.reason) for h in hist] == [(None, "in_work", ""), ("in_work", "in_repair", "Гидравлика")]
    assert any("меньше предыдущего" in w[2] for w in report.warnings if w[0] == "odometer_readings")


def test_vehicle_cards_and_insurance(old_db):
    do_import(old_db)
    with session_factory()() as db:
        m = db.scalars(select(MaintenanceItem).order_by(MaintenanceItem.id)).all()
        assert (m[0].kind, m[0].due_km, m[0].interval_km, m[0].controlled, m[0].done_at) == ("to1", 12000, 10000, True, None)
        assert (m[1].kind, m[1].controlled, m[1].done_at) == ("so", False, dt.date(2026, 10, 18))
        ev = db.scalars(select(ControlEvent).order_by(ControlEvent.id)).all()
        assert ev[0].due_date == dt.date(2027, 1, 15) and ev[1].due_hours == Decimal("1800.0")
        tires = db.scalars(select(VehicleTire).order_by(VehicleTire.id)).all()
        assert tires[0].price == Decimal("35000.00") and tires[0].season == "winter" and tires[1].remove_km == 800
        rep = db.scalar(select(VehicleRepair))
        assert rep.cost == Decimal("4500.50") and rep.qty == Decimal("2.000") and rep.executor == "СТО Север"
        fin = db.scalars(select(VehicleFinance).order_by(VehicleFinance.id)).all()
        assert [(f.kind, f.amount) for f in fin] == [("income", Decimal("120000.00")), ("expense", Decimal("0.00"))]
        osago = db.scalar(select(DossierDoc).where(DossierDoc.kind == "osago"))
        assert osago.doc_number == "ААА 123" and osago.issued_by == "Согласие" and osago.valid_until == dt.date(2026, 12, 31) and "Сумма: 12500.00" in osago.note and osago.archived_at is None
        kasko = db.scalar(select(DossierDoc).where(DossierDoc.kind == "kasko"))
        assert kasko.archived_at is not None and "не стояла на контроле" in kasko.archive_reason


def test_directories_and_money(old_db):
    do_import(old_db)
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Counterparty)) == 2
        ff = db.scalar(select(FuelFill))
        assert ff.amount == Decimal("7531.25") and ff.liters == Decimal("120.50") and ff.odometer == 2150
        fine = db.scalar(select(Fine))
        assert fine.amount == Decimal("500.00") and fine.payer == "driver" and "Водителю сообщили" in fine.note
        assert db.scalar(select(Rental.finished)) is True
        north = db.scalar(select(LegalEntity.id).where(LegalEntity.name == "ООО Север"))
        expense = db.scalar(select(MiscExpense))
        assert expense.amount == Decimal("1500.00") and expense.legal_entity_id == north        # без организации: первая организация старой базы
        fuel_card = db.scalar(select(func.count()).select_from(Vehicle))
        assert fuel_card == 4


def test_orders_documents_cash_numbers_stay_unique(old_db):
    do_import(old_db)
    with session_factory()() as db:
        orders = db.scalars(select(Order).order_by(Order.id)).all()
        assert [o.number for o in orders] == [7, 8, 15]                         # повтор «7» получил свободный номер, «Б-15» стал 15
        assert "Номер в старой программе: 07" in orders[1].note
        assert orders[0].amount == Decimal("12500.00") and orders[0].vat_pct == 20 and orders[0].rate == Decimal("2500.00")
        north = db.scalar(select(LegalEntity.id).where(LegalEntity.name == "ООО Север"))
        assert db.get(NumberCounter, (north, "order", 2026)).last_value == 15   # следующий новый заказ получит 16
        docs = db.scalars(select(Document).order_by(Document.id)).all()
        assert [(d.doc_type, d.number) for d in docs] == [("invoice", 3), ("invoice", 4), ("act", 1)]
        assert db.get(NumberCounter, (north, "doc_invoice", 2026)).last_value == 4
        ops = db.scalars(select(CashOp).order_by(CashOp.id)).all()
        assert [(o.kind, o.number) for o in ops] == [("in", 1), ("in", 2), ("out", 1), ("transfer", 1)]
        assert ops[3].to_account_id is not None and ops[0].amount == Decimal("2500.00")
        assert db.scalar(select(CashAccount.opening).where(CashAccount.name == "Касса")) == Decimal("10000.00")


def test_stock_people_calendar(old_db):
    report = do_import(old_db)
    with session_factory()() as db:
        moves = db.scalars(select(StockMove).order_by(StockMove.id)).all()
        assert [(m.kind, m.qty) for m in moves] == [("receipt", Decimal("10.000")), ("transfer", Decimal("4.000")), ("issue", Decimal("1.000"))]
        assert moves[0].price == Decimal("450.00") and moves[1].to_warehouse_id == moves[2].warehouse_id and moves[2].vehicle_id is not None
        assert db.scalar(select(PpeIssue.due_date)) == dt.date(2027, 10, 1)
        evs = db.scalars(select(EmployeeEvent).order_by(EmployeeEvent.id)).all()
        assert evs[0].kind == "medical" and evs[1].kind == "other" and evs[1].description == "Собеседование: Беседа"
        assert db.scalar(select(func.count()).select_from(TimesheetMark)) == 1                   # код «XX» неизвестен
        cal = db.scalar(select(CalendarEvent))
        assert cal.start_at == dt.datetime(2026, 3, 20, 3, 0, tzinfo=dt.timezone.utc) and cal.remind_min == 30 and cal.color == "blue"
    assert any("XX" in w[2] for w in report.warnings)


def test_trips_jobs_waybills(old_db):
    report = do_import(old_db)
    with session_factory()() as db:
        assert db.scalar(select(Trip.km)) == 120
        job = db.scalar(select(Job))
        assert job.time_from == "08:30" and job.state == "done" and job.address == "ул. Ленина 5"
        wb = db.scalars(select(Waybill).order_by(Waybill.number)).all()
        assert [(w.number, w.status) for w in wb] == [(15, "closed"), (17, "closed")]            # №16 без водителя не перенесён
        assert wb[0].start_km == Decimal("2100.0") and wb[0].end_km == Decimal("2220.0") and wb[0].fuel_added_l == Decimal("100.0")
        assert "Заказчик: ООО Заказчик" in wb[0].note
        assert wb[1].end_km is None                                                              # пробег назад не перенесён
    texts = " ".join(w[2] for w in report.warnings if w[0] == "waybills")
    assert "нет водителя" in texts and "меньше пробега при выезде" in texts


def test_requests_and_bills(old_db):
    do_import(old_db)
    with session_factory()() as db:
        reqs = db.scalars(select(ServiceRequest).order_by(ServiceRequest.id)).all()
        assert [(r.number, r.priority, r.status) for r in reqs] == [(1, "high", "waiting_parts"), (2, "urgent", "done")]
        assert reqs[0].title == "Стучит подвеска" and "слева спереди" in reqs[0].description and "Автор в старой программе: Ершов" in reqs[0].description
        assert reqs[0].created_at == dt.datetime(2026, 3, 12, 2, 0, tzinfo=dt.timezone.utc) and reqs[1].closed_at is not None
        ev = db.scalar(select(ServiceRequestEvent))
        assert ev.kind == "comment" and ev.text == "[Орлов] Заказал детали" and ev.request_id == reqs[0].id
        part = db.scalar(select(RequestPart))
        assert part.name == "Стойка стабилизатора" and part.qty == Decimal("2.000")
        bill = db.scalar(select(RepairBill))
        assert bill.request_id == reqs[0].id and bill.total == Decimal("3600.00") and bill.vat == Decimal("600.00") and bill.status == "unpaid"
        item = db.scalar(select(RepairBillItem))
        assert item.amount == Decimal("3000.00") and item.vat == Decimal("500.00") and item.bill_id == bill.id
        north = db.scalar(select(LegalEntity.id).where(LegalEntity.name == "ООО Север"))
        assert db.get(NumberCounter, (north, "request", 0)).last_value == 2


def test_not_migrated_things_are_listed(old_db):
    report = do_import(old_db)
    text = report.full_text()
    assert "boss (Ершов Н. И., администратор)" in text and "drv (Орлов, водитель, отключён)" in text
    assert "attachments" in text and "announcements" in text
    assert "оклады сотрудников не переносятся" in text


def test_second_run_adds_nothing(old_db):
    do_import(old_db)
    models = [LegalEntity, Employee, Vehicle, VehicleReading, VehicleStatusHistory, MaintenanceItem, Order, Document, CashOp, StockMove, Waybill, ServiceRequest, RepairBill, DossierDoc, LegacyId]
    before = {m.__name__: count(m) for m in models}
    report = do_import(old_db)
    assert {m.__name__: count(m) for m in models} == before
    assert report.stats["vehicles"]["new"] == 0 and report.stats["vehicles"]["linked"] == 4
    assert report.stats["orders"]["new"] == 0 and report.stats["odometer_readings"]["new"] == 0


def test_new_old_records_are_added_on_rerun(old_db):
    do_import(old_db)
    con = sqlite3.connect(old_db)
    vid = con.execute("SELECT id FROM vehicles WHERE plate='А101АА154'").fetchone()[0]
    con.execute("INSERT INTO fuel_fills (vehicle_id, fill_date, liters, amount_kop) VALUES (?, '2026-04-01', 80, 500000)", (vid,))
    con.execute("INSERT INTO odometer_readings (vehicle_id, km, read_at) VALUES (?, 2500, '2026-04-01 08:00')", (vid,))
    con.commit(); con.close()
    report = do_import(old_db)
    assert report.stats["fuel_fills"]["new"] == 1 and report.stats["odometer_readings"]["new"] == 1
    with session_factory()() as db:
        assert db.scalar(select(Vehicle.current_km).where(Vehicle.plate == "А101АА154")) == Decimal("2500.0")
        assert db.scalar(select(func.count()).select_from(FuelFill)) == 2


def test_existing_vehicle_is_linked_not_duplicated(old_db):
    with session_factory()() as db:
        ent = db.scalar(select(LegalEntity))
        vt = VehicleType(code="own", name="Свой", category="truck", primary_reading="km")
        db.add(vt); db.flush()
        db.add(Vehicle(legal_entity_id=ent.id, type_id=vt.id, plate="А101АА154"))
        db.commit()
    report = do_import(old_db)
    assert count(Vehicle) == 4                                                                  # три новых и одна уже стоявшая
    assert report.stats["vehicles"]["linked"] == 1
    with session_factory()() as db:
        old = db.scalar(select(Vehicle).where(Vehicle.plate == "А101АА154"))
        assert db.scalar(select(func.count()).select_from(MaintenanceItem).where(MaintenanceItem.vehicle_id == old.id)) == 2   # записи старой машины привязаны к ней
    assert any("уже есть" in w[2] for w in report.warnings if w[0] == "vehicles")


def test_bad_record_does_not_stop_the_rest(old_db):
    con = sqlite3.connect(old_db)
    con.execute("INSERT INTO orders (number, order_date, customer_id, distance_km) VALUES ('99', '2026-04-01', 1, 99999999999)")      # такое число новая база не вмещает
    con.commit(); con.close()
    report = do_import(old_db)
    assert report.stats["orders"]["skipped"] == 1 and report.stats["orders"]["new"] == 3
    assert any(w[0] == "orders" and "не перенесено" in w[2] for w in report.warnings)
    assert count(Vehicle) == 4 and count(Order) == 3 and count(Document) == 3                                                        # остальное перенеслось
    again = do_import(old_db)                                                                                                         # и негодная запись не мешает повторному запуску
    assert again.stats["orders"]["skipped"] == 1 and again.stats["orders"]["linked"] == 3


def test_default_entity_can_be_named(old_db):
    do_import(old_db, default_entity="ИП Юг")
    with session_factory()() as db:
        south = db.scalar(select(LegalEntity.id).where(LegalEntity.name == "ИП Юг"))
        assert db.scalar(select(MiscExpense.legal_entity_id)) == south


def test_works_without_organizations(tmp_path):
    path = make_old_db(str(tmp_path / "o.db"))
    con = sqlite3.connect(path)
    con.execute("UPDATE employees SET org_id=NULL"); con.execute("UPDATE vehicles SET org_id=NULL"); con.execute("UPDATE waybills SET org_id=NULL"); con.execute("DELETE FROM organizations")
    con.commit(); con.close()
    do_import(path)
    with session_factory()() as db:
        default = db.scalar(select(LegalEntity.id).order_by(LegalEntity.id).limit(1))               # единственное юрлицо новой программы
        assert {v.legal_entity_id for v in db.scalars(select(Vehicle))} == {default}


def test_old_database_is_not_changed(old_db):
    with open(old_db, "rb") as fh:
        before = fh.read()
    do_import(old_db)
    with open(old_db, "rb") as fh:
        assert fh.read() == before


def test_cli_preview_and_apply(old_db, capsys, tmp_path):
    from app.cli import main
    rep = tmp_path / "report.txt"
    assert main(["import-old", "--db", old_db, "--report", str(rep)]) == 0
    out = capsys.readouterr().out
    assert "ПРОБНЫЙ ЗАПУСК" in out and "ОТЧЁТ О ПЕРЕНОСЕ" in out and rep.read_text(encoding="utf-8").startswith("ОТЧЁТ О ПЕРЕНОСЕ")
    assert count(Vehicle) == 0
    assert main(["import-old", "--db", old_db, "--apply"]) == 0
    assert "данные записаны" in capsys.readouterr().out and count(Vehicle) == 4
    assert main(["import-old", "--db", str(tmp_path / "nope.db")]) == 2


def test_wrong_file_is_refused(old_db, capsys, tmp_path):
    from app.cli import main
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all, just some text " * 20)
    assert main(["import-old", "--db", str(junk), "--apply"]) == 1
    assert "ничего не изменено" in capsys.readouterr().out
    other = tmp_path / "other.db"
    con = sqlite3.connect(other); con.execute("create table t(x)"); con.commit(); con.close()
    assert main(["import-old", "--db", str(other), "--apply"]) == 1
    assert "нет таблиц старой программы" in capsys.readouterr().out
    assert count(Vehicle) == 0


def test_database_with_wal_file_is_read(tmp_path):
    """Старая программа могла оставить рядом файл журнала (-wal): свежие записи лежат в нём, их нельзя потерять."""
    path = make_old_db(str(tmp_path / "w.db"))
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("UPDATE vehicles SET plate='Я999ЯЯ154' WHERE id=(SELECT MIN(id) FROM vehicles)")
    con.commit()                                       # запись осталась в -wal, пока соединение открыто
    import os, shutil
    copy = tmp_path / "copy"; copy.mkdir()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            shutil.copyfile(path + suffix, str(copy / ("w.db" + suffix)))
    con.close()
    assert (copy / "w.db-wal").exists()
    do_import(str(copy / "w.db"))
    with session_factory()() as db:
        assert "Я999ЯЯ154" in {v.plate for v in db.scalars(select(Vehicle))}
