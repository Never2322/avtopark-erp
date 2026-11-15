# -*- coding: utf-8 -*-
"""Обращения и ремонты: заявка водителя или сотрудника проходит диагностику, согласование, ремонт и закрытие."""
import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current, load_grants, require
from app.enums import (REQUEST_APPROVAL_STEPS, REQUEST_FLOW, REQUEST_OPEN, REQUEST_PARTS_MAX, REQUEST_PRIORITIES, REQUEST_STATUSES,
                       VEHICLE_STATUSES)
from app.models import RequestPart, ServiceRequest, ServiceRequestEvent, User, Vehicle
from app.services import access, notify
from app.services import bills as bills_svc
from app.services import parts as parts_svc
from app.services.numbering import next_number
from app.services.vehicles import change_status

router = APIRouter(prefix="/requests", tags=["Обращения и ремонты"])
MONEY = dict(ge=0, le=999_999_999, max_digits=14, decimal_places=2)


class RequestCreate(BaseModel):
    vehicle_id: int
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    parts: list[dict] = Field(default_factory=list, max_length=REQUEST_PARTS_MAX)     # детали сразу при создании заявки


class RequestUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: str | None = Field(default=None, pattern="^(low|normal|high|urgent)$")
    assignee_id: int | None = None
    clear_assignee: bool = False
    due_date: dt.date | None = None
    clear_due_date: bool = False
    planned_work: str | None = Field(default=None, max_length=4000)
    cost_estimate: Decimal | None = Field(default=None, **MONEY)
    cost_actual: Decimal | None = Field(default=None, **MONEY)


class TransitionIn(BaseModel):
    to_status: str
    comment: str = Field(default="", max_length=2000)
    vehicle_status: str | None = None     # заодно сменить статус машины (например, «На ремонте»)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def visible_condition(p: Principal):
    full, own = p.scope("requests.view"), p.scope("requests.view_own")
    parts = []
    if full:
        parts.append(full.cond(ServiceRequest.legal_entity_id))
    if own:
        parts.append(and_(own.cond(ServiceRequest.legal_entity_id), ServiceRequest.reporter_id == p.id))
    if not parts:
        raise fail(403, "forbidden", "Нет доступа к обращениям")
    return or_(*parts)


def visible(p: Principal, r: ServiceRequest) -> bool:
    return p.can("requests.view", r.legal_entity_id) or (p.can("requests.view_own", r.legal_entity_id) and r.reporter_id == p.id)


def name_of(u: User | None) -> str | None:
    return (u.display_name or u.login) if u else None


def allowed_transitions(p: Principal, r: ServiceRequest) -> list[str]:
    result = []
    for to in REQUEST_FLOW[r.status]:
        if r.status == "new" and to == "cancelled" and r.reporter_id == p.id:
            result.append(to)                         # автор может отозвать свою заявку, пока её не взяли в работу
            continue
        if not p.can("requests.transition", r.legal_entity_id):
            continue
        if (r.status, to) in REQUEST_APPROVAL_STEPS and not p.can("requests.approve", r.legal_entity_id):
            continue
        result.append(to)
    return result


