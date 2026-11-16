# -*- coding: utf-8 -*-
"""Журнал действий: только чтение. Записи добавляются из остальных разделов и не меняются (триггер в базе)."""
import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.common import fail, like, page_of
from app.db import get_db
from app.deps import Principal, require
from app.models import AuditLog

router = APIRouter(prefix="/audit", tags=["Журнал действий"])

ACTIONS = {
    "stock.receipt": "Склад: поступление", "stock.issue": "Склад: расход", "stock.writeoff": "Склад: списание", "stock.transfer": "Склад: перемещение",
    "stock.issue_request": "Деталь взята со склада на обращение", "stock.receive_bill": "Товар принят на склад по счёту",
    "stock.export": "Выгрузка данных склада", "stock_moves.update": "Изменено описание движения склада",
    "stock_moves.void": "Движение склада убрано в архив", "stock_moves.restore": "Движение склада возвращено из архива",
    "announcement.create": "Написано объявление", "announcement.answer": "Ответ на объявление", "announcement.reply": "Ответ адресата на объявление",
    "announcement.close": "Объявление закрыто", "announcement.reopen": "Объявление показано снова", "announcement.void": "Объявление в архиве",
    "announcement.restore": "Объявление возвращено из архива",
    "login.ok": "Вход", "login.failed": "Неудачный вход", "login.blocked": "Вход заблокирован после неудач",
    "logout": "Выход", "logout.others": "Выход на других устройствах", "password.change": "Смена пароля",
    "user.create": "Создан пользователь", "user.update": "Изменён пользователь", "user.password_reset": "Сброшен пароль",
    "user.logout_all": "Завершены все сеансы пользователя", "role.create": "Создана роль", "role.permissions": "Изменены права роли",
    "legal_entity.create": "Создано юрлицо", "legal_entity.update": "Изменено юрлицо", "legal_entity.archive": "Юрлицо в архиве",
    "legal_entity.restore": "Юрлицо возвращено", "department.create": "Создан отдел",
    "employee.create": "Добавлен сотрудник", "employee.update": "Изменён сотрудник", "employee.archive": "Сотрудник в архиве",
    "employee.restore": "Сотрудник возвращён", "employee.assignment_add": "Сотрудник принят в юрлицо",
    "employee.assignment_end": "Работа в юрлице закончена",
    "vehicle.create": "Добавлена техника", "vehicle.update": "Изменена техника", "vehicle.archive": "Техника в архиве",
    "vehicle.restore": "Техника возвращена", "vehicle.status": "Сменён статус техники", "vehicle.assign": "Водитель закреплён",
    "vehicle.unassign": "Закрепление снято", "vehicle.export": "Выгрузка списка техники",
    "reading.create": "Введено показание", "reading.correct": "Показание исправлено", "reading.review": "Показание проверено", "readings.import": "Загрузка показаний из файла",
    "request.create": "Создано обращение", "request.update": "Изменено обращение", "request.transition": "Смена статуса обращения",
    "request.comment": "Комментарий к обращению",
    "waybill.create": "Создан путевой лист", "waybill.update": "Изменён путевой лист", "waybill.open": "Путевой лист открыт",
    "waybill.close": "Путевой лист закрыт", "waybill.cancel": "Путевой лист аннулирован", "waybill.correct": "Закрытый лист исправлен",
    "dossier.doc.create": "Добавлен документ в личное дело", "dossier.doc.update": "Изменён документ в личном деле",
    "dossier.doc.archive": "Документ отправлен в архив", "dossier.doc.restore": "Документ возвращён из архива",
    "dossier.file.add": "Добавлен скан к документу", "dossier.file.remove": "Скан убран из документа",
    "dossier.file.view": "Просмотр скана документа", "vehicle.specs": "Изменены данные в личном деле техники",
    "backup.start": "Запущена копия", "backup.done": "Копия готова", "backup.failed": "Копия не удалась",
    "backup.verify_request": "Запрошена проверка копии", "backup.verify": "Проверка копии завершена",
    "channels.update": "Изменены настройки уведомлений вне сайта", "channels.telegram_code": "Запрошен код для подключения Telegram",
    "channels.telegram_unlink": "Telegram отключён", "channels.test": "Проба уведомлений вне сайта",
    "backup.offsite": "Копия отправлена вне сервера",
    "backup.download": "Скачана копия", "backup.rotate": "Удалены старые копии",
    "jobs.arrive": "Задание: водитель приехал на объект", "jobs.finish": "Задание выполнено", "jobs.state": "Состояние задания изменено вручную",
    "jobs.copy": "Задание повторено на следующий день", "jobs.photo.add": "Добавлены фото к заданию", "jobs.photo.remove": "Фото убрано из задания",
    "entries.photo.add": "Добавлены фото к рейсу или заправке", "entries.photo.remove": "Фото убрано из рейса или заправки",
    "entries.driver_void": "Водитель убрал свою запись (рейс или заправку)",
    "roster.export": "Выгрузка сводки «Водители и техника»",
    "request_part.add": "Добавлены детали в обращение", "request_part.remove": "Деталь убрана из обращения",
    "purchase.bought": "Детали отмечены купленными", "purchase.restore": "Покупка деталей отменена", "purchases.export": "Выгрузка списка «Что купить»",
    "parts.create": "Деталь добавлена в каталог", "parts.update": "Деталь в каталоге изменена", "parts.void": "Деталь убрана из каталога",
    "parts.restore": "Деталь возвращена в каталог", "parts.export": "Выгрузка каталога деталей",
    "bill.create": "Создан счёт на ремонт", "bill.update": "Изменён счёт на ремонт", "bill.status": "Изменён статус оплаты счёта",
    "bill.void": "Счёт на ремонт в архиве", "bill.restore": "Счёт на ремонт возвращён", "bill.file.add": "Добавлен файл к счёту",
    "bill.file.remove": "Файл убран из счёта", "bill.export": "Выгрузка счетов на ремонт",
    "ppe.replace": "СИЗ заменено: старая вещь возвращена, выдана новая",
    "maintenance.done": "ТО отмечено выполненным",
    "timesheet.save": "Табель: изменены отметки", "timesheet.fill": "Табель: месяц заполнен автоматически",
    "timesheet.export": "Выгрузка табеля",
    "reminders.export": "Выгрузка напоминаний", "reports.export": "Выгрузка отчёта",
}


