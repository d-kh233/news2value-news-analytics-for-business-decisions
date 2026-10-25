"""Пароли (bcrypt) и токены входа (JWT, HS256). Секрет подписи — JWT_SECRET из окружения.

JWT — строка из трёх частей: данные (id пользователя, срок), подпись секретом. Подделать без
секрета нельзя, проверить подпись и срок можно без БД.
"""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"
# bcrypt учитывает только первые 72 байта пароля — длиннее не принимаем, чтобы не путать
MAX_PASSWORD_BYTES = 72


class TokenError(Exception):
    """Токен неверный, подделан или истёк."""


class SecretMissing(RuntimeError):
    """JWT_SECRET не задан."""


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(f"пароль длиннее {MAX_PASSWORD_BYTES} байт")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except ValueError:  # в базе не bcrypt-хеш
        return False


def _secret() -> str:
    secret = get_settings().jwt_secret
    if not secret:
        raise SecretMissing("JWT_SECRET не задан: вход по паролю недоступен")
    return secret


def create_token(user_id: int) -> tuple[str, int]:
    """Токен для пользователя и срок жизни в секундах."""
    ttl = int(get_settings().jwt_expire_minutes * 60)
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(seconds=ttl)}
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM), ttl


def decode_token(token: str) -> int:
    """id пользователя из токена; TokenError — подпись не сошлась, срок истёк, формат не тот."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM], options={"require": ["exp"]})
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise TokenError(str(exc)) from exc
