# -*- coding: utf-8 -*-
"""Построение тестового мира: два юрлица, пользователи по ролям, техника, водители. Данные создаются напрямую в базе."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import session_factory
from app.models import (Employee, EmploymentAssignment, LegalEntity, Role, User, UserRole, Vehicle, VehicleAssignment, VehicleType)
from app.security import hash_password
from app.services.readings import add_reading
from app.common import org_today

PASSWORD = "Tester-2026-Pass"
_HASH = hash_password(PASSWORD)


class Client:
    """Обёртка над TestClient: хранит cookie сессии и подставляет CSRF-заголовок на изменяющие запросы."""

    def __init__(self, app):
        self.http = TestClient(app, base_url="http://testserver")
        self.csrf = ""
        self.me = {}

    def login(self, login: str, password: str | None = None, remember: bool = False):
        r = self.http.post("/api/auth/login", json={"login": login, "password": password or PASSWORD, "remember": remember})
        if r.status_code == 200:
            self.me = r.json()
            self.csrf = self.me["csrf_token"]
        return r

    def call(self, method: str, url: str, **kw):
        headers = dict(kw.pop("headers", {}))
        if method not in ("GET", "HEAD") and self.csrf:
            headers.setdefault("X-CSRF-Token", self.csrf)
        return self.http.request(method, "/api" + url, headers=headers, **kw)

    def get(self, url, **kw):
        return self.call("GET", url, **kw)

    def post(self, url, json=None, **kw):
        return self.call("POST", url, json=json if json is not None else {}, **kw)

    def patch(self, url, json=None, **kw):
        return self.call("PATCH", url, json=json or {}, **kw)

    def put(self, url, json=None, **kw):
        return self.call("PUT", url, json=json or {}, **kw)


def build_world() -> SimpleNamespace:
    w = SimpleNamespace()
    with session_factory()() as db:
        w.A = db.scalar(select(LegalEntity).order_by(LegalEntity.id).limit(1)).id
        b = LegalEntity(name="ООО «Бета»", short_name="Бета")
        db.add(b)
        db.flush()
        w.B = b.id
        roles = {r.code: r.id for r in db.scalars(select(Role))}
        w.role_ids = roles
        types = {t.code: t.id for t in db.scalars(select(VehicleType))}
        w.type_id, w.hours_type_id = types["truck_dump"], types["loader"]

        def user(login, role, entity, employee=None):
            u = User(login=login, display_name=login.capitalize(), password_hash=_HASH)
            db.add(u)
            db.flush()
            db.add(UserRole(user_id=u.id, role_id=roles[role], legal_entity_id=entity))
            if employee is not None:
                employee.user_id = u.id
            return u

        def employee(last, entity, driver=False):
            e = Employee(last_name=last, first_name="Тест", is_driver=driver, position="водитель" if driver else "сотрудник")
            db.add(e)
            db.flush()
            db.add(EmploymentAssignment(employee_id=e.id, legal_entity_id=entity, started_on=org_today() - dt.timedelta(days=400)))
            return e

        w.d1, w.d2, w.d3 = employee("Первый", w.A, True), employee("Второй", w.A, True), employee("Третий", w.B, True)
        w.admin = user("admin", "admin", None)
        user("dir_a", "director", w.A)
        user("mech_a", "mechanic", w.A)
        user("disp_a", "dispatcher", w.A)
        user("drv1", "driver", w.A, w.d1)
        user("drv2", "driver", w.A, w.d2)
        user("acc_a", "accountant", w.A)
        user("emp_a", "employee", w.A)
        user("store_a", "storekeeper", w.A)
        user("dir_b", "director", w.B)
        user("drv3", "driver", w.B, w.d3)
        db.flush()

        admin_who = SimpleNamespace(user=w.admin, ip="test")

        def vehicle(plate, entity, type_id, km=1000):
            v = Vehicle(legal_entity_id=entity, type_id=type_id, plate=plate, make="КАМАЗ", model="65115")
            db.add(v)
            db.flush()
            add_reading(db, admin_who, v, "km", Decimal(km), recorded_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10), note="старт")
            return v

        w.va1, w.va2, w.va3 = vehicle("А001АА54", w.A, w.type_id), vehicle("А002АА54", w.A, w.type_id), vehicle("А003АА54", w.A, w.type_id)
        w.vb1 = vehicle("В001ВВ54", w.B, w.type_id)
        today = org_today()
        db.add(VehicleAssignment(vehicle_id=w.va1.id, employee_id=w.d1.id, started_on=today - dt.timedelta(days=30)))
        db.add(VehicleAssignment(vehicle_id=w.va2.id, employee_id=w.d2.id, started_on=today - dt.timedelta(days=30)))
        db.add(VehicleAssignment(vehicle_id=w.vb1.id, employee_id=w.d3.id, started_on=today - dt.timedelta(days=30)))
        db.commit()
        for name in ("va1", "va2", "va3", "vb1"):
            setattr(w, name, getattr(w, name).id)
        w.d1, w.d2, w.d3 = w.d1.id, w.d2.id, w.d3.id
        w.admin = w.admin.id
    return w
