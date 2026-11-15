# -*- coding: utf-8 -*-
"""Кнопка «Снять» в карточке машины: водитель перестаёт быть закреплённым сразу, а не «до конца дня».

«Последний день» включается в срок (ended_on = сегодня значит «работает до конца сегодняшнего дня»), поэтому «Снять»
без даты ставит последним днём вчера. Раньше ставилось сегодня: закрепление оставалось активным, а второе нажатие
отвечало «Закрепление уже снято».
"""
import datetime as dt

from app.common import org_today
from tests.test_driver_visibility import new_employee


def items(c, vid):
    return c.get(f"/vehicles/{vid}/assignments").json()["items"]


def test_unassign_takes_effect_immediately(make_client, world):
    c = make_client("admin")
    eid = new_employee(c, "Снимаемый", world.A)
    started = org_today() - dt.timedelta(days=10)
    resp = c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": eid, "started_on": started.isoformat()})
    assert resp.status_code == 201, resp.text
    aid = resp.json()["id"]
    assert items(c, world.va3)[0]["active"] is True

    r = c.post(f"/vehicles/{world.va3}/assignments/{aid}/end", {})
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert r.json()["ended_on"] == (org_today() - dt.timedelta(days=1)).isoformat()
    assert [a["active"] for a in items(c, world.va3)] == [False]          # в списке закрепление уже не действует
    assert c.post(f"/vehicles/{world.va3}/assignments/{aid}/end", {}).json()["code"] == "already_ended"
    # место освободилось: можно закрепить другого основного водителя
    other = new_employee(c, "Заменяющий", world.A)
    assert c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": other}).status_code == 201


def test_unassign_on_the_day_of_assigning_removes_the_row(make_client, world):
    c = make_client("admin")
    eid = new_employee(c, "Ошибочный", world.A)
    r = c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": eid})        # закрепили «с сегодня»
    assert r.status_code == 201
    end = c.post(f"/vehicles/{world.va3}/assignments/{r.json()['id']}/end", {})
    assert end.status_code == 200 and end.json()["active"] is False
    assert items(c, world.va3) == []                                              # ни одного полного дня не было: записи нет
    assert c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": eid}).status_code == 201


def test_explicit_last_day_today_keeps_driver_until_end_of_day(make_client, world):
    c = make_client("admin")
    eid = new_employee(c, "Дослужит", world.A)
    started = org_today() - dt.timedelta(days=5)
    aid = c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": eid, "started_on": started.isoformat()}).json()["id"]
    r = c.post(f"/vehicles/{world.va3}/assignments/{aid}/end", {"ended_on": org_today().isoformat()})
    assert r.status_code == 200 and r.json()["active"] is True                    # последний день — сегодня, ещё закреплён
    # повторное «Снять» без даты укорачивает срок до вчера (так чинятся закрепления, снятые старой кнопкой)
    again = c.post(f"/vehicles/{world.va3}/assignments/{aid}/end", {})
    assert again.status_code == 200 and again.json()["active"] is False
    assert c.post(f"/vehicles/{world.va3}/assignments/{aid}/end", {"ended_on": (started - dt.timedelta(days=1)).isoformat()}).status_code in (409, 422)
