# -*- coding: utf-8 -*-
"""Личные дела водителей (сотрудников) и техники: документы, сканы, фото, сроки действия.

Правила:
- паспорт чувствительный: для него нужно отдельное право, без него записи о паспорте не видны вообще;
- каждый просмотр или скачивание скана пишется в журнал действий;
- сканы не стираются: документ уходит в архив, а скан убирается из документа (с причиной), сам файл остаётся;
- чужое юрлицо выглядит как «не найдено».
"""
import datetime as dt
import urllib.parse
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.common import fail, like, org_today, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current, entity_filter, require
from app.enums import (ALL_DOC_KINDS, DOC_EXPIRING_DAYS, DOC_KINDS_NO_EXPIRY, INSURANCE_DOC_KINDS, EMPLOYEE_DOC_KINDS, EMPLOYEE_SENSITIVE_KINDS,
                       FUEL_TYPES, OWNERSHIP, READING_KINDS, REQUEST_STATUSES, VEHICLE_DOC_KINDS, VEHICLE_STATUSES, status_label)
from app.models import (DossierDoc, DossierDocFile, Employee, EmploymentAssignment, LegalEntity, ServiceRequest, StoredFile,
                        Vehicle, VehicleAssignment, VehicleReading, VehicleSpecs, VehicleStatusHistory)
from app.routers.employees import can_see, employee_entities, full_name, out as employee_out, visible_filter
from app.services import files as filesvc
from app.services.vehicles import active_assignment_filter

router = APIRouter(prefix="/dossier", tags=["Личные дела"])

MAX_ACTIVE_FILES_PER_DOC = 30


# ---------------------------------------------------------------- сроки

def expiry_status(valid_until: dt.date | None, today: dt.date | None = None) -> str:
    """none — срока нет; expired — просрочен; expiring — кончается в ближайшие 30 дней; ok — всё в порядке."""
    if valid_until is None:
        return "none"
    days = (valid_until - (today or org_today())).days
    return "expired" if days < 0 else "expiring" if days <= DOC_EXPIRING_DAYS else "ok"


def days_left(valid_until: dt.date | None, today: dt.date | None = None) -> int | None:
    return None if valid_until is None else (valid_until - (today or org_today())).days


# ---------------------------------------------------------------- права

def employee_doc_perm(kind: str) -> str:
    return "employee_files.view_passport" if kind in EMPLOYEE_SENSITIVE_KINDS else "employee_files.view"


def can_view_doc(p: Principal, db: Session, doc: DossierDoc) -> bool:
    if doc.employee_id:
        e = db.get(Employee, doc.employee_id)
        return bool(e) and can_see(p, db, e, employee_doc_perm(doc.kind))
    v = db.get(Vehicle, doc.vehicle_id)
    return bool(v) and p.can("vehicle_files.view", v.legal_entity_id)


def can_edit_doc_owner(p: Principal, db: Session, doc: DossierDoc) -> bool:
    if doc.employee_id:
        e = db.get(Employee, doc.employee_id)
        if not e or not can_see(p, db, e, "employee_files.edit"):
            return False
        return doc.kind not in EMPLOYEE_SENSITIVE_KINDS or can_see(p, db, e, "employee_files.view_passport")
    v = db.get(Vehicle, doc.vehicle_id)
    return bool(v) and p.can("vehicle_files.edit", v.legal_entity_id)


def get_doc(db: Session, p: Principal, doc_id: int, *, edit: bool = False) -> DossierDoc:
    doc = db.scalar(select(DossierDoc).where(DossierDoc.id == doc_id)
                    .options(selectinload(DossierDoc.files).joinedload(DossierDocFile.file)))
    if not doc or not can_view_doc(p, db, doc):
        raise fail(404, "not_found", "Документ не найден")           # чужое не раскрываем
    if edit and not can_edit_doc_owner(p, db, doc):
        raise fail(403, "forbidden", "Нет права менять документы в этом личном деле")
    return doc


def audit_entity(db: Session, doc: DossierDoc) -> int | None:
    if doc.employee_id:
        e = db.get(Employee, doc.employee_id)
        return next(iter(sorted(employee_entities(db, e))), None) if e else None
    v = db.get(Vehicle, doc.vehicle_id)
    return v.legal_entity_id if v else None


