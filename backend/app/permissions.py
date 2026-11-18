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
    ("vehicle_status.change_own", "Транспорт", "Менять статус только своей техники (на линии или в ремонте)"),
    ("assignments.manage", "Транспорт", "Закреплять водителей за техникой"),
    ("readings.create", "Показания", "Вводить показания любой техники"),
    ("readings.create_own", "Показания", "Вводить показания только своей техники"),
    ("readings.review", "Показания", "Проверять подозрительные показания"),
    ("readings.correct", "Показания", "Исправлять показания (с обязательной причиной)"),
    ("readings.import", "Показания", "Загружать показания пробега и моточасов из файла"),
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
    *_crud("rentals", "Дополнительные работы", "дополнительные работы"),
    *_crud("expenses", "Расходы", "прочие расходы"),
    *_crud("payments", "Выплаты сотрудникам", "выплаты сотрудникам"),
    *_crud("cash_accounts", "Касса", "счета и кассы"),
    *_crud("cash", "Касса", "кассовые операции"),
    *_crud("orders", "Заказы и документы", "заказы"),
    *_crud("documents", "Заказы и документы", "документы (счета, акты, накладные, УПД)"),
    *_crud("jobs", "Технические задания", "технические задания"),
    ("jobs.view_own", "Технические задания", "Видеть задания только по своей технике"),
    ("jobs.act_own", "Технические задания", "Отмечать «приехал» и «закончил» и добавлять фото по заданиям своей техники"),
    *_crud("trips", "Рейсы и заправки", "рейсы"),
    ("trips.view_own", "Рейсы и заправки", "Видеть рейсы только по своей технике"),
    ("trips.create_own", "Рейсы и заправки", "Вносить рейсы по своей технике, добавлять фото и убирать свою свежую запись"),
    *_crud("fuel_fills", "Рейсы и заправки", "заправки"),
    ("fuel_fills.view_own", "Рейсы и заправки", "Видеть заправки только по своей технике"),
    ("fuel_fills.create_own", "Рейсы и заправки", "Вносить заправки по своей технике, добавлять фото чека и убирать свою свежую запись"),
    *_crud("weigh_tickets", "Тоннаж", "талоны весов с полигона (тоннаж по ходкам)"),
    ("weigh_tickets.view_own", "Тоннаж", "Видеть талоны весов только по своей технике"),
    ("weigh_tickets.create_own", "Тоннаж", "Прикладывать талоны весов по своей технике (фото и заметка) и убирать свою свежую запись"),
    *_crud("parts", "Каталог деталей", "детали в каталоге"),
    ("purchases.view", "Что купить", "Видеть общий список «что купить» по открытым обращениям"),
    ("purchases.mark", "Что купить", "Отмечать детали купленными и возвращать их в список"),
    ("purchases.export", "Что купить", "Выгружать список «что купить» в Excel"),
    *_crud("bills", "Счета на ремонт", "счета на ремонт и запчасти"),
    ("bills.pay", "Счета на ремонт", "Отмечать счета оплаченными или отменёнными"),
    ("bills.export", "Счета на ремонт", "Выгружать счета на ремонт в Excel"),
    ("timesheet.view", "Табель", "Видеть табель: сколько заработал каждый сотрудник по дням (нужно ещё право смотреть часы работы и ремонта)"),
    ("timesheet.export", "Табель", "Выгружать табель в Excel"),
    *_crud("worklog", "Табель", "часы работы и ремонта (из них считается заработок)"),
    ("worklog.view_own", "Табель", "Видеть свои часы работы и ремонта и свой заработок"),
    ("worklog.enter", "Табель", "Вносить сотрудникам часы работы и ремонта (механик): ставки и суммы не показываются"),
    ("attendance.view", "Выход на работу", "Видеть, кто выйдет на работу в выбранный день"),
    ("attendance.edit", "Выход на работу", "Отмечать выход за сотрудника (например, по телефону)"),
    ("attendance.mark_own", "Выход на работу", "Отмечать, выйду ли я сам на работу, и видеть свои ответы"),
    *_crud("garbage_routes", "Маршруты мусоровозов", "маршруты мусоровозов (название и улицы)"),
    *_crud("route_plan", "Маршруты мусоровозов", "расписание маршрутов (какой мусоровоз на каком маршруте и в какие дни)"),
    ("route_plan.view_own", "Маршруты мусоровозов", "Видеть свои маршруты: в какой день какой маршрут и по каким улицам едет закреплённая машина"),
    ("hauls.use_own", "Счётчик ходок", "Считать свои ходки на самосвале: личный счётчик водителя, офис и директор его не видят"),
    *_crud("ppe", "СИЗ", "выдачу СИЗ"),
    *_crud("ppe_norms", "СИЗ", "нормы СИЗ (что выдаём и на сколько хватает)"),
    *_crud("employee_events", "События сотрудников", "события сотрудников (медосмотры, нарушения, премии)"),
    *_crud("calendar_events", "Календарь", "свои события в календаре"),
    ("calendar.view", "Календарь", "Видеть календарь: сроки из других разделов показываются только по тем, на которые есть право"),
    ("reminders.view", "Напоминания", "Видеть напоминания: только по тем разделам, на которые есть право"),
    *_crud("announcements", "Объявления", "объявления сотрудникам (кому адресованы, ответы, закрытие)"),
    ("announcements.read_own", "Объявления", "Читать адресованные себе объявления и отвечать на них"),
    *_crud("warehouses", "Склад", "склады"),
    *_crud("stock_items", "Склад", "товары на складе"),
    *_crud("stock_moves", "Склад", "движения товара (поступление, расход, списание, перемещение)"),
    ("roster.view", "Водители и техника", "Видеть сводку: кто на какой машине и что с ней сейчас"),
    ("roster.export", "Водители и техника", "Выгружать сводку в Excel"),
    ("reports.view", "Отчёты", "Открывать отчёты: пробег, топливо, простои и т. д. (по данным, на которые есть права)"),
    ("reports.money", "Отчёты", "Видеть в отчётах деньги: затраты, доходы, суммы заказов"),
    ("reports.export", "Отчёты", "Выгружать отчёты в Excel и CSV"),
    *_crud("tires", "Транспорт", "шины техники"),
    *_crud("vehicle_repairs", "Транспорт", "ремонтные работы по технике"),
    *_crud("vehicle_finance", "Транспорт", "затраты на спецтехнику"),
    *_crud("maintenance", "Транспорт", "плановое ТО техники"),
    *_crud("vehicle_events", "Транспорт", "контроль мероприятий по технике"),
    ("map.view", "Карта", "Открывать карту 2ГИС: поиск адреса и адреса заданий на карте"),
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
# Задания: диспетчер ведёт их целиком, бухгалтер смотрит, водитель видит и отмечает только свою технику, механик и кладовщик не видят
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
ROLE_DEFAULTS["director"] += ["timesheet.view", "timesheet.export"] + _all("ppe", "ppe_norms")
ROLE_DEFAULTS["accountant"] += ["timesheet.view", "timesheet.export"] + _all("ppe", "ppe_norms", only=_VIEW)
ROLE_DEFAULTS["dispatcher"] += ["timesheet.view"] + _all("ppe", "ppe_norms", only=_VIEW)
ROLE_DEFAULTS["storekeeper"] += _all("ppe", only=_WORK) + _all("ppe_norms", only=_WORK)
# События сотрудников, календарь и напоминания (этап 3б). Допущения: события сотрудников (медосмотры, нарушения, премии) ведут директор
# и бухгалтер, диспетчер их видит; календарём и напоминаниями пользуются все, кроме водителя; в напоминаниях каждый видит только то,
# на что у него есть право (например, механик не увидит штрафы и счета, если у него нет прав на них).
ROLE_DEFAULTS["director"] += _all("employee_events", "calendar_events") + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["accountant"] += _all("employee_events", "calendar_events", only=_WORK) + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["dispatcher"] += _all("employee_events", only=_VIEW) + _all("calendar_events", only=_WORK) + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["mechanic"] += _all("calendar_events", only=_WORK) + ["calendar.view", "reminders.view"]
ROLE_DEFAULTS["storekeeper"] += _all("calendar_events", only=_VIEW) + ["calendar.view", "reminders.view"]
# Объявления и уведомления (этап 3в). Допущения: объявления пишет директор, диспетчер пишет и отвечает, читают и отвечают все;
# уведомления на сайте получает каждый пользователь по своим делам (отдельного права на них нет).
ROLE_DEFAULTS["director"] += _all("announcements")
ROLE_DEFAULTS["dispatcher"] += _all("announcements", only=_WORK)
for _role in ("director", "mechanic", "dispatcher", "driver", "storekeeper", "accountant", "employee"):
    ROLE_DEFAULTS[_role] += ["announcements.read_own"]
