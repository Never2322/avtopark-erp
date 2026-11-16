# -*- coding: utf-8 -*-
"""Колонка «Страховка до» в списке техники: дата окончания ОСАГО и КАСКО из личного дела машины."""
import datetime as dt

from tests.test_dossier import add_vehicle_doc


def row(c, vid):
    items = c.get("/vehicles", params={"page_size": 100}).json()["items"]
    return next(v for v in items if v["id"] == vid)


def test_no_insurance_entered(make_client, world):
    c = make_client("admin")
    assert row(c, world.va1)["insurance"] is None


def test_shows_both_policies_earliest_first(make_client, world):
    c = make_client("admin")
    assert add_vehicle_doc(c, world.va1, "kasko", valid_until="2027-06-01").status_code == 201
    assert add_vehicle_doc(c, world.va1, "osago", valid_until="2027-01-01").status_code == 201
    ins = row(c, world.va1)["insurance"]
    assert [(i["kind"], i["label"], i["until"]) for i in ins] == [("osago", "ОСАГО", "2027-01-01"), ("kasko", "КАСКО", "2027-06-01")]


def test_renewed_policy_replaces_old_one(make_client, world):
    c = make_client("admin")
    assert add_vehicle_doc(c, world.va1, "osago", valid_until="2025-03-01").status_code == 201      # прошлый полис остался в деле
    assert add_vehicle_doc(c, world.va1, "osago", valid_until="2027-03-01").status_code == 201      # новый
    ins = row(c, world.va1)["insurance"]
    assert [(i["kind"], i["until"]) for i in ins] == [("osago", "2027-03-01")]                     # просроченным старый не показывается


def test_archived_other_kinds_and_no_date_are_ignored(make_client, world):
    c = make_client("admin")
    old = add_vehicle_doc(c, world.va1, "osago", valid_until="2027-02-01").json()
    assert c.post(f"/dossier/docs/{old['id']}/archive", {"reason": "заменён"}).status_code == 200
    assert add_vehicle_doc(c, world.va1, "sts", valid_until="2030-01-01").status_code == 201        # не страховка
    assert add_vehicle_doc(c, world.va1, "kasko").status_code == 201                                # без даты окончания
    assert row(c, world.va1)["insurance"] is None


def test_expired_policy_is_shown_as_is(make_client, world):
    c = make_client("admin")
    past = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    assert add_vehicle_doc(c, world.va1, "osago", valid_until=past).status_code == 201
    assert row(c, world.va1)["insurance"][0]["until"] == past


def test_hidden_from_users_without_access_to_vehicle_files(make_client, world):
    admin = make_client("admin")
    assert add_vehicle_doc(admin, world.va1, "osago", valid_until="2027-01-01").status_code == 201
    drv = make_client("drv1")
    items = drv.get("/vehicles").json()["items"]
    mine = next(v for v in items if v["id"] == world.va1)
    assert mine["insurance"] is None
