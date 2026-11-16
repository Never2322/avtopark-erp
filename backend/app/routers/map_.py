# -*- coding: utf-8 -*-
"""Карта 2ГИС: настройки для страницы карты и поиск точки по адресу.

Слежения за машинами здесь нет: карта показывает только то, что ищет человек, и адреса заданий.
Сама карта рисуется в браузере скриптом 2ГИС (ему нужен ключ из `TWOGIS_KEY`). Поиск точки по адресу идёт через сервер:
так не зависим от разрешений браузера на запросы к чужому сайту, а повторные адреса берутся из памяти и не тратят лимит ключа.
"""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.common import fail
from app.config import get_settings
from app.deps import Principal, require

router = APIRouter(prefix="/map", tags=["Карта"])

GEOCODER = "https://catalog.api.2gis.com/3.0/items/geocode"
MAX_ADDRESSES = 30
CACHE_SIZE = 2000
TIMEOUT = 6

_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


class GeocodeIn(BaseModel):
    addresses: list[str] = Field(min_length=1, max_length=MAX_ADDRESSES)


def fetch_json(url: str) -> dict:
    """Один запрос к 2ГИС. Вынесено отдельно, чтобы в проверках подменять.
    Ошибки сети и ответ не в формате JSON превращаются в OSError/ValueError — вызывающий их ловит."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "avtopark-erp"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310  (адрес собран выше, схема https)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 2ГИС отвечает JSON и с кодом 4xx (например «не найдено» или «ключ не подходит»)
        try:
            return json.loads(e.read().decode("utf-8"))
        except (ValueError, OSError):
            raise OSError(f"HTTP {e.code}") from e


def full_query(address: str, city: str) -> str:
    """К адресу без города дописывается город организации (иначе «ул. Ленина 5» найдётся в любом городе)."""
    text = " ".join(address.split())
    if city and city.lower() not in text.lower():
        return f"{city}, {text}"
    return text


def point_of(item: dict) -> tuple[float, float] | None:
    pt = item.get("point")
    if isinstance(pt, dict) and "lon" in pt and "lat" in pt:
        try:
            return float(pt["lon"]), float(pt["lat"])
        except (TypeError, ValueError):
            return None
    # запасной вариант: центр геометрии в записи «POINT(долгота широта)»
    centroid = ((item.get("geometry") or {}).get("centroid") or "").strip()
    if centroid.upper().startswith("POINT(") and centroid.endswith(")"):
        parts = centroid[6:-1].split()
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                return None
    return None


def lookup(address: str, key: str, city: str) -> dict:
    """Одна точка по адресу. status: ok / not_found / error. Успех и «не найдено» запоминаются, ошибка нет."""
    query = full_query(address, city)
    memo = query.lower()                                  # регистр букв на поиск не влияет, в памяти он один
    with _lock:
        hit = _cache.get(memo)
        if hit is not None:
            _cache.move_to_end(memo)
            return {"address": address, **hit}
    url = GEOCODER + "?" + urllib.parse.urlencode({"q": query, "fields": "items.point,items.geometry.centroid", "key": key})
    try:
        data = fetch_json(url)
    except (OSError, ValueError):
        return {"address": address, "status": "error", "message": "2ГИС не отвечает"}
    meta = data.get("meta") or {}
    code = meta.get("code", 200)
    items = (data.get("result") or {}).get("items") or []
    if code not in (200, 404):
        message = ((meta.get("error") or {}).get("message")) or "2ГИС не принял запрос"
        return {"address": address, "status": "error", "message": f"2ГИС: {message}"}
    found = None
    if code == 200:
        for it in items:
            pt = point_of(it)
            if pt:
                found = {"status": "ok", "lon": pt[0], "lat": pt[1], "title": it.get("full_name") or it.get("name") or it.get("address_name") or ""}
                break
    result = found or {"status": "not_found"}
    with _lock:
        _cache[memo] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return {"address": address, **result}


@router.get("/config")
def map_config(p: Principal = Depends(require("map.view"))):
    s = get_settings()
    key = s.twogis_key.strip()
    return {"enabled": bool(key), "key": key, "center": [s.map_lon, s.map_lat], "zoom": s.map_zoom, "city": s.map_city}


@router.post("/geocode")
def geocode(body: GeocodeIn, p: Principal = Depends(require("map.view"))):
    s = get_settings()
    key = s.twogis_key.strip()
    if not key:
        raise fail(409, "map_no_key", "Ключ 2ГИС не задан: впишите TWOGIS_KEY в настройки и перезапустите программу")
    seen: set[str] = set()
    out = []
    for raw in body.addresses:
        text = " ".join(str(raw).split())
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(lookup(text, key, s.map_city.strip()))
    return {"items": out}
