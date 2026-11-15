# -*- coding: utf-8 -*-
from sqlalchemy import text
from sqlalchemy.orm import Session


def next_number(db: Session, legal_entity_id: int, kind: str, year: int = 0) -> int:
    """Следующий номер документа. Одна атомарная команда: два одновременных запроса не получат один номер."""
    return db.execute(text(
        "INSERT INTO number_counters (legal_entity_id, kind, year, last_value) VALUES (:e, :k, :y, 1) "
        "ON CONFLICT (legal_entity_id, kind, year) DO UPDATE SET last_value = number_counters.last_value + 1 "
        "RETURNING last_value"), {"e": legal_entity_id, "k": kind, "y": year}).scalar_one()