# Склад (этап 4-1). Допущения: склад ведёт кладовщик (без права убирать записи в архив), директор делает всё, механик видит остатки и
# списывает детали на ремонт, бухгалтер смотрит. Диспетчеру и водителю склад закрыт.
ROLE_DEFAULTS["director"] += _all("warehouses", "stock_items", "stock_moves")
ROLE_DEFAULTS["storekeeper"] += _all("warehouses", "stock_items", "stock_moves", only=_WORK)
ROLE_DEFAULTS["mechanic"] += _all("warehouses", "stock_items", only=_VIEW) + ["stock_moves.view", "stock_moves.create"]
ROLE_DEFAULTS["accountant"] += _all("warehouses", "stock_items", "stock_moves", only=_VIEW)
# Отчёты (этап 4-2). Допущения: директор и бухгалтер видят отчёты вместе с деньгами и выгружают; диспетчер видит отчёты без денег и выгружает;
# механик видит отчёты без денег. Каждый столбец отчёта показывается только если есть право на раздел, из которого он взят.
ROLE_DEFAULTS["director"] += ["reports.view", "reports.money", "reports.export"]
ROLE_DEFAULTS["accountant"] += ["reports.view", "reports.money", "reports.export"]
ROLE_DEFAULTS["dispatcher"] += ["reports.view", "reports.export"]
ROLE_DEFAULTS["mechanic"] += ["reports.view"]
ROLE_DEFAULTS["director"] += ["readings.import"]
ROLE_DEFAULTS["dispatcher"] += ["readings.import"]
# Карта 2ГИС (этап 4-4). Допущение: карту открывают те, кто работает с заданиями; список заданий на ней берётся по их же правам.
for _role in ("director", "dispatcher", "driver", "mechanic"):
    ROLE_DEFAULTS[_role] += ["map.view"]
