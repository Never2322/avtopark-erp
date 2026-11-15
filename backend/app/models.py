# -*- coding: utf-8 -*-
"""Модель данных очереди 1 (MVP). Названия таблиц совпадают с «Моделью данных и связей» из пакета проектирования.

Правила:
- у бизнес-записей есть юрлицо, даты создания и изменения, авторы;
- вместо удаления — архивирование (archived_at);
- деньги хранятся в Numeric с валютой, время в UTC (timestamptz);
- показания, журнал действий, история статусов и события обращений только добавляются: изменение и удаление
  запрещены триггерами базы (см. миграцию), а не только кодом.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, LargeBinary,
                        Numeric, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.db import Base
from app.enums import (ALL_DOC_KINDS, CALENDAR_COLORS, CALENDAR_REMIND_MAX, EMPLOYEE_EVENT_KINDS, BACKUP_STATUSES, BILL_KINDS, BILL_STATUSES, CASH_ACCOUNT_KINDS, CASH_OP_KINDS, COUNTERPARTY_KINDS, DOC_TYPES, FINE_PAYERS,
                       JOB_STAGES, JOB_STATES, ORDER_STATUSES, VAT_RATES,
                       PAYMENT_KINDS, RENTAL_DIRECTIONS, TARIFF_UNITS, TIMESHEET_CODES, READING_KINDS, READING_SOURCES, REQUEST_PRIORITIES, REQUEST_STATUSES,
                       REVIEW_STATES, VEHICLE_CATEGORIES, VEHICLE_STATUSES, WAYBILL_SHIFTS, WAYBILL_STATUSES, check_in)

TS = DateTime(timezone=True)


class Audited:
    """Общие поля бизнес-записей."""
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now(), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# ---------------------------------------------------------------- организации и доступ

class LegalEntity(Audited, Base):
    __tablename__ = "legal_entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str] = mapped_column(String(80), default="")
    inn: Mapped[str] = mapped_column(String(12), default="")
    kpp: Mapped[str] = mapped_column(String(9), default="")
    ogrn: Mapped[str] = mapped_column(String(15), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    # реквизиты для счетов и других документов
    bank_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    bik: Mapped[str] = mapped_column(String(9), default="", server_default="")
    account: Mapped[str] = mapped_column(String(20), default="", server_default="")
    corr_account: Mapped[str] = mapped_column(String(20), default="", server_default="")
    head_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    accountant_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    archived_at: Mapped[dt.datetime | None] = mapped_column(TS)
    __table_args__ = (Index("uq_legal_entities_name_active", text("lower(name)"), unique=True,
                            postgresql_where=text("archived_at IS NULL")),)


class Department(Audited, Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"))
    name: Mapped[str] = mapped_column(String(120))
    archived_at: Mapped[dt.datetime | None] = mapped_column(TS)
    __table_args__ = (UniqueConstraint("legal_entity_id", "name"),)


class User(Audited, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(TS)
    last_login_ip: Mapped[str] = mapped_column(String(64), default="")
    roles: Mapped[list["UserRole"]] = relationship(back_populates="user", cascade="all, delete-orphan",
                                                   foreign_keys="UserRole.user_id")
    __table_args__ = (Index("uq_users_login_lower", text("lower(login)"), unique=True),)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    module: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(200))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class UserRole(Base):
    """Роль пользователя. legal_entity_id пусто: роль действует во всех юрлицах."""
    __tablename__ = "user_roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id"))
    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])
    role: Mapped[Role] = relationship()
    __table_args__ = (Index("uq_user_roles_scope", "user_id", "role_id", text("COALESCE(legal_entity_id, 0)"), unique=True),)


class UserSession(Base):
    """Серверная сессия: в cookie лежит случайный токен, в базе только его хэш. Выход отзывает сессию по-настоящему."""
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(TS)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")
    revoked_at: Mapped[dt.datetime | None] = mapped_column(TS)


class LoginAttempt(Base):
    """Неудачные входы по логину (в том числе несуществующему): после лимита вход блокируется на время."""
    __tablename__ = "login_attempts"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(TS)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


# ---------------------------------------------------------------- сотрудники

class Employee(Audited, Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    last_name: Mapped[str] = mapped_column(String(80))
    first_name: Mapped[str] = mapped_column(String(80), default="")
    middle_name: Mapped[str] = mapped_column(String(80), default="")
    position: Mapped[str] = mapped_column(String(120), default="")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    is_driver: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # чувствительные поля: видны только с правом employees.view_sensitive
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(120), default="")
    birth_date: Mapped[dt.date | None] = mapped_column(Date)
    hired_on: Mapped[dt.date | None] = mapped_column(Date)
    fired_on: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    archived_at: Mapped[dt.datetime | None] = mapped_column(TS)
    assignments: Mapped[list["EmploymentAssignment"]] = relationship(back_populates="employee", cascade="all, delete-orphan")


class EmploymentAssignment(Base):
    """В каком юрлице и в какие даты человек работает."""
    __tablename__ = "employment_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    position: Mapped[str] = mapped_column(String(120), default="")
    started_on: Mapped[dt.date | None] = mapped_column(Date)
    ended_on: Mapped[dt.date | None] = mapped_column(Date)
    employee: Mapped[Employee] = relationship(back_populates="assignments")
    __table_args__ = (CheckConstraint("ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on", name="dates"),)


# ---------------------------------------------------------------- автопарк

class VehicleType(Base):
    __tablename__ = "vehicle_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(20))
    primary_reading: Mapped[str] = mapped_column(String(20))
    __table_args__ = (CheckConstraint(check_in("category", VEHICLE_CATEGORIES), name="category"),
                      CheckConstraint(check_in("primary_reading", READING_KINDS), name="primary_reading"))


class Vehicle(Audited, Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("vehicle_types.id"))
    plate: Mapped[str] = mapped_column(String(20))
    internal_no: Mapped[str] = mapped_column(String(30), default="")
    make: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    vin: Mapped[str | None] = mapped_column(String(17))
    year: Mapped[int | None] = mapped_column(Integer)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    status: Mapped[str] = mapped_column(String(20), default="in_work", server_default="in_work")
    status_changed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    # Текущие показания пересчитываются из журнала показаний, руками не правятся.
    current_km: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    current_hours: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    note: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[dt.datetime | None] = mapped_column(TS)
    type: Mapped[VehicleType] = relationship()
    legal_entity: Mapped[LegalEntity] = relationship()
    __table_args__ = (
        CheckConstraint(check_in("status", VEHICLE_STATUSES), name="status"),
        Index("uq_vehicles_plate_active", "plate", unique=True, postgresql_where=text("archived_at IS NULL")),
        Index("uq_vehicles_vin_active", "vin", unique=True, postgresql_where=text("archived_at IS NULL AND vin IS NOT NULL")),
        Index("ix_vehicles_entity_status", "legal_entity_id", "status"),
    )


class VehicleStatusHistory(Base):
    """Каждый переход статуса: когда, кто, почему, по какой заявке. Только добавляется."""
    __tablename__ = "vehicle_status_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(300), default="")
    changed_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    request_id: Mapped[int | None] = mapped_column(ForeignKey("service_requests.id"))
    __table_args__ = (CheckConstraint(check_in("status", VEHICLE_STATUSES), name="status"),)


class VehicleAssignment(Base):
    """Закрепление водителя за машиной на период. Закрывается датой окончания, не удаляется."""
    __tablename__ = "vehicle_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="primary", server_default="primary")
    started_on: Mapped[dt.date] = mapped_column(Date)
    ended_on: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    vehicle: Mapped[Vehicle] = relationship()
    employee: Mapped[Employee] = relationship()
    __table_args__ = (CheckConstraint("role IN ('primary', 'backup')", name="role"),
                      CheckConstraint("ended_on IS NULL OR ended_on >= started_on", name="dates"))


class VehicleReading(Base):
    """Показание пробега или моточасов. Только добавляется: исправление — новая запись со ссылкой corrects_id."""
    __tablename__ = "vehicle_readings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 1))
    recorded_at: Mapped[dt.datetime] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    note: Mapped[str] = mapped_column(String(300), default="")
    waybill_id: Mapped[int | None] = mapped_column(ForeignKey("waybills.id"))
    corrects_id: Mapped[int | None] = mapped_column(ForeignKey("vehicle_readings.id"))
    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    review_state: Mapped[str] = mapped_column(String(12), default="ok", server_default="ok")
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    review_comment: Mapped[str] = mapped_column(String(300), default="")
    __table_args__ = (
        CheckConstraint(check_in("kind", READING_KINDS), name="kind"),
        CheckConstraint(check_in("source", READING_SOURCES), name="source"),
        CheckConstraint(check_in("review_state", REVIEW_STATES), name="review_state"),
        CheckConstraint("value >= 0", name="value_not_negative"),
        Index("ix_vehicle_readings_vehicle_kind_time", "vehicle_id", "kind", "recorded_at"),
        Index("uq_vehicle_readings_one_correction", "corrects_id", unique=True, postgresql_where=text("corrects_id IS NOT NULL")),
    )


# ---------------------------------------------------------------- обращения и ремонты

class ServiceRequest(Audited, Base):
    __tablename__ = "service_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="normal", server_default="normal")
    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    planned_work: Mapped[str] = mapped_column(Text, default="")
    cost_estimate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cost_actual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB", server_default="RUB")
    closed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    vehicle: Mapped[Vehicle] = relationship()
    __table_args__ = (
        CheckConstraint(check_in("status", REQUEST_STATUSES), name="status"),
        CheckConstraint(check_in("priority", REQUEST_PRIORITIES), name="priority"),
        UniqueConstraint("legal_entity_id", "number"),
    )


class ServiceRequestEvent(Base):
    """Лента обращения: комментарии, смены статуса, назначения. Только добавляется."""
    __tablename__ = "service_request_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("service_requests.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (CheckConstraint("kind IN ('comment', 'status', 'assign', 'edit')", name="kind"),)


# ---------------------------------------------------------------- путевые листы

class Waybill(Audited, Base):
    __tablename__ = "waybills"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    shift: Mapped[str] = mapped_column(String(8), default="day", server_default="day")
    work_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="draft", server_default="draft")
    route: Mapped[str] = mapped_column(String(300), default="")
    work_site: Mapped[str] = mapped_column(String(200), default="")
    start_km: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    end_km: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    start_hours: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    end_hours: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    fuel_start_l: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    fuel_added_l: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    fuel_end_l: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    note: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[dt.datetime | None] = mapped_column(TS)
    closed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancel_reason: Mapped[str] = mapped_column(String(300), default="")
    vehicle: Mapped[Vehicle] = relationship()
    driver: Mapped[Employee] = relationship()
    __table_args__ = (
        CheckConstraint(check_in("status", WAYBILL_STATUSES), name="status"),
        CheckConstraint(check_in("shift", WAYBILL_SHIFTS), name="shift"),
        CheckConstraint("end_km IS NULL OR start_km IS NULL OR end_km >= start_km", name="km_order"),
        CheckConstraint("end_hours IS NULL OR start_hours IS NULL OR end_hours >= start_hours", name="hours_order"),
        UniqueConstraint("legal_entity_id", "year", "number"),
        # на одну машину одновременно не больше одного открытого путевого листа
        Index("uq_waybills_one_open_per_vehicle", "vehicle_id", unique=True, postgresql_where=text("status = 'open'")),
    )


class WaybillCorrection(Base):
    """Изменение закрытого путевого листа: что было, что стало, кто и почему. Только добавляется."""
    __tablename__ = "waybill_corrections"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    waybill_id: Mapped[int] = mapped_column(ForeignKey("waybills.id"), index=True)
    reason: Mapped[str] = mapped_column(String(300))
    before: Mapped[dict] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


class NumberCounter(Base):
    """Сквозная нумерация без дыр и повторов: следующий номер берётся одной атомарной командой."""
    __tablename__ = "number_counters"
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- журнал и копии

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now(), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    user_login: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    legal_entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (Index("ix_audit_log_entity", "entity_type", "entity_id"),)


class BackupJob(Base):
    __tablename__ = "backup_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(10), default="running")
    trigger: Mapped[str] = mapped_column(String(10), default="manual")
    filename: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    initiated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    error: Mapped[str] = mapped_column(Text, default="")
    schema_version: Mapped[str] = mapped_column(String(40), default="")
    counts_before: Mapped[dict] = mapped_column(JSONB, default=dict)
    counts_after: Mapped[dict] = mapped_column(JSONB, default=dict)
    file_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    restore_checked_at: Mapped[dt.datetime | None] = mapped_column(TS)
    restore_check: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (CheckConstraint(check_in("status", BACKUP_STATUSES), name="status"),)


# ---------------------------------------------------------------- личные дела: файлы и документы

class StoredFile(Base):
    """Загруженный файл (скан, фото). Хранится в базе, поэтому попадает в резервные копии и проверяется вместе с ними.
    Сам файл никогда не меняется и не удаляется (триггер): ошибочный скан убирают из документа, а не стирают."""
    __tablename__ = "stored_files"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(60))
    original_name: Mapped[str] = mapped_column(String(200), default="")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # содержимое грузится только когда нужно (отдельно от строки списка)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    thumb: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    uploaded_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (Index("ix_stored_files_sha256", "sha256"),)


class DossierDoc(Audited, Base):
    """Документ в личном деле сотрудника или техники: вид, номер, сроки и один или несколько сканов."""
    __tablename__ = "dossier_docs"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), index=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200), default="")
    doc_number: Mapped[str] = mapped_column(String(60), default="")
    issued_on: Mapped[dt.date | None] = mapped_column(Date)
    valid_until: Mapped[dt.date | None] = mapped_column(Date)
    issued_by: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[dt.datetime | None] = mapped_column(TS)
    archived_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    archive_reason: Mapped[str] = mapped_column(String(300), default="")
    files: Mapped[list["DossierDocFile"]] = relationship(back_populates="doc", order_by="DossierDocFile.position, DossierDocFile.id")
    __table_args__ = (
        CheckConstraint(check_in("kind", ALL_DOC_KINDS), name="kind"),
        CheckConstraint("num_nonnulls(employee_id, vehicle_id) = 1", name="one_owner"),
        CheckConstraint("issued_on IS NULL OR valid_until IS NULL OR valid_until >= issued_on", name="dates"),
        Index("ix_dossier_docs_valid_until", "valid_until"),
    )


class DossierDocFile(Base):
    """Скан в документе. Убрать можно (с отметкой кто и когда), стереть нельзя."""
    __tablename__ = "dossier_doc_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("dossier_docs.id"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    added_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    removed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remove_reason: Mapped[str] = mapped_column(String(300), default="")
    doc: Mapped[DossierDoc] = relationship(back_populates="files")
    file: Mapped[StoredFile] = relationship()


class VehicleSpecs(Base):
    """Технические данные и сведения о собственнике для личного дела техники (по одной строке на машину)."""
    __tablename__ = "vehicle_specs"
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), primary_key=True)
    color: Mapped[str] = mapped_column(String(40), default="")
    engine_no: Mapped[str] = mapped_column(String(40), default="")
    chassis_no: Mapped[str] = mapped_column(String(40), default="")
    body_no: Mapped[str] = mapped_column(String(40), default="")
    engine_power_hp: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    engine_volume_cc: Mapped[int | None] = mapped_column(Integer)
    fuel_type: Mapped[str] = mapped_column(String(20), default="")
    max_weight_kg: Mapped[int | None] = mapped_column(Integer)
    empty_weight_kg: Mapped[int | None] = mapped_column(Integer)
    eco_class: Mapped[str] = mapped_column(String(10), default="")
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    ownership: Mapped[str] = mapped_column(String(20), default="")
    registered_on: Mapped[dt.date | None] = mapped_column(Date)
    commissioned_on: Mapped[dt.date | None] = mapped_column(Date)
    tank_liters: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))       # объём бака, л
    fuel_norm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))         # норма расхода, л на 100 км
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now(), onupdate=func.now())
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# ---------------------------------------------------------------- деньги и справочники

class Voidable:
    """Запись не удаляется: её убирают в архив с причиной, и из архива можно вернуть."""
    archived_at: Mapped[dt.datetime | None] = mapped_column(TS)
    archived_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    archive_reason: Mapped[str] = mapped_column(String(300), default="", server_default="")


Money = Numeric(14, 2)


class Counterparty(Audited, Voidable, Base):
    """Контрагент: общий справочник для всех юрлиц."""
    __tablename__ = "counterparties"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="customer", server_default="customer")
    inn: Mapped[str] = mapped_column(String(12), default="", server_default="")
    kpp: Mapped[str] = mapped_column(String(9), default="", server_default="")
    address: Mapped[str] = mapped_column(String(300), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(40), default="", server_default="")
    email: Mapped[str] = mapped_column(String(120), default="", server_default="")
    contact: Mapped[str] = mapped_column(String(120), default="", server_default="")
    bank_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    bik: Mapped[str] = mapped_column(String(9), default="", server_default="")
    account: Mapped[str] = mapped_column(String(20), default="", server_default="")
    corr_account: Mapped[str] = mapped_column(String(20), default="", server_default="")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("kind", COUNTERPARTY_KINDS), name="kind"),
        Index("uq_counterparties_name_active", text("lower(name)"), unique=True, postgresql_where=text("archived_at IS NULL")),
    )


class Tariff(Audited, Voidable, Base):
    """Тариф: сколько стоит единица работы техники. Общий справочник."""
    __tablename__ = "tariffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(8), default="hour", server_default="hour")
    rate: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    min_amount: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("unit", TARIFF_UNITS), name="unit"),
        CheckConstraint("rate >= 0 AND min_amount >= 0", name="amounts"),
        Index("uq_tariffs_name_active", text("lower(name)"), unique=True, postgresql_where=text("archived_at IS NULL")),
    )


class Constant(Audited, Voidable, Base):
    """Именованное число или текст, которое используется в расчётах (например, цена топлива)."""
    __tablename__ = "constants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    value: Mapped[str] = mapped_column(String(300), default="", server_default="")
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (Index("uq_constants_name_active", text("lower(name)"), unique=True, postgresql_where=text("archived_at IS NULL")),)


class FuelCard(Audited, Voidable, Base):
    __tablename__ = "fuel_cards"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    number: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(80), default="", server_default="")
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    limit_month_l: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    valid_until: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (
        CheckConstraint("limit_month_l IS NULL OR limit_month_l >= 0", name="limit"),
        Index("uq_fuel_cards_number_active", text("lower(number)"), unique=True, postgresql_where=text("archived_at IS NULL")),
    )


class Fine(Audited, Voidable, Base):
    __tablename__ = "fines"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    fine_date: Mapped[dt.date] = mapped_column(Date)
    resolution_number: Mapped[str] = mapped_column(String(60), default="", server_default="")
    article: Mapped[str] = mapped_column(String(120), default="", server_default="")
    amount: Mapped[Decimal] = mapped_column(Money)
    discount_until: Mapped[dt.date | None] = mapped_column(Date)
    due_date: Mapped[dt.date | None] = mapped_column(Date)
    payer: Mapped[str] = mapped_column(String(10), default="company", server_default="company")
    paid_at: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("payer", FINE_PAYERS), name="payer"),
        CheckConstraint("amount >= 0", name="amount"),
        Index("ix_fines_vehicle_date", "vehicle_id", "fine_date"),
    )


class Rental(Audited, Voidable, Base):
    __tablename__ = "rentals"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    direction: Mapped[str] = mapped_column(String(4), default="out", server_default="out")
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"))
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    finished: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("direction", RENTAL_DIRECTIONS), name="direction"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="dates"),
        CheckConstraint("rate >= 0", name="rate"),
    )


class MiscExpense(Audited, Voidable, Base):
    __tablename__ = "misc_expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    op_date: Mapped[dt.date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(80), default="", server_default="")
    amount: Mapped[Decimal] = mapped_column(Money)
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (CheckConstraint("amount > 0", name="amount"),)


class EmployeePayment(Audited, Voidable, Base):
    __tablename__ = "employee_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    pay_date: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(10), default="salary", server_default="salary")
    amount: Mapped[Decimal] = mapped_column(Money)
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (CheckConstraint(check_in("kind", PAYMENT_KINDS), name="kind"), CheckConstraint("amount > 0", name="amount"))


class CashAccount(Audited, Voidable, Base):
    __tablename__ = "cash_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(10), default="cash", server_default="cash")
    opening: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("kind", CASH_ACCOUNT_KINDS), name="kind"),
        CheckConstraint("opening >= 0", name="opening"),
        Index("uq_cash_accounts_name_active", "legal_entity_id", text("lower(name)"), unique=True, postgresql_where=text("archived_at IS NULL")),
    )


class CashOp(Audited, Voidable, Base):
    __tablename__ = "cash_ops"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    op_date: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(10))
    account_id: Mapped[int] = mapped_column(ForeignKey("cash_accounts.id"), index=True)
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("cash_accounts.id"))
    amount: Mapped[Decimal] = mapped_column(Money)
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(80), default="", server_default="")
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("kind", CASH_OP_KINDS), name="kind"),
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("(kind = 'transfer' AND to_account_id IS NOT NULL AND to_account_id <> account_id) "
                        "OR (kind <> 'transfer' AND to_account_id IS NULL)", name="transfer"),
        UniqueConstraint("legal_entity_id", "kind", "year", "number", name="uq_cash_ops_number"),
    )


class Order(Audited, Voidable, Base):
    """Заказ клиента на перевозку или работу техники. Номер свой в каждом юрлице и году."""
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    order_date: Mapped[dt.date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="new", server_default="new")
    customer_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), index=True)
    point_from: Mapped[str] = mapped_column(String(200), default="", server_default="")
    point_to: Mapped[str] = mapped_column(String(200), default="", server_default="")
    cargo: Mapped[str] = mapped_column(String(200), default="", server_default="")
    cargo_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    distance_km: Mapped[int | None] = mapped_column(Integer)
    hours: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    depart_at: Mapped[dt.datetime | None] = mapped_column(TS)
    deliver_at: Mapped[dt.datetime | None] = mapped_column(TS)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    tariff_id: Mapped[int | None] = mapped_column(ForeignKey("tariffs.id"))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    rate: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    amount: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    vat_pct: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("status", ORDER_STATUSES), name="status"),
        CheckConstraint("vat_pct IN (" + ", ".join(VAT_RATES) + ")", name="vat"),
        CheckConstraint("rate >= 0 AND amount >= 0", name="amounts"),
        CheckConstraint("deliver_at IS NULL OR depart_at IS NULL OR deliver_at >= depart_at", name="times"),
        UniqueConstraint("legal_entity_id", "year", "number", name="uq_orders_number"),
    )


class Document(Audited, Voidable, Base):
    """Счёт, акт, накладная или УПД. Номер свой в каждом юрлице, виде документа и году."""
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(8))
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    doc_date: Mapped[dt.date] = mapped_column(Date, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), index=True)
    subject: Mapped[str] = mapped_column(Text, default="", server_default="")
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=1, server_default="1")
    unit: Mapped[str] = mapped_column(String(20), default="усл.", server_default="усл.")
    price: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    vat_pct: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    paid_date: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("doc_type", DOC_TYPES), name="type"),
        CheckConstraint("vat_pct IN (" + ", ".join(VAT_RATES) + ")", name="vat"),
        CheckConstraint("qty > 0 AND price >= 0", name="amounts"),
        UniqueConstraint("legal_entity_id", "doc_type", "year", "number", name="uq_documents_number"),
    )


class Job(Audited, Voidable, Base):
    """Наряд на день: куда поехала техника, что делать, отметки «приехал» и «закончил». Юрлицо берётся из техники."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    job_date: Mapped[dt.date] = mapped_column(Date, index=True)
    time_from: Mapped[str] = mapped_column(String(5), default="", server_default="")
    customer: Mapped[str] = mapped_column(String(120), default="", server_default="")
    address: Mapped[str] = mapped_column(String(200))
    task: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    state: Mapped[str] = mapped_column(String(10), default="planned", server_default="planned")
    arrived_at: Mapped[dt.datetime | None] = mapped_column(TS)
    arrived_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    finished_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    result_note: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("state", JOB_STATES), name="state"),
        Index("ix_jobs_vehicle_date", "vehicle_id", "job_date"),
    )


