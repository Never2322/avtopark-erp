-- Схема базы, фаза 1. Свой дизайн, написан заново по карте приложения.
-- Даты хранятся как ГГГГ-ММ-ДД, время показаний как ГГГГ-ММ-ДД ЧЧ:ММ.
-- AUTOINCREMENT: идентификаторы не переиспользуются после удаления записи.
-- Деньги хранятся целым числом копеек.

CREATE TABLE IF NOT EXISTS organizations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    legal_form  TEXT NOT NULL DEFAULT '',
    phone       TEXT NOT NULL DEFAULT '',
    fax         TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS employees (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id       INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    last_name    TEXT NOT NULL CHECK (length(trim(last_name)) > 0),
    first_name   TEXT NOT NULL DEFAULT '',
    middle_name  TEXT NOT NULL DEFAULT '',
    birth_date   TEXT,
    position     TEXT NOT NULL DEFAULT '',
    salary_kop   INTEGER NOT NULL DEFAULT 0 CHECK (salary_kop >= 0),
    tab_number   TEXT NOT NULL DEFAULT '',
    is_driver    INTEGER NOT NULL DEFAULT 0 CHECK (is_driver IN (0, 1)),
    phone_work   TEXT NOT NULL DEFAULT '',
    phone_home   TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_employees_org ON employees(org_id);

CREATE TABLE IF NOT EXISTS vehicles (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id             INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    group_kind         TEXT NOT NULL DEFAULT 'truck'
                       CHECK (group_kind IN ('truck', 'car', 'trailer', 'special')),
    vehicle_type       TEXT NOT NULL DEFAULT '',
    make               TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    plate              TEXT NOT NULL DEFAULT '',
    garage_number      TEXT NOT NULL DEFAULT '',
    odometer_start     INTEGER NOT NULL DEFAULT 0 CHECK (odometer_start >= 0),
    engine_hours_start REAL    NOT NULL DEFAULT 0 CHECK (engine_hours_start >= 0),
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'repair', 'reserve', 'written_off')),
    owner              TEXT NOT NULL DEFAULT '',
    has_tracker        INTEGER NOT NULL DEFAULT 0 CHECK (has_tracker IN (0, 1)),
    driver1_id         INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    driver2_id         INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    note               TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    vin                TEXT NOT NULL DEFAULT '',
    made_year          INTEGER CHECK (made_year IS NULL OR made_year BETWEEN 1900 AND 2100),
    fuel_type          TEXT NOT NULL DEFAULT '',
    fuel_norm          REAL CHECK (fuel_norm IS NULL OR fuel_norm >= 0),
    tank_capacity      REAL CHECK (tank_capacity IS NULL OR tank_capacity >= 0),
    limit_km_month     INTEGER CHECK (limit_km_month IS NULL OR limit_km_month >= 0),
    work_state         TEXT NOT NULL DEFAULT 'work'
                       CHECK (work_state IN ('work', 'repair', 'vacation', 'dayoff')),
    work_state_at      TEXT,
    work_state_note    TEXT NOT NULL DEFAULT '',
    photo              TEXT NOT NULL DEFAULT '',
    photo_credit       TEXT NOT NULL DEFAULT '',
    gps_lat            REAL,
    gps_lon            REAL,
    gps_speed          REAL,
    gps_at             TEXT,
    gps_km_last        REAL,
    gps_hours_last     REAL
);
CREATE INDEX IF NOT EXISTS idx_vehicles_org ON vehicles(org_id);

CREATE TABLE IF NOT EXISTS odometer_readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id    INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    km            INTEGER NOT NULL CHECK (km >= 0),
    engine_hours  REAL CHECK (engine_hours >= 0),
    read_at       TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    user_id       INTEGER,
    user_name     TEXT NOT NULL DEFAULT '',
    client_id     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_readings_vehicle ON odometer_readings(vehicle_id, read_at);

-- Три таблицы с контролем по сроку и пробегу. Общее правило:
-- запись "на контроле" (controlled = 1) и подходит к порогу или прошла его -> напоминание.

