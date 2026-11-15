# -*- coding: utf-8 -*-
"""Справочники значений: коды хранятся в базе, подписи показываются людям."""

VEHICLE_STATUSES = {
    "in_work": "В работе",
    "in_repair": "На ремонте",
    "awaiting_repair": "Ожидает ремонта",
    "parked": "На стоянке",
    "day_off": "Выходной",
    "rented": "В аренде",
    "unused": "Не используется",
    "written_off": "Списана",
}
# Для этих статусов причина обязательна: потом по истории должно быть понятно, что произошло.
STATUS_NEEDS_REASON = {"in_repair", "awaiting_repair", "written_off"}
STATUS_TERMINAL = {"written_off"}

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
TARIFF_UNITS = {"km": "за километр", "hour": "за час", "ton": "за тонну", "trip": "за рейс", "day": "за сутки"}
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


# ---------------------------------------------------------------- наряды на день (перенос из старой программы)
JOB_STATES = {"planned": "Запланирован", "on_site": "На объекте", "done": "Выполнен", "cancelled": "Отменён"}
JOB_STAGES = {"before": "До работы", "after": "После работы"}
JOB_MAX_PHOTOS_PER_STAGE = 10
# Дата наряда не дальше этого числа дней от сегодняшней (защита от опечаток в году)
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
BILL_YEAR_MIN, BILL_YEAR_MAX = 2000, 2100


# ---------------------------------------------------------------- табель и СИЗ (перенос из старой программы)
# Коды по мотивам унифицированной формы Т-13. Один код на день: «Я», «Я 6» (с часами), «Б».
TIMESHEET_CODES = {
    "Я": "Явка", "Н": "Работа в ночное время", "РВ": "Работа в выходной или праздничный день",
    "С": "Сверхурочная работа (только часы)", "К": "Командировка", "ОТ": "Ежегодный отпуск",
    "ОД": "Дополнительный отпуск", "ДО": "Отпуск без сохранения зарплаты", "Р": "Отпуск по беременности и родам",
    "ОЖ": "Отпуск по уходу за ребёнком", "Б": "Больничный", "Т": "Нетрудоспособность без пособия",
    "ПР": "Прогул", "НН": "Неявка по невыясненным причинам", "РП": "Простой по вине работодателя", "В": "Выходной",
}
TIMESHEET_DAY_CODES = ("Я", "Н", "РВ", "К")                 # считаются рабочим днём, по умолчанию 8 часов
TIMESHEET_ABSENCE_CODES = ("ОТ", "ОД", "ДО", "Р", "ОЖ", "Б", "Т", "ПР", "НН", "РП")
TIMESHEET_DEFAULT_HOURS = 8
TIMESHEET_FILL_MODES = {"weekends": "Выходные («В»)", "weekdays": "Будни до сегодняшнего дня («Я»)", "waybills": "По путевым листам"}
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