class JobPhoto(Base):
    """Фото к наряду: «до работы» или «после работы». Фото не стирается: его убирают с отметкой кто и почему."""
    __tablename__ = "job_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), index=True)
    stage: Mapped[str] = mapped_column(String(6))
    added_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    removed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remove_reason: Mapped[str] = mapped_column(String(300), default="", server_default="")
    file: Mapped[StoredFile] = relationship()
    __table_args__ = (CheckConstraint(check_in("stage", JOB_STAGES), name="stage"),)


class Trip(Audited, Voidable, Base):
    """Рейс, который вносит водитель (или диспетчер): маршрут, груз, накладная, километры. Юрлицо берётся из техники."""
    __tablename__ = "trips"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    trip_date: Mapped[dt.date] = mapped_column(Date, index=True)
    route: Mapped[str] = mapped_column(String(200))
    customer: Mapped[str] = mapped_column(String(120), default="", server_default="")
    cargo: Mapped[str] = mapped_column(String(200), default="", server_default="")
    cargo_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 1))
    doc_number: Mapped[str] = mapped_column(String(60), default="", server_default="")
    km: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    client_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    __table_args__ = (
        CheckConstraint("cargo_kg IS NULL OR cargo_kg >= 0", name="cargo"),
        CheckConstraint("km IS NULL OR km >= 0", name="km"),
        Index("ix_trips_vehicle_date", "vehicle_id", "trip_date"),
        # повторная отправка с телефона (без связи) не создаёт вторую запись
        Index("uq_trips_client", "created_by_id", "client_id", unique=True, postgresql_where=text("client_id <> ''")),
    )


