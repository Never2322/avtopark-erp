# -*- coding: utf-8 -*-
"""Карта 2ГИС: настройки для страницы, права, поиск точки по адресу (город к адресу, запоминание, «не найдено», сбои 2ГИС).
Настоящий 2ГИС в тестах не вызывается: ответы подставляются вместо `fetch_json`."""
import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from app.config import get_settings
from app.routers import map_


@pytest.fixture(autouse=True)
def clean_cache():
    map_._cache.clear()
    yield
    map_._cache.clear()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "twogis_key", "test-key-123")


class Fake:
    """Подмена fetch_json: помнит адреса запросов и отдаёт заготовленный ответ."""

    def __init__(self, reply):
        self.reply, self.urls = reply, []

    def __call__(self, url):
        self.urls.append(url)
        r = self.reply(url) if callable(self.reply) else self.reply
        if isinstance(r, Exception):
            raise r
        return r

    def queries(self):
        return [urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["q"][0] for u in self.urls]


def found(lon=82.93, lat=55.04, name="Новосибирск, Ленина, 5"):
    return {"meta": {"code": 200}, "result": {"items": [{"full_name": name, "point": {"lon": lon, "lat": lat}}], "total": 1}}


NOT_FOUND = {"meta": {"code": 404, "error": {"message": "Results not found"}}}


def geocode(c, *addresses):
    return c.post("/map/geocode", {"addresses": list(addresses)})


# ---------------------------------------------------------------- настройки и права

def test_config_without_key(make_client, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "twogis_key", "")
    r = make_client("dir_a").get("/map/config")
    assert r.status_code == 200
    j = r.json()
    assert j["enabled"] is False and j["key"] == "" and j["city"] == "Новосибирск" and j["zoom"] == 11 and len(j["center"]) == 2


def test_config_with_key(make_client, world, with_key, monkeypatch):
    monkeypatch.setattr(get_settings(), "map_lon", 37.6)
    monkeypatch.setattr(get_settings(), "map_lat", 55.7)
    j = make_client("dir_a").get("/map/config").json()
    assert j["enabled"] is True and j["key"] == "test-key-123" and j["center"] == [37.6, 55.7]


def test_rights(make_client, world, with_key, monkeypatch):
    fake = Fake(found())
    monkeypatch.setattr(map_, "fetch_json", fake)
    for login in ("dir_a", "disp_a", "mech_a", "drv1", "admin"):
        c = make_client(login)
        assert c.get("/map/config").status_code == 200, login
        assert geocode(c, "Ленина 5").status_code == 200, login
    for login in ("acc_a", "emp_a", "store_a"):
        c = make_client(login)
        assert c.get("/map/config").status_code == 403, login
        assert geocode(c, "Ленина 5").status_code == 403, login


def test_needs_login(make_client, world):
    c = make_client()
    assert c.get("/map/config").status_code == 401
    assert c.post("/map/geocode", {"addresses": ["x"]}).status_code == 401


# ---------------------------------------------------------------- поиск точки по адресу

def test_geocode_without_key(make_client, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "twogis_key", "")
    fake = Fake(found())
    monkeypatch.setattr(map_, "fetch_json", fake)
    r = geocode(make_client("dir_a"), "Ленина 5")
    assert r.status_code == 409 and r.json()["code"] == "map_no_key" and not fake.urls


def test_geocode_found_adds_city_and_key(make_client, world, with_key, monkeypatch):
    fake = Fake(found(82.9, 55.0, "Новосибирск, Ленина, 5"))
    monkeypatch.setattr(map_, "fetch_json", fake)
    r = geocode(make_client("dir_a"), "  ул. Ленина   5 ")
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item == {"address": "ул. Ленина 5", "status": "ok", "lon": 82.9, "lat": 55.0, "title": "Новосибирск, Ленина, 5"}
    assert fake.queries() == ["Новосибирск, ул. Ленина 5"]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(fake.urls[0]).query)
    assert q["key"] == ["test-key-123"] and "items.point" in q["fields"][0]
    assert fake.urls[0].startswith("https://catalog.api.2gis.com/3.0/items/geocode?")


def test_city_not_doubled(make_client, world, with_key, monkeypatch):
    fake = Fake(found())
    monkeypatch.setattr(map_, "fetch_json", fake)
    geocode(make_client("dir_a"), "г. новосибирск, Красный проспект 1")
    assert fake.queries() == ["г. новосибирск, Красный проспект 1"]


def test_repeat_comes_from_memory(make_client, world, with_key, monkeypatch):
    fake = Fake(found())
    monkeypatch.setattr(map_, "fetch_json", fake)
    c = make_client("dir_a")
    first = geocode(c, "Ленина 5").json()["items"][0]
    again = geocode(c, "ленина  5").json()["items"][0]
    assert len(fake.urls) == 1
    assert again["lon"] == first["lon"] and again["status"] == "ok"


def test_not_found_is_remembered(make_client, world, with_key, monkeypatch):
    fake = Fake(NOT_FOUND)
    monkeypatch.setattr(map_, "fetch_json", fake)
    c = make_client("dir_a")
    assert geocode(c, "Нигде 1").json()["items"][0]["status"] == "not_found"
    assert geocode(c, "Нигде 1").json()["items"][0]["status"] == "not_found"
    assert len(fake.urls) == 1


