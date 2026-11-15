# -*- coding: utf-8 -*-
"""Контроль сроков по технике (ТО и мероприятия): сколько осталось до порога по дате, пробегу и моточасам.

Правило как в старой программе: порог наступил, если по любому из показателей осталось 0 и меньше; «скоро» — если по любому
осталось не больше запаса (14 дней, 500 км, 10 моточасов). Запись снята с контроля или выполнена — уровень «closed».
"""
import datetime as dt
from decimal import Decimal

from app.enums import CONTROL_LEAD_DAYS, CONTROL_LEAD_HOURS, CONTROL_LEAD_KM

TONES = {"due": "error", "soon": "warning", "ok": "success", "closed": "neutral"}
LABELS = {"due": "Наступил", "soon": "Скоро", "ok": "В порядке", "closed": "Закрыто"}


def evaluate(*, due_date: dt.date | None, due_km: int | None, due_hours: Decimal | float | None, controlled: bool, closed: bool,
             today: dt.date, cur_km: Decimal | float | None, cur_hours: Decimal | float | None) -> dict:
    """level: closed / due / soon / ok; left — сколько осталось (days, km, hours); urgency — чем меньше, тем срочнее."""
    left: dict[str, float] = {}
    if due_date is not None:
        left["days"] = (due_date - today).days
    if due_km is not None and cur_km is not None:
        left["km"] = float(due_km) - float(cur_km)
    if due_hours is not None and cur_hours is not None:
        left["hours"] = round(float(due_hours) - float(cur_hours), 1)
    lead = {"days": CONTROL_LEAD_DAYS, "km": CONTROL_LEAD_KM, "hours": CONTROL_LEAD_HOURS}
    if closed or not controlled:
        level = "closed"
    elif not left:
        level = "ok"
    elif any(v <= 0 for v in left.values()):
        level = "due"
    elif any(left[k] <= lead[k] for k in left):
        level = "soon"
    else:
        level = "ok"
    urgency = min((left[k] / lead[k] for k in left), default=1e9)
    return {"level": level, "left": left, "urgency": urgency}


def _num(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ").replace("-", "−") if float(x).is_integer() else f"{x:,.1f}".replace(",", " ").replace("-", "−")


def left_text(left: dict[str, float]) -> str:
    parts = []
    if "days" in left:
        parts.append(f"{int(left['days'])} дн.".replace("-", "−"))
    if "km" in left:
        parts.append(f"{_num(left['km'])} км")
    if "hours" in left:
        parts.append(f"{_num(left['hours'])} м·ч")
    return " · ".join(parts) or "—"


def badge(ev: dict) -> dict:
    return {"text": LABELS[ev["level"]], "tone": TONES[ev["level"]]}