class FuelFill(Audited, Voidable, Base):
    """Заправка: литры, сумма, пробег, полный ли бак. По полным заправкам считается расход на 100 км."""
    __tablename__ = "fuel_fills"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    fill_date: Mapped[dt.date] = mapped_column(Date, index=True)
    liters: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    amount: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    odometer: Mapped[int | None] = mapped_column(Integer)
    full_tank: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    station: Mapped[str] = mapped_column(String(80), default="", server_default="")
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    client_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    __table_args__ = (
        CheckConstraint("liters > 0", name="liters"),
        CheckConstraint("amount >= 0", name="amount"),
        CheckConstraint("odometer IS NULL OR odometer >= 0", name="odometer"),
        Index("ix_fuel_fills_vehicle_date", "vehicle_id", "fill_date"),
        Index("uq_fuel_fills_client", "created_by_id", "client_id", unique=True, postgresql_where=text("client_id <> ''")),
    )


class EntryPhoto(Base):
    """Фото к рейсу (накладная) или заправке (чек). Принадлежит ровно одной записи. Убирается с отметкой, не стирается."""
    __tablename__ = "entry_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), index=True)
    fuel_fill_id: Mapped[int | None] = mapped_column(ForeignKey("fuel_fills.id"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), index=True)
    added_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    removed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remove_reason: Mapped[str] = mapped_column(String(300), default="", server_default="")
    file: Mapped[StoredFile] = relationship()
    __table_args__ = (CheckConstraint("(trip_id IS NOT NULL) <> (fuel_fill_id IS NOT NULL)", name="owner"),)


