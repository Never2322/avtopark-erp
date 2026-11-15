# -*- coding: utf-8 -*-
"""Каталог прав и стартовые наборы по ролям.

Модель из пакета проектирования («Роли и права»): запрет по умолчанию, действия view / create / edit / archive /
approve / export разделены. Суффикс _own означает «только своё»: водитель видит свои путевые листы и свою технику.
Это стартовая модель: её нужно подтвердить с руководителями, поэтому роли можно править в разделе «Роли и права».
"""

def _crud(prefix: str, module: str, noun: str) -> list[tuple[str, str, str]]:
    """Четыре права на раздел: видеть, добавлять, менять, убирать в архив (удалять записи нельзя нигде)."""
    return [(f"{prefix}.view", module, f"Видеть {noun}"), (f"{prefix}.create", module, f"Добавлять {noun}"),
            (f"{prefix}.edit", module, f"Менять {noun}"), (f"{prefix}.void", module, f"Убирать {noun} в архив и возвращать")]


# (код, модуль, описание)
PERMISSIONS: list[tuple[str, str, str]] = [
    ("dashboard.view", "Главная", "Видеть главную страницу"),
    ("vehicles.view", "Транспорт", "Видеть всю технику"),
    ("vehicles.view_own", "Транспорт", "Видеть только закреплённую за собой технику"),
    ("vehicles.create", "Транспорт", "Добавлять технику"),
    ("vehicles.edit", "Транспорт", "Менять данные техники"),
    ("vehicles.archive", "Транспорт", "Отправлять технику в архив и возвращать"),
    ("vehicles.export", "Транспорт", "Выгружать список техники в Excel"),
    ("vehicle_status.change", "Транспорт", "Менять статус техники"),
    ("assignments.manage", "Транспорт", "Закреплять водителей за техникой"),
    ("readings.create", "Показания", "Вводить показания любой техники"),
    ("readings.create_own", "Показания", "Вводить показания только своей техники"),
    ("readings.review", "Показания", "Проверять подозрительные показания"),
    ("readings.correct", "Показания", "Исправлять показания (с обязательной причиной)"),
    ("requests.view", "Обращения и ремонты", "Видеть все обращения"),
    ("requests.view_own", "Обращения и ремонты", "Видеть только свои обращения"),
    ("requests.create", "Обращения и ремонты", "Создавать обращения"),
    ("requests.edit", "Обращения и ремонты", "Вести обращение: исполнитель, работы, стоимость"),
    ("requests.transition", "Обращения и ремонты", "Переводить обращение по статусам"),
    ("requests.approve", "Обращения и ремонты", "Согласовывать ремонт"),
    ("waybills.view", "Путевые листы", "Видеть все путевые листы"),
    ("waybills.view_own", "Путевые листы", "Видеть только свои путевые листы"),
    ("waybills.create", "Путевые листы", "Создавать путевые листы"),
    ("waybills.create_own", "Путевые листы", "Создавать только свои путевые листы"),
    ("waybills.close", "Путевые листы", "Открывать и закрывать любые путевые листы"),
    ("waybills.close_own", "Путевые листы", "Открывать и закрывать только свои путевые листы"),
    ("waybills.correct", "Путевые листы", "Исправлять закрытые путевые листы"),
    ("waybills.cancel", "Путевые листы", "Аннулировать путевые листы"),
    ("employees.view", "Персонал", "Видеть сотрудников"),
    ("employees.view_sensitive", "Персонал", "Видеть телефон, почту и дату рождения"),
    ("employees.create", "Персонал", "Добавлять сотрудников"),
    ("employees.edit", "Персонал", "Менять данные сотрудников"),
    ("employees.archive", "Персонал", "Отправлять сотрудников в архив"),
    ("employee_files.view", "Личные дела водителей", "Видеть личные дела: фото, права, СТС, ПТС"),
    ("employee_files.view_passport", "Личные дела водителей", "Видеть паспорта (отдельное право: это личные данные)"),
    ("employee_files.edit", "Личные дела водителей", "Добавлять и менять документы в личных делах"),
    ("vehicle_files.view", "Личные дела техники", "Видеть личные дела техники и документы"),
    ("vehicle_files.edit", "Личные дела техники", "Добавлять и менять документы и данные в личных делах техники"),
    *_crud("counterparties", "Справочники", "контрагентов"),
    *_crud("tariffs", "Справочники", "тарифы"),
    *_crud("constants", "Справочники", "константы"),
    *_crud("fuel_cards", "Топливные карты", "топливные карты"),
    *_crud("fines", "Штрафы", "штрафы"),
    *_crud("rentals", "Аренда техники", "аренды"),
    *_crud("expenses", "Расходы", "прочие расходы"),
    *_crud("payments", "Выплаты сотрудникам", "выплаты сотрудникам"),
    *_crud("cash_accounts", "Касса", "счета и кассы"),
    *_crud("cash", "Касса", "кассовые операции"),
    *_crud("orders", "Заказы и документы", "заказы"),
    *_crud("documents", "Заказы и документы", "документы (счета, акты, накладные, УПД)"),
    *_crud("jobs", "Наряды на день", "наряды на день"),
    ("jobs.view_own", "Наряды на день", "Видеть наряды только по своей технике"),
    ("jobs.act_own", "Наряды на день", "Отмечать «приехал» и «закончил» и добавлять фото по нарядам своей техники"),
    *_crud("trips", "Рейсы и заправки", "рейсы"),
    ("trips.view_own", "Рейсы и заправки", "Видеть рейсы только по своей технике"),
    ("trips.create_own", "Рейсы и заправки", "Вносить рейсы по своей технике, добавлять фото и убирать свою свежую запись"),
    *_crud("fuel_fills", "Рейсы и заправки", "заправки"),
    ("fuel_fills.view_own", "Рейсы и заправки", "Видеть заправки только по своей технике"),
    ("fuel_fills.create_own", "Рейсы и заправки", "Вносить заправки по своей технике, добавлять фото чека и убирать свою свежую запись"),
    *_crud("parts", "Каталог деталей", "детали в каталоге"),
    ("purchases.view", "Что купить", "Видеть общий список «что купить» по открытым обращениям"),
    ("purchases.mark", "Что купить", "Отмечать детали купленными и возвращать их в список"),
    ("purchases.export", "Что купить", "Выгружать список «что купить» в Excel"),
    *_crud("bills", "Счета на ремонт", "счета на ремонт и запчасти"),
    ("bills.pay", "Счета на ремонт", "Отмечать счета оплаченными или отменёнными"),
    ("bills.export", "Счета на ремонт", "Выгружать счета на ремонт в Excel"),
    ("timesheet.view", "Табель", "Видеть табель учёта рабочего времени"),
    ("timesheet.edit", "Табель", "Ставить и менять отметки в табеле, заполнять месяц"),
    ("timesheet.export", "Табель", "Выгружать табель в Excel"),
    *_crud("ppe", "СИЗ", "выдачу СИЗ"),
    *_crud("ppe_norms", "СИЗ", "нормы СИЗ (что выдаём и на сколько хватает)"),
    *_crud("employee_events", "События сотрудников", "события сотрудников (медосмотры, нарушения, премии)"),
    *_crud("calendar_events", "Календарь", "свои события в календаре"),
    ("calendar.view", "Календарь", "Видеть календарь: сроки из других разделов показываются только по тем, на которые есть право"),
    ("reminders.view", "Напоминания", "Видеть напоминания: только по тем разделам, на которые есть право"),
    ("roster.view", "Водители и техника", "Видеть сводку: кто на какой машине и что с ней сейчас"),
    ("roster.export", "Водители и техника", "Выгружать сводку в Excel"),
    ("legal_entities.view", "Организации", "Видеть юридические лица"),
    ("legal_entities.manage", "Организации", "Добавлять и менять юридические лица"),
    ("users.view", "Администрирование", "Видеть пользователей"),
    ("users.manage", "Администрирование", "Создавать пользователей и назначать роли"),
    ("roles.manage", "Администрирование", "Менять права ролей"),
    ("audit.view", "Администрирование", "Смотреть журнал действий"),
    ("backups.manage", "Администрирование", "Делать, скачивать и проверять резервные копии"),
]
PERMISSION_CODES = {p[0] for p in PERMISSIONS}