def out(r: AuditLog) -> dict:
    return {"id": r.id, "at": r.at.isoformat(), "user_id": r.user_id, "user_login": r.user_login, "action": r.action,
            "action_label": ACTIONS.get(r.action, r.action), "entity_type": r.entity_type, "entity_id": r.entity_id,
            "legal_entity_id": r.legal_entity_id, "ip": r.ip, "details": r.details}


@router.get("/actions")
def actions(p: Principal = Depends(require("audit.view"))):
    return {"items": [{"code": k, "label": v} for k, v in ACTIONS.items()]}


@router.get("")
def list_audit(q: str = "", action: str = "", user: str = "", entity_type: str = "", entity_id: int | None = None,
               date_from: dt.date | None = None, date_to: dt.date | None = None, legal_entity_id: int | None = None,
               page: int = 1, page_size: int = 50, p: Principal = Depends(require("audit.view")), db: Session = Depends(get_db)):
    scope = p.scope("audit.view")
    # записи без юрлица (вход, пользователи, копии) видны только тому, у кого право на журнал действует везде
    conds = []
    if not scope.everything:
        conds.append(scope.cond(AuditLog.legal_entity_id))
    if legal_entity_id is not None:
        if not scope.allows(legal_entity_id):
            raise fail(403, "forbidden", "Нет доступа к журналу этого юрлица")
        conds.append(AuditLog.legal_entity_id == legal_entity_id)
    if action:
        conds.append(AuditLog.action == action)
    if user.strip():
        conds.append(AuditLog.user_login.ilike(like(user.strip())))
    if entity_type:
        conds.append(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        conds.append(AuditLog.entity_id == entity_id)
    if date_from:
        conds.append(AuditLog.at >= dt.datetime.combine(date_from, dt.time.min, tzinfo=dt.timezone.utc))
    if date_to:
        conds.append(AuditLog.at < dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc))
    if q.strip():
        t = like(q.strip())
        conds.append(or_(AuditLog.user_login.ilike(t), AuditLog.action.ilike(t), AuditLog.entity_type.ilike(t)))
    query = select(AuditLog)
    if conds:
        query = query.where(and_(*conds))
    rows, total = page_of(db, query.order_by(AuditLog.at.desc(), AuditLog.id.desc()), page, page_size)
    return {"items": [out(r[0]) for r in rows], "total": total, "page": page, "page_size": page_size}
