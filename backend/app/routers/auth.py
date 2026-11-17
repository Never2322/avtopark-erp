# -*- coding: utf-8 -*-
import datetime as dt

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import audit
from app.common import fail, utcnow
from app.config import get_settings
from app.db import get_db
from app.deps import COOKIE, Principal, client_ip, current_even_if_password_temporary
from app.models import Employee, LegalEntity, LoginAttempt, Role, User, UserRole, UserSession
from app.services import hauls as hauls_svc
from app.services import weigh as weigh_svc
from app.security import DUMMY_HASH, hash_password, new_token, password_problem, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["Вход"])


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


def me_payload(db: Session, p: Principal) -> dict:
    s = get_settings()
    entities = db.scalars(select(LegalEntity).where(LegalEntity.archived_at.is_(None)).order_by(LegalEntity.name)).all()
    visible = p.visible_entities()
    employee = db.get(Employee, p.employee_id) if p.employee_id else None
    roles = db.execute(select(Role.code, Role.name, UserRole.legal_entity_id).join(UserRole, UserRole.role_id == Role.id)
                       .where(UserRole.user_id == p.id)).all()
    return {
        "id": p.id, "login": p.login, "display_name": p.user.display_name,
        "must_change_password": p.user.must_change_password,
        "employee_id": p.employee_id,
        "employee_name": " ".join(x for x in (employee.last_name, employee.first_name) if x) if employee else None,
        "roles": [{"code": c, "name": n, "legal_entity_id": e} for c, n, e in roles],
        "permissions": {code: {"all": sc.everything, "entities": sorted(sc.ids)} for code, sc in p.grants.items()},
        "legal_entities": [{"id": e.id, "name": e.name, "short_name": e.short_name or e.name} for e in entities if visible.allows(e.id)],
        "csrf_token": p.session.csrf_token,
        "org_timezone": s.org_timezone,
        # что включено именно этому человеку, а не его роли: вкладка «Тоннаж» нужна, когда за ним закреплена машина с галочкой «Вести тоннаж»,
        # а «Счётчик ходок» только водителю, за которым закреплён самосвал
        "features": {"tonnage": weigh_svc.has_tonnage(db, p), "hauls": hauls_svc.has_hauls(db, p)},
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s, now, ip = get_settings(), utcnow(), client_ip(request)
    key = body.login.strip().lower()[:64]
    attempt = db.get(LoginAttempt, key)
    if attempt and attempt.locked_until and attempt.locked_until > now:
        minutes = max(1, int((attempt.locked_until - now).total_seconds() // 60) + 1)
        audit.log(db, None, "login.blocked", "user", login=key, ip=ip)
        db.commit()
        raise fail(429, "locked", f"Слишком много неудачных попыток. Подождите {minutes} мин. и попробуйте снова")
    user = db.scalar(select(User).where(func.lower(User.login) == key))
    ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password) and bool(user) and user.is_active
    if not ok:
        if not attempt:
            attempt = LoginAttempt(key=key, failures=0)
            db.add(attempt)
        attempt.failures = (attempt.failures or 0) + 1
        attempt.updated_at = now
        locked = attempt.failures >= s.lock_after_failures
        if locked:
            attempt.locked_until, attempt.failures = now + dt.timedelta(minutes=s.lock_minutes), 0
        audit.log(db, None, "login.failed", "user", user.id if user else None, login=key, ip=ip, locked=locked)
        db.commit()
        # Один и тот же ответ для неверного пароля, неизвестного логина и отключённого пользователя.
        raise fail(401, "bad_credentials", "Неверный логин или пароль")
    if attempt:
        db.delete(attempt)
    ttl = dt.timedelta(days=s.session_remember_days) if body.remember else dt.timedelta(hours=s.session_hours)
    token, csrf = new_token(), new_token()
    sess = UserSession(token_hash=token_hash(token), user_id=user.id, csrf_token=csrf, expires_at=now + ttl,
                       ip=ip, user_agent=request.headers.get("user-agent", "")[:200])
    db.add(sess)
    user.last_login_at, user.last_login_ip = now, ip
    db.flush()
    from app.deps import load_grants
    p = Principal(user, load_grants(db, user.id), sess, ip,
                  db.scalar(select(Employee.id).where(Employee.user_id == user.id, Employee.archived_at.is_(None))))
    audit.log(db, p, "login.ok", "user", user.id, remember=body.remember)
    payload = me_payload(db, p)
    db.commit()
    response.set_cookie(COOKIE, token, max_age=int(ttl.total_seconds()), httponly=True, samesite="lax",
                        secure=s.cookie_secure, path="/")
    return payload


@router.post("/logout")
def logout(response: Response, p: Principal = Depends(current_even_if_password_temporary), db: Session = Depends(get_db)):
    p.session.revoked_at = utcnow()
    audit.log(db, p, "logout", "user", p.id)
    db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/logout-others")
def logout_others(p: Principal = Depends(current_even_if_password_temporary), db: Session = Depends(get_db)):
    n = db.execute(update(UserSession).where(UserSession.user_id == p.id, UserSession.id != p.session.id,
                                             UserSession.revoked_at.is_(None)).values(revoked_at=utcnow())).rowcount
    audit.log(db, p, "logout.others", "user", p.id, sessions=n)
    db.commit()
    return {"ended": n}


@router.get("/me")
def me(p: Principal = Depends(current_even_if_password_temporary), db: Session = Depends(get_db)):
    return me_payload(db, p)


@router.post("/password")
def change_password(body: PasswordIn, p: Principal = Depends(current_even_if_password_temporary),
                    db: Session = Depends(get_db)):
    if not verify_password(p.user.password_hash, body.current_password):
        raise fail(422, "bad_password", "Текущий пароль указан неверно", "current_password")
    problem = password_problem(body.new_password, p.login)
    if problem:
        raise fail(422, "weak_password", problem, "new_password")
    if body.new_password == body.current_password:
        raise fail(422, "same_password", "Новый пароль должен отличаться от текущего", "new_password")
    p.user.password_hash = hash_password(body.new_password)
    p.user.must_change_password = False
    p.user.password_changed_at = utcnow()
    # После смены пароля все остальные входы этого человека закрываются.
    db.execute(update(UserSession).where(UserSession.user_id == p.id, UserSession.id != p.session.id,
                                         UserSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    audit.log(db, p, "password.change", "user", p.id)
    db.commit()
    return {"ok": True}
