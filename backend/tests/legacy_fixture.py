# -*- coding: utf-8 -*-
"""Вымышленная копия старой программы для проверки переноса: схема настоящая, данные придуманы (с «подвохами»)."""
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).parent / "fixtures" / "legacy_schema.sql"


def make_old_db(path: str) -> str:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    x = con.execute

    def ins(table, **kw):
        cols = ", ".join(kw)
        marks = ", ".join("?" for _ in kw)
        return x(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(kw.values())).lastrowid

    # --- две организации
    o1 = ins("organizations", name="Север", legal_form="ООО", phone="+7 383 000-00-01", email="sever@example.test")
    o2 = ins("organizations", name="Юг", legal_form="ИП")
    # --- сотрудники
    e1 = ins("employees", org_id=o1, last_name="Ершов", first_name="Николай", middle_name="Иванович", position="Водитель", salary_kop=6500000, tab_number="12", is_driver=1,
             phone_work="+7 900 000-00-01", phone_home="+7 900 000-00-02", birth_date="1985-03-04", note="Категории C, E")
    e2 = ins("employees", org_id=o1, last_name="Орлов", first_name="Алексей", position="Механик", is_driver=0)
    e3 = ins("employees", org_id=o2, last_name="Громов", first_name="Пётр", position="Водитель", is_driver=1)
    # --- техника
    v1 = ins("vehicles", org_id=o1, group_kind="truck", vehicle_type="Самосвал", make="КАМАЗ", model="65115", plate="А101АА154", garage_number="101", odometer_start=1000,
             status="active", owner="ООО Север", driver1_id=e1, driver2_id=None, vin="XTC651150A0000001", made_year=2015, fuel_type="Дизельное топливо", fuel_norm=32.5,
             tank_capacity=350, limit_km_month=6000, work_state="work", created_at="2024-03-01 09:00:00")
    v2 = ins("vehicles", org_id=o1, group_kind="special", vehicle_type="Экскаватор", make="JCB", model="3CX", plate="Т202ТТ54", odometer_start=0, engine_hours_start=1500.5,
             status="repair", work_state="repair", work_state_at="2026-10-01 10:00", work_state_note="Гидравлика", created_at="2024-03-02 09:00:00")
    v3 = ins("vehicles", org_id=o2, group_kind="trailer", vehicle_type="", make="Тонар", model="", plate="", status="active", created_at="2024-03-03 09:00:00", driver1_id=e3)
    v4 = ins("vehicles", org_id=o1, group_kind="car", vehicle_type="Легковой", make="Lada", model="Largus", plate="В303ВВ54", status="reserve", odometer_start=5000,
             created_at="2024-03-04 09:00:00")
    # --- показания: у v1 есть «лишнее» меньшее значение, у v2 моточасы
    for km, at in [(1000, "2026-01-10 08:00"), (1500, "2026-02-10 08:00"), (1400, "2026-02-20 08:00"), (2100, "2026-03-10 08:00")]:
        ins("odometer_readings", vehicle_id=v1, km=km, read_at=at, source="manual", user_name="Орлов")
    ins("odometer_readings", vehicle_id=v2, km=0, engine_hours=1600.0, read_at="2026-03-01 08:00", source="gps")
    ins("odometer_readings", vehicle_id=v2, km=0, engine_hours=1650.5, read_at="2026-04-01 08:00", source="manual")
    # --- состояния техники
    ins("vehicle_state_log", vehicle_id=v2, state="work", set_at="2026-09-01 08:00", note="")
    ins("vehicle_state_log", vehicle_id=v2, state="repair", set_at="2026-10-01 10:00", note="Гидравлика")
    # --- карточка техники
    ins("maintenance", vehicle_id=v1, kind="ТО-1", work="Замена масла", interval_km=10000, due_odometer=12000, due_date=None, controlled=1)
    ins("maintenance", vehicle_id=v1, kind="СО", work="Сезонное обслуживание", due_date="2026-10-20", done_at="2026-10-18", controlled=0)
    ins("events", vehicle_id=v1, event_type="Тахограф", title="Поверка тахографа", due_date="2027-01-15", controlled=1)
    ins("events", vehicle_id=v2, event_type="Другое", title="Замена ремня", due_hours=1800.0, controlled=1)
    ins("tires", vehicle_id=v1, brand="Michelin", size="315/80 R22.5", season="winter", install_date="2025-11-01", install_km=900, price_kop=3500000, wear_pct=30)
    ins("tires", vehicle_id=v1, brand="Nokian", season="summer", install_date="2025-05-01", remove_date="2025-10-30", install_km=100, remove_km=800)
    ins("repairs", vehicle_id=v1, repair_date="2026-02-15", odometer=1450, kind="part", title="Колодки тормозные", part_number="K-77", qty=2, cost_kop=450050, executor="СТО Север")
    ins("vehicle_finance", vehicle_id=v1, op_date="2026-03-01", kind="income", category="Аренда", amount_kop=12000000, description="Сдача в аренду")
    ins("vehicle_finance", vehicle_id=v1, op_date="2026-03-02", kind="expense", category="Мойка", amount_kop=0, description="Бесплатная мойка")
    ins("insurance", vehicle_id=v1, kind="ОСАГО", start_date="2026-01-01", end_date="2026-12-31", company="Согласие", policy_number="ААА 123", amount_kop=1250000, controlled=1)
    ins("insurance", vehicle_id=v1, kind="КАСКО", start_date="2025-01-01", end_date="2025-12-31", company="Ингос", controlled=0)
    # --- справочники
    c1 = ins("counterparties", name="ООО Заказчик", kind="customer", inn="5400000001", kpp="540001001", address="Новосибирск", bank_name="Банк", bik="045004000", account="40702810000000000001")
    c2 = ins("counterparties", name="Поставщик Запчастей", kind="supplier")
    t1 = ins("tariffs", name="Самосвал, час", unit="hour", rate_kop=250000, min_kop=500000)
    ins("constants", name="Цена топлива", value="62.5")
    ins("parts_catalog", name="Масло моторное 15W40", name_key="масло моторное 15w40", article="M-15", unit="л", uses=5)
    ins("fuel_cards", number="7000111", provider="Роснефть", vehicle_id=v1, limit_month_l=3000, valid_until="2027-05-31")
    ins("fuel_fills", vehicle_id=v1, fill_date="2026-03-05", liters=120.5, price_kop=6250, amount_kop=753125, odometer=2150, full_tank=1, station="АЗС-1")
    ins("vehicle_fines", vehicle_id=v1, fine_date="2026-03-12", resolution_number="188101", article="12.9", amount_kop=50000, due_date="2026-05-12", payer="driver", driver_told=1)
    ins("vehicle_rentals", vehicle_id=v4, direction="out", counterparty_id=c1, start_date="2026-05-01", end_date="2026-05-31", rate_kop=300000, finished=1)
    ins("misc_expenses", op_date="2026-03-03", category="Связь", amount_kop=150000, description="Интернет", created_by="admin")
    # --- заказы и документы (повторяющиеся и нечисловые номера)
    ord1 = ins("orders", number="7", order_date="2026-03-10", status="done", customer_id=c1, point_from="А", point_to="Б", cargo="Щебень", cargo_kg=20000, distance_km=120, tariff_id=t1,
               qty=5, rate_kop=250000, amount_kop=1250000, vat_pct=20, vehicle_id=v1, driver_id=e1)
    ins("orders", number="07", order_date="2026-03-11", status="new", customer_id=c1, point_from="В", point_to="Г", amount_kop=100000)
    ins("orders", number="Б-15", order_date="2026-03-12", status="confirmed", customer_id=c2, amount_kop=200000)
    ins("documents", doc_type="invoice", number="3", doc_date="2026-03-11", order_id=ord1, counterparty_id=c1, subject="Услуги самосвала", qty=5, unit="ч", price_kop=250000, vat_pct=20, paid_date="2026-03-20")
    ins("documents", doc_type="invoice", number="03", doc_date="2026-03-15", counterparty_id=c1, subject="Дубль номера", price_kop=100000)
    ins("documents", doc_type="act", number="АКТ-1", doc_date="2026-03-16", order_id=ord1, counterparty_id=c1, price_kop=1250000)
    # --- склад и касса
    w1 = ins("warehouses", name="Главный склад")
    w2 = ins("warehouses", name="Гараж")
    i1 = ins("stock_items", name="Фильтр масляный", sku="FM-1", unit="шт.", min_qty=2)
    ins("stock_moves", move_date="2026-03-01", kind="receipt", item_id=i1, warehouse_id=w1, qty=10, price_kop=45000, counterparty_id=c2, doc_number="УПД-5")
    ins("stock_moves", move_date="2026-03-02", kind="transfer", item_id=i1, warehouse_id=w1, to_warehouse_id=w2, qty=4)
    ins("stock_moves", move_date="2026-03-03", kind="issue", item_id=i1, warehouse_id=w2, qty=1, vehicle_id=v1)
    a1 = ins("cash_accounts", name="Касса", kind="cash", opening_kop=1000000)
    a2 = ins("cash_accounts", name="Расчётный счёт", kind="bank", opening_kop=5000000)
    ins("cash_ops", op_date="2026-03-04", kind="in", account_id=a1, amount_kop=250000, number="1", category="Выручка", counterparty_id=c1)
    ins("cash_ops", op_date="2026-03-05", kind="in", account_id=a1, amount_kop=100000, number="ПКО-1", category="Выручка")
    ins("cash_ops", op_date="2026-03-06", kind="out", account_id=a1, amount_kop=50000, number="", category="Канцелярия", employee_id=e2)
    ins("cash_ops", op_date="2026-03-07", kind="transfer", account_id=a1, to_account_id=a2, amount_kop=200000, number="1")
    # --- люди
    ins("employee_payments", employee_id=e1, pay_date="2026-03-05", kind="advance", amount_kop=1500000)
    n1 = ins("ppe_norms", name="Куртка зимняя", wear_months=24)
    ins("ppe_issues", employee_id=e1, ppe_id=n1, issue_date="2025-10-01", qty=1, size="52", due_date="2027-10-01")
    ins("employee_events", employee_id=e1, kind="Медосмотр", event_date="2026-02-01", due_date="2027-02-01", controlled=1)
    ins("employee_events", employee_id=e1, kind="Собеседование", event_date="2026-02-02", description="Беседа", amount_kop=0)
    ins("timesheet_marks", employee_id=e1, work_date="2026-03-02", code="Я", hours=8)
    ins("timesheet_marks", employee_id=e1, work_date="2026-03-03", code="XX")
    ins("calendar_events", title="Позвонить поставщику", start_at="2026-03-20 10:00", all_day=0, color="blue", vehicle_id=v1, remind_min=30)
    # --- рейсы, наряды, путевые листы
    ins("trips", vehicle_id=v1, trip_date="2026-03-08", route="А — Б", customer="ООО Заказчик", cargo="Щебень", cargo_kg=18000, doc_number="ТН-9", km=120)
    ins("jobs", job_date="2026-03-09", vehicle_id=v2, customer="Заказчик", address="ул. Ленина 5", task="Копка траншеи", time_from="08:30", state="done", arrived_at="2026-03-09 08:40", finished_at="2026-03-09 17:00")
    ins("waybills", number="15", issue_date="2026-03-10", valid_from="2026-03-10", valid_to="2026-03-10", status="closed", org_id=o1, vehicle_id=v1, driver_id=e1, route="А — Б", customer="ООО Заказчик",
        odo_out=2100, odo_in=2220, fuel_out=50, fuel_issued=100, fuel_in=70)
    ins("waybills", number="16", issue_date="2026-03-11", valid_from="2026-03-11", valid_to="2026-03-11", status="issued", vehicle_id=v1, driver_id=None, route="Без водителя")
    ins("waybills", number="17", issue_date="2026-03-12", valid_from="2026-03-12", valid_to="2026-03-12", status="closed", vehicle_id=v1, driver_id=e1, odo_out=2300, odo_in=2200)
    # --- обращения и счета
    tk = ins("tickets", vehicle_id=v1, author_name="Ершов", text="Стучит подвеска\nслева спереди", priority="urgent", state="parts", created_at="2026-03-12 09:00:00", updated_at="2026-03-12 09:00:00")
    ins("ticket_parts", ticket_id=tk, name="Стойка стабилизатора", article="ST-1", qty=2, unit="шт")
    ins("ticket_comments", ticket_id=tk, author_name="Орлов", text="Заказал детали", created_at="2026-03-12 12:00:00")
    ins("tickets", vehicle_id=v2, text="Течёт гидравлика", priority="stopped", state="done", closed_at="2026-03-20 10:00", created_at="2026-03-13 09:00:00", updated_at="2026-03-13 09:00:00")
    b1 = ins("repair_bills", kind="uploaded", number="СЧ-100", bill_date="2026-03-14", supplier_id=c2, supplier_name="Поставщик Запчастей", vehicle_id=v1, ticket_id=tk, vat_pct=20,
             total_kop=360000, vat_kop=60000, status="unpaid")
    ins("repair_bill_items", bill_id=b1, pos=1, name="Стойка стабилизатора", article="ST-1", qty=2, unit="шт", price_kop=150000, sum_kop=300000, vat_kop=50000)
    # --- то, что не переносится
    ins("users", login="boss", display_name="Ершов Н. И.", role="admin", pw_hash="x")
    ins("users", login="drv", display_name="Орлов", role="driver", pw_hash="x", active=0)
    ins("attachments", owner="ticket", owner_id=tk, filename="a.jpg", stored="abc.jpg", mime="image/jpeg", size=10)
    ins("announcements", title="Объявление", body="Текст")
    con.commit()
    con.close()
    return path