# ---------------------------------------------------------------- детали, «что купить», счета на ремонт

class PartCatalog(Audited, Voidable, Base):
    """Справочник типовых деталей: подсказки при вводе. «Использований» растёт, когда деталь вносят в обращение."""
    __tablename__ = "parts_catalog"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200))       # название для сравнения: без регистра, «ё», знаков
    article: Mapped[str] = mapped_column(String(60), default="", server_default="")
    unit: Mapped[str] = mapped_column(String(12), default="шт", server_default="шт")
    category: Mapped[str] = mapped_column(String(60), default="", server_default="")
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (
        Index("uq_parts_catalog_key_active", "name_key", unique=True, postgresql_where=text("archived_at IS NULL")),
    )


class RequestPart(Base):
    """Деталь, нужная для обращения. Попадает в общий список «что купить», пока не отмечена купленной."""
    __tablename__ = "request_parts"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("service_requests.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    article: Mapped[str] = mapped_column(String(60), default="", server_default="")
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=1, server_default="1")
    unit: Mapped[str] = mapped_column(String(12), default="шт", server_default="шт")
    added_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    bought_at: Mapped[dt.datetime | None] = mapped_column(TS)
    bought_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    removed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (CheckConstraint("qty > 0", name="qty"),)


class RepairBill(Audited, Voidable, Base):
    """Счёт на ремонт и запчасти: выписан в программе (со строками) или получен от поставщика (файл и сумма)."""
    __tablename__ = "repair_bills"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="uploaded", server_default="uploaded")
    number: Mapped[str] = mapped_column(String(40))
    bill_date: Mapped[dt.date] = mapped_column(Date, index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"))
    supplier_name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    supplier_inn: Mapped[str] = mapped_column(String(12), default="", server_default="")
    supplier_kpp: Mapped[str] = mapped_column(String(9), default="", server_default="")
    supplier_addr: Mapped[str] = mapped_column(String(250), default="", server_default="")
    supplier_phone: Mapped[str] = mapped_column(String(40), default="", server_default="")
    bank_name: Mapped[str] = mapped_column(String(160), default="", server_default="")
    bik: Mapped[str] = mapped_column(String(9), default="", server_default="")
    corr_account: Mapped[str] = mapped_column(String(20), default="", server_default="")
    account: Mapped[str] = mapped_column(String(20), default="", server_default="")
    basis: Mapped[str] = mapped_column(String(120), default="", server_default="")
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("service_requests.id"), index=True)
    vat_pct: Mapped[int] = mapped_column(Integer, default=22, server_default="22")
    total: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    vat: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(10), default="unpaid", server_default="unpaid")
    paid_date: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("kind", BILL_KINDS), name="kind"),
        CheckConstraint(check_in("status", BILL_STATUSES), name="status"),
        CheckConstraint("vat_pct IN (" + ", ".join(VAT_RATES) + ")", name="vat_pct"),
        CheckConstraint("total >= 0 AND vat >= 0 AND vat <= total", name="amounts"),
        CheckConstraint("(status = 'paid') = (paid_date IS NOT NULL)", name="paid_date"),
        Index("ix_repair_bills_number", "legal_entity_id", "bill_date"),
    )


