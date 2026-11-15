# -*- coding: utf-8 -*-
"""Справочники и деньги: права, юрлица, проверки значений, архив, расчётные колонки, касса."""
import datetime as dt

import pytest
from sqlalchemy import select

from app.db import session_factory
from app.models import AuditLog
from app.common import org_today

TODAY = org_today()


def d(offset: int = 0) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def fine(w, **kw):
    body = {"vehicle_id": w.va1, "fine_date": d(-10), "amount": "500", "resolution_number": "18810154", "payer": "company"}
    body.update(kw)
    return body


def err_fields(r):
    assert r.status_code == 422, r.text
    return r.json()["detail"]["fields"] if "detail" in r.json() else r.json()["fields"]


def audit_actions(prefix: str) -> list[str]:
    with session_factory()() as db:
        return [a for a in db.scalars(select(AuditLog.action).order_by(AuditLog.id)) if a.startswith(prefix)]


# ---------------------------------------------------------------- справочники

def test_counterparty_crud_unique_and_archive(world, make_client):
    c = make_client("dir_a")
    r = c.post("/counterparties", {"name": "ООО Север", "kind": "customer", "inn": "5400123456", "phone": "+7 913 000-00-00"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["can_edit"] and r.json()["can_void"]

    dup = c.post("/counterparties", {"name": "  ооо север  "})
    assert "name" in err_fields(dup)

    assert "inn" in err_fields(c.post("/counterparties", {"name": "Х", "inn": "123"}))
    assert "inn" in err_fields(c.post("/counterparties", {"name": "Х", "inn": "54001234ab"}))
    assert "email" in err_fields(c.post("/counterparties", {"name": "Х", "email": "не почта"}))
    assert "name" in err_fields(c.post("/counterparties", {"name": "   "}))
    assert "kind" in err_fields(c.post("/counterparties", {"name": "Y", "kind": "alien"}))

    r = c.patch(f"/counterparties/{cid}", {"phone": "8-800"})
    assert r.status_code == 200 and r.json()["phone"] == "8-800" and r.json()["inn"] == "5400123456"

    assert c.post(f"/counterparties/{cid}/void", {"reason": "аб"}).status_code == 422
    r = c.post(f"/counterparties/{cid}/void", {"reason": "закрылись"})
    assert r.status_code == 200 and r.json()["archived"] is True
    assert c.get("/counterparties").json()["total"] == 0
    assert c.get("/counterparties?archived=true").json()["total"] == 1
    assert c.patch(f"/counterparties/{cid}", {"phone": "1"}).status_code == 409        # в архиве не правят

    # имя освободилось; вернуть старую запись нельзя, пока есть новая с тем же именем
    assert c.post("/counterparties", {"name": "ООО Север"}).status_code == 201
    assert c.post(f"/counterparties/{cid}/restore").status_code == 409

    actions = audit_actions("counterparties.")
    assert "counterparties.create" in actions and "counterparties.update" in actions and "counterparties.void" in actions


def test_counterparties_are_shared_between_entities(world, make_client):
    a, b = make_client("dir_a"), make_client("dir_b")
    cid = a.post("/counterparties", {"name": "Общий заказчик"}).json()["id"]
    assert [x["id"] for x in b.get("/counterparties").json()["items"]] == [cid]


def test_dictionary_permissions(world, make_client):
    assert make_client("drv1").get("/counterparties").status_code == 403
    assert make_client("emp_a").get("/tariffs").status_code == 403
    mech = make_client("mech_a")
    assert mech.get("/tariffs").status_code == 200
    assert mech.post("/tariffs", {"name": "Т", "unit": "hour", "rate": "100"}).status_code == 403
    store = make_client("store_a")
    assert store.get("/counterparties").status_code == 200
    assert store.post("/counterparties", {"name": "Не мой"}).status_code == 403
    disp = make_client("disp_a")
    t = make_client("acc_a").post("/tariffs", {"name": "Самосвал, час", "unit": "hour", "rate": "1500,50", "min_amount": "3000"})
    assert t.status_code == 201 and t.json()["rate"] == 1500.5
    assert disp.patch(f"/tariffs/{t.json()['id']}", {"rate": "1"}).status_code == 403          # диспетчер тарифы только смотрит
    assert disp.post(f"/tariffs/{t.json()['id']}/void", {"reason": "тест"}).status_code == 403


def test_tariff_and_constant_validation(world, make_client):
    c = make_client("dir_a")
    assert "rate" in err_fields(c.post("/tariffs", {"name": "А", "unit": "hour", "rate": "-5"}))
    assert "rate" in err_fields(c.post("/tariffs", {"name": "А", "unit": "hour", "rate": "abc"}))
    assert "rate" in err_fields(c.post("/tariffs", {"name": "А", "unit": "hour", "rate": "9999999999999"}))
    assert "unit" in err_fields(c.post("/tariffs", {"name": "А", "unit": "parsec", "rate": "1"}))
    assert c.post("/tariffs", {"name": "А", "unit": "km", "rate": "12,345"}).json()["rate"] == 12.35      # округляем до копеек
    assert c.post("/constants", {"name": "Цена ДТ, ₽/л", "value": "68,5"}).status_code == 201
    assert "name" in err_fields(c.post("/constants", {"name": "цена дт, ₽/л", "value": "1"}))
    assert "value" in err_fields(c.post("/constants", {"name": "Пусто", "value": ""}))


# ---------------------------------------------------------------- штрафы

def test_fine_create_scope_and_isolation(world, make_client):
    w = world
    a, b = make_client("dir_a"), make_client("dir_b")
    r = a.post("/fines", fine(w, driver_id=w.d1))
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["legal_entity_id"] == w.A and row["vehicle_id_label"].startswith("А001АА54") and row["driver_id_label"].startswith("Первый")
    fid = row["id"]

    # чужое юрлицо: не видно, не правится, не добавляется
    assert b.get("/fines").json()["total"] == 0
    assert b.patch(f"/fines/{fid}", {"amount": "1"}).status_code == 404
    assert b.post(f"/fines/{fid}/void", {"reason": "тест тест"}).status_code == 404
    assert "vehicle_id" in err_fields(b.post("/fines", fine(w)))                       # техника юрлица A
    assert "vehicle_id" in err_fields(a.post("/fines", fine(w, vehicle_id=w.vb1)))
    assert "vehicle_id" in err_fields(a.post("/fines", fine(w, vehicle_id=999999)))
    assert "driver_id" in err_fields(a.post("/fines", fine(w, driver_id=w.d3)))        # водитель из юрлица B
    assert a.get(f"/fines?legal_entity_id={w.B}").status_code == 403


def test_fine_validation_and_status(world, make_client):
    w = world
    c = make_client("acc_a")
    assert "amount" in err_fields(c.post("/fines", fine(w, amount="-1")))
    assert "amount" in err_fields(c.post("/fines", fine(w, amount="сто")))
    assert "amount" in err_fields(c.post("/fines", fine(w, amount="")))
    assert "fine_date" in err_fields(c.post("/fines", fine(w, fine_date=d(30))))
    assert "fine_date" in err_fields(c.post("/fines", fine(w, fine_date="1999-01-01")))
    assert "due_date" in err_fields(c.post("/fines", fine(w, due_date=d(-20))))
    assert "discount_until" in err_fields(c.post("/fines", fine(w, discount_until=d(10), due_date=d(5))))
    assert "paid_at" in err_fields(c.post("/fines", fine(w, paid_at=d(-30))))
    assert "paid_at" in err_fields(c.post("/fines", fine(w, paid_at=d(40))))
    assert "payer" in err_fields(c.post("/fines", fine(w, payer="кто-то")))
    assert "vehicle_id" in err_fields(c.post("/fines", {"fine_date": d(-1), "amount": "1"}))

    def state(**kw):
        r = c.post("/fines", fine(w, **kw))
        assert r.status_code == 201, r.text
        return r.json()["state"]["text"]

    assert state(paid_at=d(-1)) == "Оплачен"
    assert state(due_date=d(-1)) == "Просрочен"
    assert state(due_date=d(0)) == "Последний день"
    assert state(due_date=d(5)) == "Скоро срок"
    assert state(due_date=d(60)) == "Не оплачен"
    assert state() == "Не оплачен"
    assert state(discount_until=d(2), due_date=d(60)) == "Скоро срок"           # скидка ещё действует: срок — её конец
    assert state(discount_until=d(-2), due_date=d(60)) == "Не оплачен"          # скидка кончилась: общий срок


def test_fine_state_filter_totals_search_and_export(world, make_client):
    w = world
    c = make_client("dir_a")
    c.post("/fines", fine(w, amount="1000", paid_at=d(-1), resolution_number="PAID-1"))
    c.post("/fines", fine(w, amount="2500,50", due_date=d(-1), resolution_number="LATE-1"))
    c.post("/fines", fine(w, vehicle_id=w.va2, amount="300", resolution_number="OPEN-1", fine_date=d(-3)))
    full = c.get("/fines").json()
    assert full["total"] == 3 and full["totals"]["amount"] == 3800.5
    assert {x["resolution_number"] for x in c.get("/fines?state=paid").json()["items"]} == {"PAID-1"}
    assert {x["resolution_number"] for x in c.get("/fines?state=overdue").json()["items"]} == {"LATE-1"}
    assert {x["resolution_number"] for x in c.get("/fines?state=unpaid").json()["items"]} == {"LATE-1", "OPEN-1"}
    assert c.get("/fines?state=bogus").status_code == 422
    assert c.get("/fines?q=А002АА").json()["total"] == 1                                  # поиск по госномеру
    assert c.get("/fines?q=late").json()["total"] == 1
    assert c.get("/fines?payer=company").json()["total"] == 3 and c.get("/fines?payer=driver").json()["total"] == 0
    assert c.get("/fines?payer=nonsense").status_code == 422
    assert c.get(f"/fines?date_from={d(-5)}").json()["total"] == 1
    assert c.get("/fines?date_from=вчера").status_code == 422
    assert c.get("/fines?page_size=2").json()["items"].__len__() == 2

    r = c.get("/fines/export.csv")
    assert r.status_code == 200 and r.content.startswith("﻿".encode())
    lines = r.text.strip().split("\r\n")
    assert len(lines) == 4 and "Сумма" in lines[0] and "2500,50" in "\n".join(lines)
    assert "export" in " ".join(audit_actions("fines."))


def test_fine_roles(world, make_client):
    w = world
    disp, mech, acc = make_client("disp_a"), make_client("mech_a"), make_client("acc_a")
    r = disp.post("/fines", fine(w))
    assert r.status_code == 201 and r.json()["can_edit"] is True and r.json()["can_void"] is False
    fid = r.json()["id"]
    assert disp.post(f"/fines/{fid}/void", {"reason": "ошибка ввода"}).status_code == 403
    assert mech.get("/fines").status_code == 200 and mech.post("/fines", fine(w)).status_code == 403
    assert mech.patch(f"/fines/{fid}", {"amount": "1"}).status_code == 403
    assert make_client("drv1").get("/fines").status_code == 403
    assert acc.post(f"/fines/{fid}/void", {"reason": "ошибка ввода"}).status_code == 200
    assert acc.post(f"/fines/{fid}/restore").status_code == 200
    assert acc.post(f"/fines/{fid}/restore").status_code == 409


def test_fine_update_keeps_other_fields_and_logs_changes(world, make_client):
    w = world
    c = make_client("dir_a")
    fid = c.post("/fines", fine(w, amount="700", note="первая")).json()["id"]
    r = c.patch(f"/fines/{fid}", {"amount": "900", "paid_at": d(-1)})
    assert r.status_code == 200 and r.json()["amount"] == 900 and r.json()["note"] == "первая" and r.json()["state"]["text"] == "Оплачен"
    r = c.patch(f"/fines/{fid}", {"amount": "abc"})
    assert "amount" in err_fields(r)
    assert c.get("/fines").json()["items"][0]["amount"] == 900                              # неверное значение ничего не стёрло
    assert "vehicle_id" in err_fields(c.patch(f"/fines/{fid}", {"vehicle_id": w.vb1}))      # техника другого юрлица
    with session_factory()() as db:
        upd = [a for a in db.scalars(select(AuditLog).where(AuditLog.action == "fines.update"))][0]
        assert upd.details["changes"]["amount"] == [700.0, 900.0]


# ---------------------------------------------------------------- топливные карты

def test_fuel_cards(world, make_client):
    w = world
    c = make_client("dir_a")
    r = c.post("/fuel_cards", {"legal_entity_id": w.A, "number": "7005 0001", "provider": "Лукойл", "vehicle_id": w.va1,
                               "limit_month_l": "800,5", "valid_until": d(10)})
    assert r.status_code == 201, r.text
    assert r.json()["state"]["text"] == "Скоро кончится" and r.json()["limit_month_l"] == 800.5
    assert "number" in err_fields(c.post("/fuel_cards", {"legal_entity_id": w.A, "number": "7005 0001"}))
    assert "vehicle_id" in err_fields(c.post("/fuel_cards", {"legal_entity_id": w.A, "number": "X1", "vehicle_id": w.vb1}))
    assert "limit_month_l" in err_fields(c.post("/fuel_cards", {"legal_entity_id": w.A, "number": "X2", "limit_month_l": "-1"}))
    assert "legal_entity_id" in err_fields(c.post("/fuel_cards", {"number": "X3"}))
    assert c.post("/fuel_cards", {"legal_entity_id": w.B, "number": "X4"}).status_code == 403
    assert c.post("/fuel_cards", {"legal_entity_id": w.A, "number": "Old", "valid_until": d(-3)}).json()["state"]["text"] == "Срок вышел"
    assert c.get("/fuel_cards?state=expired").json()["total"] == 1
    assert c.get("/fuel_cards?state=expiring").json()["total"] == 1
    assert c.get("/fuel_cards?q=а001аа").json()["total"] == 1
    assert make_client("dir_b").get("/fuel_cards").json()["total"] == 0
    assert c.get("/fuel_cards/meta").json()["suggestions"]["provider"] == ["Лукойл"]


# ---------------------------------------------------------------- аренда

def rental(w, **kw):
    body = {"vehicle_id": w.va1, "direction": "out", "start_date": d(-10), "end_date": d(-5), "rate": "1000"}
    body.update(kw)
    return body


def test_rentals_days_total_and_overlap(world, make_client):
    w = world
    c = make_client("dir_a")
    cp = make_client("dir_a").post("/counterparties", {"name": "Арендатор"}).json()["id"]
    r = c.post("/rentals", rental(w, counterparty_id=cp))
    assert r.status_code == 201, r.text
    assert r.json()["days"] == 6 and r.json()["total"] == 6000 and r.json()["counterparty_id_label"] == "Арендатор"
    first = r.json()["id"]

    assert "start_date" in err_fields(c.post("/rentals", rental(w, start_date=d(-7), end_date=d(-2))))     # пересечение
    assert "start_date" in err_fields(c.post("/rentals", rental(w, start_date=d(-5), end_date=None)))      # без конца: пересекается на границе
    assert c.post("/rentals", rental(w, direction="in", start_date=d(-7), end_date=d(-2))).status_code == 201   # другое направление
    assert c.post("/rentals", rental(w, vehicle_id=w.va2, start_date=d(-7), end_date=d(-2))).status_code == 201  # другая техника
    assert c.post("/rentals", rental(w, start_date=d(-4), end_date=d(-1))).status_code == 201             # встык, но после
    assert "end_date" in err_fields(c.post("/rentals", rental(w, start_date=d(0), end_date=d(-3))))
    assert "end_date" in err_fields(c.post("/rentals", rental(w, vehicle_id=w.va3, end_date=None, finished=True)))
    assert "rate" in err_fields(c.post("/rentals", rental(w, vehicle_id=w.va3, rate="-1")))
    assert "counterparty_id" in err_fields(c.post("/rentals", rental(w, vehicle_id=w.va3, counterparty_id=424242)))

    # архивная аренда дни не занимает
    assert c.post(f"/rentals/{first}/void", {"reason": "ошибка"}).status_code == 200
    assert c.post("/rentals", rental(w, start_date=d(-9), end_date=d(-6))).status_code == 201
    back = c.post(f"/rentals/{first}/restore")                                       # теперь она пересекается с новой
    assert back.status_code == 409 and "пересека" in back.json()["message"]

    # без даты окончания считаем до сегодня; итог по странице
    r = c.post("/rentals", rental(w, vehicle_id=w.va3, start_date=d(-2), end_date=None, rate="100"))
    assert r.json()["days"] == 3 and r.json()["total"] == 300 and r.json()["state"]["text"] == "Идёт"
    assert c.get("/rentals").json()["totals"]["total"] > 0
    assert make_client("dir_b").get("/rentals").json()["total"] == 0


def test_rental_edit_cannot_create_overlap(world, make_client):
    w = world
    c = make_client("dir_a")
    a = c.post("/rentals", rental(w, start_date=d(-20), end_date=d(-15))).json()["id"]
    c.post("/rentals", rental(w, start_date=d(-10), end_date=d(-5)))
    assert "start_date" in err_fields(c.patch(f"/rentals/{a}", {"end_date": d(-8)}))
    assert c.patch(f"/rentals/{a}", {"end_date": d(-12), "finished": True}).json()["state"]["text"] == "Закончена"
    assert c.get("/rentals?state=finished").json()["total"] == 1 and c.get("/rentals?state=active").json()["total"] == 1


# ---------------------------------------------------------------- расходы и выплаты

def test_expenses_and_payments(world, make_client):
    w = world
    acc = make_client("acc_a")
    r = acc.post("/expenses", {"legal_entity_id": w.A, "op_date": d(-1), "category": "Мойка", "amount": "1200", "description": "три машины"})
    assert r.status_code == 201, r.text
    acc.post("/expenses", {"legal_entity_id": w.A, "op_date": d(-2), "category": "Связь", "amount": "300,40"})
    assert acc.get("/expenses").json()["totals"]["amount"] == 1500.4
    assert "amount" in err_fields(acc.post("/expenses", {"legal_entity_id": w.A, "op_date": d(-1), "category": "Х", "amount": "0"}))
    assert "category" in err_fields(acc.post("/expenses", {"legal_entity_id": w.A, "op_date": d(-1), "category": "", "amount": "5"}))
    assert "op_date" in err_fields(acc.post("/expenses", {"legal_entity_id": w.A, "op_date": d(20), "category": "Х", "amount": "5"}))
    assert acc.post("/expenses", {"legal_entity_id": w.B, "op_date": d(-1), "category": "Х", "amount": "5"}).status_code == 403
    assert acc.get("/expenses?q=мойка").json()["total"] == 1
    assert acc.get("/expenses/meta").json()["suggestions"]["category"][:1] in (["Мойка"], ["Связь"])
    assert make_client("dir_b").get("/expenses").json()["total"] == 0
    assert make_client("emp_a").get("/expenses").status_code == 403

    p = acc.post("/payments", {"legal_entity_id": w.A, "employee_id": w.d1, "pay_date": d(-1), "kind": "advance", "amount": "10000"})
    assert p.status_code == 201, p.text
    assert p.json()["employee_id_label"].startswith("Первый")
    assert "employee_id" in err_fields(acc.post("/payments", {"legal_entity_id": w.A, "employee_id": w.d3, "pay_date": d(-1), "kind": "salary", "amount": "1"}))
    assert "employee_id" in err_fields(acc.post("/payments", {"legal_entity_id": w.A, "employee_id": 777777, "pay_date": d(-1), "kind": "salary", "amount": "1"}))
    assert "kind" in err_fields(acc.post("/payments", {"legal_entity_id": w.A, "employee_id": w.d1, "pay_date": d(-1), "kind": "подарок", "amount": "1"}))
    assert acc.get("/payments?q=первый").json()["total"] == 1                                # поиск по имени сотрудника
    assert acc.get("/payments?kind=salary").json()["total"] == 0
    assert make_client("disp_a").get("/payments").status_code == 403


# ---------------------------------------------------------------- счета и касса

def account(c, w, name="Касса офиса", opening="1000", entity=None):
    r = c.post("/cash_accounts", {"legal_entity_id": entity or w.A, "name": name, "kind": "cash", "opening": opening})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def op(c, w, **kw):
    body = {"legal_entity_id": w.A, "op_date": d(-1), "kind": "in", "amount": "100"}
    body.update(kw)
    return c.post("/cash", body)


def balance(c, acc_id):
    return next(x["balance"] for x in c.get("/cash_accounts").json()["items"] if x["id"] == acc_id)


def test_cash_balances_numbers_and_transfers(world, make_client):
    w = world
    c = make_client("acc_a")
    cash, bank = account(c, w), account(c, w, "Расчётный счёт", "5000")
    assert "name" in err_fields(c.post("/cash_accounts", {"legal_entity_id": w.A, "name": " касса ОФИСА ", "opening": "0"}))
    assert "opening" in err_fields(c.post("/cash_accounts", {"legal_entity_id": w.A, "name": "Минус", "opening": "-5"}))

    r1 = op(c, w, account_id=cash, amount="500", category="Выручка")
    r2 = op(c, w, account_id=cash, amount="200,50", kind="out", category="Мойка")
    r3 = op(c, w, account_id=cash, amount="300", kind="in")
    assert (r1.json()["doc_no"], r2.json()["doc_no"], r3.json()["doc_no"]) == (f"ПКО-1/{TODAY.year}", f"РКО-1/{TODAY.year}", f"ПКО-2/{TODAY.year}")
    assert balance(c, cash) == 1000 + 500 - 200.5 + 300

    t = op(c, w, kind="transfer", account_id=cash, to_account_id=bank, amount="600")
    assert t.status_code == 201 and t.json()["doc_no"] == f"ПЕР-1/{TODAY.year}" and t.json()["to_account_id_label"] == "Расчётный счёт"
    assert balance(c, cash) == 1599.5 - 600 and balance(c, bank) == 5600

    # отмена операции возвращает остаток; номер не переиспользуется
    assert c.post(f"/cash/{r3.json()['id']}/void", {"reason": "записали дважды"}).status_code == 200
    assert balance(c, cash) == 1299.5 - 600
    assert op(c, w, account_id=cash, amount="1").json()["doc_no"] == f"ПКО-3/{TODAY.year}"
    assert c.post(f"/cash/{r3.json()['id']}/restore").status_code == 200
    assert balance(c, cash) == 1299.5 - 600 + 300 + 1

    assert c.get("/cash_accounts").json()["totals"]["balance"] > 0
    assert c.get("/cash?kind=transfer").json()["total"] == 1
    assert c.get("/cash?q=мойка").json()["total"] == 1
    assert c.get(f"/cash?date_from={d(0)}").json()["total"] == 0
    assert c.get("/cash/export.csv").text.count("\r\n") == 6


def test_cash_validation(world, make_client):
    w = world
    c = make_client("acc_a")
    cash, bank = account(c, w), account(c, w, "Банк", "0")
    other = account(make_client("dir_b"), w, "Касса B", "0", entity=w.B)

    assert "to_account_id" in err_fields(op(c, w, kind="transfer", account_id=cash))
    assert "to_account_id" in err_fields(op(c, w, kind="transfer", account_id=cash, to_account_id=cash))
    assert "account_id" in err_fields(op(c, w, account_id=other))                           # счёт чужого юрлица
    assert "to_account_id" in err_fields(op(c, w, kind="transfer", account_id=cash, to_account_id=other))
    assert "account_id" in err_fields(op(c, w))                                              # счёт не выбран
    assert "amount" in err_fields(op(c, w, account_id=cash, amount="0"))
    assert "amount" in err_fields(op(c, w, account_id=cash, amount="1e3"))
    assert "kind" in err_fields(op(c, w, account_id=cash, kind="подарок"))
    assert "op_date" in err_fields(op(c, w, account_id=cash, op_date=d(10)))
    assert "employee_id" in err_fields(op(c, w, account_id=cash, kind="out", employee_id=w.d3))
    assert "counterparty_id" in err_fields(op(c, w, account_id=cash, counterparty_id=5555))
    # для прихода «на счёт» не нужен и не сохраняется
    ok = op(c, w, account_id=cash, to_account_id=bank)
    assert ok.status_code == 201 and ok.json()["to_account_id"] is None

    # вид и год номера после добавления не меняются
    oid = ok.json()["id"]
    assert "kind" in err_fields(c.patch(f"/cash/{oid}", {"kind": "out"}))
    assert "op_date" in err_fields(c.patch(f"/cash/{oid}", {"op_date": f"{TODAY.year - 1}-12-31"}))
    assert c.patch(f"/cash/{oid}", {"amount": "250", "note": "исправили"}).json()["amount"] == 250

    # счёт с операциями можно убрать в архив, но выбрать его в новой операции уже нельзя
    assert c.post(f"/cash_accounts/{bank}/void", {"reason": "закрыт"}).status_code == 200
    assert "to_account_id" in err_fields(op(c, w, kind="transfer", account_id=cash, to_account_id=bank))


def test_cash_overdraw_warning(world, make_client):
    w = world
    c = make_client("acc_a")
    cash = account(c, w, opening="100")
    r = op(c, w, account_id=cash, kind="out", amount="150")
    assert r.status_code == 201 and len(r.json()["warnings"]) == 1 and "отрицательным" in r.json()["warnings"][0]
    assert op(c, w, account_id=cash, kind="in", amount="1000").json()["warnings"] == []


def test_cash_isolation_and_permissions(world, make_client):
    w = world
    a, b = make_client("acc_a"), make_client("dir_b")
    cash = account(a, w)
    r = op(a, w, account_id=cash)
    assert b.get("/cash").json()["total"] == 0 and b.get("/cash_accounts").json()["total"] == 0
    assert b.patch(f"/cash/{r.json()['id']}", {"amount": "1"}).status_code == 404
    assert op(b, w, legal_entity_id=w.B, account_id=cash).status_code == 422             # чужой счёт
    for login in ("disp_a", "mech_a", "drv1", "emp_a", "store_a"):
        assert make_client(login).get("/cash").status_code == 403, login
        assert make_client(login).get("/cash_accounts").status_code == 403, login
    # администратор видит всё
    adm = make_client("admin")
    assert adm.get("/cash").json()["total"] == 1


# ---------------------------------------------------------------- выбор значений в полях и описание разделов

def test_options_are_scoped(world, make_client):
    w = world
    acc = make_client("acc_a")
    plates = {x["label"].split(" ")[0] for x in acc.get("/options/vehicle").json()["items"]}
    assert plates == {"А001АА54", "А002АА54", "А003АА54"}                                  # только юрлицо A, хотя vehicles.view у бухгалтера может быть шире
    emps = {x["label"] for x in acc.get("/options/employee").json()["items"]}
    assert all("Третий" not in e for e in emps) and any("Первый" in e for e in emps)
    assert acc.get(f"/options/vehicle?legal_entity_id={w.B}").status_code == 403
    assert acc.get("/options/vehicle?q=А002").json()["items"].__len__() == 1
    assert acc.get("/options/counterparty").status_code == 200
    assert acc.get("/options/nothing").status_code == 404
    assert make_client("drv1").get("/options/counterparty").status_code == 403
    adm = make_client("admin")
    assert len(adm.get("/options/vehicle").json()["items"]) == 4
    assert [x["label"] for x in adm.get(f"/options/vehicle?legal_entity_id={w.B}").json()["items"]][0].startswith("В001ВВ54")


@pytest.mark.parametrize("key", ["counterparties", "tariffs", "constants", "fuel_cards", "fines", "rentals", "expenses", "payments", "cash_accounts", "cash"])
def test_meta_describes_every_section(world, make_client, key):
    c = make_client("admin")
    m = c.get(f"/{key}/meta")
    assert m.status_code == 200, m.text
    body = m.json()
    assert body["key"] == key and body["title"] and body["fields"] and body["can"]["create"] is True
    assert all({"name", "label", "kind"} <= set(f) for f in body["fields"])
    if body["scope"] == "entity":
        assert {e["id"] for e in body["entities"]} == {world.A, world.B}
    assert c.get(f"/{key}").json()["total"] == 0
    assert make_client("drv1").get(f"/{key}/meta").status_code == 403
