# -*- coding: utf-8 -*-
"""Справочники значений: коды хранятся в базе, подписи показываются людям."""

VEHICLE_STATUSES = {
    "in_work": "На линии",
    "in_repair": "В ремонте",
}
# Старые статусы остались только в истории смен статусов (её в базе менять нельзя). В новых записях их нет.
LEGACY_VEHICLE_STATUSES = {
    "awaiting_repair": "Ожидает ремонта", "parked": "На стоянке", "day_off": "Выходной",
    "rented": "В аренде", "unused": "Не используется", "written_off": "Списана",
}
# К какому из двух статусов относится старый: ожидание ремонта считается ремонтом, остальное линией.
LEGACY_STATUS_TO_NEW = {"awaiting_repair": "in_repair", "parked": "in_work", "day_off": "in_work", "rented": "in_work",
                        "unused": "in_work", "written_off": "in_work"}
ALL_STATUS_CODES = {**LEGACY_VEHICLE_STATUSES, **VEHICLE_STATUSES}


def status_label(code: str | None) -> str:
    """Название статуса, в том числе старого из истории."""
    return ALL_STATUS_CODES.get(code or "", code or "")


def status_now(code: str | None) -> str:
    """Старый статус из истории приводит к одному из двух нынешних."""
    return code if code in VEHICLE_STATUSES else LEGACY_STATUS_TO_NEW.get(code or "", "in_work")


# Для этого статуса причина обязательна: потом по истории должно быть понятно, что произошло.
STATUS_NEEDS_REASON = {"in_repair"}

VEHICLE_CATEGORIES = {"truck": "Грузовой", "special": "Спецтехника", "car": "Легковой", "trailer": "Прицеп", "other": "Прочее"}
READING_KINDS = {"km": "Пробег, км", "engine_hours": "Моточасы, ч"}
READING_SOURCES = {"manual": "Ручной ввод", "waybill": "Путевой лист", "gps": "GPS", "import": "Импорт", "fuel": "Заправка"}
# ok и confirmed учитываются в текущих показаниях; pending ждёт проверки; rejected отклонено.
REVIEW_STATES = ("ok", "pending", "confirmed", "rejected")
EFFECTIVE_STATES = ("ok", "confirmed")

REQUEST_STATUSES = {
    "new": "Новая заявка",
    "diagnostics": "Диагностика",
    "approval": "Согласование",
    "in_progress": "В работе",
    "waiting_parts": "Ожидает запчасти или подрядчика",
    "done": "Выполнено",
    "closed": "Закрыто",
    "cancelled": "Отменено",
}
REQUEST_PRIORITIES = {"low": "Низкий", "normal": "Обычный", "high": "Высокий", "urgent": "Срочный"}
REQUEST_OPEN = ("new", "diagnostics", "approval", "in_progress", "waiting_parts", "done")
# Откуда куда можно переводить обращение. Всё остальное запрещено.
REQUEST_FLOW = {
    "new": ("diagnostics", "cancelled"),
    "diagnostics": ("approval", "in_progress", "cancelled"),
    "approval": ("in_progress", "diagnostics", "cancelled"),
    "in_progress": ("waiting_parts", "done"),
    "waiting_parts": ("in_progress", "done"),
    "done": ("closed", "in_progress"),
    "closed": (),
    "cancelled": (),
}
# Эти переходы — решение о согласовании: нужно отдельное право requests.approve.
REQUEST_APPROVAL_STEPS = {("approval", "in_progress"), ("approval", "cancelled")}

WAYBILL_STATUSES = {"draft": "Черновик", "open": "Открыт", "closed": "Закрыт", "cancelled": "Аннулирован"}
WAYBILL_SHIFTS = {"day": "Дневная", "night": "Ночная", "full": "Сутки"}

BACKUP_STATUSES = ("running", "ok", "failed")


def check_in(column: str, values) -> str:
    """Текст CHECK-ограничения «значение из списка»."""
    return "%s IN (%s)" % (column, ", ".join("'%s'" % v for v in values))


