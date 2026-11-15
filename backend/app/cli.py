# -*- coding: utf-8 -*-
"""Команды для администратора сервера:  python -m app.cli <команда>

  init            права, роли, справочники, первое юрлицо, первый администратор (из ADMIN_LOGIN и ADMIN_PASSWORD)
  create-admin    создать администратора вручную
  seed-demo       вымышленные данные для знакомства с системой (НЕ запускать на рабочей базе)
  backup          сделать копию и проверить её восстановлением
  verify <id>     проверить восстановлением ранее сделанную копию
  import-old      перенос данных из старой программы (SQLite): без --apply только показывает, что получится
  decrypt-backup  расшифровать копию, пришедшую вне сервера (Telegram или почта)
"""
import argparse
import datetime as dt
import sys
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select

from app.bootstrap import bootstrap, create_admin
from app.config import get_settings
from app.db import session_factory
from app.models import (Department, Employee, EmploymentAssignment, LegalEntity, Role, ServiceRequest, ServiceRequestEvent, User,
                        UserRole, Vehicle, VehicleAssignment, VehicleType, Waybill)
from app.security import hash_password, temp_password
from app.services import backup
from app.services.numbering import next_number
from app.services.readings import add_reading
from app.services.vehicles import change_status


def cmd_init(_args) -> int:
    with session_factory()() as db:
        print("Готово:", bootstrap(db))
    return 0


def cmd_create_admin(args) -> int:
    with session_factory()() as db:
        try:
            user = create_admin(db, args.login, args.password, args.name or "", must_change=False)
        except ValueError as exc:
            print("Ошибка:", exc)
            return 1
        db.commit()
        print(f"Администратор «{user.login}» создан.")
    return 0


def cmd_backup(_args) -> int:
    with session_factory()() as db:
        try:
            job = backup.create_job(db, None, "manual")
        except backup.BackupError as exc:
            print("Ошибка:", exc)
            return 1
    backup.run_job(job.id, verify=True)
    with session_factory()() as db:
        job = db.get(type(job), job.id)
        print("Статус:", job.status, "| файл:", job.filename, "| размер:", job.size_bytes)
        if job.error:
            print("Причина:", job.error)
        print("Проверка восстановлением:", (job.restore_check or {}).get("ok"))
        return 0 if job.status == "ok" and (job.restore_check or {}).get("ok") else 2


def cmd_verify(args) -> int:
    result = backup.verify_job(args.id)
    print(result)
    return 0 if result.get("ok") else 2


DEMO = {
    "people": [
        ("Орлов", "Антон", "Сергеевич", "водитель", True), ("Зайцев", "Игорь", "Павлович", "водитель", True),
        ("Белов", "Максим", "Олегович", "водитель", True), ("Громова", "Елена", "Викторовна", "диспетчер", False),
        ("Крылов", "Дмитрий", "Андреевич", "механик", False), ("Ершов", "Николай", "Ильич", "руководитель", False),
    ],
    "vehicles": [  # госномер, марка, модель, тип, пробег или моточасы, юрлицо (0 или 1)
        ("А101АА154", "КАМАЗ", "65115", "truck_dump", ("km", 184200), 0), ("А102АА154", "КАМАЗ", "43118", "truck_flatbed", ("km", 96400), 0),
        ("А103АА154", "МАЗ", "5440", "truck_tractor", ("km", 311000), 0), ("А201ВВ54", "Амкодор", "332С4", "loader", ("engine_hours", 5120), 0),
        ("А202ВВ54", "JCB", "3CX", "excavator", ("engine_hours", 3340), 0), ("А301СС154", "МКМ", "6000", "snow_machine", ("engine_hours", 880), 1),
        ("А302СС154", "КО", "440", "truck_waste", ("km", 142700), 1), ("А303СС154", "Lada", "Largus", "car", ("km", 58800), 1),
    ],
}


