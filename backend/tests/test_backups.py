# -*- coding: utf-8 -*-
"""Критерий: восстановление из резервной копии проверено. Копия делается в одном снимке, разворачивается во временную базу и сверяется."""
from pathlib import Path

from sqlalchemy import select

from app.db import session_factory
from app.models import BackupJob
from app.services import backup
from tests.conftest import BACKUPS


def start(admin):
    r = admin.post("/backups")
    assert r.status_code == 202, r.text
    return r.json()["id"]


def job(admin, bid):
    return admin.get(f"/backups/{bid}").json()


def test_backup_is_made_and_verified_by_real_restore(make_client, world):
    make_client("dir_a").post("/requests", {"vehicle_id": world.va1, "title": "Данные для копии"})
    admin = make_client("admin")
    bid = start(admin)
    b = job(admin, bid)                                   # фоновая задача уже отработала
    assert b["status"] == "ok", b
    check = b["restore_check"]
    assert check["ok"] is True and check["mismatches"] == [] and check["schema_ok"] is True
    assert check["protective_triggers"] >= 7              # защита от переписывания переехала вместе с данными
    assert b["verified"] and b["restore_checked_at"] and b["tables"] > 20 and b["rows_total"] > 50
    assert b["size_bytes"] > 1000 and len(b["sha256"]) == 64 and b["file_available"]
    files = list(Path(BACKUPS).glob("erp_*.dump"))
    assert len(files) == 1 and files[0].name == b["filename"]
    assert files[0].read_bytes()[:5] == b"PGDMP"
    listing = admin.get("/backups").json()
    assert listing["warnings"] == [] and listing["items"][0]["id"] == bid
    # временная база после проверки удалена
    from sqlalchemy import create_engine, text
    from tests.conftest import ADMIN_URL
    eng = create_engine(ADMIN_URL)
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM pg_database WHERE datname LIKE 'erp_verify_%'")).scalar() == 0
    eng.dispose()


def test_only_full_admin_can_touch_backups(make_client, world):
    admin = make_client("admin")
    bid = start(admin)
    for who in ("dir_a", "mech_a", "drv1", "acc_a"):
        c = make_client(who)
        assert c.get("/backups").status_code == 403
        assert c.post("/backups").status_code == 403
        assert c.get(f"/backups/{bid}").status_code == 403
        assert c.get(f"/backups/{bid}/download").status_code == 403
        assert c.post(f"/backups/{bid}/verify").status_code == 403


def test_download_gives_the_file_and_is_logged(make_client, world):
    admin = make_client("admin")
    bid = start(admin)
    r = admin.get(f"/backups/{bid}/download")
    assert r.status_code == 200 and r.content[:5] == b"PGDMP"
    assert "backup.download" in [a["action"] for a in admin.get("/audit", params={"page_size": 50}).json()["items"]]
    assert admin.get("/backups/9999/download").status_code == 404


def test_damaged_file_fails_verification(make_client, world):
    admin = make_client("admin")
    bid = start(admin)
    path = next(Path(BACKUPS).glob("erp_*.dump"))
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    assert admin.post(f"/backups/{bid}/verify").status_code == 202
    b = job(admin, bid)
    assert b["verified"] is False and "Контрольная сумма" in b["restore_check"]["error"]
    assert any("проверен" in w for w in admin.get("/backups").json()["warnings"])


def test_deleted_file_is_reported_not_crashing(make_client, world):
    admin = make_client("admin")
    bid = start(admin)
    next(Path(BACKUPS).glob("erp_*.dump")).unlink()
    assert job(admin, bid)["file_available"] is False
    assert admin.post(f"/backups/{bid}/verify").status_code == 409
    assert admin.get(f"/backups/{bid}/download").status_code == 404


def test_counts_in_backup_match_database_at_snapshot(make_client, world):
    admin = make_client("admin")
    bid = start(admin)
    with session_factory()() as db:
        j = db.get(BackupJob, bid)
        assert j.counts_before["vehicles"] == 4 and j.counts_before["employees"] == 3 and j.counts_after == j.counts_before


def test_second_backup_cannot_start_while_first_is_running(make_client, world):
    with session_factory()() as db:
        db.add(BackupJob(status="running", trigger="manual"))
        db.commit()
    assert make_client("admin").post("/backups").status_code == 409


def test_rotation_keeps_latest_and_protects_the_verified_one(make_client, world):
    admin = make_client("admin")
    first = start(admin)                                       # проверенная копия
    for _ in range(4):
        with session_factory()() as db:
            j = backup.create_job(db, None, "auto")
        backup.run_job(j.id, verify=False)                     # без проверки, чтобы тест шёл быстро
    with session_factory()() as db:
        jobs = db.scalars(select(BackupJob).order_by(BackupJob.id)).all()
        alive = [j.id for j in jobs if not j.file_deleted]
        assert len(jobs) == 5 and first in alive                # backup_keep=3 свежих + защищённая проверенная
        assert len(alive) == 4 and len(list(Path(BACKUPS).glob("erp_*.dump"))) == 4
        assert [j.id for j in jobs if j.file_deleted] == [jobs[1].id]


def test_failed_backup_leaves_a_reason_and_no_file(make_client, world, monkeypatch):
    monkeypatch.setenv("PG_BIN_DIR", "/nonexistent")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        admin = make_client("admin")
        bid = start(admin)
        b = job(admin, bid)
        assert b["status"] == "failed" and b["error"] and not list(Path(BACKUPS).glob("erp_*.dump"))
        assert any("копий ещё нет" in w for w in admin.get("/backups").json()["warnings"])
    finally:
        monkeypatch.delenv("PG_BIN_DIR")
        get_settings.cache_clear()


def test_scheduled_run_makes_and_verifies_a_copy(make_client, world):
    backup.run_scheduled()
    with session_factory()() as db:
        j = db.scalar(select(BackupJob).order_by(BackupJob.id.desc()))
        assert j.trigger == "auto" and j.status == "ok" and (j.restore_check or {}).get("ok")
