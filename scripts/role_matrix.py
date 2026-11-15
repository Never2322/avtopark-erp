# -*- coding: utf-8 -*-
"""Пересобирает таблицу прав в docs/Роли-и-права.md из backend/app/permissions.py.

Запуск из корня проекта:  PYTHONPATH=backend python3 scripts/role_matrix.py
Всё, что в документе выше строки-метки, остаётся как есть; таблица ниже метки пишется заново.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))
from app.permissions import GLOBAL_ONLY, PERMISSIONS, ROLE_DEFAULTS, ROLES  # noqa: E402

MARK = "<!-- ТАБЛИЦА НИЖЕ СОБИРАЕТСЯ СКРИПТОМ scripts/role_matrix.py, ВРУЧНУЮ ЕЁ НЕ ПРАВЬТЕ -->"
DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "Роли-и-права.md"


def table() -> str:
    names = [r[1] for r in ROLES]
    codes = [r[0] for r in ROLES]
    lines = ["| Что можно | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    module = None
    for code, mod, text in PERMISSIONS:
        if mod != module:
            module = mod
            lines.append(f"| **{mod}** |" + " |" * len(names))
        cells = []
        for rc in codes:
            allowed = rc == "admin" or code in ROLE_DEFAULTS.get(rc, [])
            cells.append("да" if allowed else "")
        lines.append(f"| {text}{' *' if code in GLOBAL_ONLY else ''} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    text = DOC.read_text(encoding="utf-8")
    head = text.split(MARK)[0].rstrip() + "\n\n" if MARK in text else None
    if head is None:
        # первый запуск: таблица начинается с первой строки, которая начинается с «| Что можно»
        idx = text.index("| Что можно")
        head = text[:idx].rstrip() + "\n\n"
    DOC.write_text(head + MARK + "\n\n" + table() + "\n", encoding="utf-8")
    print("готово:", DOC)


if __name__ == "__main__":
    main()
