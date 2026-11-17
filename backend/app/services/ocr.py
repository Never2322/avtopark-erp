# -*- coding: utf-8 -*-
"""Чтение текста со сканов и фотографий счетов: внешняя программа tesseract (русский и английский словари) и pdftoppm для PDF-сканов.

Если программ нет на сервере (например, образ не пересобран), `available()` возвращает False, а чтение молча отключается:
человек заполняет счёт вручную, как раньше. Всё, что здесь прочитано, только подсказка: сумму и номер человек или проверка по
«сумме прописью» подтверждают в `routers/bills.py`.
"""
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger("erp.ocr")

LANGS = "rus+eng"
KEYWORDS = ("счет", "счёт", "итого", "инн", "поставщик", "покупатель", "ндс", "оплат", "всего", "кпп", "бик")
ENOUGH = 60                    # столько «очков» значит: это похоже на счёт, другие повороты листа пробовать не нужно
PDF_PAGES = 3                  # у счетов страниц мало; больше трёх не читаем (долго, а нужное обычно на первой)
TOTAL_SECONDS = 90             # общий бюджет времени на один запрос
STEP_SECONDS = 40              # предел на один запуск tesseract или pdftoppm
_slots = threading.BoundedSemaphore(2)      # одновременно не больше двух чтений: распознавание нагружает процессор


def available() -> bool:
    return shutil.which("tesseract") is not None


def pdf_available() -> bool:
    return available() and shutil.which("pdftoppm") is not None


def score(text: str) -> int:
    """Насколько текст похож на счёт: слова-метки и суммы с копейками."""
    low = text.lower()
    return sum(low.count(k) for k in KEYWORDS) * 10 + len(re.findall(r"\d{1,3}(?:[  ]\d{3})*,\d{2}", text))


def normalize(img: Image.Image) -> Image.Image:
    """Серое изображение с выровненным контрастом и подходящим размером (мелкое растягиваем, огромное сжимаем)."""
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    w, h = img.size
    if w < 1600 or w > 2600:
        k = 2000 / w
        img = img.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
    return img


def _tesseract(img: Image.Image, psm: int, deadline: float) -> str:
    left = deadline - time.monotonic()
    if left <= 1:
        return ""
    with tempfile.TemporaryDirectory(prefix="ocr-") as d:
        src = Path(d) / "in.png"
        img.save(src)
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        try:
            r = subprocess.run(["tesseract", str(src), "stdout", "-l", LANGS, "--psm", str(psm)], capture_output=True,
                               timeout=min(STEP_SECONDS, left), env=env)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning("tesseract не отработал: %s", e)
            return ""
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def _best_of_rotations(img: Image.Image, deadline: float, scorer=None, enough: int = ENOUGH) -> str:
    scorer = scorer or score
    best, best_score = "", -1
    for angle in (0, 90, 270, 180):                      # листы часто снимают боком или вверх ногами
        text = _tesseract(img.rotate(angle, expand=True) if angle else img, 6, deadline)
        s = scorer(text)
        if s > best_score:
            best, best_score = text, s
        if best_score >= enough or time.monotonic() >= deadline:
            break
    return best


def text_from_image(data: bytes, scorer=None, enough: int = ENOUGH) -> str:
    """Текст с фото или скана. Пустая строка, если программы нет, картинка не открылась или время вышло.
    scorer и enough говорят, как узнать «правильный» поворот листа: по умолчанию похожесть на счёт, для талонов весов свои слова."""
    if not available():
        return ""
    deadline = time.monotonic() + TOTAL_SECONDS
    with _slots:
        try:
            with Image.open(io.BytesIO(data)) as raw:
                raw.load()
                img = normalize(raw)
        except Exception as e:                              # повреждённый файл: подсказки не будет, человек заполнит сам
            log.warning("картинка не открылась: %s", e)
            return ""
        return _best_of_rotations(img, deadline, scorer, enough)


def text_from_scanned_pdf(data: bytes) -> str:
    """Текст PDF без текстового слоя (скан): страницы превращаются в картинки и читаются как фото."""
    if not pdf_available():
        return ""
    deadline = time.monotonic() + TOTAL_SECONDS
    parts: list[str] = []
    with _slots, tempfile.TemporaryDirectory(prefix="ocr-pdf-") as d:
        src = Path(d) / "in.pdf"
        src.write_bytes(data)
        try:
            subprocess.run(["pdftoppm", "-r", "200", "-l", str(PDF_PAGES), "-png", str(src), str(Path(d) / "page")], capture_output=True,
                           timeout=STEP_SECONDS, check=True)
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("PDF не превратился в картинки: %s", e)
            return ""
        for page in sorted(Path(d).glob("page-*.png")):
            if time.monotonic() >= deadline:
                break
            try:
                with Image.open(page) as raw:
                    raw.load()
                    img = normalize(raw)
            except Exception:
                continue
            parts.append(_best_of_rotations(img, deadline))
    return "\n".join(t for t in parts if t.strip())