def audit_owner(doc: DossierDoc) -> tuple[str, int]:
    return ("employee", doc.employee_id) if doc.employee_id else ("vehicle", doc.vehicle_id)


# ---------------------------------------------------------------- вывод

def file_out(link: DossierDocFile) -> dict:
    f = link.file
    return {"link_id": link.id, "file_id": f.id, "name": f.original_name, "content_type": f.content_type,
            "size_bytes": f.size_bytes, "is_image": f.content_type in filesvc.IMAGE_TYPES, "width": f.width, "height": f.height,
            "added_at": link.added_at.isoformat()}


def doc_out(doc: DossierDoc) -> dict:
    active = [l for l in doc.files if l.removed_at is None]
    status = expiry_status(doc.valid_until)
    return {
        "id": doc.id, "kind": doc.kind, "kind_label": ALL_DOC_KINDS[doc.kind], "title": doc.title, "doc_number": doc.doc_number,
        "issued_on": doc.issued_on.isoformat() if doc.issued_on else None,
        "valid_until": doc.valid_until.isoformat() if doc.valid_until else None,
        "expiry": status, "days_left": days_left(doc.valid_until),
        "issued_by": doc.issued_by, "note": doc.note, "cost": float(doc.cost) if doc.cost is not None else None,
        "archived": doc.archived_at is not None, "archive_reason": doc.archive_reason,
        "archived_at": doc.archived_at.isoformat() if doc.archived_at else None,
        "created_at": doc.created_at.isoformat(),
        "files": [file_out(l) for l in active], "removed_files": len(doc.files) - len(active),
    }


def cover_of(docs: list[DossierDoc]) -> int | None:
    """Главное фото: первый скан в самом новом (не архивном) документе вида «Фото»."""
    for d in sorted((d for d in docs if d.kind == "photo" and d.archived_at is None), key=lambda d: -d.id):
        for l in d.files:
            if l.removed_at is None and l.file.content_type in filesvc.IMAGE_TYPES:
                return l.file_id
    return None


def summarize(docs: list[DossierDoc]) -> dict:
    live = [d for d in docs if d.archived_at is None and d.kind not in DOC_KINDS_NO_EXPIRY]
    statuses = [expiry_status(d.valid_until) for d in live]
    dates = sorted(d.valid_until for d in live if d.valid_until)
    return {"documents": len([d for d in docs if d.archived_at is None and d.kind != "photo"]),
            "expired": statuses.count("expired"), "expiring": statuses.count("expiring"),
            "nearest_until": dates[0].isoformat() if dates else None}


def load_docs(db: Session, *, employee_ids=None, vehicle_ids=None) -> dict[int, list[DossierDoc]]:
    col = DossierDoc.employee_id if employee_ids is not None else DossierDoc.vehicle_id
    ids = employee_ids if employee_ids is not None else vehicle_ids
    if not ids:
        return {}
    docs = db.scalars(select(DossierDoc).where(col.in_(ids)).options(selectinload(DossierDoc.files).joinedload(DossierDocFile.file))
                      .order_by(DossierDoc.id)).all()
    grouped: dict[int, list[DossierDoc]] = {}
    for d in docs:
        grouped.setdefault(d.employee_id if employee_ids is not None else d.vehicle_id, []).append(d)
    return grouped


def parse_date(value: str | None, field: str) -> dt.date | None:
    if value is None or not value.strip():
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        raise fail(422, "bad_date", "Нужна дата в формате ГГГГ-ММ-ДД", field) from None


# ---------------------------------------------------------------- сотрудники: список и карточка