def out(db: Session, p: Principal, r: ServiceRequest, detailed: bool = False) -> dict:
    reporter, assignee = db.get(User, r.reporter_id) if r.reporter_id else None, db.get(User, r.assignee_id) if r.assignee_id else None
    v = r.vehicle
    sees_money = p.can("requests.edit", r.legal_entity_id) or p.can("requests.approve", r.legal_entity_id)
    data = {
        "id": r.id, "number": r.number, "legal_entity_id": r.legal_entity_id,
        "vehicle": {"id": v.id, "plate": v.plate, "name": f"{v.make} {v.model}".strip(), "status": v.status},
        "title": r.title, "priority": r.priority, "priority_label": REQUEST_PRIORITIES[r.priority],
        "status": r.status, "status_label": REQUEST_STATUSES[r.status], "open": r.status in REQUEST_OPEN,
        "reporter": name_of(reporter), "assignee": name_of(assignee), "assignee_id": r.assignee_id,
        "due_date": r.due_date.isoformat() if r.due_date else None,
        "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat(),
        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        "overdue": bool(r.due_date and r.due_date < dt.date.today() and r.status in REQUEST_OPEN),
    }
    if detailed:
        data.update({
            "description": r.description, "planned_work": r.planned_work, "currency": r.currency,
            "cost_estimate": float(r.cost_estimate) if r.cost_estimate is not None and sees_money else None,
            "cost_actual": float(r.cost_actual) if r.cost_actual is not None and sees_money else None,
            "money_visible": sees_money,
            "allowed_transitions": [{"status": s, "label": REQUEST_STATUSES[s]} for s in allowed_transitions(p, r)],
            "actions": {"edit": p.can("requests.edit", r.legal_entity_id) and r.status in REQUEST_OPEN,
                        "comment": r.status not in ("closed", "cancelled"),
                        "set_vehicle_status": p.can("vehicle_status.change", r.legal_entity_id)},
        })
        data["parts"] = parts_svc.block(db, p, r)
        data["bills"] = bills_svc.request_block(db, p, r)
        evs = db.execute(select(ServiceRequestEvent, User.display_name, User.login).outerjoin(User, User.id == ServiceRequestEvent.author_id)
                         .where(ServiceRequestEvent.request_id == r.id).order_by(ServiceRequestEvent.id)).all()
        data["events"] = [{"id": e.id, "kind": e.kind, "text": e.text, "created_at": e.created_at.isoformat(), "author": n or l,
                           "from_status": e.from_status, "to_status": e.to_status,
                           "from_label": REQUEST_STATUSES.get(e.from_status or ""), "to_label": REQUEST_STATUSES.get(e.to_status or "")}
                          for e, n, l in evs]
    return data


def get_request(db: Session, p: Principal, rid: int) -> ServiceRequest:
    r = db.get(ServiceRequest, rid)
    if not r or not visible(p, r):
        raise fail(404, "not_found", "Обращение не найдено")
    return r


FIELD_NAMES = {"title": "суть", "description": "описание", "priority": "срочность", "planned_work": "план работ", "due_date": "срок",
               "cost_estimate": "смета", "cost_actual": "фактические затраты"}


def add_event(db: Session, r: ServiceRequest, p: Principal, kind: str, text: str = "", from_status=None, to_status=None):
    db.add(ServiceRequestEvent(request_id=r.id, kind=kind, text=text, author_id=p.id, from_status=from_status, to_status=to_status))


@router.get("/meta")
def meta(p: Principal = Depends(current)):
    return {"statuses": [{"code": k, "label": v} for k, v in REQUEST_STATUSES.items()],
            "priorities": [{"code": k, "label": v} for k, v in REQUEST_PRIORITIES.items()]}


@router.get("/assignees")
def assignees(legal_entity_id: int, p: Principal = Depends(require("requests.edit")), db: Session = Depends(get_db)):
    """Кого можно назначить исполнителем: активные пользователи с правом вести обращения в этом юрлице."""
    if not p.can("requests.edit", legal_entity_id):
        raise fail(403, "forbidden", "Нет права вести обращения этого юрлица")
    result = []
    for u in db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.display_name, User.login)):
        scope = load_grants(db, u.id).get("requests.edit")
        if scope and scope.allows(legal_entity_id):
            result.append({"id": u.id, "name": name_of(u)})
    return {"items": result}


