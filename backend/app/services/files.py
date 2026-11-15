# -*- coding: utf-8 -*-
"""Загрузка файлов: тип определяется по содержимому (а не по имени и не по тому, что назвал браузер), размер ограничен,
у картинок делается маленькое превью для списков. Файлы хранятся в базе (см. StoredFile)."""
import hashlib
import io
import re
from dataclasses import dataclass

from PIL import Image, ImageOps

from app.common import fail

MAX_FILE_BYTES = 10 * 1024 * 1024         # один файл
MAX_FILES_PER_REQUEST = 10
MAX_PIXELS = 50_000_000                    # защита от «бомб»: маленький файл, который раскрывается в гигантскую картинку
THUMB_SIZE = (480, 480)
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_TYPES = IMAGE_TYPES | {"application/pdf"}
TYPE_LABEL = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WebP", "application/pdf": "PDF"}


@dataclass
class Prepared:
    data: bytes
    content_type: str
    sha256: str
    name: str
    width: int | None = None
    height: int | None = None
    thumb: bytes | None = None

    @property
    def is_image(self) -> bool:
        return self.content_type in IMAGE_TYPES


def sniff(data: bytes) -> str | None:
    """Тип по первым байтам. Всё, что не JPEG, PNG, WebP или PDF, отклоняется."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return None


def clean_name(name: str | None) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:200] or "файл"


def make_thumb(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail(THUMB_SIZE)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80, optimize=True)
    return out.getvalue()


def prepare(filename: str | None, data: bytes, *, images_only: bool = False) -> Prepared:
    """Проверяет один файл и готовит его к записи. Понятная ошибка для человека, если файл не подходит."""
    name = clean_name(filename)
    if not data:
        raise fail(422, "empty_file", f"Файл «{name}» пустой", "files")
    if len(data) > MAX_FILE_BYTES:
        raise fail(422, "file_too_big", f"Файл «{name}» больше {MAX_FILE_BYTES // (1024 * 1024)} МБ: сожмите или уменьшите его", "files")
    content_type = sniff(data)
    if content_type is None or content_type not in ALLOWED_TYPES:
        raise fail(422, "bad_file_type", f"Файл «{name}» не подходит: нужны фото (JPEG, PNG, WebP) или PDF", "files")
    if images_only and content_type not in IMAGE_TYPES:
        raise fail(422, "photo_not_image", f"Для фото нужна картинка (JPEG, PNG или WebP), а «{name}» не картинка", "files")
    prepared = Prepared(data=data, content_type=content_type, sha256=hashlib.sha256(data).hexdigest(), name=name)
    if prepared.is_image:
        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()                       # проверяет целостность, не раскрывая картинку целиком
            with Image.open(io.BytesIO(data)) as img:
                prepared.width, prepared.height = img.size
                if img.size[0] * img.size[1] > MAX_PIXELS:         # Pillow сам только предупреждает, поэтому проверяем сами
                    raise Image.DecompressionBombError("too many pixels")
                if img.format == "JPEG":
                    img.draft("RGB", THUMB_SIZE)                  # большое фото раскрываем сразу уменьшенным: быстро и без лишней памяти
                img.load()
                prepared.thumb = make_thumb(img)
        except Image.DecompressionBombError:
            raise fail(422, "image_too_large", f"Картинка «{name}» слишком большая по размеру в пикселях", "files") from None
        except Exception:                            # повреждённый или поддельный файл
            raise fail(422, "bad_image", f"Файл «{name}» повреждён или это не картинка", "files") from None
    return prepared
