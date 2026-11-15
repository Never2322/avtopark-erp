# -*- coding: utf-8 -*-
"""«Сегодня» считается по часовому поясу организации, а не по времени сервера (UTC).

Из-за этого было так: после полуночи по Новосибирску (с 00:00 до 07:00) форма записывала сотруднику «работает с 16 ноября»,
а сервер ещё считал 15-е и отвечал «Сотрудник не работает в юрлице этой машины».
Чтобы проверка не зависела от времени запуска, берутся два пояса: самый «ранний» (+14) и самый «поздний» (−12).
В любой момент сутки хотя бы в одном из них отличаются от суток по UTC.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.common import org_today
from app.config import get_settings

ZONES = ("Pacific/Kiritimati", "Etc/GMT+12")


@pytest.mark.parametrize("zone", ZONES)
def test_org_today_follows_org_timezone(monkeypatch, zone):
    monkeypatch.setattr(get_settings(), "org_timezone", zone)
    assert org_today() == dt.datetime.now(ZoneInfo(zone)).date()


@pytest.mark.parametrize("zone", ZONES)
def test_driver_started_today_by_org_calendar_can_be_assigned(make_client, world, monkeypatch, zone):
    monkeypatch.setattr(get_settings(), "org_timezone", zone)
    c = make_client("admin")
    today = org_today()
    r = c.post("/employees", {"last_name": "Ночной", "first_name": zone[-4:], "is_driver": True,
                              "assignments": [{"legal_entity_id": world.A, "started_on": today.isoformat()}]})
    assert r.status_code == 201, r.text
    got = c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": r.json()["id"], "role": "backup"})
    assert got.status_code == 201, got.text
    assert got.json()["started_on"] == today.isoformat()            # дата закрепления по умолчанию тоже «сегодня» организации


@pytest.mark.parametrize("zone", ZONES)
def test_driver_working_until_today_by_org_calendar_still_counts(make_client, world, monkeypatch, zone):
    monkeypatch.setattr(get_settings(), "org_timezone", zone)
    c = make_client("admin")
    today = org_today()
    r = c.post("/employees", {"last_name": "Последний", "first_name": zone[-4:], "is_driver": True,
                              "assignments": [{"legal_entity_id": world.A, "ended_on": today.isoformat()}]})
    assert r.status_code == 201, r.text
    assert c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": r.json()["id"], "role": "backup"}).status_code == 201


def test_tomorrow_by_org_calendar_is_not_yet_working(make_client, world):
    c = make_client("admin")
    r = c.post("/employees", {"last_name": "Завтрашний", "is_driver": True,
                              "assignments": [{"legal_entity_id": world.A, "started_on": (org_today() + dt.timedelta(days=1)).isoformat()}]})
    assert r.status_code == 201, r.text
    bad = c.post(f"/vehicles/{world.va3}/assignments", {"employee_id": r.json()["id"], "role": "backup"})
    assert bad.status_code == 422 and bad.json()["code"] == "wrong_entity"