@router.get("")
def list_requests(q: str = "", status: str = "", priority: str = "", vehicle_id: int | None = None, assignee: str = "",
                  mine: bool = False, legal_entity_id: int | None = None, page: int = 1, page_size: int = 50,
                  p: Principal = Depends(current), db: Session = Depends(get_db)):
    base = visible_condition(p)
    if legal_entity_id is not None:
        if not (p.scope("requests.view") | p.scope("requests.view_own")).allows(legal_entity_id):
            raise fail(403, "forbidden", "Нет доступа к этому юридическому лицу")
        base = and_(base, ServiceRequest.legal_entity_id == legal_entity_id)
    if vehicle_id:
        base = and_(base, ServiceRequest.vehicle_id == vehicle_id)
    if mine:
        base = and_(base, ServiceRequest.reporter_id == p.id)
    if assignee == "me":
        base = and_(base, ServiceRequest.assignee_id == p.id)
    elif assignee == "none":
        base = and_(base, ServiceRequest.assignee_id.is_(None))
    if priority:
        if priority not in REQUEST_PRIORITIES:
            raise fail(422, "bad_priority", "Неизвестный приоритет", "priority")
        base = and_(base, ServiceRequest.priority == priority)
    if q.strip():
        t = like(q.strip())
        conds = [ServiceRequest.title.ilike(t), ServiceRequest.vehicle_id.in_(select(Vehicle.id).where(Vehicle.plate.ilike(t.replace(" ", ""))))]
        if q.strip().lstrip("№#").isdigit():
            conds.append(ServiceRequest.number == int(q.strip().lstrip("№#")))
        base = and_(base, or_(*conds))
    counts = dict(db.execute(select(ServiceRequest.status, func.count()).where(base).group_by(ServiceRequest.status)).all())
    query = select(ServiceRequest).where(base)
    if status == "open":
        query = query.where(ServiceRequest.status.in_(REQUEST_OPEN))
    elif status:
        if status not in REQUEST_STATUSES:
            raise fail(422, "bad_status", "Неизвестный статус", "status")
        query = query.where(ServiceRequest.status == status)
    urgency = case({"urgent": 0, "high": 1, "normal": 2, "low": 3}, value=ServiceRequest.priority, else_=4)
    query = query.order_by(ServiceRequest.status.in_(REQUEST_OPEN).desc(), urgency, ServiceRequest.created_at.desc())
    rows, total = page_of(db, query, page, page_size)
    return {"items": [out(db, p, r[0]) for r in rows], "total": total, "page": page, "page_size": page_size,
            "counts": {k: counts.get(k, 0) for k in REQUEST_STATUSES}, "open_total": sum(counts.get(k, 0) for k in REQUEST_OPEN)}


@router.post("", status_code=201)
def create_request(body: RequestCreate, p: Principal = Depends(require("requests.create")), db: Session = Depends(get_db)):
    v = db.get(Vehicle, body.vehicle_id)
    if not v or not access.can_pick(p, db, v):
        raise fail(422, "bad_vehicle", "Машина не найдена или недоступна вам", "vehicle_id")
    if not p.can("requests.create", v.legal_entity_id):
        raise fail(403, "forbidden", "Нет права создавать обращения по технике этого юрлица")
    if v.archived_at or v.status == "written_off":
        raise fail(409, "vehicle_archived", "Машина в архиве или списана", "vehicle_id")
    lines = parts_svc.clean_lines(body.parts)
    for i, line in enumerate(lines):
        if any(parts_svc.same_part(line, y) for y in lines[:i]):
            raise fail(409, "duplicate_part", f"«{line['name']}» записана в списке дважды", "parts")
    r = ServiceRequest(legal_entity_id=v.legal_entity_id, number=next_number(db, v.legal_entity_id, "request"), vehicle_id=v.id,
                       title=body.title.strip(), description=body.description.strip(), priority=body.priority,
                       reporter_id=p.id, created_by_id=p.id, updated_by_id=p.id)
    db.add(r)
    db.flush()
    add_event(db, r, p, "status", "Обращение создано", None, "new")
    if lines:
        for line in lines:
            db.add(RequestPart(request_id=r.id, added_by_id=p.id, **line))
        parts_svc.learn(db, lines)
        add_event(db, r, p, "edit", "Добавлены детали: " + ", ".join(f"{x['name']} — {parts_svc.qty_text(x['qty'])} {x['unit']}" for x in lines))
    audit.log(db, p, "request.create", "request", r.id, r.legal_entity_id, number=r.number, vehicle=v.plate, priority=r.priority,
              parts=[x["name"] for x in lines])
    notify.on_request_new(db, r, p.id)
    db.commit()
    db.refresh(r)
    return out(db, p, r, detailed=True)


