"""Логи приложения и маскирование секретов (B8+B9).

MaskingFormatter заменяет на *** то, что не должно попасть в лог: email, токены (Bearer, JWT),
ключи (sk-…), значения после password= / token= / secret= / api_key= и точные значения
секретов из настроек (LLM_API_KEY, JWT_SECRET, пароли базы и демо-пользователей). Маска
действует на готовую строку лога целиком, включая трейсбек (список вызовов при ошибке).
Сообщение длиннее MESSAGE_MAX_LEN обрезается: полные тексты новостей и профилей в лог не идут.
"""

import logging
import os
import re
from functools import lru_cache

MASK = "***"
MESSAGE_MAX_LEN = 2000
# переменные окружения, значения которых маскируются в любом месте строки
SECRET_ENV = ("LLM_API_KEY", "JWT_SECRET", "POSTGRES_PASSWORD", "DEMO_USER_PASSWORD")
MIN_SECRET_LEN = 4  # короче — слишком много ложных совпадений

_PATTERNS = [
    # email
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), MASK),
    # Authorization: Bearer <токен>
    (re.compile(r"(?i)(bearer\s+)[\w.~+/=-]+"), rf"\g<1>{MASK}"),
    # JWT: три части base64 через точку, первая начинается с eyJ
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"), MASK),
    # ключи API вида sk-...
    (re.compile(r"\bsk-[\w-]{12,}"), MASK),
    # password=..., "api_key": "...", token: ...
    (
        re.compile(
            r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|authorization)"
            r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+"
        ),
        rf"\g<1>{MASK}",
    ),
    # пароль в адресе БД: postgresql://user:пароль@host
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), rf"\g<1>{MASK}@"),
]


@lru_cache
def _secret_values() -> tuple[str, ...]:
    from app.core.config import get_settings

    s = get_settings()
    values = [os.environ.get(name, "") for name in SECRET_ENV]
    values += [s.jwt_secret, s.llm_api_key, s.postgres_password, s.demo_user_password]
    found = {v for v in values if v and len(v) >= MIN_SECRET_LEN}
    # длинные первыми: секрет, который содержит другой, маскируется целиком
    return tuple(sorted(found, key=len, reverse=True))


def mask_secrets(text: str) -> str:
    """Строка без секретов: известные значения и шаблоны заменены на ***."""
    if not text:
        return text
    for value in _secret_values():
        text = text.replace(value, MASK)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if len(message) > MESSAGE_MAX_LEN:
            record.msg = message[:MESSAGE_MAX_LEN] + f"… [обрезано, всего {len(message)} символов]"
            record.args = ()
        return mask_secrets(super().format(record))


FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# логгеры uvicorn пишут через свои обработчики — им тоже ставим маскирующий форматтер
_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")


def install_masking(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.setFormatter(MaskingFormatter(FORMAT))


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=FORMAT)
    install_masking(logging.getLogger())
    for name in _UVICORN:
        install_masking(logging.getLogger(name))