# ---------------------------------------------------------------- личные дела (документы и сканы)
# Виды документов в личном деле сотрудника. Паспорт чувствительный: для него нужно отдельное право.
EMPLOYEE_DOC_KINDS = {
    "photo": "Фото сотрудника",
    "passport": "Паспорт",
    "driver_license": "Водительское удостоверение",
    "sts": "СТС",
    "pts": "ПТС",
    "other": "Другой документ",
}
EMPLOYEE_SENSITIVE_KINDS = {"passport"}
# Виды документов в личном деле техники.
VEHICLE_DOC_KINDS = {
    "photo": "Фото техники",
    "sts": "СТС",
    "pts": "ПТС",
    "osago": "ОСАГО",
    "kasko": "КАСКО",
    "diagnostic": "Диагностическая карта (техосмотр)",
    "contract": "Договор (купля, лизинг, аренда)",
    "permit": "Разрешение или пропуск",
    "other": "Другой документ",
}
ALL_DOC_KINDS = {**EMPLOYEE_DOC_KINDS, **VEHICLE_DOC_KINDS}
# Виды без срока действия: для них даты окончания не спрашиваем.
DOC_KINDS_NO_EXPIRY = {"photo"}
# За сколько дней до конца срока документ считается «скоро истекает».
DOC_EXPIRING_DAYS = 30

OWNERSHIP = {"own": "Собственность", "leasing": "Лизинг", "rent": "Аренда"}
FUEL_TYPES = {"diesel": "Дизель", "petrol": "Бензин", "gas": "Газ", "electric": "Электро", "other": "Другое"}


# ---------------------------------------------------------------- деньги и справочники (перенос из старой программы)
COUNTERPARTY_KINDS = {"customer": "Заказчик", "supplier": "Поставщик", "both": "Заказчик и поставщик"}
TARIFF_UNITS = {"km": "за километр", "hour": "за час", "ton": "за тонну", "ton_tier": "за тонну, с порогом за день (мусоровозы)", "trip": "за рейс", "day": "за сутки"}
TARIFF_KINDS = {"customer": "Для заказчика (цена работы техники)", "work": "Оплата работы водителю", "repair": "Оплата ремонта водителю"}
PAY_TARIFF_KINDS = ("work", "repair")                     # из них считается заработок
PAY_TARIFF_UNITS = ("hour", "day")                        # заработок считают за час или за день
WORK_TARIFF_UNITS = ("hour", "day", "ton", "ton_tier")   # оплату работы водителю считают ещё и за тонну (мусоровозы): по талонам весов
TON_UNITS = ("ton", "ton_tier")                           # оплата по тоннам из талонов; ton_tier: до порога за день одна ставка, от порога другая
TIER_TONS_DEFAULT = 12                                    # порог по умолчанию, т за день
WORKLOG_KINDS = {"work": "Работа", "repair": "Ремонт"}
WORKLOG_SOURCES = {"driver": "Внёс водитель", "staff": "Внесли в офисе", "mechanic": "Внёс механик"}      # «водитель»: старые записи, сам водитель часы больше не вносит
WORKLOG_BACKDATE_DAYS = 7              # механик вносит часы не раньше чем за столько дней; директор и диспетчер — за любой день
WORKLOG_UNDO_HOURS = 48                # механик сам убирает внесённую им запись только первые столько часов
WORKLOG_MAX_HOURS = 24                 # в сутках не больше
ATTENDANCE_SOURCES = {"driver": "Отметил сам", "staff": "Отметили в офисе"}
ATTENDANCE_AHEAD_DAYS = 14             # водитель отвечает «выйду / не выйду» не дальше чем на столько дней вперёд
ATTENDANCE_BACK_DAYS = 7               # офис может поправить ответ не раньше чем за столько дней
ATTENDANCE_AHEAD_OFFICE_DAYS = 60      # и не дальше чем на столько дней вперёд
ATTENDANCE_SHOW_DAYS = 7               # сколько дней водитель видит на своей странице
FINE_PAYERS = {"company": "Платит компания", "driver": "Удерживается с водителя"}
RENTAL_DIRECTIONS = {"out": "Сдаём в аренду", "in": "Берём в аренду"}
PAYMENT_KINDS = {"salary": "Зарплата", "advance": "Аванс", "bonus": "Премия", "other": "Прочее", "fine": "Удержание (штраф)"}
CASH_ACCOUNT_KINDS = {"cash": "Касса (наличные)", "bank": "Расчётный счёт", "deposit": "Депозит"}
CASH_OP_KINDS = {"in": "Приход", "out": "Расход", "transfer": "Перевод между счетами"}

