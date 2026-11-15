# -*- coding: utf-8 -*-
"""Резервные копии базы: создать, проверить восстановлением, скачать. Только полный доступ (backups.manage)."""
import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.config import get_settings
from app.db import get_db
from app.deps import Principal, require_global
from app.models import BackupJob, User
from app.services import backup, channels, offsite

router = APIRouter(prefix="/backups", tags=["Резервные копии"])
STALE_AFTER = dt.timedelta(hours=36)


def out(job: BackupJob, who: str | None = None) -> dict:
    check = job.restore_check or None
    return {
        "id": job.id, "status": job.status, "trigger": job.trigger, "trigger_label": "Автоматически" if job.trigger == "auto" else "Вручную",
        "started_at": job.started_at.isoformat(), "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "filename": job.filename, "size_bytes": job.size_bytes, "sha256": job.sha256, "error": job.error,
        "schema_version": job.schema_version, "file_available": bool(backup.file_path(job)) if job.status == "ok" else False,
        "file_deleted": job.file_deleted, "initiated_by": who,
        "tables": len(job.counts_before or {}), "rows_total": sum((job.counts_before or {}).values()),
        "restore_checked_at": job.restore_checked_at.isoformat() if job.restore_checked_at else None,
        "restore_check": check,
        "verified": bool(check and check.get("ok")),
        "verifying": bool(check and check.get("state") == "running"),
        "offsite": job.offsite or None,
    }


def names(db: Session, jobs: list[BackupJob]) -> dict[int, str]:
    ids = {j.initiated_by_id for j in jobs if j.initiated_by_id}
    rows = db.execute(select(User.id, User.display_name, User.login).where(User.id.in_(ids))).all() if ids else []
    return {i: (n or l) for i, n, l in rows}


def get_job(db: Session, bid: int) -> BackupJob:
    job = db.get(BackupJob, bid)
    if not job:
        raise fail(404, "not_found", "Копия не найдена")
    return job


@router.get("")
def list_backups(p: Principal = Depends(require_global("backups.manage")), db: Session = Depends(get_db)):
    jobs = db.scalars(select(BackupJob).order_by(BackupJob.started_at.desc(), BackupJob.id.desc()).limit(100)).all()
    who = names(db, jobs)
    last_ok = next((j for j in jobs if j.status == "ok"), None)
    last_verified = next((j for j in jobs if j.status == "ok" and (j.restore_check or {}).get("ok")), None)
    warnings = []
    if not last_ok:
        warnings.append("Резервных копий ещё нет. Сделайте первую.")
    elif utcnow() - last_ok.finished_at > STALE_AFTER:
        warnings.append("Последней копии больше суток. Проверьте, что ночное копирование работает.")
    if last_ok and not last_verified and (last_ok.restore_check or {}).get("state") != "running":
        warnings.append("Ни одна копия ещё не проверена восстановлением. Нажмите «Проверить».")
    elif last_ok and last_verified and last_ok.id != last_verified.id and (last_ok.restore_check or {}).get("state") != "running":
        warnings.append("Самая свежая копия не проверена или проверка не прошла.")
    s = get_settings()
    return {"items": [out(j, who.get(j.initiated_by_id)) for j in jobs], "warnings": warnings,
            "settings": {"keep": s.backup_keep, "schedule": s.backup_schedule, "hour": s.backup_hour, "timezone": s.org_timezone,
                         "offsite": offsite.plan()}}


@router.post("", status_code=202)
def start_backup(tasks: BackgroundTasks, p: Principal = Depends(require_global("backups.manage")), db: Session = Depends(get_db)):
    try:
        job = backup.create_job(db, p, "manual")
    except backup.BackupError as exc:
        raise fail(409, "backup_running", str(exc))
    tasks.add_task(backup.run_job, job.id, True)
    return out(job, p.user.display_name or p.login)


@router.get("/{bid}")
def read_backup(bid: int, p: Principal = Depends(require_global("backups.manage")), db: Session = Depends(get_db)):
    job = get_job(db, bid)
    return out(job, names(db, [job]).get(job.initiated_by_id))


@router.post("/{bid}/verify", status_code=202)
def verify_backup(bid: int, tasks: BackgroundTasks, p: Principal = Depends(require_global("backups.manage")), db: Session = Depends(get_db)):
    job = get_job(db, bid)
    if job.status != "ok":
        raise fail(409, "bad_state", "Проверять можно только готовую копию")
    if not backup.file_path(job):
        raise fail(409, "file_missing", "Файл этой копии уже удалён")
    if (job.restore_check or {}).get("state") == "running":
        raise fail(409, "already_running", "Проверка уже идёт")
    job.restore_check = {"state": "running"}
    audit.log(db, p, "backup.verify_request", "backup", job.id, None)
    db.commit()
    tasks.add_task(backup.verify_job, job.id)
    return out(job)


@router.get("/{bid}/download")
def download(bid: int, p: Principal = Depends(require_global("backups.manage")), db: Session = Depends(get_db)):
    job = get_job(db, bid)
    path = backup.file_path(job)
    if job.status != "ok" or not path:
        raise fail(404, "file_missing", "Файл этой копии недоступен")
    audit.log(db, p, "backup.download", "backup", job.id, None, filename=job.filename)
    db.commit()
    return FileResponse(path, media_type="application/octet-stream", filename=job.filename)


@router.post("/{bid}/send-offsite", status_code=202)
def send_offsite(bid: int, tasks: BackgroundTasks, p: Principal = Depends(require_global("backups.manage")), db: Session = Depends(get_db)):
    """Отправить готовую копию вне сервера (зашифрованной): в Telegram и/или на почту, как задано в настройках."""
    job = get_job(db, bid)
    if job.status != "ok" or not backup.file_path(job):
        raise fail(409, "file_missing", "Отправлять можно только готовую копию, файл которой ещё есть")
    if not channels.offsite_enabled():
        raise fail(409, "offsite_off", "Копии вне сервера выключены: в настройках сервера не задан пароль шифрования OFFSITE_PASSWORD")
    plan = offsite.plan()
    if not (plan["telegram"] or plan["email"]):
        raise fail(409, "offsite_no_target", "Некуда отправлять: подключите Telegram (право на копии) или укажите OFFSITE_EMAIL_TO")
    audit.log(db, p, "backup.offsite", "backup", job.id, None)
    db.commit()
    tasks.add_task(offsite.send, job.id)
    return out(job)