@router.get("/employees")
def list_employee_files(q: str = "", legal_entity_id: int | None = None, drivers_only: bool = True, archived: bool = False,
                        problems: bool = False, page: int = 1, page_size: int = 50,
                        p: Principal = Depends(require("employee_files.view")), db: Session = Depends(get_db)):
    scope = entity_filter(p, "employee_files.view", legal_entity_id)
    query = select(Employee).order_by(Employee.last_name, Employee.first_name, Employee.id)
    cond = visible_filter(scope)
    if cond is not None:
        query = query.where(cond)
    if legal_entity_id is not None:
        query = query.where(exists().where(EmploymentAssignment.employee_id == Employee.id,
                                           EmploymentAssignment.legal_entity_id == legal_entity_id))
    query = query.where(Employee.archived_at.is_not(None) if archived else Employee.archived_at.is_(None))
    if drivers_only:
        query = query.where(Employee.is_driver.is_(True))
    if q.strip():
        t = like(q.strip())
        query = query.where(or_(Employee.last_name.ilike(t), Employee.first_name.ilike(t), Employee.middle_name.ilike(t), Employee.position.ilike(t)))
    if problems:
        # есть просроченный или скоро истекающий документ
        horizon = org_today() + dt.timedelta(days=DOC_EXPIRING_DAYS)
        query = query.where(exists().where(DossierDoc.employee_id == Employee.id, DossierDoc.archived_at.is_(None),
                                           DossierDoc.kind.notin_(tuple(DOC_KINDS_NO_EXPIRY)), DossierDoc.valid_until <= horizon,
                                           DossierDoc.kind.notin_(tuple(EMPLOYEE_SENSITIVE_KINDS))))
    rows, total = page_of(db, query, page, page_size)
    employees = [r[0] for r in rows]
    docs = load_docs(db, employee_ids=[e.id for e in employees])
    items = []
    for e in employees:
        see_passport = can_see(p, db, e, "employee_files.view_passport")
        visible = [d for d in docs.get(e.id, []) if d.kind not in EMPLOYEE_SENSITIVE_KINDS or see_passport]
        ents = db.scalars(select(LegalEntity).where(LegalEntity.id.in_(employee_entities(db, e)))).all() if e.assignments else []
        licences = sorted((d.valid_until for d in visible if d.kind == "driver_license" and d.archived_at is None and d.valid_until), reverse=True)
        items.append({"id": e.id, "full_name": full_name(e), "position": e.position, "is_driver": e.is_driver,
                      "archived": e.archived_at is not None,
                      "legal_entities": [{"id": x.id, "name": x.short_name or x.name} for x in ents],
                      "photo_file_id": cover_of(visible), "summary": summarize(visible),
                      "license_until": licences[0].isoformat() if licences else None,
                      "license_expiry": expiry_status(licences[0]) if licences else "none"})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/employees/{eid}")
def read_employee_file(eid: int, p: Principal = Depends(require("employee_files.view")), db: Session = Depends(get_db)):
    e = db.get(Employee, eid)
    if not e or not can_see(p, db, e, "employee_files.view"):
        raise fail(404, "not_found", "Сотрудник не найден")
    see_passport = can_see(p, db, e, "employee_files.view_passport")
    can_edit = can_see(p, db, e, "employee_files.edit")
    docs = [d for d in load_docs(db, employee_ids=[e.id]).get(e.id, []) if d.kind not in EMPLOYEE_SENSITIVE_KINDS or see_passport]
    head = employee_out(db, p, e, detailed=True) if p.can("employees.view") and can_see(p, db, e, "employees.view") else None
    if head is None:                                  # нет права на карточку сотрудника: даём только имя и должность
        head = {"id": e.id, "full_name": full_name(e), "position": e.position, "is_driver": e.is_driver, "archived": e.archived_at is not None,
                "legal_entities": [{"id": x.id, "name": x.short_name or x.name}
                                   for x in db.scalars(select(LegalEntity).where(LegalEntity.id.in_(employee_entities(db, e))))]}
    return {
        "employee": head, "photo_file_id": cover_of(docs), "can_edit": can_edit, "can_passport": see_passport,
        "kinds": [{"code": k, "label": v, "sensitive": k in EMPLOYEE_SENSITIVE_KINDS, "expires": k not in DOC_KINDS_NO_EXPIRY}
                  for k, v in EMPLOYEE_DOC_KINDS.items() if k not in EMPLOYEE_SENSITIVE_KINDS or see_passport],
        "documents": [doc_out(d) for d in reversed(docs) if d.archived_at is None],
        "archived_documents": [doc_out(d) for d in reversed(docs) if d.archived_at is not None],
        "summary": summarize(docs),
    }


# ---------------------------------------------------------------- добавление и изменение документов

