# -*- coding: utf-8 -*-
"""Резервные копии: pg_dump в одном снимке с подсчётом строк, проверка восстановления во временную базу, ротация, расписание.

Копия считается надёжной только после проверки: файл целиком разворачивается во временную базу, число строк во всех
таблицах сравнивается с тем, что было в момент снимка, и сверяется версия схемы. Подсчёт и pg_dump идут в одном
снимке (pg_export_snapshot), поэтому расхождение означает настоящую проблему, а не работу пользователей в это время.
"""
import asyncio
import datetime as dt
import hashlib
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app import audit
from app.common import utcnow
from app.config import get_settings
from app.db import engine, session_factory
from app.models import BackupJob

log = logging.getLogger("erp.backup")
ADVISORY_KEY = 7_461_001
TIMEOUT = 3600
# Сколько защитных триггеров должно быть в восстановленной базе; без них копия не годится.
# Зависит от версии схемы: 0001 — 4 таблицы «только добавлять» и защита показаний; 0002 добавила защиту файлов и сканов.
PROTECTIVE_TRIGGERS_BY_SCHEMA = {"0001": 5}
PROTECTIVE_TRIGGERS = 7                      # для актуальной схемы


def expected_triggers(schema_version: str | None) -> int:
    return PROTECTIVE_TRIGGERS_BY_SCHEMA.get(schema_version or "", PROTECTIVE_TRIGGERS)


class BackupError(Exception):
    pass


def _url() -> URL:
    return make_url(get_settings().database_url)


def _bin(name: str) -> str:
    folder = get_settings().pg_bin_dir
    return str(Path(folder) / name) if folder else name


def _env(url: URL) -> dict:
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def _conn_args(url: URL, dbname: str | None = None) -> list[str]:
    args = ["-h", url.host or "localhost", "-p", str(url.port or 5432), "-U", url.username or "postgres"]
    return args + ([dbname] if dbname else [])


def backup_dir() -> Path:
    path = Path(get_settings().backup_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_path(job: BackupJob) -> Path | None:
    """Путь к файлу копии; только внутри папки копий."""
    if not job.filename or job.file_deleted:
        return None
    root = backup_dir().resolve()
    path = (root / job.filename).resolve()
    return path if path.parent == root and path.is_file() else None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def table_names(conn) -> list[str]:
    rows = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))
    return [r[0] for r in rows]


def count_rows(conn, tables: list[str]) -> dict[str, int]:
    return {t: int(conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()) for t in tables}


def schema_version(conn) -> str:
    try:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar() or ""
    except Exception:
        conn.rollback()
        return ""


def _run(cmd: list[str], env: dict):
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=TIMEOUT)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-5:]
        raise BackupError(f"{Path(cmd[0]).name}: " + " | ".join(tail))


# ---------------------------------------------------------------- создание копии

def create_job(db: Session, who, trigger: str) -> BackupJob:
    running = db.scalar(select(BackupJob.id).where(BackupJob.status == "running",
                                                   BackupJob.started_at > utcnow() - dt.timedelta(minutes=45)))
    if running:
        raise BackupError("Другая копия уже делается: дождитесь её окончания")
    job = BackupJob(status="running", trigger=trigger, initiated_by_id=who.user.id if who else None)
    db.add(job)
    db.flush()
    audit.log(db, who, "backup.start", "backup", job.id, None, trigger=trigger, login="система")
    db.commit()
    return job