ORDER_STATUSES = {"new": "Новый", "confirmed": "Подтверждён", "in_progress": "В пути", "done": "Выполнен", "cancelled": "Отменён"}
VAT_RATES = {"0": "Без НДС", "5": "5 %", "7": "7 %", "10": "10 %", "20": "20 %", "22": "22 %"}
DOC_TYPES = {"invoice": "Счёт", "act": "Акт", "tn": "Транспортная накладная", "upd": "УПД"}
DOC_FULL_NAMES = {"invoice": "Счёт на оплату", "act": "Акт оказанных услуг", "tn": "Транспортная накладная",
                  "upd": "Универсальный передаточный документ"}


# ---------------------------------------------------------------- технические задания (перенос из старой программы)
JOB_STATES = {"planned": "Запланирован", "on_site": "На объекте", "done": "Выполнен", "cancelled": "Отменён"}
JOB_STAGES = {"before": "До работы", "after": "После работы"}
JOB_MAX_PHOTOS_PER_STAGE = 10
# Дата задания не дальше этого числа дней от сегодняшней (защита от опечаток в году)
JOB_DATE_WINDOW_DAYS = 366


# ---------------------------------------------------------------- записи водителей: рейсы и заправки
ENTRY_MAX_PHOTOS = 3             # фото накладной или чека к одной записи
ENTRY_LATE_DAYS = 31             # водитель вносит запись задним числом не дальше, чем на месяц
ENTRY_DELETE_HOURS = 24          # свою запись водитель может убрать в течение суток
FUEL_OVERRUN_PCT = 30            # расход выше нормы на столько процентов считается подозрительным
FUEL_MAX_LITERS = 2000
ENTRY_KINDS = {"trip": "Рейс", "fuel": "Заправка"}


# ---------------------------------------------------------------- детали, «что купить», счета на ремонт (перенос из старой программы)
PART_UNITS = ["шт", "комплект", "л", "кг", "м", "пара"]
BILL_UNITS = PART_UNITS + ["ч", "усл."]
REQUEST_PARTS_MAX = 60               # деталей в одном обращении
REQUEST_PARTS_OPEN = ("new", "diagnostics", "approval", "in_progress", "waiting_parts")   # детали такого обращения ещё нужны
BILL_KINDS = {"issued": "Выписан в программе", "uploaded": "Получен от поставщика"}
BILL_STATUSES = {"unpaid": "Не оплачен", "paid": "Оплачен", "cancelled": "Отменён"}
BILL_MAX_ITEMS = 100
BILL_MAX_FILES = 10
REPAIR_PHOTO_MAX = 20                # фото к одному результату ремонта
REPAIR_WHAT_MAX = 2000               # сколько знаков в описании «что отремонтировали»
BILL_YEAR_MIN, BILL_YEAR_MAX = 2000, 2100


# ---------------------------------------------------------------- табель и СИЗ (перенос из старой программы)
# Коды старого кадрового табеля. Сайт их больше не показывает: таблица `timesheet_marks` осталась, чтобы не потерять перенесённые данные.
TIMESHEET_CODES = {
    "Я": "Явка", "Н": "Работа в ночное время", "РВ": "Работа в выходной или праздничный день",
    "С": "Сверхурочная работа (только часы)", "К": "Командировка", "ОТ": "Ежегодный отпуск",
    "ОД": "Дополнительный отпуск", "ДО": "Отпуск без сохранения зарплаты", "Р": "Отпуск по беременности и родам",
    "ОЖ": "Отпуск по уходу за ребёнком", "Б": "Больничный", "Т": "Нетрудоспособность без пособия",
    "ПР": "Прогул", "НН": "Неявка по невыясненным причинам", "РП": "Простой по вине работодателя", "В": "Выходной",
}
PPE_WEAR_MAX_MONTHS = 120
PPE_SOON_DAYS = 30               # до замены осталось не больше: «скоро»