def read_uploads(uploads: list[UploadFile], *, images_only: bool, required: bool) -> list[filesvc.Prepared]:
    uploads = [u for u in uploads if u is not None and (u.filename or u.size)]
    if required and not uploads:
        raise fail(422, "no_file", "Приложите хотя бы один файл", "files")
    if len(uploads) > filesvc.MAX_FILES_PER_REQUEST:
        raise fail(422, "too_many_files", f"За один раз можно загрузить не больше {filesvc.MAX_FILES_PER_REQUEST} файлов", "files")
    return [filesvc.prepare(u.filename, u.file.read(filesvc.MAX_FILE_BYTES + 1), images_only=images_only) for u in uploads]


def store(db: Session, p: Principal, prepared: list[filesvc.Prepared]) -> list[StoredFile]:
    stored = []
    for f in prepared:
        sf = StoredFile(sha256=f.sha256, size_bytes=len(f.data), content_type=f.content_type, original_name=f.name,
                        width=f.width, height=f.height, data=f.data, thumb=f.thumb, uploaded_by_id=p.id)
        db.add(sf)
        stored.append(sf)
    db.flush()
    return stored


def attach(doc: DossierDoc, stored: list[StoredFile], p: Principal) -> None:
    start = len([l for l in doc.files if l.removed_at is None])
    for i, sf in enumerate(stored):
        doc.files.append(DossierDocFile(file_id=sf.id, position=start + i, added_by_id=p.id, file=sf))


def parse_cost(text: str | None) -> Decimal | None:
    t = (text or "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    if not t:
        return None
    try:
        v = Decimal(t)
    except Exception:
        raise fail(422, "bad_cost", "Стоимость полиса: впишите число, например 12500", "cost") from None
    if v < 0 or v > 100_000_000:
        raise fail(422, "bad_cost", "Стоимость полиса указана неверно", "cost")
    return v.quantize(Decimal("0.01"))


def create_doc(db: Session, p: Principal, *, employee: Employee | None, vehicle: Vehicle | None, kind: str, title: str,
               doc_number: str, issued_on: str | None, valid_until: str | None, issued_by: str, note: str,
               files: list[UploadFile], cost: str | None = None) -> DossierDoc:
    kinds = EMPLOYEE_DOC_KINDS if employee else VEHICLE_DOC_KINDS
    if kind not in kinds:
        raise fail(422, "bad_kind", "Выберите вид документа из списка", "kind")
    issued, until = parse_date(issued_on, "issued_on"), parse_date(valid_until, "valid_until")
    if kind in DOC_KINDS_NO_EXPIRY:
        issued = until = None
    if issued and until and until < issued:
        raise fail(422, "bad_dates", "Срок действия не может кончаться раньше даты выдачи", "valid_until")
    price = parse_cost(cost) if kind in INSURANCE_DOC_KINDS else None
    prepared = read_uploads(files, images_only=kind == "photo", required=kind == "photo")
    doc = DossierDoc(employee_id=employee.id if employee else None, vehicle_id=vehicle.id if vehicle else None, kind=kind,
                     title=(title.strip() or kinds[kind])[:200], doc_number=doc_number.strip()[:60], issued_on=issued,
                     valid_until=until, issued_by=issued_by.strip()[:200], note=note.strip()[:2000], cost=price,
                     created_by_id=p.id, updated_by_id=p.id)
    db.add(doc)
    db.flush()
    db.refresh(doc, ["files"])
    attach(doc, store(db, p, prepared), p)
    db.flush()
    owner, owner_id = audit_owner(doc)
    audit.log(db, p, "dossier.doc.create", owner, owner_id, audit_entity(db, doc), doc_id=doc.id, kind=kind, files=len(prepared))
    return doc


@router.post("/employees/{eid}/docs", status_code=201)
def add_employee_doc(eid: int, kind: str = Form(...), title: str = Form(""), doc_number: str = Form(""),
                     issued_on: str | None = Form(None), valid_until: str | None = Form(None), issued_by: str = Form(""),
                     note: str = Form(""), files: list[UploadFile] = File(default=[]),
                     p: Principal = Depends(require("employee_files.edit")), db: Session = Depends(get_db)):
    e = db.get(Employee, eid)
    if not e or not can_see(p, db, e, "employee_files.edit"):
        raise fail(404, "not_found", "Сотрудник не найден")
    if kind in EMPLOYEE_SENSITIVE_KINDS and not can_see(p, db, e, "employee_files.view_passport"):
        raise fail(403, "forbidden", "Паспорта добавляют только те, у кого есть право на паспорта")
    doc = create_doc(db, p, employee=e, vehicle=None, kind=kind, title=title, doc_number=doc_number, issued_on=issued_on,
                     valid_until=valid_until, issued_by=issued_by, note=note, files=files)
    db.commit()
    return doc_out(get_doc(db, p, doc.id))


class DocPatch(BaseModel):
    title: str = Field(default="", max_length=200)
    doc_number: str = Field(default="", max_length=60)
    issued_on: dt.date | None = None
    valid_until: dt.date | None = None
    issued_by: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)
    cost: Decimal | None = Field(default=None, ge=0, le=100_000_000)