def run_job(job_id: int, verify: bool = True) -> None:
    """Делает копию, потом (по умолчанию) сразу проверяет её восстановлением. Любая ошибка записывается в карточку копии."""
    started = time.monotonic()
    url = _url()
    with session_factory()() as db:
        job = db.get(BackupJob, job_id)
        path = backup_dir() / f"erp_{dt.datetime.now(ZoneInfo(get_settings().org_timezone)):%Y%m%d_%H%M%S}_{job.id}.dump"
        try:
            with engine().connect() as conn:
                conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar_one()
                counts = count_rows(conn, table_names(conn))
                version = schema_version(conn)
                _run([_bin("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", f"--snapshot={snapshot}",
                      "--file", str(path), *_conn_args(url, url.database)], _env(url))
                conn.rollback()
            if not path.is_file() or path.stat().st_size == 0:
                raise BackupError("Файл копии не создан")
            job.filename, job.size_bytes, job.sha256 = path.name, path.stat().st_size, sha256_of(path)
            job.counts_before, job.schema_version = counts, version
            job.status, job.finished_at, job.error = "ok", utcnow(), ""
            db.commit()
        except Exception as exc:                           # noqa: BLE001 — любая причина должна попасть в карточку копии
            log.exception("Копия %s не сделана", job_id)
            path.unlink(missing_ok=True)
            db.rollback()
            job = db.get(BackupJob, job_id)
            job.status, job.finished_at, job.error = "failed", utcnow(), str(exc)[:1500]
            audit.log(db, None, "backup.failed", "backup", job.id, None, login="система", error=job.error)
            db.commit()
            return
        audit.log(db, None, "backup.done", "backup", job.id, None, login="система", size=job.size_bytes, sha256=job.sha256[:16],
                  seconds=round(time.monotonic() - started, 1))
        db.commit()
    if verify:
        verify_job(job_id)
    rotate()


# ---------------------------------------------------------------- проверка восстановления

def verify_job(job_id: int) -> dict:
    """Разворачивает копию во временную базу и сравнивает с тем, что было в момент снимка."""
    url = _url()
    result: dict = {"state": "done", "ok": False, "mismatches": [], "checked_tables": 0}
    started = time.monotonic()
    temp = "erp_verify_" + uuid.uuid4().hex[:10]
    with session_factory()() as db:
        job = db.get(BackupJob, job_id)
        job.restore_check = {"state": "running"}
        db.commit()
        try:
            path = file_path(job)
            if not path:
                raise BackupError("Файл копии не найден на диске")
            if sha256_of(path) != job.sha256:
                raise BackupError("Контрольная сумма файла не совпала: файл повреждён или изменён")
            admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
            try:
                with admin.connect() as c:
                    c.execute(text(f'CREATE DATABASE "{temp}"'))
                _run([_bin("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error", "-d", temp,
                      *_conn_args(url)[:6], str(path)], _env(url))
                check = create_engine(url.set(database=temp))
                try:
                    with check.connect() as c:
                        after = count_rows(c, table_names(c))
                        restored_version = schema_version(c)
                        triggers = int(c.execute(text(
                            "SELECT count(*) FROM pg_trigger t JOIN pg_proc f ON f.oid = t.tgfoid "
                            "WHERE NOT t.tgisinternal AND f.proname IN ('forbid_change', 'guard_readings', 'guard_doc_files')")).scalar_one())
                finally:
                    check.dispose()
                before = job.counts_before or {}
                result["checked_tables"] = len(before)
                for t in sorted(set(before) | set(after)):
                    if before.get(t) != after.get(t):
                        result["mismatches"].append({"table": t, "before": before.get(t), "after": after.get(t)})
                result["schema_ok"] = restored_version == job.schema_version and bool(restored_version)
                result["schema_version"] = restored_version
                result["protective_triggers"] = triggers
                result["rows_total"] = sum(after.values())
                result["ok"] = not result["mismatches"] and result["schema_ok"] and triggers >= expected_triggers(job.schema_version)
                job.counts_after = after
            finally:
                with admin.connect() as c:
                    c.execute(text(f'DROP DATABASE IF EXISTS "{temp}" WITH (FORCE)'))
                admin.dispose()
        except Exception as exc:                           # noqa: BLE001
            log.exception("Проверка копии %s не прошла", job_id)
            result["error"] = str(exc)[:1500]
        result["seconds"] = round(time.monotonic() - started, 1)
        job = db.get(BackupJob, job_id)
        job.restore_check, job.restore_checked_at = result, utcnow()
        audit.log(db, None, "backup.verify", "backup", job.id, None, login="система", ok=result["ok"],
                  mismatches=len(result["mismatches"]), error=result.get("error", ""))
        db.commit()
    return result


# ---------------------------------------------------------------- ротация и расписание

def rotate() -> int:
    """Оставляет последние backup_keep удачных копий; самая свежая проверенная копия не удаляется никогда."""
    keep = max(get_settings().backup_keep, 1)
    removed = 0
    with session_factory()() as db:
        jobs = db.scalars(select(BackupJob).where(BackupJob.status == "ok", BackupJob.file_deleted.is_(False))
                          .order_by(BackupJob.started_at.desc(), BackupJob.id.desc())).all()
        protected = next((j.id for j in jobs if (j.restore_check or {}).get("ok")), None)
        for job in jobs[keep:]:
            if job.id == protected:
                continue
            path = file_path(job)
            if path:
                path.unlink(missing_ok=True)
            job.file_deleted = True
            removed += 1
        for job in db.scalars(select(BackupJob).where(BackupJob.status == "failed", BackupJob.file_deleted.is_(False))):
            job.file_deleted = True
        if removed:
            audit.log(db, None, "backup.rotate", "backup", None, None, login="система", removed=removed, keep=keep)
        db.commit()
    return removed


def run_scheduled() -> None:
    """Ночная копия. Блокировка в базе не даёт двум экземплярам сервера сделать копию одновременно."""
    with engine().connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_KEY}).scalar():
            return
        try:
            with session_factory()() as db:
                try:
                    job = create_job(db, None, "auto")
                except BackupError:
                    return
            run_job(job.id, verify=True)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_KEY})
            lock.commit()


async def scheduler() -> None:
    settings = get_settings()
    tz = ZoneInfo(settings.org_timezone)
    while True:
        now = dt.datetime.now(tz)
        nxt = now.replace(hour=settings.backup_hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += dt.timedelta(days=1)
        wait = (nxt - now).total_seconds()
        if wait > 3600:                                   # просыпаемся раз в час, чтобы не зависеть от скачков времени
            await asyncio.sleep(3600)
            continue
        await asyncio.sleep(wait + 1)
        try:
            await asyncio.to_thread(run_scheduled)
        except Exception:                                  # noqa: BLE001
            log.exception("Ночная копия не выполнена")
        await asyncio.sleep(60)