# Права, которые имеют смысл только «везде», без привязки к одному юрлицу (общие справочники и администрирование).
GLOBAL_ONLY = {"legal_entities.manage", "users.view", "users.manage", "roles.manage", "backups.manage"}

ROLES: list[tuple[str, str, str]] = [
    ("admin", "Администратор", "Всё, включая пользователей, права, копии базы"),
    ("director", "Директор", "Контроль всей работы, согласование ремонта, журнал действий"),
    ("mechanic", "Механик", "Ведёт ремонты, вводит показания, видит технику"),
    ("dispatcher", "Диспетчер", "Назначает водителей, ведёт путевые листы"),
    ("driver", "Водитель", "Своя техника, свои путевые листы, обращения"),
    ("storekeeper", "Кладовщик", "Видит технику, создаёт обращения (склад появится в следующих очередях)"),
    ("accountant", "Бухгалтер", "Смотрит технику, путевые листы, сотрудников (финансы появятся в следующих очередях)"),
    ("employee", "Сотрудник", "Свой профиль и обращения"),
]

_REQ_STAFF = ["requests.view", "requests.create"]
ROLE_DEFAULTS: dict[str, list[str]] = {
    "admin": sorted(PERMISSION_CODES),
    "director": [
        "dashboard.view", "vehicles.view", "vehicles.create", "vehicles.edit", "vehicles.archive", "vehicles.export",
        "vehicle_status.change", "assignments.manage", "readings.create", "readings.review", "readings.correct",
        "requests.view", "requests.create", "requests.edit", "requests.transition", "requests.approve",
        "waybills.view", "waybills.create", "waybills.close", "waybills.correct", "waybills.cancel",
        "employees.view", "employees.view_sensitive", "employees.create", "employees.edit", "employees.archive",
        "employee_files.view", "employee_files.view_passport", "employee_files.edit", "vehicle_files.view", "vehicle_files.edit",
        "legal_entities.view", "audit.view",
    ],
    "mechanic": [
        "dashboard.view", "vehicles.view", "vehicle_status.change", "readings.create",
        "requests.view", "requests.create", "requests.edit", "requests.transition",
        "waybills.view", "employees.view", "vehicle_files.view", "vehicle_files.edit", "legal_entities.view",
    ],
    "dispatcher": [
        "dashboard.view", "vehicles.view", "vehicles.export", "vehicle_status.change", "assignments.manage",
        "readings.create", *_REQ_STAFF, "waybills.view", "waybills.create", "waybills.close", "waybills.cancel",
        "employees.view", "employee_files.view", "vehicle_files.view", "legal_entities.view",
    ],
    "driver": [
        "dashboard.view", "vehicles.view_own", "readings.create_own", "requests.view_own", "requests.create",
        "waybills.view_own", "waybills.create_own", "waybills.close_own",
    ],
    "storekeeper": ["dashboard.view", "vehicles.view", "requests.view_own", "requests.create", "legal_entities.view"],
    "accountant": ["dashboard.view", "vehicles.view", "waybills.view", "employees.view", "requests.view", "vehicle_files.view",
                   "legal_entities.view"],
    "employee": ["dashboard.view", "requests.view_own", "requests.create"],
}