@router.get("/{rid}")
def read_request(rid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    return out(db, p, get_request(db, p, rid), detailed=True)


@router.patch("/{rid}")
def update_request(rid: int, body: RequestUpdate, p: Principal = Depends(require("requests.edit")), db: Session = Depends(get_db)):
    r = get_request(db, p, rid)
    if not p.can("requests.edit", r.legal_entity_id):
        raise fail(403, "forbidden", "Нет права вести обращения этого юрлица")
    if r.status not in REQUEST_OPEN:
        raise fail(409, "request_closed", "Обращение закрыто: менять его нельзя")
    changed = []
    for f in ("title", "description", "priority", "planned_work"):
        v = getattr(body, f)
        if v is not None and (v.strip() if isinstance(v, str) else v) != getattr(r, f):
            setattr(r, f, v.strip() if isinstance(v, str) else v)
            changed.append(f)
    if body.due_date is not None or body.clear_due_date:
        new_due = None if body.clear_due_date else body.due_date
        if new_due != r.due_date:
            r.due_date = new_due
            changed.append("due_date")
    for f in ("cost_estimate", "cost_actual"):
        v = getattr(body, f)
        if v is not None and v != getattr(r, f):
            setattr(r, f, v)
            changed.append(f)
    if body.assignee_id is not None or body.clear_assignee:
        new_id = None if body.clear_assignee else body.assignee_id
        if new_id != r.assignee_id:
            if new_id is not None:
                u = db.get(User, new_id)
                scope = load_grants(db, u.id).get("requests.edit") if u and u.is_active else None
                if not (scope and scope.allows(r.legal_entity_id)):
                    raise fail(422, "bad_assignee", "Исполнителем может быть только сотрудник с правом вести обращения", "assignee_id")
            r.assignee_id = new_id
            add_event(db, r, p, "assign", "Исполнитель: " + (name_of(db.get(User, new_id)) if new_id else "не назначен"))
            changed.append("assignee")
            if new_id is not None:
                notify.on_request_assigned(db, r, new_id, p.id)
    non_assign = [c for c in changed if c != "assignee"]
    if non_assign:
        add_event(db, r, p, "edit", "Изменено: " + ", ".join(FIELD_NAMES.get(c, c) for c in non_assign))
    if changed:
        r.updated_by_id = p.id
        audit.log(db, p, "request.update", "request", r.id, r.legal_entity_id, number=r.number, changed=changed)
    db.commit()
    db.refresh(r)
    return out(db, p, r, detailed=True)


@router.post("/{rid}/transition")
def transition(rid: int, body: TransitionIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_request(db, p, rid)
    if body.to_status not in REQUEST_STATUSES:
        raise fail(422, "bad_status", "Неизвестный статус", "to_status")
    if body.to_status not in REQUEST_FLOW[r.status]:
        raise fail(409, "bad_transition", f"Из статуса «{REQUEST_STATUSES[r.status]}» нельзя перейти в «{REQUEST_STATUSES[body.to_status]}»")
    if body.to_status not in allowed_transitions(p, r):
        if (r.status, body.to_status) in REQUEST_APPROVAL_STEPS and p.can("requests.transition", r.legal_entity_id):
            raise fail(403, "approve_required", "Согласовать или отклонить ремонт может только тот, у кого есть право согласования")
        raise fail(403, "forbidden", "Нет права переводить обращение в этот статус")
    comment = body.comment.strip()
    if body.to_status == "cancelled" and not comment:
        raise fail(422, "reason_required", "Укажите причину отмены", "comment")
    old = r.status
    r.status, r.updated_by_id = body.to_status, p.id
    r.closed_at = utcnow() if body.to_status in ("closed", "cancelled") else None
    add_event(db, r, p, "status", comment, old, body.to_status)
    if body.vehicle_status:
        if body.vehicle_status not in VEHICLE_STATUSES:
            raise fail(422, "bad_status", "Неизвестный статус машины", "vehicle_status")
        if not p.can("vehicle_status.change", r.legal_entity_id):
            raise fail(403, "forbidden", "Нет права менять статус техники")
        change_status(db, p, r.vehicle, body.vehicle_status, f"Обращение №{r.number}: {REQUEST_STATUSES[body.to_status].lower()}", r.id)
    audit.log(db, p, "request.transition", "request", r.id, r.legal_entity_id, number=r.number, from_status=old,
              to_status=body.to_status, vehicle_status=body.vehicle_status, comment=comment)
    notify.on_request_status(db, r, body.to_status, comment, p.id)
    db.commit()
    db.refresh(r)
    return out(db, p, r, detailed=True)


@router.post("/{rid}/comments", status_code=201)
def comment(rid: int, body: CommentIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    r = get_request(db, p, rid)
    if r.status in ("closed", "cancelled"):
        raise fail(409, "request_closed", "Обращение закрыто: комментарии больше не добавляются")
    add_event(db, r, p, "comment", body.text.strip())
    r.updated_by_id = p.id
    r.updated_at = utcnow()
    audit.log(db, p, "request.comment", "request", r.id, r.legal_entity_id, number=r.number)
    notify.on_request_comment(db, r, body.text.strip(), p.id)
    db.commit()
    return out(db, p, r, detailed=True)
