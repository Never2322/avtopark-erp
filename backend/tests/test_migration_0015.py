# -*- coding: utf-8 -*-
"""Миграция 0015: статусов техники остаётся два; старые переводятся, списанные уходят в архив, история остаётся нетронутой."""
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

from tests.conftest import ADMIN_URL, BACKEND

DB = "erp_mig15"
URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB


def alembic(*args):
    done = subprocess.run([sys.executable, "-m", "alembic", "-x", f"url={URL}", *args], cwd=BACKEND, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1500:]


@pytest.fixture()
def old_database():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{DB}"'))
    alembic("upgrade", "0014")
    engine = create_engine(URL)
    yield engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
    admin.dispose()


OLD = ["in_work", "in_repair", "awaiting_repair", "parked", "day_off", "rented", "unused", "written_off"]
EXPECT = {"in_work": "in_work", "in_repair": "in_repair", "awaiting_repair": "in_repair", "parked": "in_work", "day_off": "in_work",
          "rented": "in_work", "unused": "in_work", "written_off": "in_work"}


def test_old_statuses_are_converted(old_database):
    with old_database.begin() as db:
        db.execute(text("INSERT INTO legal_entities (name, short_name, inn, kpp, ogrn, address, phone, email) VALUES ('ООО А','А','','','','','','')"))
        db.execute(text("INSERT INTO vehicle_types (code, name, category, primary_reading) VALUES ('t','Тип','truck','km')"))
        for i, status in enumerate(OLD):
            db.execute(text("INSERT INTO vehicles (legal_entity_id, type_id, plate, internal_no, make, model, note, status) "
                            "VALUES (1, 1, :p, '', '', '', '', :s)"), {"p": f"А00{i}АА54", "s": status})
        db.execute(text("INSERT INTO vehicle_status_history (vehicle_id, from_status, status, reason) "
                        "SELECT id, 'in_work', status, 'раньше' FROM vehicles WHERE status = 'day_off'"))
    alembic("upgrade", "head")
    with old_database.connect() as db:
        rows = {r[0]: r[1:] for r in db.execute(text("SELECT status_before.plate, v.status, v.archived_at IS NOT NULL "
                                                     "FROM (SELECT id, plate FROM vehicles) status_before JOIN vehicles v USING (id)")).all()}
        got = {OLD[i]: rows[f"А00{i}АА54"] for i in range(len(OLD))}
        for old, (new, archived) in ((o, got[o]) for o in OLD):
            assert new == EXPECT[old], old
            assert archived is (old == "written_off"), old                       # в архив уходят только списанные
        # у каждой переведённой машины добавлена запись, прежняя запись осталась
        hist = db.execute(text("SELECT from_status, status, reason FROM vehicle_status_history ORDER BY id")).all()
        converted = [h for h in hist if h[2].startswith("Статусы упрощены")]
        assert sorted(h[0] for h in converted) == sorted(o for o in OLD if EXPECT[o] != o)
        assert any(h[2] == "раньше" for h in hist)
        # новые коды можно записать, старые в машинах уже нельзя
        bad = db.execute(text("SELECT count(*) FROM vehicles WHERE status NOT IN ('in_work','in_repair')")).scalar()
        assert bad == 0
    with old_database.begin() as db:
        with pytest.raises(Exception):
            db.execute(text("UPDATE vehicles SET status = 'parked' WHERE id = 1"))


def test_downgrade_restores_old_constraint(old_database):
    alembic("upgrade", "head")
    alembic("downgrade", "0014")
    with old_database.begin() as db:
        db.execute(text("INSERT INTO legal_entities (name, short_name, inn, kpp, ogrn, address, phone, email) VALUES ('ООО А','А','','','','','','')"))
        db.execute(text("INSERT INTO vehicle_types (code, name, category, primary_reading) VALUES ('t','Тип','truck','km')"))
        db.execute(text("INSERT INTO vehicles (legal_entity_id, type_id, plate, internal_no, make, model, note, status) VALUES (1,1,'А001АА54','','','','','parked')"))