# Вкладки техники (этап 5-2). Допущения: директор делает всё; механик ведёт шины, ремонты, ТО и контроль (без права убирать в архив),
# финансы по технике видит; диспетчер видит шины, ремонты, ТО и контроль; бухгалтер видит ремонты и финансы; кладовщик и водитель их не видят.
ROLE_DEFAULTS["director"] += _all("tires", "vehicle_repairs", "vehicle_finance", "maintenance", "vehicle_events")
ROLE_DEFAULTS["mechanic"] += _all("tires", "vehicle_repairs", "maintenance", "vehicle_events", only=_WORK) + _all("vehicle_finance", only=_VIEW)
ROLE_DEFAULTS["dispatcher"] += _all("tires", "vehicle_repairs", "maintenance", "vehicle_events", only=_VIEW)
ROLE_DEFAULTS["accountant"] += _all("vehicle_repairs", "tires", only=_VIEW) + _all("vehicle_finance", only=_WORK)
# Зарплатный табель: часы и заработок видят и правят директор, бухгалтер и диспетчер (отмену записи тоже делает диспетчер);
# водитель часы сам не вносит: видит только свои записи и свой заработок. Механик вносит часы сотрудникам, но денег не видит.
ROLE_DEFAULTS["director"] += _all("worklog")
ROLE_DEFAULTS["accountant"] += _all("worklog")
ROLE_DEFAULTS["dispatcher"] += _all("worklog")
ROLE_DEFAULTS["driver"] += ["worklog.view_own"]
ROLE_DEFAULTS["mechanic"] += ["worklog.enter"]
# Выход на работу: водитель отвечает сам; директор и диспетчер видят ответы и могут отметить за человека.
ROLE_DEFAULTS["director"] += ["attendance.view", "attendance.edit"]
ROLE_DEFAULTS["dispatcher"] += ["attendance.view", "attendance.edit"]
ROLE_DEFAULTS["driver"] += ["attendance.mark_own"]
# Личный счётчик ходок: вкладка есть только у водителя, за которым закреплён самосвал. Записи видит он один, в отчёты они не идут.
ROLE_DEFAULTS["driver"] += ["hauls.use_own"]
# Водитель сам отмечает, на линии его машина или в ремонте (причину ремонта пишет); офис видит это в истории статусов и в уведомлениях.
ROLE_DEFAULTS["driver"] += ["vehicle_status.change_own"]
# Тоннаж: водитель прикладывает талон весов (фото и заметка), сам тоннаж читается с фото. Директор и диспетчер видят все талоны и вписывают тоннаж,
# если программа не смогла его прочитать надёжно; бухгалтер только смотрит. Вкладка «Тоннаж» у водителя появляется, когда за ним закреплена
# машина с галочкой «Вести тоннаж» (на мусоровозах и самосвалах она стоит сама).
ROLE_DEFAULTS["director"] += _all("weigh_tickets")
ROLE_DEFAULTS["dispatcher"] += _all("weigh_tickets")
ROLE_DEFAULTS["accountant"] += _all("weigh_tickets", only=_VIEW)
ROLE_DEFAULTS["driver"] += ["weigh_tickets.view_own", "weigh_tickets.create_own"]
# Маршруты мусоровозов: директор делает всё, диспетчер ведёт маршруты и расписание (без архива), водитель видит свои
# маршруты (по закреплённой за ним машине). Остальным роль не нужна: при необходимости выдаётся в «Ролях и правах».
ROLE_DEFAULTS["director"] += _all("garbage_routes", "route_plan")
ROLE_DEFAULTS["dispatcher"] += _all("garbage_routes", "route_plan", only=_WORK)
ROLE_DEFAULTS["driver"] += ["route_plan.view_own"]
assert all(c in PERMISSION_CODES for codes in ROLE_DEFAULTS.values() for c in codes)
