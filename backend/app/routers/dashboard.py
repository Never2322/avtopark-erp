# -*- coding: utf-8 -*-
"""Главная: только настоящие данные и только то, на что у человека есть право. Разделы без права не попадают в ответ."""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.common import fail
from app.db import get_db
from app.deps import Principal, current
from app.enums import REQUEST_OPEN, REQUEST_PRIORITIES, REQUEST_STATUSES, VEHICLE_STATUSES
from app.models import AuditLog, Employee, ServiceRequest, Vehicle, VehicleReading, Waybill
from app.routers import audit_log
from app.routers import requests_ as req_router
from app.routers import waybills as wb_router
from app.services import access

router = APIRouter(prefix="/dashboard", tags=["Главная"])


def jobs_summary(db: Session, p: Principal, legal_entity_id: int | None) -> dict:
    """Наряды на сегодня: сколько всего, сколько ещё не закончено, и сколько не закрыто за прошлые дни. Водителю только по своей технике."""
    from app.models import Job
    from app.resources import job_today
    from app.routers.jobs import OPEN_STATES, mine
    today = job_today()
    full = p.scope("jobs.view")
    cond = [Job.archived_at.is_(None)]
    if full:
        cond.append(full.cond(Job.legal_entity_id))
    else:
        cond += [p.scope("jobs.view_own").cond(Job.legal_entity_id), Job.vehicle_id.in_(mine(db, p) or {0})]
    if legal_entity_id is not None:
        cond.append(Job.legal_entity_id == legal_entity_id)
    today_rows = dict(db.execute(select(Job.state, func.count()).where(*cond, Job.job_date == today).group_by(Job.state)).all())
    stale = db.scalar(select(func.count()).where(*cond, Job.job_date < today, Job.state.in_(OPEN_STATES))) or 0
    return {"today": sum(today_rows.values()), "open": sum(today_rows.get(k, 0) for k in OPEN_STATES),
            "done": today_rows.get("done", 0), "stale": stale, "own": not full}


def check_entity(p: Principal, legal_entity_id: int | None):
    if legal_entity_id is not None and not p.visible_entities().allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")


@router.get("")
def dashboard(legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not p.can("dashboard.view"):
        raise fail(403, "forbidden", "Нет доступа к главной странице")
    check_entity(p, legal_entity_id)
    today = dt.date.today()
    data: dict = {"date": today.isoformat(), "vehicles": None, "requests": None, "waybills": None, "readings": None, "audit": None, "jobs": None}

    if p.can("vehicles.view") or p.can("vehicles.view_own"):
        cond = and_(access.view_condition(p, db), Vehicle.archived_at.is_(None))
        if legal_entity_id is not None:
            cond = and_(cond, Vehicle.legal_entity_id == legal_entity_id)
        by = dict(db.execute(select(Vehicle.status, func.count()).where(cond).group_by(Vehicle.status)).all())
        data["vehicles"] = {"total": sum(by.values()),
                            "by_status": [{"code": k, "label": v, "count": by.get(k, 0)} for k, v in VEHICLE_STATUSES.items()],
                            "attention": by.get("in_repair", 0) + by.get("awaiting_repair", 0)}

    if p.can("requests.view") or p.can("requests.view_own"):
        cond = req_router.visible_condition(p)
        if legal_entity_id is not None:
            cond = and_(cond, ServiceRequest.legal_entity_id == legal_entity_id)
        by = dict(db.execute(select(ServiceRequest.status, func.count()).where(cond).group_by(ServiceRequest.status)).all())
        open_cond = and_(cond, ServiceRequest.status.in_(REQUEST_OPEN))
        urgent = db.scalar(select(func.count()).where(open_cond, ServiceRequest.priority.in_(("urgent", "high")))) or 0
        overdue = db.scalar(select(func.count()).where(open_cond, ServiceRequest.due_date < today)) or 0
        waiting = db.scalar(select(func.count()).where(and_(cond, ServiceRequest.status == "approval"))) or 0
        latest = db.scalars(select(ServiceRequest).where(open_cond).order_by(
            ServiceRequest.priority.in_(("urgent", "high")).desc(), ServiceRequest.created_at.desc()).limit(6)).all()
        data["requests"] = {"open": sum(by.get(k, 0) for k in REQUEST_OPEN), "urgent": urgent, "overdue": overdue,
                            "awaiting_approval": waiting,
                            "by_status": [{"code": k, "label": v, "count": by.get(k, 0)} for k, v in REQUEST_STATUSES.items()],
                            "latest": [{"id": r.id, "number": r.number, "title": r.title, "status": r.status,
                                        "status_label": REQUEST_STATUSES[r.status], "priority": r.priority,
                                        "priority_label": REQUEST_PRIORITIES[r.priority], "plate": r.vehicle.plate} for r in latest]}

    if p.can("waybills.view") or p.can("waybills.view_own"):
        cond = wb_router.visible_condition(p)
        if legal_entity_id is not None:
            cond = and_(cond, Waybill.legal_entity_id == legal_entity_id)
        opened = db.scalars(select(Waybill).where(cond, Waybill.status == "open").order_by(Waybill.opened_at).limit(8)).all()
        drafts = db.scalar(select(func.count()).where(cond, Waybill.status == "draft")) or 0
        today_n = db.scalar(select(func.count()).where(cond, Waybill.work_date == today, Waybill.status != "cancelled")) or 0
        # лист открыт дольше суток — скорее всего, его забыли закрыть
        stale = [w for w in opened if w.opened_at and (dt.datetime.now(dt.timezone.utc) - w.opened_at) > dt.timedelta(hours=24)]
        data["waybills"] = {"open": len(opened), "drafts": drafts, "today": today_n, "forgotten": len(stale),
                            "open_list": [{"id": w.id, "number_label": wb_router.number_label(w), "plate": w.vehicle.plate,
                                           "driver": wb_router.driver_name(w.driver), "opened_at": w.opened_at.isoformat() if w.opened_at else None}
                                          for w in opened]}

    if p.can("jobs.view") or p.can("jobs.view_own"):
        data["jobs"] = jobs_summary(db, p, legal_entity_id)

    if p.can("readings.review"):
        scope = p.scope("readings.review")
        cond = and_(VehicleReading.review_state == "pending", VehicleReading.vehicle_id.in_(
            select(Vehicle.id).where(scope.cond(Vehicle.legal_entity_id), *([Vehicle.legal_entity_id == legal_entity_id] if legal_entity_id else []))))
        data["readings"] = {"pending": db.scalar(select(func.count()).where(cond)) or 0}

    if p.can("audit.view"):
        scope = p.scope("audit.view")
        conds = [] if scope.everything else [scope.cond(AuditLog.legal_entity_id)]
        if legal_entity_id is not None:
            conds.append(AuditLog.legal_entity_id == legal_entity_id)
        rows = db.scalars(select(AuditLog).where(*conds).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(8)).all()
        data["audit"] = {"latest": [audit_log.out(r) for r in rows]}
    return data