# ---------------------------------------------------------------- события сотрудников, календарь, напоминания (этап 3б)
EMPLOYEE_EVENT_KINDS = {
    "medical": "Медосмотр", "license": "Водительское удостоверение", "briefing": "Инструктаж", "violation": "Нарушение",
    "accident": "ДТП", "bonus": "Премия", "fine": "Штраф", "other": "Другое",
}
EMPLOYEE_EVENT_MONEY_KINDS = ("bonus", "fine")          # у этих видов есть сумма
CALENDAR_COLORS = {"teal": "Бирюзовый", "blue": "Синий", "amber": "Жёлтый", "red": "Красный", "green": "Зелёный", "violet": "Фиолетовый"}
CALENDAR_VIEWS = {"month": "Месяц", "week": "Неделя", "day": "День", "year": "Год"}
CALENDAR_REMIND_MAX = 60 * 24 * 30       # напомнить можно не раньше чем за 30 суток (в минутах)
REMINDER_LEAD_DAYS = 30                  # события сотрудников, аренда и свои события: «скоро» за столько дней
BILL_UNPAID_DAYS = 7                     # счёт на ремонт, не оплаченный столько дней, попадает в напоминания


# ---------------------------------------------------------------- уведомления и объявления (этап 3в)
NOTIFICATION_KINDS = {
    "request": "Обращение", "reply": "Ответ", "announce": "Объявление", "job": "Задание", "parts": "Куплено", "reminder": "Напоминание",
    "stock": "Склад", "info": "Сообщение",
}
NOTIFICATION_KEEP_DAYS = 120             # прочитанные уведомления хранятся столько дней
NOTIFICATION_KEYED_KEEP_DAYS = 400       # уведомления-напоминания хранятся дольше: по ним программа знает, что уже сообщала
NOTIFICATION_DIGEST_FROM = 6             # если разом набралось не меньше столько напоминаний, придёт одно общее
ANNOUNCEMENT_AUDIENCES = {"drivers": "Всем водителям", "all": "Всем сотрудникам с входом", "pick": "Выбранным"}
ANNOUNCEMENT_TITLE_MAX = 120
ANNOUNCEMENT_BODY_MAX = 3000
ANNOUNCEMENT_REPLY_MAX = 1000


# ---------------------------------------------------------------- склад (этап 4-1)
STOCK_MOVE_KINDS = {"receipt": "Поступление", "issue": "Расход", "writeoff": "Списание", "transfer": "Перемещение"}
STOCK_MAX_QTY = 1_000_000                # одним движением не больше
STOCK_UNITS = PART_UNITS

# ---------------------------------------------------------------- вкладки техники: шины, ремонты, финансы, ТО, контроль
TIRE_SEASONS = {"summer": "Лето", "winter": "Зима", "all": "Всесезонные"}
ROUTE_PLAN_KINDS = {"weekly": "По дням недели", "date": "На одну дату"}      # расписание маршрутов мусоровозов
WEEKDAY_FIELDS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")            # поля дней недели в расписании (понедельник = 0)
VEHICLE_REPAIR_KINDS = {"repair": "Работа", "part": "Деталь", "material": "Материал"}
VEHICLE_FINANCE_KINDS = {"expense": "Расход", "income": "Доход"}
MAINTENANCE_KINDS = {"so": "СО", "to1": "ТО-1", "to2": "ТО-2", "other": "Другое"}
# «Скоро» для контроля сроков по технике: за сколько дней, километров и моточасов до порога (как в старой программе)
CONTROL_LEAD_DAYS = 14
CONTROL_LEAD_KM = 500
CONTROL_LEAD_HOURS = 10

# Уведомления вне сайта (Telegram и почта): очередь отправки и её состояния
OUTBOX_CHANNELS = ("telegram", "email")
OUTBOX_STATES = ("new", "sent", "failed", "skipped")
OUTBOX_MAX_ATTEMPTS = 5
OUTBOX_RETRY_MINUTES = (1, 5, 15, 60)           # через сколько повторять после неудачи (дальше последнее значение)
OUTBOX_KEEP_DAYS = 14                           # отправленное хранится столько дней
OUTBOX_FAILED_KEEP_DAYS = 60


# ---------------------------------------------------------------- счётчик ходок (личный счётчик водителя самосвала)
HAUL_DAYS_SHOWN = 30                     # сколько дней водитель видит в списке дней
HAUL_DAY_LIMIT = 300                     # больше столько ходок за день не записывается: это уже ошибка, а не работа
HAUL_TZ_MIN = (-720, 840)                # допустимый сдвиг часового пояса телефона от UTC, минут
