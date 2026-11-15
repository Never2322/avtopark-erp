# -*- coding: utf-8 -*-
"""Главная: только настоящие данные и только то, на что у человека есть право. Разделы без права не попадают в ответ."""
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.common import fail, org_today
from app.db import get_db
from app.deps import Principal, current
from app.enums import MAINTENANCE_KINDS, REQUEST_OPEN, REQUEST_PRIORITIES, REQUEST_STATUSES, VEHICLE_STATUSES
from app.models import AuditLog, MaintenanceItem, Order, ServiceRequest, Vehicle, VehicleReading, Waybill
from app.routers import audit_log
from app.routers import requests_ as req_router
from app.routers import waybills as wb_router
from app.services import access
from app.services import control as control_svc
from app.services import reminders as reminders_svc
from app.services import reports as rep

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


def expenses(db: Session, p: Principal, legal_entity_id: int | None) -> dict | None:
    """Затраты по месяцам за полгода: топливо, ремонт, прочее. Только тем, у кого есть право на деньги в отчётах."""
    if not (p.can("reports.money") and p.can("vehicles.view")):
        return None
    today, now = org_today(), dt.datetime.now(dt.timezone.utc)
    months, ids = [], None
    for y, m in rep.last_months(today, 6):
        a, b = rep.month_bounds(y, m)
        c = rep.Ctx(db=db, p=p, entity=legal_entity_id, d_from=a, d_to=b, today=today, now=now)
        if ids is None:
            try:
                ids = [v.id for v in rep.load_vehicles(c)]
            except HTTPException:
                return None
        parts = rep.money_parts(c, ids)
        tot = lambda *keys: round(sum(sum(parts.get(k, {}).values()) for k in keys), 2)
        fuel, repair, other = tot("fuel"), tot("bills", "requests"), tot("fines", "rent_in")
        months.append({"label": rep.MONTHS_SHORT[m], "fuel": fuel, "repair": repair, "other": other, "total": round(fuel + repair + other, 2)})
    cur, prev = months[-1]["total"], months[-2]["total"]
    return {"months": months, "month_total": cur, "prev_total": prev,
            "delta_pct": round((cur - prev) / prev * 100) if prev > 0 else None}


def maintenance_summary(db: Session, p: Principal, legal_entity_id: int | None) -> dict | None:
    """Ближайшее плановое ТО: по дате и пробегу, самое срочное первым. Считает только то, что уже контролируется."""
    scope = p.scope("maintenance.view")
    if not scope:
        return None
    today = org_today()
    q = (select(MaintenanceItem, Vehicle).join(Vehicle, Vehicle.id == MaintenanceItem.vehicle_id)
         .where(MaintenanceItem.archived_at.is_(None), Vehicle.archived_at.is_(None), MaintenanceItem.controlled.is_(True),
                MaintenanceItem.done_at.is_(None), scope.cond(MaintenanceItem.legal_entity_id)))
    if legal_entity_id is not None:
        q = q.where(MaintenanceItem.legal_entity_id == legal_entity_id)
    rows = []
    for item, v in db.execute(q):
        ev = control_svc.evaluate(due_date=item.due_date, due_km=item.due_km, due_hours=None, controlled=True, closed=False,
                                  today=today, cur_km=v.current_km, cur_hours=v.current_hours)
        if ev["level"] != "closed":
            rows.append((ev["urgency"], item.id, item, v, ev))
    rows.sort(key=lambda r: (r[0], r[1]))
    return {"soon": sum(1 for r in rows if r[4]["level"] in ("due", "soon")),
            "items": [{"id": i.id, "vehicle_id": v.id, "plate": v.plate, "name": f"{v.make} {v.model}".strip(),
                       "kind_label": MAINTENANCE_KINDS.get(i.kind, i.kind), "work": i.work, "level": ev["level"],
                       "left_text": control_svc.left_text(ev["left"]), "due_date": i.due_date.isoformat() if i.due_date else None}
                      for _, _, i, v, ev in rows[:3]]}


def orders_summary(db: Session, p: Principal, legal_entity_id: int | None) -> dict | None:
    """Заказы: сколько в работе и как шли новые заказы последние две недели (для маленького графика)."""
    scope = p.scope("orders.view")
    if not scope:
        return None
    today = org_today()
    cond = [Order.archived_at.is_(None), scope.cond(Order.legal_entity_id)]
    if legal_entity_id is not None:
        cond.append(Order.legal_entity_id == legal_entity_id)
    active = db.scalar(select(func.count()).where(*cond, Order.status.in_(("new", "confirmed", "in_progress")))) or 0
    since = today - dt.timedelta(days=13)
    per_day = dict(db.execute(select(Order.order_date, func.count()).where(*cond, Order.status != "cancelled", Order.order_date >= since,
                                                                          Order.order_date <= today).group_by(Order.order_date)).all())
    days = [per_day.get(since + dt.timedelta(days=i), 0) for i in range(14)]
    return {"active": active, "days": days, "week": sum(days[7:]), "prev_week": sum(days[:7])}


def check_entity(p: Principal, legal_entity_id: int | None):
    if legal_entity_id is not None and not p.visible_entities().allows(legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")


@router.get("")
def dashboard(legal_entity_id: int | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    if not p.can("dashboard.view"):
        raise fail(403, "forbidden", "Нет доступа к главной странице")
    check_entity(p, legal_entity_id)
    today = org_today()
    data: dict = {"date": today.isoformat(), "vehicles": None, "requests": None, "waybills": None, "readings": None, "audit": None, "jobs": None,
            "expenses": None, "maintenance": None, "orders": None, "reminders": None}

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

    data["expenses"] = expenses(db, p, legal_entity_id)
    data["maintenance"] = maintenance_summary(db, p, legal_entity_id)
    data["orders"] = orders_summary(db, p, legal_entity_id)
    if p.can("reminders.view"):
        items = reminders_svc.reminders(db, p, legal_entity_id)
        data["reminders"] = {"total": len(items), "due": sum(1 for r in items if r["level"] == "due"), "items": items[:5]}
    return data