def test_empty_items_is_not_found(make_client, world, with_key, monkeypatch):
    monkeypatch.setattr(map_, "fetch_json", Fake({"meta": {"code": 200}, "result": {"items": [], "total": 0}}))
    assert geocode(make_client("dir_a"), "Пусто 1").json()["items"][0]["status"] == "not_found"


def test_network_error_not_remembered(make_client, world, with_key, monkeypatch):
    fake = Fake(OSError("нет связи"))
    monkeypatch.setattr(map_, "fetch_json", fake)
    c = make_client("dir_a")
    item = geocode(c, "Ленина 5").json()["items"][0]
    assert item["status"] == "error" and "2ГИС" in item["message"]
    monkeypatch.setattr(map_, "fetch_json", Fake(found()))
    assert geocode(c, "Ленина 5").json()["items"][0]["status"] == "ok"            # после сбоя повторный поиск не застревает


def test_bad_key_reply_is_error_with_message(make_client, world, with_key, monkeypatch):
    fake = Fake({"meta": {"code": 403, "error": {"message": "API key is invalid"}}})
    monkeypatch.setattr(map_, "fetch_json", fake)
    c = make_client("dir_a")
    item = geocode(c, "Ленина 5").json()["items"][0]
    assert item["status"] == "error" and "API key is invalid" in item["message"]
    geocode(c, "Ленина 5")
    assert len(fake.urls) == 2                                                    # ошибка ключа не запоминается


def test_broken_json_is_error(make_client, world, with_key, monkeypatch):
    monkeypatch.setattr(map_, "fetch_json", Fake(ValueError("не JSON")))
    assert geocode(make_client("dir_a"), "Ленина 5").json()["items"][0]["status"] == "error"


def test_centroid_fallback(make_client, world, with_key, monkeypatch):
    monkeypatch.setattr(map_, "fetch_json", Fake({"meta": {"code": 200}, "result": {"items": [{"name": "Дом", "geometry": {"centroid": "POINT(83.1 54.9)"}}]}}))
    item = geocode(make_client("dir_a"), "Дом 1").json()["items"][0]
    assert item["status"] == "ok" and item["lon"] == 83.1 and item["lat"] == 54.9 and item["title"] == "Дом"


def test_several_addresses_deduplicated(make_client, world, with_key, monkeypatch):
    fake = Fake(lambda url: found(lon=82.0 + len(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]) / 100))
    monkeypatch.setattr(map_, "fetch_json", fake)
    r = geocode(make_client("dir_a"), "Ленина 5", "ленина 5", "Мира 10", "")
    items = r.json()["items"]
    assert [i["address"] for i in items] == ["Ленина 5", "Мира 10"] and len(fake.urls) == 2


def test_validation(make_client, world, with_key, monkeypatch):
    monkeypatch.setattr(map_, "fetch_json", Fake(found()))
    c = make_client("dir_a")
    assert c.post("/map/geocode", {"addresses": []}).status_code == 422
    assert geocode(c, *[f"Улица {i}" for i in range(31)]).status_code == 422
    assert geocode(c, *[f"Улица {i}" for i in range(30)]).status_code == 200


# ---------------------------------------------------------------- мелочи

def test_full_query_and_point_of():
    assert map_.full_query("Ленина 5", "") == "Ленина 5"
    assert map_.full_query("Ленина   5", "Омск") == "Омск, Ленина 5"
    assert map_.full_query("Омск Ленина 5", "омск") == "Омск Ленина 5"
    assert map_.point_of({"point": {"lon": "1.5", "lat": "2.5"}}) == (1.5, 2.5)
    assert map_.point_of({"point": {"lon": "x", "lat": 1}}) is None
    assert map_.point_of({"geometry": {"centroid": "POINT(1 2)"}}) == (1.0, 2.0)
    assert map_.point_of({"geometry": {"centroid": "POINT(1)"}}) is None
    assert map_.point_of({}) is None


def test_fetch_json_reads_error_body(monkeypatch):
    body = json.dumps(NOT_FOUND).encode()

    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(body))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert map_.fetch_json("https://catalog.api.2gis.com/x")["meta"]["code"] == 404


def test_fetch_json_non_json_error(monkeypatch):
    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"<html>"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(OSError):
        map_.fetch_json("https://catalog.api.2gis.com/x")


def test_real_request_to_local_server(make_client, world, with_key, monkeypatch):
    """Настоящий сетевой запрос (urllib) к местному серверу, который отвечает как 2ГИС: проверяется и адрес запроса, и разбор ответа."""
    import http.server
    import threading

    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["q"][0]
            code, body = (404, NOT_FOUND) if "Нигде" in q else (200, found(83.0, 54.9, q))
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setattr(map_, "GEOCODER", f"http://127.0.0.1:{srv.server_port}/3.0/items/geocode")
        items = geocode(make_client("dir_a"), "Ленина 5", "Нигде 1").json()["items"]
    finally:
        srv.shutdown()
        srv.server_close()
    assert items[0]["status"] == "ok" and items[0]["lon"] == 83.0 and items[0]["title"] == "Новосибирск, Ленина 5"
    assert items[1]["status"] == "not_found"                     # ответ 404 с телом JSON читается как «не найдено»
    assert all("key=test-key-123" in s for s in seen) and len(seen) == 2


def test_server_down_is_error(make_client, world, with_key, monkeypatch):
    monkeypatch.setattr(map_, "GEOCODER", "http://127.0.0.1:9/none")       # порт, на котором никто не слушает
    item = geocode(make_client("dir_a"), "Ленина 5").json()["items"][0]
    assert item["status"] == "error"