@router.patch("/docs/{doc_id}")
def update_doc(doc_id: int, body: DocPatch, p: Principal = Depends(current), db: Session = Depends(get_db)):
    doc = get_doc(db, p, doc_id, edit=True)
    if doc.archived_at:
        raise fail(409, "archived", "Документ в архиве: сначала верните его")
    if doc.kind in DOC_KINDS_NO_EXPIRY and (body.issued_on or body.valid_until):
        raise fail(422, "no_expiry", "У фото нет срока действия", "valid_until")
    if body.issued_on and body.valid_until and body.valid_until < body.issued_on:
        raise fail(422, "bad_dates", "Срок действия не может кончаться раньше даты выдачи", "valid_until")
    changed = []
    if doc.kind not in INSURANCE_DOC_KINDS:
        body.cost = None
    elif body.cost is not None:
        body.cost = body.cost.quantize(Decimal("0.01"))
    for field in ("title", "doc_number", "issued_on", "valid_until", "issued_by", "note", "cost"):
        value = getattr(body, field)
        if isinstance(value, str):
            value = value.strip()
        if field == "title" and not value:
            value = ALL_DOC_KINDS[doc.kind]
        if getattr(doc, field) != value:
            setattr(doc, field, value)
            changed.append(field)
    doc.updated_by_id = p.id
    owner, owner_id = audit_owner(doc)
    audit.log(db, p, "dossier.doc.update", owner, owner_id, audit_entity(db, doc), doc_id=doc.id, kind=doc.kind, changed=changed)
    db.commit()
    return doc_out(get_doc(db, p, doc_id))