class RepairBillItem(Base):
    """Строка выписанного счёта. Сумма и НДС строки считаются при сохранении счёта."""
    __tablename__ = "repair_bill_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("repair_bills.id", ondelete="CASCADE"), index=True)
    pos: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    article: Mapped[str] = mapped_column(String(60), default="", server_default="")
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit: Mapped[str] = mapped_column(String(12), default="шт", server_default="шт")
    price: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    amount: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    vat: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    __table_args__ = (CheckConstraint("qty > 0 AND price >= 0 AND amount >= 0 AND vat >= 0", name="values"),)


class RepairBillFile(Base):
    """Файл к счёту (PDF поставщика, фото). Не стирается: убирается с отметкой."""
    __tablename__ = "repair_bill_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    bill_id: Mapped[int] = mapped_column(ForeignKey("repair_bills.id"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), index=True)
    added_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    added_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[dt.datetime | None] = mapped_column(TS)
    removed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remove_reason: Mapped[str] = mapped_column(String(300), default="", server_default="")
    file: Mapped[StoredFile] = relationship()


# ---------------------------------------------------------------- табель и СИЗ

class TimesheetMark(Audited, Base):
    """Одна отметка в табеле: сотрудник, день и код («Я», «Б»…), у «Я» и «С» бывают часы. Пустая клетка = нет строки."""
    __tablename__ = "timesheet_marks"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    work_date: Mapped[dt.date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(3))
    hours: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    __table_args__ = (
        UniqueConstraint("legal_entity_id", "employee_id", "work_date"),
        CheckConstraint(check_in("code", TIMESHEET_CODES), name="code"),
        CheckConstraint("hours IS NULL OR (hours >= 0 AND hours <= 24)", name="hours"),
        Index("ix_timesheet_marks_period", "legal_entity_id", "work_date"),
    )


