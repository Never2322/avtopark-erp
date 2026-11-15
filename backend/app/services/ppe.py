# -*- coding: utf-8 -*-
"""СИЗ: срок замены и состояние выдачи. Считается одинаково в списке, в карточке сотрудника и в напоминаниях."""
import calendar
import datetime as dt

from app.enums import PPE_SOON_DAYS


def add_months(d: dt.date, months: int) -> dt.date:
    """Дата через N месяцев; если такого числа в месяце нет (31 января + 1 месяц), берётся последний день месяца."""
    n = d.year * 12 + d.month - 1 + months
    year, month = n // 12, n % 12 + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def due_for(issue_date: dt.date | None, wear_months: int | None) -> dt.date | None:
    return add_months(issue_date, wear_months) if issue_date and wear_months else None


def state_of(issue, today: dt.date) -> dict:
    """level: closed (возвращено), ok, soon (скоро), due (пора). left — дней до замены (отрицательное: просрочено)."""
    if issue.returned_date is not None:
        return {"level": "closed", "text": "Возвращено", "tone": "neutral", "left": None, "left_text": "—"}
    if issue.due_date is None:
        return {"level": "ok", "text": "Без срока", "tone": "neutral", "left": None, "left_text": "—"}
    left = (issue.due_date - today).days
    if left < 0:
        return {"level": "due", "text": "Просрочено", "tone": "error", "left": left, "left_text": f"просрочено на {-left} дн."}
    if left == 0:
        return {"level": "due", "text": "Пора менять", "tone": "error", "left": 0, "left_text": "срок сегодня"}
    if left <= PPE_SOON_DAYS:
        return {"level": "soon", "text": "Скоро замена", "tone": "warning", "left": left, "left_text": f"{left} дн."}
    return {"level": "ok", "text": "Носит", "tone": "success", "left": left, "left_text": f"{left} дн."}
