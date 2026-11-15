# -*- coding: utf-8 -*-
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Проверяем пароль и тогда, когда логина нет, чтобы по времени ответа нельзя было узнать, какие логины существуют.
DUMMY_HASH = _hasher.hash("пустышка-для-выравнивания-времени")

WEAK = {"12345678", "123456789", "1234567890", "qwerty123", "password", "password1", "11111111", "qwertyui",
        "йцукенгш", "пароль123", "admin123", "administrator"}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def temp_password() -> str:
    """Временный пароль без путаных символов (0/O, 1/l/I), его читают по бумажке и набирают на телефоне."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def password_problem(password: str, login: str = "") -> str | None:
    if len(password) < 8:
        return "Пароль не короче 8 символов"
    if len(password) > 128:
        return "Пароль не длиннее 128 символов"
    if login and password.lower() == login.lower():
        return "Пароль не должен совпадать с логином"
    if password.lower() in WEAK or len(set(password)) < 4:
        return "Слишком простой пароль: придумайте другой"
    return None