class PpeNorm(Audited, Voidable, Base):
    """Что выдаём и на сколько месяцев хватает (общий справочник для всех юрлиц)."""
    __tablename__ = "ppe_norms"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    wear_months: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (
        CheckConstraint("wear_months IS NULL OR (wear_months >= 1 AND wear_months <= 120)", name="wear_months"),
        Index("uq_ppe_norms_name_active", text("lower(name)"), unique=True, postgresql_where=text("archived_at IS NULL")),
    )


class PpeIssue(Audited, Voidable, Base):
    """Выдача СИЗ сотруднику: что, когда, сколько, до какого срока носить и когда вернул."""
    __tablename__ = "ppe_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    ppe_id: Mapped[int] = mapped_column(ForeignKey("ppe_norms.id"), index=True)
    issue_date: Mapped[dt.date] = mapped_column(Date, index=True)
    qty: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    size: Mapped[str] = mapped_column(String(20), default="", server_default="")
    due_date: Mapped[dt.date | None] = mapped_column(Date, index=True)
    returned_date: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    __table_args__ = (
        CheckConstraint("qty >= 1 AND qty <= 1000", name="qty"),
        CheckConstraint("due_date IS NULL OR due_date >= issue_date", name="due_date"),
        CheckConstraint("returned_date IS NULL OR returned_date >= issue_date", name="returned_date"),
    )


