# -*- coding: utf-8 -*-
"""Журнал заправок: в форме обязательны только дата, техника и литры; остальное «Дополнительно»; фото чека прикрепляется сразу."""
from app.models import FuelFill  # noqa: F401  (проверка, что модель на месте)
from tests.test_dossier import png
from tests.test_entries import day

REQUIRED = {"fill_date", "vehicle_id", "liters"}
OPTIONAL = {"driver_id", "amount", "odometer", "full_tank", "station", "note"}


def test_meta_has_three_main_fields_and_optional_block(world, make_client):
    d = make_client("disp_a")
    meta = d.get("/fuel_fills/meta").json()
    by = {f["name"]: f for f in meta["fields"]}
    assert {n for n, f in by.items() if not f["more"]} == REQUIRED
    assert {n for n, f in by.items() if f["more"]} == OPTIONAL
    assert all(by[n]["required"] for n in REQUIRED)
    assert meta["photos"] == {"kind": "fuel", "label": "Фото чека"}
    # у других разделов фото при добавлении нет и «Дополнительно» не появляется
    other = d.get("/fines/meta").json()
    assert other["photos"] is None and not any(f["more"] for f in other["fields"])


def test_photo_option_needs_edit_right(world, make_client):
    mech = make_client("mech_a")                             # механик заправки видит, но не правит
    meta = mech.get("/fuel_fills/meta").json()
    assert meta["can"]["edit"] is False and meta["photos"] is None


def test_create_with_date_vehicle_liters_only_then_attach_photo(world, make_client):
    d = make_client("disp_a")
    r = d.post("/fuel_fills", {"fill_date": day(), "vehicle_id": world.va1, "liters": "55"})
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["liters"] == 55 or str(row["liters"]).startswith("55")
    assert float(row["amount"] or 0) == 0 and row["odometer"] is None
    assert row["driver_id"] == world.d1                      # водитель подставился по закреплению машины
    # фото чека уходит отдельным запросом сразу после сохранения записи (так делает форма)
    up = d.call("POST", f"/entries/fuel/{row['id']}/photos", files=[("files", ("check.png", png(), "image/png"))])
    assert up.status_code == 200, up.text
    listed = d.get("/fuel_fills").json()["items"]
    mine = next(x for x in listed if x["id"] == row["id"])
    assert mine["photos"] == 1
    # без обязательных полей запись не создаётся
    bad = d.post("/fuel_fills", {"fill_date": day(), "vehicle_id": world.va1})
    assert bad.status_code == 422