@router.post("/docs/{doc_id}/files", status_code=201)
def add_doc_files(doc_id: int, files: list[UploadFile] = File(default=[]), p: Principal = Depends(current),
                  db: Session = Depends(get_db)):
    doc = get_doc(db, p, doc_id, edit=True)
    if doc.archived_at:
        raise fail(409, "archived", "Документ в архиве: сначала верните его")
    prepared = read_uploads(files, images_only=doc.kind == "photo", required=True)
    if len([l for l in doc.files if l.removed_at is None]) + len(prepared) > MAX_ACTIVE_FILES_PER_DOC:
        raise fail(422, "too_many_files", f"В одном документе не больше {MAX_ACTIVE_FILES_PER_DOC} сканов", "files")
    attach(doc, store(db, p, prepared), p)
    doc.updated_by_id = p.id
    db.flush()
    owner, owner_id = audit_owner(doc)
    audit.log(db, p, "dossier.file.add", owner, owner_id, audit_entity(db, doc), doc_id=doc.id, kind=doc.kind, files=len(prepared))
    db.commit()
    return doc_out(get_doc(db, p, doc_id))


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/docs/{doc_id}/files/{link_id}/remove")
def remove_doc_file(doc_id: int, link_id: int, body: ReasonIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    doc = get_doc(db, p, doc_id, edit=True)
    link = next((l for l in doc.files if l.id == link_id and l.removed_at is None), None)
    if not link:
        raise fail(404, "not_found", "Скан не найден")
    link.removed_at, link.removed_by_id, link.remove_reason = utcnow(), p.id, body.reason.strip()
    owner, owner_id = audit_owner(doc)
    audit.log(db, p, "dossier.file.remove", owner, owner_id, audit_entity(db, doc), doc_id=doc.id, kind=doc.kind, file_id=link.file_id,
              reason=body.reason.strip())
    db.commit()
    return doc_out(get_doc(db, p, doc_id))


@router.post("/docs/{doc_id}/archive")
def archive_doc(doc_id: int, body: ReasonIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    doc = get_doc(db, p, doc_id, edit=True)
    if doc.archived_at:
        raise fail(409, "archived", "Документ уже в архиве")
    doc.archived_at, doc.archived_by_id, doc.archive_reason, doc.updated_by_id = utcnow(), p.id, body.reason.strip(), p.id
    owner, owner_id = audit_owner(doc)
    audit.log(db, p, "dossier.doc.archive", owner, owner_id, audit_entity(db, doc), doc_id=doc.id, kind=doc.kind, reason=body.reason.strip())
    db.commit()
    return doc_out(get_doc(db, p, doc_id))


@router.post("/docs/{doc_id}/restore")
def restore_doc(doc_id: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    doc = get_doc(db, p, doc_id, edit=True)
    if not doc.archived_at:
        raise fail(409, "not_archived", "Документ не в архиве")
    doc.archived_at, doc.archived_by_id, doc.archive_reason, doc.updated_by_id = None, None, "", p.id
    owner, owner_id = audit_owner(doc)
    audit.log(db, p, "dossier.doc.restore", owner, owner_id, audit_entity(db, doc), doc_id=doc.id, kind=doc.kind)
    db.commit()
    return doc_out(get_doc(db, p, doc_id))


# ---------------------------------------------------------------- выдача файлов

@router.get("/files/{file_id}")
def get_file(file_id: int, thumb: bool = False, download: bool = False, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Файл отдаётся только тому, у кого есть право на документ, в котором он лежит. Каждый просмотр пишется в журнал
    (кроме маленьких превью в списках: иначе журнал утонет)."""
    links = db.scalars(select(DossierDocFile).where(DossierDocFile.file_id == file_id, DossierDocFile.removed_at.is_(None))).all()
    doc = None
    for link in links:
        candidate = db.get(DossierDoc, link.doc_id)
        if candidate and can_view_doc(p, db, candidate):
            doc = candidate
            break
    if doc is None:
        raise fail(404, "not_found", "Файл не найден")
    sf = db.get(StoredFile, file_id)
    if thumb and sf.thumb:
        data, content_type = sf.thumb, "image/jpeg"
    else:
        data, content_type = sf.data, sf.content_type
        owner, owner_id = audit_owner(doc)
        audit.log(db, p, "dossier.file.view", owner, owner_id, audit_entity(db, doc), doc_id=doc.id, kind=doc.kind, file_id=file_id,
                  mode="download" if download else "view")
        db.commit()
    name = urllib.parse.quote(sf.original_name or "file")
    disposition = ("attachment" if download else "inline") + f"; filename*=UTF-8''{name}"
    return Response(content=data, media_type=content_type, headers={"Content-Disposition": disposition, "Cache-Control": "private, no-store"})


# ---------------------------------------------------------------- техника: список и карточка

@router.get("/vehicles")
def list_vehicle_files(q: str = "", legal_entity_id: int | None = None, status: str = "", archived: bool = False,
                       problems: bool = False, page: int = 1, page_size: int = 50,
                       p: Principal = Depends(require("vehicle_files.view")), db: Session = Depends(get_db)):
    scope = entity_filter(p, "vehicle_files.view", legal_entity_id)
    query = select(Vehicle).where(scope.cond(Vehicle.legal_entity_id)).order_by(Vehicle.plate)
    query = query.where(Vehicle.archived_at.is_not(None) if archived else Vehicle.archived_at.is_(None))
    if status in VEHICLE_STATUSES:
        query = query.where(Vehicle.status == status)
    if q.strip():
        t = like(q.strip())
        query = query.where(or_(Vehicle.plate.ilike(t), Vehicle.make.ilike(t), Vehicle.model.ilike(t), Vehicle.vin.ilike(t), Vehicle.internal_no.ilike(t)))
    if problems:
        horizon = org_today() + dt.timedelta(days=DOC_EXPIRING_DAYS)
        query = query.where(exists().where(DossierDoc.vehicle_id == Vehicle.id, DossierDoc.archived_at.is_(None),
                                           DossierDoc.kind.notin_(tuple(DOC_KINDS_NO_EXPIRY)), DossierDoc.valid_until <= horizon))
    rows, total = page_of(db, query, page, page_size)
    vehicles = [r[0] for r in rows]
    docs = load_docs(db, vehicle_ids=[v.id for v in vehicles])
    entities = {x.id: (x.short_name or x.name) for x in db.scalars(select(LegalEntity))}
    items = []
    for v in vehicles:
        mine = docs.get(v.id, [])
        items.append({"id": v.id, "plate": v.plate, "name": f"{v.make} {v.model}".strip(), "type": v.type.name, "status": v.status,
                      "status_label": VEHICLE_STATUSES[v.status], "legal_entity_id": v.legal_entity_id,
                      "legal_entity": entities.get(v.legal_entity_id, ""), "archived": v.archived_at is not None,
                      "photo_file_id": cover_of(mine), "summary": summarize(mine)})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_vehicle(db: Session, p: Principal, vid: int, perm: str = "vehicle_files.view") -> Vehicle:
    v = db.get(Vehicle, vid)
    if not v or not p.can(perm, v.legal_entity_id):
        raise fail(404, "not_found", "Техника не найдена")
    return v


def specs_out(s: VehicleSpecs | None) -> dict:
    keys = ("color", "engine_no", "chassis_no", "body_no", "fuel_type", "eco_class", "owner_name", "ownership", "note")
    data = {k: (getattr(s, k) if s else "") for k in keys}
    for k in ("engine_power_hp", "engine_volume_cc", "max_weight_kg", "empty_weight_kg", "tank_liters", "fuel_norm"):
        v = getattr(s, k) if s else None
        data[k] = float(v) if isinstance(v, Decimal) else v
    for k in ("registered_on", "commissioned_on"):
        v = getattr(s, k) if s else None
        data[k] = v.isoformat() if v else None
    return data


@router.get("/vehicles/{vid}")
def read_vehicle_file(vid: int, p: Principal = Depends(require("vehicle_files.view")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid)
    docs = load_docs(db, vehicle_ids=[v.id]).get(v.id, [])
    specs = db.get(VehicleSpecs, v.id)
    entity = db.get(LegalEntity, v.legal_entity_id)
    data = {
        "vehicle": {"id": v.id, "plate": v.plate, "internal_no": v.internal_no, "make": v.make, "model": v.model, "vin": v.vin,
                    "year": v.year, "type": v.type.name, "category": v.type.category, "status": v.status,
                    "status_label": VEHICLE_STATUSES[v.status], "legal_entity_id": v.legal_entity_id,
                    "legal_entity": (entity.short_name or entity.name) if entity else "", "archived": v.archived_at is not None,
                    "current_km": float(v.current_km) if v.current_km is not None else None,
                    "current_hours": float(v.current_hours) if v.current_hours is not None else None, "note": v.note},
        "specs": specs_out(specs), "photo_file_id": cover_of(docs), "can_edit": p.can("vehicle_files.edit", v.legal_entity_id),
        "kinds": [{"code": k, "label": lbl, "expires": k not in DOC_KINDS_NO_EXPIRY} for k, lbl in VEHICLE_DOC_KINDS.items()],
        "options": {"ownership": [{"code": k, "label": lbl} for k, lbl in OWNERSHIP.items()],
                    "fuel_type": [{"code": k, "label": lbl} for k, lbl in FUEL_TYPES.items()]},
        "documents": [doc_out(d) for d in reversed(docs) if d.archived_at is None],
        "archived_documents": [doc_out(d) for d in reversed(docs) if d.archived_at is not None],
        "summary": summarize(docs),
        "history": history(db, p, v),
    }
    return data


def history(db: Session, p: Principal, v: Vehicle) -> dict:
    """Что известно о машине из других разделов. Показываем только то, на что у человека есть право."""
    out: dict = {}
    if p.can("vehicles.view", v.legal_entity_id):
        rows = db.execute(select(VehicleAssignment, Employee).join(Employee, Employee.id == VehicleAssignment.employee_id)
                          .where(VehicleAssignment.vehicle_id == v.id).order_by(VehicleAssignment.started_on.desc()).limit(10)).all()
        out["drivers"] = [{"name": full_name(e), "role": a.role, "started_on": a.started_on.isoformat(),
                           "ended_on": a.ended_on.isoformat() if a.ended_on else None} for a, e in rows]
        readings = db.scalars(select(VehicleReading).where(VehicleReading.vehicle_id == v.id, VehicleReading.review_state.in_(("ok", "confirmed")))
                              .order_by(VehicleReading.recorded_at.desc(), VehicleReading.id.desc()).limit(5)).all()
        out["readings"] = [{"kind": r.kind, "kind_label": READING_KINDS[r.kind], "value": float(r.value), "recorded_at": r.recorded_at.isoformat()}
                           for r in readings]
        hist = db.scalars(select(VehicleStatusHistory).where(VehicleStatusHistory.vehicle_id == v.id)
                          .order_by(VehicleStatusHistory.changed_at.desc(), VehicleStatusHistory.id.desc()).limit(8)).all()
        out["statuses"] = [{"status": h.status, "status_label": status_label(h.status), "reason": h.reason, "changed_at": h.changed_at.isoformat()}
                           for h in hist]
    if p.can("requests.view", v.legal_entity_id):
        reqs = db.scalars(select(ServiceRequest).where(ServiceRequest.vehicle_id == v.id).order_by(ServiceRequest.id.desc()).limit(5)).all()
        out["requests"] = [{"id": r.id, "number": r.number, "title": r.title, "status": r.status, "status_label": REQUEST_STATUSES[r.status]} for r in reqs]
    return out


class SpecsIn(BaseModel):
    color: str = Field(default="", max_length=40)
    engine_no: str = Field(default="", max_length=40)
    chassis_no: str = Field(default="", max_length=40)
    body_no: str = Field(default="", max_length=40)
    engine_power_hp: Decimal | None = Field(default=None, ge=0, le=5000)
    engine_volume_cc: int | None = Field(default=None, ge=0, le=100000)
    fuel_type: str = ""
    max_weight_kg: int | None = Field(default=None, ge=0, le=1_000_000)
    empty_weight_kg: int | None = Field(default=None, ge=0, le=1_000_000)
    eco_class: str = Field(default="", max_length=10)
    tank_liters: Decimal | None = Field(default=None, gt=0, le=5000)
    fuel_norm: Decimal | None = Field(default=None, gt=0, le=500)
    owner_name: str = Field(default="", max_length=200)
    ownership: str = ""
    registered_on: dt.date | None = None
    commissioned_on: dt.date | None = None
    note: str = Field(default="", max_length=2000)


@router.put("/vehicles/{vid}/specs")
def save_specs(vid: int, body: SpecsIn, p: Principal = Depends(require("vehicle_files.edit")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid, "vehicle_files.edit")
    if body.fuel_type and body.fuel_type not in FUEL_TYPES:
        raise fail(422, "bad_value", "Выберите вид топлива из списка", "fuel_type")
    if body.ownership and body.ownership not in OWNERSHIP:
        raise fail(422, "bad_value", "Выберите форму владения из списка", "ownership")
    specs = db.get(VehicleSpecs, v.id)
    if specs is None:
        specs = VehicleSpecs(vehicle_id=v.id)
        db.add(specs)
        db.flush()
    changed = []
    for field, value in body.model_dump().items():
        if isinstance(value, str):
            value = value.strip()
        if getattr(specs, field) != value:
            setattr(specs, field, value)
            changed.append(field)
    specs.updated_by_id = p.id
    audit.log(db, p, "vehicle.specs", "vehicle", v.id, v.legal_entity_id, plate=v.plate, changed=changed)
    db.commit()
    return specs_out(specs)


@router.post("/vehicles/{vid}/docs", status_code=201)
def add_vehicle_doc(vid: int, kind: str = Form(...), title: str = Form(""), doc_number: str = Form(""),
                    issued_on: str | None = Form(None), valid_until: str | None = Form(None), issued_by: str = Form(""),
                    note: str = Form(""), cost: str | None = Form(None), files: list[UploadFile] = File(default=[]),
                    p: Principal = Depends(require("vehicle_files.edit")), db: Session = Depends(get_db)):
    v = get_vehicle(db, p, vid, "vehicle_files.edit")
    doc = create_doc(db, p, employee=None, vehicle=v, kind=kind, title=title, doc_number=doc_number, issued_on=issued_on,
                     valid_until=valid_until, issued_by=issued_by, note=note, files=files, cost=cost)
    db.commit()
    return doc_out(get_doc(db, p, doc.id))
