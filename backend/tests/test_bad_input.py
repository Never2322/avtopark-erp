# -*- coding: utf-8 -*-
"""Мусор на входе: нулевой символ в адресе и в тексте даёт понятный отказ 422, а не ошибку сервера.
Найдено при проверке сайта: нулевой байт в поиске или в комментарии вызывал 500."""
from tests.test_status_and_requests import open_request


def test_nul_in_search_is_rejected(world, make_client):
    c = make_client("dir_a")
    r = c.get("/vehicles", params={"q": "a\x00b"})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"
    assert c.get("/vehicles", params={"q": "обычный поиск"}).status_code == 200


def test_nul_in_json_text_is_rejected(world, make_client):
    c = make_client("dir_a")
    rid = open_request(c, world.va1)["id"]
    r = c.post(f"/requests/{rid}/comments", {"text": "до\x00после"})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"
    # комментарий не добавился, а обычный текст принимается
    assert c.post(f"/requests/{rid}/comments", {"text": "обычный комментарий"}).status_code == 201


def test_literal_backslash_u0000_text_is_allowed(world, make_client):
    c = make_client("dir_a")
    rid = open_request(c, world.va1)["id"]
    r = c.post(f"/requests/{rid}/comments", {"text": "в тексте написано \\u0000 буквами"})
    assert r.status_code == 201, r.text


def test_nul_in_login_form_is_rejected(app):
    from fastapi.testclient import TestClient
    r = TestClient(app).post("/api/auth/login", json={"login": "a\x00", "password": "x"})
    assert r.status_code == 422 and r.json()["code"] == "bad_value"


def test_huge_number_is_an_input_error_not_a_crash(world, make_client):
    c = make_client("dir_a")
    r = c.post("/fuel_fills", {"vehicle_id": world.va1, "fill_date": "2026-11-10", "liters": "99999999999999999999999", "amount": "1"})
    assert 400 <= r.status_code < 500, r.text
