# -*- coding: utf-8 -*-
"""Объявления сотрудникам юрлица с перепиской.

Пишет тот, у кого есть право `announcements.create` в этом юрлице: всем водителям, всем сотрудникам с входом или выбранным.
Адресат видит объявление в разделе «Мои объявления» и в колокольчике, может ответить; у каждого адресата своя переписка,
отвечают ему сотрудники прямо на сайте. Объявление не удаляют: его закрывают (пропадает у адресатов) или убирают в архив с причиной.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, like, org_today, page_of, utcnow
from app.db import get_db
from app.deps import Principal, current, entity_filter, require
from app.enums import ANNOUNCEMENT_AUDIENCES, ANNOUNCEMENT_BODY_MAX, ANNOUNCEMENT_REPLY_MAX, ANNOUNCEMENT_TITLE_MAX
from app.models import (Announcement, AnnouncementReply, AnnouncementTarget, Employee, EmploymentAssignment, LegalEntity, Notification,
                        User)
from app.services import notify
from app.services.people import user_names

router = APIRouter(prefix="/announcements", tags=["Объявления"])
news = APIRouter(prefix="/news", tags=["Объявления"])


class AnnouncementIn(BaseModel):
    legal_entity_id: int
    title: str = ""
    body: str = ""
    audience: str = "drivers"
    user_ids: list[int] = Field(default_factory=list)


class TextIn(BaseModel):
    text: str = ""


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def clean_text(raw: str, limit: int, what: str, field: str = "text") -> str:
    value = (raw or "").strip()
    if not value:
        raise fail(422, "required", f"Напишите {what}", field)
    if len(value) > limit:
        raise fail(422, "too_long", f"{what.capitalize()} длиннее {limit} знаков", field)
    return value


# ---------------------------------------------------------------- адресаты

def candidates(db: Session, entity_id: int) -> list[dict]:
    """Кому можно адресовать: работает в юрлице сейчас, у него есть вход и право читать объявления."""
    allowed = notify.users_with(db, "announcements.read_own", entity_id)
    if not allowed:
        return []
    today = org_today()
    q = (select(Employee).join(EmploymentAssignment, EmploymentAssignment.employee_id == Employee.id)
         .join(User, User.id == Employee.user_id)
         .where(EmploymentAssignment.legal_entity_id == entity_id,
                or_(EmploymentAssignment.started_on.is_(None), EmploymentAssignment.started_on <= today),
                or_(EmploymentAssignment.ended_on.is_(None), EmploymentAssignment.ended_on >= today),
                Employee.user_id.in_(allowed), Employee.archived_at.is_(None), User.is_active.is_(True))
         .distinct().order_by(Employee.last_name, Employee.first_name, Employee.id))
    return [{"user_id": e.user_id, "name": " ".join(x for x in (e.last_name, e.first_name) if x), "is_driver": e.is_driver, "position": e.position}
            for e in db.scalars(q)]


@router.get("/audience")
def audience(legal_entity_id: int, p: Principal = Depends(require("announcements.create")), db: Session = Depends(get_db)):
    if not p.can("announcements.create", legal_entity_id):
        raise fail(403, "forbidden", "Нет права писать объявления в этом юрлице")
    return {"people": candidates(db, legal_entity_id), "audiences": [{"value": k, "label": v} for k, v in ANNOUNCEMENT_AUDIENCES.items()]}


# ---------------------------------------------------------------- вывод

def reply_out(r: AnnouncementReply, names: dict[int, str]) -> dict:
    return {"id": r.id, "author": names.get(r.author_id, ""), "from_staff": r.from_staff, "text": r.text, "created_at": r.created_at.isoformat()}


def get_ann(db: Session, p: Principal, aid: int, perm: str = "announcements.view") -> Announcement:
    a = db.get(Announcement, aid)
    if not a or not p.can("announcements.view", a.legal_entity_id):
        raise fail(404, "not_found", "Объявление не найдено")
    if perm != "announcements.view" and not p.can(perm, a.legal_entity_id):
        raise fail(403, "forbidden", "Нет доступа к этому действию")
    return a


def head(db: Session, p: Principal, a: Announcement, names: dict[int, str]) -> dict:
    return {"id": a.id, "title": a.title, "body": a.body, "author": names.get(a.created_by_id, ""), "created_at": a.created_at.isoformat(),
            "closed": a.closed, "archived": a.archived_at is not None, "archive_reason": a.archive_reason, "audience": a.audience,
            "audience_label": ANNOUNCEMENT_AUDIENCES.get(a.audience, ""), "legal_entity_id": a.legal_entity_id,
            "can_edit": p.can("announcements.edit", a.legal_entity_id), "can_void": p.can("announcements.void", a.legal_entity_id)}


# ---------------------------------------------------------------- сотрудники: список, чтение, ответы

@router.get("")
def list_all(q: str = "", archived: bool = False, legal_entity_id: int | None = None, page: int = 1, page_size: int = 50,
             p: Principal = Depends(require("announcements.view")), db: Session = Depends(get_db)):
    scope = entity_filter(p, "announcements.view", legal_entity_id)
    query = select(Announcement).where(scope.cond(Announcement.legal_entity_id),
                                       Announcement.archived_at.is_not(None) if archived else Announcement.archived_at.is_(None))
    if q.strip():
        query = query.where(Announcement.title.ilike(like(q.strip())))
    rows, total = page_of(db, query.order_by(Announcement.closed, Announcement.id.desc()), page, page_size)
    items = [r[0] for r in rows]
    ids = [a.id for a in items]
    seen, wait, answers = {}, {}, {}
    if ids:
        for aid, n, s in db.execute(select(AnnouncementTarget.announcement_id, func.count(), func.count(AnnouncementTarget.read_at))
                                    .where(AnnouncementTarget.announcement_id.in_(ids)).group_by(AnnouncementTarget.announcement_id)):
            seen[aid] = (n, s)
        for aid, n in db.execute(select(AnnouncementReply.announcement_id, func.count()).where(AnnouncementReply.announcement_id.in_(ids),
                                 AnnouncementReply.from_staff.is_(False)).group_by(AnnouncementReply.announcement_id)):
            answers[aid] = n
        last: dict[tuple[int, int], bool] = {}                                  # чья реплика в переписке последняя
        for aid, tuid, from_staff in db.execute(select(AnnouncementReply.announcement_id, AnnouncementReply.thread_user_id,
                                                       AnnouncementReply.from_staff).where(AnnouncementReply.announcement_id.in_(ids))
                                                .order_by(AnnouncementReply.id)):
            last[(aid, tuid)] = from_staff
        for (aid, _tuid), from_staff in last.items():
            if not from_staff:
                wait[aid] = wait.get(aid, 0) + 1
    names = user_names(db, {a.created_by_id for a in items})
    ent = {e.id: (e.short_name or e.name) for e in db.scalars(select(LegalEntity))}
    return {"items": [{**head(db, p, a, names), "body": "", "legal_entity": ent.get(a.legal_entity_id, ""), "total": seen.get(a.id, (0, 0))[0],
                       "seen": seen.get(a.id, (0, 0))[1], "answers": answers.get(a.id, 0), "waiting": wait.get(a.id, 0)} for a in items],
            "total": total, "page": max(page, 1), "page_size": min(max(page_size, 1), 200), "can_create": bool(p.scope("announcements.create"))}


@router.post("", status_code=201)
def create(body: AnnouncementIn, p: Principal = Depends(require("announcements.create")), db: Session = Depends(get_db)):
    entity = db.get(LegalEntity, body.legal_entity_id)
    if not entity or entity.archived_at:
        raise fail(422, "bad_entity", "Выберите юридическое лицо", "legal_entity_id")
    if not p.can("announcements.create", entity.id):
        raise fail(403, "forbidden", "Нет права писать объявления в этом юрлице")
    title = clean_text(body.title, ANNOUNCEMENT_TITLE_MAX, "заголовок", "title")
    text = (body.body or "").strip()
    if len(text) > ANNOUNCEMENT_BODY_MAX:
        raise fail(422, "too_long", f"Текст длиннее {ANNOUNCEMENT_BODY_MAX} знаков", "body")
    if body.audience not in ANNOUNCEMENT_AUDIENCES:
        raise fail(422, "bad_audience", "Выберите, кому адресовано", "audience")
    people = [c for c in candidates(db, entity.id) if c["user_id"] != p.id]
    if body.audience == "drivers":
        ids = [c["user_id"] for c in people if c["is_driver"]]
        if not ids:
            raise fail(422, "no_recipients", "В этом юрлице нет водителей со входом в программу: создайте им пользователей или выберите других адресатов", "audience")
    elif body.audience == "all":
        ids = [c["user_id"] for c in people]
        if not ids:
            raise fail(422, "no_recipients", "В этом юрлице некому адресовать: нет сотрудников со входом в программу", "audience")
    else:
        pool = {c["user_id"] for c in people}
        ids = list(dict.fromkeys(body.user_ids))
        if not ids:
            raise fail(422, "no_recipients", "Отметьте хотя бы одного получателя", "user_ids")
        if any(i not in pool for i in ids):
            raise fail(422, "bad_recipient", "Кого-то из отмеченных нельзя выбрать: он не работает в этом юрлице или не может читать объявления", "user_ids")
    a = Announcement(legal_entity_id=entity.id, title=title, body=text, audience=body.audience, created_by_id=p.id, updated_by_id=p.id)
    db.add(a)
    db.flush()
    db.add_all(AnnouncementTarget(announcement_id=a.id, user_id=i) for i in ids)
    notify.push(db, ids, "announce", f"Объявление: {title}", notify.short(text), f"/news/{a.id}", entity.id, p.id)
    audit.log(db, p, "announcement.create", "announcement", a.id, entity.id, title=title, audience=body.audience, recipients=len(ids))
    db.commit()
    return {"id": a.id, "recipients": len(ids)}


@router.get("/{aid}")
def read(aid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    a = get_ann(db, p, aid)
    targets = list(db.scalars(select(AnnouncementTarget).where(AnnouncementTarget.announcement_id == aid)))
    replies = list(db.scalars(select(AnnouncementReply).where(AnnouncementReply.announcement_id == aid).order_by(AnnouncementReply.id)))
    names = user_names(db, {a.created_by_id, *[t.user_id for t in targets], *[r.author_id for r in replies]})
    threads: dict[int, list] = {}
    for r in replies:
        threads.setdefault(r.thread_user_id, []).append(r)
    people = [{"user_id": t.user_id, "name": names.get(t.user_id, ""), "read_at": t.read_at.isoformat() if t.read_at else None,
               "thread": [reply_out(r, names) for r in threads.get(t.user_id, [])],
               "waiting": bool(threads.get(t.user_id)) and not threads[t.user_id][-1].from_staff} for t in targets]
    people.sort(key=lambda x: (not x["waiting"], x["name"]))
    db.execute(update(Notification).where(Notification.user_id == p.id, Notification.url == f"/announcements/{aid}", Notification.read_at.is_(None))
               .values(read_at=utcnow()))
    db.commit()
    return {**head(db, p, a, names), "people": people}


@router.post("/{aid}/answer/{user_id}", status_code=201)
def answer(aid: int, user_id: int, body: TextIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    a = get_ann(db, p, aid, "announcements.edit")
    if a.archived_at:
        raise fail(409, "archived", "Объявление в архиве: сначала верните его")
    if not db.get(AnnouncementTarget, (aid, user_id)):
        raise fail(404, "not_found", "Этому человеку объявление не адресовано")
    text = clean_text(body.text, ANNOUNCEMENT_REPLY_MAX, "ответ")
    db.add(AnnouncementReply(announcement_id=aid, thread_user_id=user_id, author_id=p.id, from_staff=True, text=text))
    notify.push(db, [user_id], "reply", f"Ответ по объявлению «{notify.short(a.title, 60)}»", notify.short(text), f"/news/{aid}", a.legal_entity_id, p.id)
    audit.log(db, p, "announcement.answer", "announcement", aid, a.legal_entity_id, to_user=user_id)
    db.commit()
    return {"ok": True}


def _set_closed(db: Session, p: Principal, aid: int, closed: bool) -> dict:
    a = get_ann(db, p, aid, "announcements.edit")
    if a.archived_at:
        raise fail(409, "archived", "Объявление в архиве: сначала верните его")
    if a.closed != closed:
        a.closed, a.updated_by_id = closed, p.id
        audit.log(db, p, "announcement.close" if closed else "announcement.reopen", "announcement", a.id, a.legal_entity_id, title=a.title)
        db.commit()
    return {"closed": a.closed}


@router.post("/{aid}/close")
def close(aid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    """Убирает объявление из списка адресатов. Переписка остаётся."""
    return _set_closed(db, p, aid, True)


@router.post("/{aid}/reopen")
def reopen(aid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    return _set_closed(db, p, aid, False)


@router.post("/{aid}/void")
def void(aid: int, body: VoidIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    a = get_ann(db, p, aid, "announcements.void")
    if a.archived_at:
        raise fail(409, "archived", "Объявление уже в архиве")
    a.archived_at, a.archived_by_id, a.archive_reason, a.updated_by_id = utcnow(), p.id, body.reason.strip(), p.id
    audit.log(db, p, "announcement.void", "announcement", a.id, a.legal_entity_id, title=a.title, reason=body.reason.strip())
    db.commit()
    return {"archived": True}


@router.post("/{aid}/restore")
def restore(aid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    a = get_ann(db, p, aid, "announcements.void")
    if not a.archived_at:
        raise fail(409, "not_archived", "Объявление не в архиве")
    a.archived_at, a.archived_by_id, a.archive_reason, a.updated_by_id = None, None, "", p.id
    audit.log(db, p, "announcement.restore", "announcement", a.id, a.legal_entity_id, title=a.title)
    db.commit()
    return {"archived": False}


# ---------------------------------------------------------------- адресат: «Мои объявления»

def mine_or_404(db: Session, p: Principal, aid: int) -> tuple[Announcement, AnnouncementTarget]:
    a = db.get(Announcement, aid)
    t = db.get(AnnouncementTarget, (aid, p.id)) if a else None
    if not a or not t or a.closed or a.archived_at or not p.can("announcements.read_own", a.legal_entity_id):
        raise fail(404, "not_found", "Объявление не найдено")
    return a, t


@news.get("")
def my_list(p: Principal = Depends(require("announcements.read_own")), db: Session = Depends(get_db)):
    scope = p.scope("announcements.read_own")
    rows = db.execute(select(Announcement, AnnouncementTarget.read_at).join(AnnouncementTarget, AnnouncementTarget.announcement_id == Announcement.id)
                      .where(AnnouncementTarget.user_id == p.id, Announcement.closed.is_(False), Announcement.archived_at.is_(None),
                             scope.cond(Announcement.legal_entity_id))
                      .order_by(AnnouncementTarget.read_at.is_(None).desc(), Announcement.id.desc()).limit(100)).all()
    ids = [a.id for a, _ in rows]
    msgs = {aid: n for aid, n in db.execute(select(AnnouncementReply.announcement_id, func.count()).where(AnnouncementReply.announcement_id.in_(ids),
            AnnouncementReply.thread_user_id == p.id).group_by(AnnouncementReply.announcement_id))} if ids else {}
    names = user_names(db, {a.created_by_id for a, _ in rows})
    return {"items": [{"id": a.id, "title": a.title, "author": names.get(a.created_by_id, ""), "created_at": a.created_at.isoformat(),
                       "read": read_at is not None, "messages": msgs.get(a.id, 0), "preview": notify.short(a.body, 160)} for a, read_at in rows]}


@news.get("/{aid}")
def my_read(aid: int, p: Principal = Depends(current), db: Session = Depends(get_db)):
    a, t = mine_or_404(db, p, aid)
    changed = False
    if t.read_at is None:
        t.read_at, changed = utcnow(), True
    n = db.execute(update(Notification).where(Notification.user_id == p.id, Notification.url == f"/news/{aid}", Notification.read_at.is_(None))
                   .values(read_at=utcnow())).rowcount
    if changed or n:
        db.commit()
    replies = list(db.scalars(select(AnnouncementReply).where(AnnouncementReply.announcement_id == aid, AnnouncementReply.thread_user_id == p.id)
                              .order_by(AnnouncementReply.id)))
    names = user_names(db, {a.created_by_id, *[r.author_id for r in replies]})
    return {"id": a.id, "title": a.title, "body": a.body, "author": names.get(a.created_by_id, ""), "created_at": a.created_at.isoformat(),
            "thread": [reply_out(r, names) for r in replies]}


@news.post("/{aid}/reply", status_code=201)
def my_reply(aid: int, body: TextIn, p: Principal = Depends(current), db: Session = Depends(get_db)):
    a, _t = mine_or_404(db, p, aid)
    text = clean_text(body.text, ANNOUNCEMENT_REPLY_MAX, "ответ")
    db.add(AnnouncementReply(announcement_id=aid, thread_user_id=p.id, author_id=p.id, from_staff=False, text=text))
    who = user_names(db, {p.id}).get(p.id, p.login)
    notify.push(db, notify.users_with(db, "announcements.edit", a.legal_entity_id), "reply",
                f"{who} ответил на объявление «{notify.short(a.title, 60)}»", notify.short(text), f"/announcements/{aid}", a.legal_entity_id, p.id)
    audit.log(db, p, "announcement.reply", "announcement", aid, a.legal_entity_id)
    db.commit()
    return {"ok": True}