def cmd_seed_demo(args) -> int:
    if not args.yes:
        print("Команда создаёт вымышленные данные и пользователей. Добавьте --yes, если это учебная база.")
        return 1
    with session_factory()() as db:
        bootstrap(db)
        admin = db.scalar(select(User).order_by(User.id).limit(1))
        if not admin:
            print("Сначала создайте администратора (init или create-admin).")
            return 1
        if db.scalar(select(func.count()).select_from(Vehicle)):
            print("В базе уже есть техника: демо-данные не добавляются.")
            return 1
        who = SimpleNamespace(user=admin, ip="cli")
        main = db.scalar(select(LegalEntity).order_by(LegalEntity.id).limit(1))
        second = LegalEntity(name="ООО «Чистый двор»", short_name="Чистый двор", created_by_id=admin.id, updated_by_id=admin.id)
        db.add(second)
        db.flush()
        entities = [main, second]
        for e in entities:
            db.add(Department(legal_entity_id=e.id, name="Основной", created_by_id=admin.id, updated_by_id=admin.id))
        today = dt.date.today()
        staff = []
        for last, first, middle, position, is_driver in DEMO["people"]:
            emp = Employee(last_name=last, first_name=first, middle_name=middle, position=position, is_driver=is_driver,
                           hired_on=today - dt.timedelta(days=700), created_by_id=admin.id, updated_by_id=admin.id)
            db.add(emp)
            db.flush()
            for e in entities if position in ("руководитель", "диспетчер") else entities[:1]:
                db.add(EmploymentAssignment(employee_id=emp.id, legal_entity_id=e.id, position=position, started_on=today - dt.timedelta(days=700)))
            staff.append(emp)
        types = {t.code: t for t in db.scalars(select(VehicleType))}
        cars = []
        for plate, make, model, tcode, (kind, value), idx in DEMO["vehicles"]:
            v = Vehicle(legal_entity_id=entities[idx].id, type_id=types[tcode].id, plate=plate, make=make, model=model,
                        created_by_id=admin.id, updated_by_id=admin.id)
            db.add(v)
            db.flush()
            add_reading(db, who, v, kind, Decimal(value), recorded_at=utcnow_minus(30), note="Начальное значение")
            add_reading(db, who, v, kind, Decimal(value) + (Decimal(850) if kind == "km" else Decimal(40)), recorded_at=utcnow_minus(2))
            cars.append(v)
        for emp, v in zip(staff[:3], cars[:3]):
            db.add(VehicleAssignment(vehicle_id=v.id, employee_id=emp.id, started_on=today - dt.timedelta(days=90)))
        change_status(db, who, cars[1], "in_repair", "Течь масла из КПП")
        change_status(db, who, cars[4], "day_off")
        req = ServiceRequest(legal_entity_id=cars[1].legal_entity_id, number=next_number(db, cars[1].legal_entity_id, "request"),
                             vehicle_id=cars[1].id, title="Течь масла из КПП", description="Пятно под машиной после смены",
                             priority="high", status="diagnostics", reporter_id=admin.id, created_by_id=admin.id, updated_by_id=admin.id)
        db.add(req)
        db.flush()
        db.add(ServiceRequestEvent(request_id=req.id, kind="status", to_status="new", text="Обращение создано", author_id=admin.id))
        db.add(ServiceRequestEvent(request_id=req.id, kind="status", from_status="new", to_status="diagnostics", author_id=admin.id))
        db.add(Waybill(legal_entity_id=cars[0].legal_entity_id, year=today.year, number=next_number(db, cars[0].legal_entity_id, "waybill", today.year),
                       vehicle_id=cars[0].id, driver_id=staff[0].id, work_date=today, shift="day", route="Склад — объект на Северном проезде",
                       created_by_id=admin.id, updated_by_id=admin.id))
        db.flush()
        print("\nДемо-пользователи (пароли показываются один раз, при первом входе система попросит сменить):")
        for login, name, role, emp_idx, scope in (("director", "Ершов Н. И.", "director", 5, None), ("dispatcher", "Громова Е. В.", "dispatcher", 3, None),
                                                  ("mechanic", "Крылов Д. А.", "mechanic", 4, None), ("driver", "Орлов А. С.", "driver", 0, main.id)):
            password = temp_password()
            u = User(login=login, display_name=name, password_hash=hash_password(password), must_change_password=True,
                     created_by_id=admin.id, updated_by_id=admin.id)
            db.add(u)
            db.flush()
            db.add(UserRole(user_id=u.id, role_id=db.scalar(select(Role.id).where(Role.code == role)), legal_entity_id=scope))
            staff[emp_idx].user_id = u.id
            print(f"  {login:11s} {password}   ({name})")
        db.commit()
    return 0


def cmd_import_old(args) -> int:
    import os
    from app.legacy_import import run_import
    if not os.path.isfile(args.db):
        print(f"Файл старой базы не найден: {args.db}")
        return 2
    with session_factory()() as db:
        try:
            report = run_import(db, args.db, apply=args.apply, default_entity=args.default_entity or "")
        except Exception as exc:                                           # noqa: BLE001
            db.rollback()
            print(f"Перенос остановлен, в новой базе ничего не изменено: {exc}")
            return 1
    print(report.text())
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report.full_text() + "\n")
        print(f"\nПолный отчёт: {args.report}")
    print("\n" + ("ГОТОВО: данные записаны в новую базу." if args.apply else "ПРОБНЫЙ ЗАПУСК: в базу ничего не записано. Чтобы записать, запустите с --apply."))
    return 0


def cmd_decrypt_backup(args) -> int:
    from app.services.offsite import decrypt_file
    return decrypt_file(args.src, args.dst, args.password or None)


def utcnow_minus(days: int) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    a = sub.add_parser("create-admin")
    a.add_argument("--login", required=True)
    a.add_argument("--password", required=True)
    a.add_argument("--name", default="")
    a.set_defaults(fn=cmd_create_admin)
    d = sub.add_parser("seed-demo")
    d.add_argument("--yes", action="store_true")
    d.set_defaults(fn=cmd_seed_demo)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    v = sub.add_parser("verify")
    v.add_argument("id", type=int)
    v.set_defaults(fn=cmd_verify)
    i = sub.add_parser("import-old")
    i.add_argument("--db", required=True, help="путь к файлу старой базы (SQLite)")
    i.add_argument("--apply", action="store_true", help="записать в базу (без этого запуск пробный)")
    i.add_argument("--default-entity", default="", help="название юрлица для данных без организации (по умолчанию первая организация)")
    i.add_argument("--report", default="", help="куда сохранить полный отчёт")
    i.set_defaults(fn=cmd_import_old)
    x = sub.add_parser("decrypt-backup")
    x.add_argument("--in", dest="src", required=True, help="зашифрованный файл копии (*.dump.enc)")
    x.add_argument("--out", dest="dst", required=True, help="куда записать расшифрованный файл (*.dump)")
    x.add_argument("--password", default="", help="пароль шифрования; если не указан, берётся OFFSITE_PASSWORD или спрашивается")
    x.set_defaults(fn=cmd_decrypt_backup)
    args = parser.parse_args(argv)
    get_settings()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
