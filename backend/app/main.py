# -*- coding: utf-8 -*-
import asyncio
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import engine, session_factory

log = logging.getLogger("uvicorn.error")

# Нулевой символ (код 0) база не принимает в тексте: без проверки на входе запрос заканчивался бы ошибкой сервера.
_NUL_IN_JSON = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000", re.I)
_BAD_VALUE = {"code": "bad_value", "field": None,
              "message": "Текст содержит недопустимые служебные символы. Уберите их и повторите"}


class RejectNul:
    """Отклоняет запросы, где в адресе или в JSON-теле есть нулевой символ."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        bad = b"%00" in scope.get("query_string", b"") or b"\x00" in scope.get("query_string", b"")
        if not bad and scope["method"] in ("POST", "PUT", "PATCH"):
            ctype = dict(scope["headers"]).get(b"content-type", b"")
            if ctype.startswith(b"application/json"):
                chunks, more = [], True
                while more:
                    msg = await receive()
                    if msg["type"] == "http.disconnect":
                        return
                    chunks.append(msg.get("body", b""))
                    more = msg.get("more_body", False)
                body = b"".join(chunks)
                bad = bool(_NUL_IN_JSON.search(body)) or b"\x00" in body
                done = False

                async def replay():
                    nonlocal done
                    if done:
                        return {"type": "http.disconnect"}
                    done = True
                    return {"type": "http.request", "body": body, "more_body": False}
                receive = replay
        if bad:
            return await JSONResponse(_BAD_VALUE, status_code=422)(scope, receive, send)
        await self.app(scope, receive, send)

VALIDATION_TEXT = {
    "missing": "Заполните поле",
    "string_too_short": "Слишком короткое значение",
    "string_too_long": "Слишком длинное значение",
    "int_parsing": "Нужно целое число", "int_from_float": "Нужно целое число",
    "float_parsing": "Нужно число", "decimal_parsing": "Нужно число", "decimal_type": "Нужно число",
    "date_parsing": "Нужна дата", "date_from_datetime_parsing": "Нужна дата", "date_type": "Нужна дата",
    "datetime_parsing": "Нужны дата и время", "datetime_type": "Нужны дата и время",
    "literal_error": "Выберите значение из списка", "enum": "Выберите значение из списка",
    "greater_than": "Значение слишком маленькое", "greater_than_equal": "Значение слишком маленькое",
    "less_than": "Значение слишком большое", "less_than_equal": "Значение слишком большое",
    "bool_parsing": "Нужно да или нет", "string_type": "Нужен текст",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.bootstrap import bootstrap
    from app.services import backup
    with session_factory()() as db:
        bootstrap(db)
    from app.services import channels, notify
    s = get_settings()
    task = asyncio.create_task(backup.scheduler()) if s.backup_schedule else None
    sweeper = asyncio.create_task(notify.scheduler()) if s.notify_schedule else None
    sender = asyncio.create_task(channels.worker()) if s.notify_schedule and (channels.telegram_enabled() or channels.email_enabled()) else None
    reader = asyncio.create_task(channels.poller()) if s.notify_schedule and channels.telegram_enabled() else None
    yield
    for t in (task, sweeper, sender, reader):
        if t:
            t.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Контур", version="0.1.0", lifespan=lifespan,
                  description="API внутренней ERP автопарка (очередь 1: вход и роли, юрлица, сотрудники, техника, показания, "
                              "статусы, обращения и ремонты, путевые листы, личные дела водителей и техники, журнал действий, резервные копии).",
                  docs_url="/api/docs" if s.docs_enabled else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if s.docs_enabled else None)

    app.add_middleware(RejectNul)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(exc.detail, status_code=exc.status_code)
        messages = {404: "Не найдено", 405: "Метод не поддерживается", 401: "Войдите в систему", 403: "Нет доступа"}
        return JSONResponse({"code": "http_%d" % exc.status_code, "message": messages.get(exc.status_code, str(exc.detail)),
                             "field": None}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = {}
        for err in exc.errors():
            loc = [str(x) for x in err["loc"] if x not in ("body", "query", "path")]
            name = ".".join(loc) or "_"
            fields.setdefault(name, VALIDATION_TEXT.get(err["type"], "Некорректное значение"))
        return JSONResponse({"code": "validation", "message": "Проверьте отмеченные поля", "field": None, "fields": fields},
                            status_code=422)

    @app.exception_handler(DataError)
    async def bad_value(request: Request, exc: DataError):
        # база отказалась принять значение (нулевой байт в тексте, слишком большое число и т. п.) — это ошибка ввода, а не сбой
        log.warning("Недопустимое значение в запросе %s %s: %s", request.method, request.url.path, str(exc.orig)[:200])
        return JSONResponse({"code": "bad_value", "field": None,
                             "message": "Недопустимое значение: уберите служебные символы и проверьте числа и даты"}, status_code=422)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        log.exception("Необработанная ошибка в запросе %s %s", request.method, request.url.path)
        return JSONResponse({"code": "server_error", "field": None,
                             "message": "Что-то пошло не так на сервере. Повторите действие; если не получится — сообщите администратору"},
                            status_code=500)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health", tags=["Служебное"])
    def health():
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    from app.routers import (audit_log, auth, backups, bills, dashboard, dossier, driver, employees, entries, jobs, legal_entities, purchases, readings,
                             request_parts, requests_, roles, roster, timesheet, trade, users, vehicles, waybills, ppe, calendar_view, reminders, notifications, announcements, stock, reports, readings_import, map_, control, channels, payroll, attendance, tonnage, hauls, finance, routes, repair_photos, loaders, menu, garbage_pay)
    from app import resources  # noqa: F401  (описания разделов регистрируются при импорте)
    from app.registry import RESOURCES, build_router, options_router
    for module in (auth, legal_entities, users, roles, employees, vehicles, readings, requests_, waybills, dashboard,
                   dossier, audit_log, backups, trade, jobs, entries, roster, driver, request_parts, purchases, bills, ppe, timesheet, calendar_view, reminders, notifications, announcements, stock, reports, readings_import, map_, control, channels, payroll, attendance, tonnage, hauls, finance, routes, repair_photos, loaders, menu, garbage_pay):
        app.include_router(module.router, prefix="/api")
    app.include_router(announcements.news, prefix="/api")
    for res in RESOURCES.values():
        app.include_router(build_router(res), prefix="/api")
    app.include_router(options_router, prefix="/api")
    return app


app = create_app()