def _all(*prefixes: str, only: tuple[str, ...] = ("view", "create", "edit", "void")) -> list[str]:
    return [f"{p}.{a}" for p in prefixes for a in only]


# Деньги и справочники. Это допущения: роли подтверждаются с руководителями (см. docs/Роли-и-права.md).
_VIEW = ("view",)
_WORK = ("view", "create", "edit")
ROLE_DEFAULTS["director"] += _all("counterparties", "tariffs", "constants", "fuel_cards", "fines", "rentals", "expenses", "payments", "cash_accounts", "cash", "orders", "documents", "jobs")
ROLE_DEFAULTS["accountant"] += (_all("counterparties", "tariffs", "expenses", "payments", "cash_accounts", "cash", "fines", "rentals", "orders", "documents")
                                + _all("fuel_cards", only=_WORK) + _all("constants", only=_VIEW))
ROLE_DEFAULTS["dispatcher"] += (_all("counterparties", "tariffs", "documents", only=_VIEW)
                                + _all("fines", "rentals", "fuel_cards", "orders", only=_WORK))
ROLE_DEFAULTS["mechanic"] += _all("fuel_cards", "fines", "rentals", "tariffs", only=_VIEW)
# Наряды: диспетчер ведёт их целиком, бухгалтер смотрит, водитель видит и отмечает только свою технику, механик и кладовщик не видят
ROLE_DEFAULTS["dispatcher"] += _all("jobs")
ROLE_DEFAULTS["accountant"] += _all("jobs", only=_VIEW)
ROLE_DEFAULTS["driver"] += ["jobs.view_own", "jobs.act_own"]
# Рейсы и заправки: директор и бухгалтер (деньги за топливо) ведут целиком, диспетчер вносит и правит, механик смотрит заправки (расход),
# водитель вносит и видит записи только по своей технике
ROLE_DEFAULTS["director"] += _all("trips", "fuel_fills")
ROLE_DEFAULTS["accountant"] += _all("trips", "fuel_fills")
ROLE_DEFAULTS["dispatcher"] += _all("trips", "fuel_fills", only=_WORK)
ROLE_DEFAULTS["mechanic"] += _all("fuel_fills", only=_VIEW)
ROLE_DEFAULTS["driver"] += ["trips.view_own", "trips.create_own", "fuel_fills.view_own", "fuel_fills.create_own"]
# Сводка «Водители и техника»: директор и диспетчер смотрят и выгружают, механик смотрит (какая машина на ремонте и у кого)
ROLE_DEFAULTS["director"] += ["roster.view", "roster.export"]
ROLE_DEFAULTS["dispatcher"] += ["roster.view", "roster.export"]
ROLE_DEFAULTS["mechanic"] += ["roster.view"]
ROLE_DEFAULTS["storekeeper"] += _all("counterparties", only=_VIEW)
# Детали, «что купить» и счета на ремонт (этап 2в). Допущения: деталями и покупками занимаются механик и директор,
# счета ведут директор и бухгалтер, механик может внести полученный счёт, но не оплачивать и не менять его.
ROLE_DEFAULTS["director"] += _all("parts", "bills") + ["purchases.view", "purchases.mark", "purchases.export", "bills.pay", "bills.export"]
ROLE_DEFAULTS["mechanic"] += _all("parts") + ["purchases.view", "purchases.mark", "purchases.export", "bills.view", "bills.create"]
ROLE_DEFAULTS["accountant"] += _all("bills") + ["bills.pay", "bills.export", "purchases.view", "parts.view"]
ROLE_DEFAULTS["storekeeper"] += ["parts.view", "purchases.view"]
# Табель и СИЗ (этап 3а). Допущения: табель ведут директор, бухгалтер (по нему считают зарплату) и диспетчер (знает, кто выходил),
# СИЗ выдаёт кладовщик, директор всё видит и может менять нормы, бухгалтер и диспетчер только смотрят выдачу.
ROLE_DEFAULTS["director"] += ["timesheet.view", "timesheet.edit", "timesheet.export"] + _all("ppe", "ppe_norms")
ROLE_DEFAULTS["accountant"] += ["timesheet.view", "timesheet.edit", "timesheet.export"] + _all("ppe", "ppe_norms", only=_VIEW)
ROLE_DEFAULTS["dispatcher"] += ["timesheet.view", "timesheet.edit"] + _all("ppe", "ppe_norms", only=_VIEW)
ROLE_DEFAULTS["storekeeper"] += _all("ppe", only=_WORK) + _all("ppe_norms", only=_WORK)
# События сотрудников, календарь и напоминания (этап 3б). Допущения: события сотрудников (медосмотры, нарушения, премии) ведут директор
# и бухгалтер, диспетчер их видит; календарём и напоминаниями пользуются все, кроме водителя; в напоминаниях каждый видит только то,
# на что у него есть право (например, механик не увидит штрафы и счета, если у него нет прав на них).
ROLE_DEFAULTS["director"] += _all("employee_events", "calendar_events") + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["accountant"] += _all("employee_events", "calendar_events", only=_WORK) + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["dispatcher"] += _all("employee_events", only=_VIEW) + _all("calendar_events", only=_WORK) + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["mechanic"] += _all("calendar_events", only=_WORK) + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["storekeeper"] += _all("calendar_events", only=_VIEW) + ["calendar.view", "reminders.view"]
assert all(c in PERMISSION_CODES for codes in ROLE_DEFAULTS.values() for c in codes)