CREATE TABLE IF NOT EXISTS maintenance (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id     INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'ТО-1'
                   CHECK (kind IN ('СО', 'ТО-1', 'ТО-2', 'Другое')),
    work           TEXT NOT NULL DEFAULT '',
    interval_km    INTEGER CHECK (interval_km IS NULL OR interval_km > 0),
    due_odometer   INTEGER CHECK (due_odometer IS NULL OR due_odometer >= 0),
    due_date       TEXT,
    done_at        TEXT,
    controlled     INTEGER NOT NULL DEFAULT 1 CHECK (controlled IN (0, 1)),
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (due_odometer IS NOT NULL OR due_date IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_maintenance_vehicle ON maintenance(vehicle_id, controlled);

CREATE TABLE IF NOT EXISTS insurance (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id     INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL DEFAULT 'ОСАГО',
    start_date     TEXT,
    end_date       TEXT NOT NULL,
    company        TEXT NOT NULL DEFAULT '',
    policy_number  TEXT NOT NULL DEFAULT '',
    amount_kop     INTEGER NOT NULL DEFAULT 0 CHECK (amount_kop >= 0),
    paid_at        TEXT,
    document       TEXT NOT NULL DEFAULT '',
    favorite       INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    controlled     INTEGER NOT NULL DEFAULT 1 CHECK (controlled IN (0, 1)),
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_insurance_vehicle ON insurance(vehicle_id, controlled);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id     INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL DEFAULT 'Другое',
    title          TEXT NOT NULL CHECK (length(trim(title)) > 0),
    unit           TEXT NOT NULL DEFAULT '',
    interval_km    INTEGER CHECK (interval_km IS NULL OR interval_km > 0),
    due_odometer   INTEGER CHECK (due_odometer IS NULL OR due_odometer >= 0),
    due_date       TEXT,
    due_hours      REAL CHECK (due_hours IS NULL OR due_hours >= 0),
    controlled     INTEGER NOT NULL DEFAULT 1 CHECK (controlled IN (0, 1)),
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (due_odometer IS NOT NULL OR due_date IS NOT NULL OR due_hours IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_events_vehicle ON events(vehicle_id, controlled);

CREATE TABLE IF NOT EXISTS constants (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    value  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- ===== Этап 1: топливо, шины, ремонты, финансы, путевые листы, календарь, события персонала =====

CREATE TABLE IF NOT EXISTS fuel_cards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    number       TEXT NOT NULL UNIQUE CHECK (length(trim(number)) > 0),
    provider     TEXT NOT NULL DEFAULT '',
    vehicle_id   INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    limit_month_l REAL CHECK (limit_month_l IS NULL OR limit_month_l >= 0),
    valid_until  TEXT,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fuel_fills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id   INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    fill_date    TEXT NOT NULL,
    liters       REAL NOT NULL CHECK (liters > 0),
    price_kop    INTEGER NOT NULL DEFAULT 0 CHECK (price_kop >= 0),
    amount_kop   INTEGER NOT NULL DEFAULT 0 CHECK (amount_kop >= 0),
    odometer     INTEGER CHECK (odometer IS NULL OR odometer >= 0),
    full_tank    INTEGER NOT NULL DEFAULT 1 CHECK (full_tank IN (0, 1)),
    station      TEXT NOT NULL DEFAULT '',
    card_id      INTEGER REFERENCES fuel_cards(id) ON DELETE SET NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    user_id      INTEGER,
    user_name    TEXT NOT NULL DEFAULT '',
    client_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fuel_vehicle ON fuel_fills(vehicle_id, fill_date);

CREATE TABLE IF NOT EXISTS tires (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id   INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    brand        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    size         TEXT NOT NULL DEFAULT '',
    season       TEXT NOT NULL DEFAULT 'all' CHECK (season IN ('summer', 'winter', 'all')),
    serial       TEXT NOT NULL DEFAULT '',
    position     TEXT NOT NULL DEFAULT '',
    install_date TEXT,
    install_km   INTEGER CHECK (install_km IS NULL OR install_km >= 0),
    remove_date  TEXT,
    remove_km    INTEGER CHECK (remove_km IS NULL OR remove_km >= 0),
    price_kop    INTEGER NOT NULL DEFAULT 0 CHECK (price_kop >= 0),
    wear_pct     INTEGER CHECK (wear_pct IS NULL OR wear_pct BETWEEN 0 AND 100),
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tires_vehicle ON tires(vehicle_id);

CREATE TABLE IF NOT EXISTS repairs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id   INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    repair_date  TEXT NOT NULL,
    odometer     INTEGER CHECK (odometer IS NULL OR odometer >= 0),
    kind         TEXT NOT NULL DEFAULT 'repair' CHECK (kind IN ('repair', 'part', 'material')),
    title        TEXT NOT NULL CHECK (length(trim(title)) > 0),
    part_number  TEXT NOT NULL DEFAULT '',
    qty          REAL NOT NULL DEFAULT 1 CHECK (qty > 0),
    cost_kop     INTEGER NOT NULL DEFAULT 0 CHECK (cost_kop >= 0),
    executor     TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_repairs_vehicle ON repairs(vehicle_id, repair_date);

CREATE TABLE IF NOT EXISTS vehicle_finance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id   INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    op_date      TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'expense' CHECK (kind IN ('expense', 'income')),
    category     TEXT NOT NULL DEFAULT '',
    amount_kop   INTEGER NOT NULL CHECK (amount_kop >= 0),
    description  TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_finance_vehicle ON vehicle_finance(vehicle_id, op_date);

CREATE TABLE IF NOT EXISTS waybills (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    number         TEXT NOT NULL CHECK (length(trim(number)) > 0),
    issue_date     TEXT NOT NULL,
    valid_from     TEXT NOT NULL,
    valid_to       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'issued' CHECK (status IN ('draft', 'issued', 'closed')),
    org_id         INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    issuer_id      INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    vehicle_id     INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    trailer_id     INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    driver_id      INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    driver2_id     INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    transport_kind TEXT NOT NULL DEFAULT 'cargo'
                   CHECK (transport_kind IN ('cargo', 'passengers', 'own', 'taxi')),
    comm_kind      TEXT NOT NULL DEFAULT 'city'
                   CHECK (comm_kind IN ('city', 'suburban', 'intercity', 'international')),
    route          TEXT NOT NULL DEFAULT '',
    customer       TEXT NOT NULL DEFAULT '',
    cargo          TEXT NOT NULL DEFAULT '',
    cargo_kg       REAL CHECK (cargo_kg IS NULL OR cargo_kg >= 0),
    depart_at      TEXT,
    return_at      TEXT,
    odo_out        INTEGER CHECK (odo_out IS NULL OR odo_out >= 0),
    odo_in         INTEGER CHECK (odo_in IS NULL OR odo_in >= 0),
    hours_out      REAL CHECK (hours_out IS NULL OR hours_out >= 0),
    hours_in       REAL CHECK (hours_in IS NULL OR hours_in >= 0),
    fuel_out       REAL CHECK (fuel_out IS NULL OR fuel_out >= 0),
    fuel_issued    REAL CHECK (fuel_issued IS NULL OR fuel_issued >= 0),
    fuel_in        REAL CHECK (fuel_in IS NULL OR fuel_in >= 0),
    medic_by       TEXT NOT NULL DEFAULT '',
    medic_at       TEXT,
    tech_by        TEXT NOT NULL DEFAULT '',
    tech_at        TEXT,
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_waybills_number ON waybills(number, substr(issue_date, 1, 4));
CREATE INDEX IF NOT EXISTS idx_waybills_vehicle ON waybills(vehicle_id, issue_date);

CREATE TABLE IF NOT EXISTS waybill_templates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL CHECK (length(trim(title)) > 0),
    start_at     TEXT NOT NULL,
    end_at       TEXT,
    all_day      INTEGER NOT NULL DEFAULT 0 CHECK (all_day IN (0, 1)),
    color        TEXT NOT NULL DEFAULT 'teal' CHECK (color IN ('teal', 'blue', 'amber', 'red', 'green', 'violet')),
    vehicle_id   INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    employee_id  INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    remind_min   INTEGER CHECK (remind_min IS NULL OR remind_min >= 0),
    done         INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_calendar_start ON calendar_events(start_at);

CREATE TABLE IF NOT EXISTS employee_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL DEFAULT 'Другое',
    event_date   TEXT,
    due_date     TEXT,
    amount_kop   INTEGER NOT NULL DEFAULT 0 CHECK (amount_kop >= 0),
    description  TEXT NOT NULL DEFAULT '',
    controlled   INTEGER NOT NULL DEFAULT 1 CHECK (controlled IN (0, 1)),
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_empevents_employee ON employee_events(employee_id);

-- ----------------------------------------------------------------------
-- Этап 2: контрагенты, тарифы, заказы, документы
-- ----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS counterparties (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    kind         TEXT NOT NULL DEFAULT 'customer' CHECK (kind IN ('customer', 'supplier', 'both')),
    inn          TEXT NOT NULL DEFAULT '',
    kpp          TEXT NOT NULL DEFAULT '',
    address      TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    contact      TEXT NOT NULL DEFAULT '',
    bank_name    TEXT NOT NULL DEFAULT '',
    bik          TEXT NOT NULL DEFAULT '',
    account      TEXT NOT NULL DEFAULT '',
    corr_account TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tariffs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    unit       TEXT NOT NULL DEFAULT 'km' CHECK (unit IN ('km', 'hour', 'ton', 'trip', 'day')),
    rate_kop   INTEGER NOT NULL DEFAULT 0 CHECK (rate_kop >= 0),
    min_kop    INTEGER NOT NULL DEFAULT 0 CHECK (min_kop >= 0),
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    number      TEXT NOT NULL CHECK (length(trim(number)) > 0),
    order_date  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'confirmed', 'in_progress', 'done', 'cancelled')),
    customer_id INTEGER NOT NULL REFERENCES counterparties(id),
    point_from  TEXT NOT NULL DEFAULT '',
    point_to    TEXT NOT NULL DEFAULT '',
    cargo       TEXT NOT NULL DEFAULT '',
    cargo_kg    REAL CHECK (cargo_kg IS NULL OR cargo_kg >= 0),
    distance_km INTEGER CHECK (distance_km IS NULL OR distance_km >= 0),
    hours       REAL CHECK (hours IS NULL OR hours >= 0),
    tariff_id   INTEGER REFERENCES tariffs(id) ON DELETE SET NULL,
    qty         REAL CHECK (qty IS NULL OR qty >= 0),
    rate_kop    INTEGER NOT NULL DEFAULT 0 CHECK (rate_kop >= 0),
    amount_kop  INTEGER NOT NULL DEFAULT 0 CHECK (amount_kop >= 0),
    vat_pct     INTEGER NOT NULL DEFAULT 0 CHECK (vat_pct IN (0, 5, 7, 10, 20, 22)),
    vehicle_id  INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    driver_id   INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    depart_at   TEXT,
    deliver_at  TEXT,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_number ON orders(number, substr(order_date, 1, 4));
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id, order_date);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type        TEXT NOT NULL CHECK (doc_type IN ('invoice', 'act', 'tn', 'upd')),
    number          TEXT NOT NULL CHECK (length(trim(number)) > 0),
    doc_date        TEXT NOT NULL,
    order_id        INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
    subject         TEXT NOT NULL DEFAULT '',
    qty             REAL NOT NULL DEFAULT 1 CHECK (qty > 0),
    unit            TEXT NOT NULL DEFAULT 'усл.',
    price_kop       INTEGER NOT NULL DEFAULT 0 CHECK (price_kop >= 0),
    vat_pct         INTEGER NOT NULL DEFAULT 0 CHECK (vat_pct IN (0, 5, 7, 10, 20, 22)),
    paid_date       TEXT,
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_number ON documents(doc_type, number, substr(doc_date, 1, 4));
CREATE INDEX IF NOT EXISTS idx_documents_counterparty ON documents(counterparty_id, doc_date);

-- ---- этап 2, часть Б: склад, касса, табель ---------------------------------

CREATE TABLE IF NOT EXISTS warehouses (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    note  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stock_items (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    sku      TEXT NOT NULL DEFAULT '',
    unit     TEXT NOT NULL DEFAULT 'шт.',
    min_qty  REAL NOT NULL DEFAULT 0 CHECK (min_qty >= 0),
    note     TEXT NOT NULL DEFAULT ''
);

-- warehouse_id: куда (поступление) или откуда (расход, списание, перемещение)
CREATE TABLE IF NOT EXISTS stock_moves (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    move_date        TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('receipt', 'issue', 'writeoff', 'transfer')),
    item_id          INTEGER NOT NULL REFERENCES stock_items(id) ON DELETE RESTRICT,
    warehouse_id     INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE RESTRICT,
    to_warehouse_id  INTEGER REFERENCES warehouses(id) ON DELETE RESTRICT,
    qty              REAL NOT NULL CHECK (qty > 0),
    price_kop        INTEGER NOT NULL DEFAULT 0 CHECK (price_kop >= 0),
    counterparty_id  INTEGER REFERENCES counterparties(id) ON DELETE SET NULL,
    vehicle_id       INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    doc_number       TEXT NOT NULL DEFAULT '',
    note             TEXT NOT NULL DEFAULT '',
    CHECK ((kind = 'transfer' AND to_warehouse_id IS NOT NULL AND to_warehouse_id <> warehouse_id)
           OR (kind <> 'transfer' AND to_warehouse_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_stock_moves_item ON stock_moves(item_id, warehouse_id);
CREATE INDEX IF NOT EXISTS idx_stock_moves_date ON stock_moves(move_date);

CREATE TABLE IF NOT EXISTS cash_accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    kind         TEXT NOT NULL DEFAULT 'cash' CHECK (kind IN ('cash', 'bank', 'deposit')),
    opening_kop  INTEGER NOT NULL DEFAULT 0 CHECK (opening_kop >= 0),
    note         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cash_ops (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    op_date          TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('in', 'out', 'transfer')),
    account_id       INTEGER NOT NULL REFERENCES cash_accounts(id) ON DELETE RESTRICT,
    to_account_id    INTEGER REFERENCES cash_accounts(id) ON DELETE RESTRICT,
    amount_kop       INTEGER NOT NULL CHECK (amount_kop > 0),
    number           TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL DEFAULT '',
    counterparty_id  INTEGER REFERENCES counterparties(id) ON DELETE SET NULL,
    employee_id      INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    order_id         INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    note             TEXT NOT NULL DEFAULT '',
    CHECK ((kind = 'transfer' AND to_account_id IS NOT NULL AND to_account_id <> account_id)
           OR (kind <> 'transfer' AND to_account_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_cash_ops_account ON cash_ops(account_id, op_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_ops_number ON cash_ops(kind, substr(op_date, 1, 4), number)
    WHERE number <> '';

CREATE TABLE IF NOT EXISTS timesheet_marks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    work_date    TEXT NOT NULL,
    code         TEXT NOT NULL CHECK (length(code) > 0),
    hours        REAL CHECK (hours IS NULL OR (hours >= 0 AND hours <= 24)),
    UNIQUE (employee_id, work_date)
);
CREATE INDEX IF NOT EXISTS idx_timesheet_date ON timesheet_marks(work_date);

-- ---- этап 2, часть В: выплаты, СИЗ, аренда -----------------------------------

CREATE TABLE IF NOT EXISTS employee_payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    pay_date     TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'salary' CHECK (kind IN ('salary', 'advance', 'bonus', 'other', 'fine')),
    amount_kop   INTEGER NOT NULL CHECK (amount_kop > 0),
    note         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_payments_employee ON employee_payments(employee_id, pay_date);

CREATE TABLE IF NOT EXISTS ppe_norms (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    wear_months  INTEGER NOT NULL DEFAULT 0 CHECK (wear_months >= 0),
    note         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ppe_issues (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id    INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    ppe_id         INTEGER NOT NULL REFERENCES ppe_norms(id) ON DELETE RESTRICT,
    issue_date     TEXT NOT NULL,
    qty            INTEGER NOT NULL DEFAULT 1 CHECK (qty >= 1),
    size           TEXT NOT NULL DEFAULT '',
    due_date       TEXT,
    returned_date  TEXT,
    note           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ppe_issues_employee ON ppe_issues(employee_id);

CREATE TABLE IF NOT EXISTS vehicle_rentals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id       INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    direction        TEXT NOT NULL DEFAULT 'out' CHECK (direction IN ('out', 'in')),
    counterparty_id  INTEGER REFERENCES counterparties(id) ON DELETE SET NULL,
    start_date       TEXT NOT NULL,
    end_date         TEXT,
    rate_kop         INTEGER NOT NULL DEFAULT 0 CHECK (rate_kop >= 0),
    finished         INTEGER NOT NULL DEFAULT 0 CHECK (finished IN (0, 1)),
    note             TEXT NOT NULL DEFAULT '',
    CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_rentals_vehicle ON vehicle_rentals(vehicle_id, start_date);

-- Этап 3: пользователи. Пока нет ни одного, программа пароль не спрашивает.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(login) BETWEEN 3 AND 32),
    display_name  TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'manager' CHECK (role IN ('admin', 'manager', 'viewer', 'driver', 'mechanic')),
    pw_hash       TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login    TEXT,
    must_change   INTEGER NOT NULL DEFAULT 0,
    sess_ver      INTEGER NOT NULL DEFAULT 0,
    last_ip       TEXT NOT NULL DEFAULT '',
    tg_chat       TEXT NOT NULL DEFAULT '',
    tg_notify     INTEGER NOT NULL DEFAULT 1,
    tg_code       TEXT NOT NULL DEFAULT '',
    tg_code_exp   TEXT NOT NULL DEFAULT '',
    employee_id   INTEGER REFERENCES employees(id) ON DELETE SET NULL
);

-- Выход по кнопке «Выйти» отзывает именно эту сессию: копия cookie после выхода больше не работает.
CREATE TABLE IF NOT EXISTS revoked_sessions (
    sid    TEXT PRIMARY KEY,
    until  REAL NOT NULL
);

-- Этап 3: правила обработки входящей почты (читаются только заголовки писем, ящик не меняется).
CREATE TABLE IF NOT EXISTS mail_rules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL CHECK (length(trim(name)) > 0),
    sender_contains   TEXT NOT NULL DEFAULT '',
    subject_contains  TEXT NOT NULL DEFAULT '',
    to_calendar       INTEGER NOT NULL DEFAULT 0 CHECK (to_calendar IN (0, 1)),
    to_telegram       INTEGER NOT NULL DEFAULT 0 CHECK (to_telegram IN (0, 1)),
    to_email          INTEGER NOT NULL DEFAULT 0 CHECK (to_email IN (0, 1)),
    active            INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    CHECK (length(trim(sender_contains)) > 0 OR length(trim(subject_contains)) > 0)
);
CREATE TABLE IF NOT EXISTS mail_seen (
    message_key  TEXT PRIMARY KEY,
    seen_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mail_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at  TEXT NOT NULL DEFAULT '',
    sender       TEXT NOT NULL DEFAULT '',
    subject      TEXT NOT NULL DEFAULT '',
    rules        TEXT NOT NULL DEFAULT '',
    result       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------------------------------------------------------
-- Водители, статусы техники, обращения, счета на ремонт
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS user_vehicles (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, vehicle_id)
);

CREATE TABLE IF NOT EXISTS vehicle_state_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    state      TEXT NOT NULL CHECK (state IN ('work', 'repair', 'vacation', 'dayoff')),
    note       TEXT NOT NULL DEFAULT '',
    set_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_name  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_state_log_vehicle ON vehicle_state_log(vehicle_id, id);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id  INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    author_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL CHECK (length(trim(text)) > 0),
    priority    TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal', 'urgent', 'stopped')),
    state       TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new', 'in_work', 'parts', 'done', 'rejected')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    closed_at   TEXT,
    due_date    TEXT,
    auto_key    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tickets_vehicle ON tickets(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state, id);

CREATE TABLE IF NOT EXISTS ticket_parts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    name      TEXT NOT NULL CHECK (length(trim(name)) > 0),
    article   TEXT NOT NULL DEFAULT '',
    qty       REAL NOT NULL DEFAULT 1 CHECK (qty > 0),
    unit      TEXT NOT NULL DEFAULT 'шт',
    bought_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticket_parts_ticket ON ticket_parts(ticket_id);

CREATE TABLE IF NOT EXISTS ticket_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'comment' CHECK (kind IN ('comment', 'system')),
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket ON ticket_comments(ticket_id, id);

CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner       TEXT NOT NULL CHECK (owner IN ('ticket', 'bill', 'fuel', 'trip')),
    owner_id    INTEGER NOT NULL,
    filename    TEXT NOT NULL,
    stored      TEXT NOT NULL UNIQUE,
    mime        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    uploaded_by TEXT NOT NULL DEFAULT '',
    ocr_state   TEXT NOT NULL DEFAULT '',
    ocr_text    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_attachments_owner ON attachments(owner, owner_id);

CREATE TABLE IF NOT EXISTS repair_bills (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL DEFAULT 'uploaded' CHECK (kind IN ('issued', 'uploaded')),
    number         TEXT NOT NULL CHECK (length(trim(number)) > 0),
    bill_date      TEXT NOT NULL,
    supplier_id    INTEGER REFERENCES counterparties(id) ON DELETE SET NULL,
    supplier_name  TEXT NOT NULL DEFAULT '',
    supplier_inn   TEXT NOT NULL DEFAULT '',
    supplier_kpp   TEXT NOT NULL DEFAULT '',
    supplier_addr  TEXT NOT NULL DEFAULT '',
    supplier_phone TEXT NOT NULL DEFAULT '',
    bank_name      TEXT NOT NULL DEFAULT '',
    bik            TEXT NOT NULL DEFAULT '',
    corr_account   TEXT NOT NULL DEFAULT '',
    account        TEXT NOT NULL DEFAULT '',
    basis          TEXT NOT NULL DEFAULT '',
    vehicle_id     INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    ticket_id      INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
    vat_pct        INTEGER NOT NULL DEFAULT 22 CHECK (vat_pct IN (0, 5, 7, 10, 20, 22)),
    total_kop      INTEGER NOT NULL DEFAULT 0 CHECK (total_kop >= 0),
    vat_kop        INTEGER NOT NULL DEFAULT 0 CHECK (vat_kop >= 0),
    status         TEXT NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid', 'paid', 'cancelled')),
    paid_date      TEXT,
    note           TEXT NOT NULL DEFAULT '',
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_bills_date ON repair_bills(bill_date);
CREATE INDEX IF NOT EXISTS idx_bills_vehicle ON repair_bills(vehicle_id);

CREATE TABLE IF NOT EXISTS repair_bill_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id   INTEGER NOT NULL REFERENCES repair_bills(id) ON DELETE CASCADE,
    pos       INTEGER NOT NULL DEFAULT 1,
    name      TEXT NOT NULL CHECK (length(trim(name)) > 0),
    article   TEXT NOT NULL DEFAULT '',
    qty       REAL NOT NULL DEFAULT 1 CHECK (qty > 0),
    unit      TEXT NOT NULL DEFAULT 'шт',
    price_kop INTEGER NOT NULL DEFAULT 0 CHECK (price_kop >= 0),
    sum_kop   INTEGER NOT NULL DEFAULT 0 CHECK (sum_kop >= 0),
    vat_kop   INTEGER NOT NULL DEFAULT 0 CHECK (vat_kop >= 0)
);
CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON repair_bill_items(bill_id, pos);


-- ===== Этап 5: уведомления на сайте, очередь Telegram, журнал действий =====

CREATE TABLE IF NOT EXISTS inbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL DEFAULT 'info',
    title      TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    read_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_inbox_user ON inbox(user_id, read_at, id);

CREATE TABLE IF NOT EXISTS tg_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL,
    text       TEXT NOT NULL,
    tries      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    user_id   INTEGER,
    user_name TEXT NOT NULL DEFAULT '',
    ip        TEXT NOT NULL DEFAULT '',
    action    TEXT NOT NULL,
    object    TEXT NOT NULL DEFAULT '',
    details   TEXT NOT NULL DEFAULT ''
);

-- Этап 5: объявления водителям с перепиской, каталог деталей.
CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL CHECK (length(trim(title)) > 0),
    body        TEXT NOT NULL DEFAULT '',
    author_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    closed      INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1))
);

CREATE TABLE IF NOT EXISTS announcement_targets (
    announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at         TEXT,
    PRIMARY KEY (announcement_id, user_id)
);

-- Переписка по объявлению у каждого водителя своя: driver_id говорит, чья это переписка.
CREATE TABLE IF NOT EXISTS announcement_replies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    announcement_id INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
    driver_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_name     TEXT NOT NULL DEFAULT '',
    from_staff      INTEGER NOT NULL DEFAULT 0 CHECK (from_staff IN (0, 1)),
    text            TEXT NOT NULL CHECK (length(trim(text)) > 0),
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ann_replies ON announcement_replies(announcement_id, driver_id, id);

CREATE TABLE IF NOT EXISTS parts_catalog (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL CHECK (length(trim(name)) > 0),
    name_key  TEXT NOT NULL UNIQUE,
    article   TEXT NOT NULL DEFAULT '',
    unit      TEXT NOT NULL DEFAULT 'шт',
    category  TEXT NOT NULL DEFAULT '',
    uses      INTEGER NOT NULL DEFAULT 0,
    note      TEXT NOT NULL DEFAULT ''
);

-- Рейсы, которые записывает водитель: маршрут, груз, номер накладной и фото накладной (вложения с owner = 'trip').
CREATE TABLE IF NOT EXISTS trips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id  INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    trip_date   TEXT NOT NULL,
    route       TEXT NOT NULL DEFAULT '',
    customer    TEXT NOT NULL DEFAULT '',
    cargo       TEXT NOT NULL DEFAULT '',
    cargo_kg    REAL CHECK (cargo_kg IS NULL OR cargo_kg >= 0),
    doc_number  TEXT NOT NULL DEFAULT '',
    km          INTEGER CHECK (km IS NULL OR km >= 0),
    note        TEXT NOT NULL DEFAULT '',
    user_id     INTEGER,
    user_name   TEXT NOT NULL DEFAULT '',
    client_id   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, trip_date);

-- Этап 5.4: штрафы ГИБДД. Срок оплаты и скидка дают напоминание; оплаченный штраф напоминаний не даёт.
CREATE TABLE IF NOT EXISTS vehicle_fines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id        INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    fine_date         TEXT NOT NULL,
    resolution_number TEXT NOT NULL DEFAULT '',
    article           TEXT NOT NULL DEFAULT '',
    amount_kop        INTEGER NOT NULL DEFAULT 0 CHECK (amount_kop >= 0),
    discount_until    TEXT,
    due_date          TEXT,
    payer             TEXT NOT NULL DEFAULT 'company' CHECK (payer IN ('company', 'driver')),
    paid_at           TEXT,
    note              TEXT NOT NULL DEFAULT '',
    driver_told       INTEGER NOT NULL DEFAULT 0 CHECK (driver_told IN (0, 1)),
    remind_key        TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_fines_vehicle ON vehicle_fines(vehicle_id, fine_date);

-- Наряды на день: кому, куда и что сделать. Водитель отмечает «приехал» и «закончил», прикладывает фото до и после.
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_date    TEXT NOT NULL,
    vehicle_id  INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    customer    TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL CHECK (length(trim(address)) > 0),
    task        TEXT NOT NULL DEFAULT '',
    time_from   TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'planned' CHECK (state IN ('planned', 'on_site', 'done', 'cancelled')),
    arrived_at  TEXT,
    arrived_by  TEXT NOT NULL DEFAULT '',
    finished_at TEXT,
    finished_by TEXT NOT NULL DEFAULT '',
    result_note TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_date ON jobs(job_date, id);
CREATE INDEX IF NOT EXISTS idx_jobs_vehicle ON jobs(vehicle_id, job_date);

CREATE TABLE IF NOT EXISTS job_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL CHECK (stage IN ('before', 'after')),
    stored      TEXT NOT NULL UNIQUE,
    mime        TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    uploaded_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_job_photos_job ON job_photos(job_id, stage, id);

-- Уведомления на телефон при закрытом сайте (Web Push): подписки устройств и очередь на отправку.
CREATE TABLE IF NOT EXISTS push_subs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    agent      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_ok    TEXT,
    fails      INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_push_subs_user ON push_subs(user_id);

CREATE TABLE IF NOT EXISTS push_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Общие расходы фирмы, не привязанные к машине (аренда, связь, канцелярия). Расходы по машине лежат в vehicle_finance.
CREATE TABLE IF NOT EXISTS misc_expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    op_date     TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '',
    amount_kop  INTEGER NOT NULL CHECK (amount_kop > 0),
    description TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_misc_expenses_date ON misc_expenses(op_date, id);