# ---------------------------------------------------------------- события сотрудников и календарь

class EmployeeEvent(Audited, Voidable, Base):
    """Событие сотрудника: медосмотр, инструктаж, нарушение, ДТП, премия. «Следующий срок» даёт напоминание."""
    __tablename__ = "employee_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="other", server_default="other")
    event_date: Mapped[dt.date | None] = mapped_column(Date, index=True)
    due_date: Mapped[dt.date | None] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Money, default=0, server_default="0")
    description: Mapped[str] = mapped_column(String(300), default="", server_default="")
    controlled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("kind", EMPLOYEE_EVENT_KINDS), name="kind"),
        CheckConstraint("amount >= 0", name="amount"),
        CheckConstraint("event_date IS NULL OR due_date IS NULL OR due_date >= event_date", name="dates"),
    )


class CalendarEvent(Audited, Voidable, Base):
    """Своё событие в календаре: встреча, дело, срок. Время хранится с поясом, показывается по часам организации."""
    __tablename__ = "calendar_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    start_at: Mapped[dt.datetime] = mapped_column(TS, index=True)
    end_at: Mapped[dt.datetime | None] = mapped_column(TS)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    color: Mapped[str] = mapped_column(String(8), default="teal", server_default="teal")
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    remind_min: Mapped[int | None] = mapped_column(Integer)
    done: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    __table_args__ = (
        CheckConstraint(check_in("color", CALENDAR_COLORS), name="color"),
        CheckConstraint("end_at IS NULL OR end_at >= start_at", name="dates"),
        CheckConstraint(f"remind_min IS NULL OR (remind_min >= 0 AND remind_min <= {CALENDAR_REMIND_MAX})", name="remind_min"),
    )
