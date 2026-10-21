from datetime import UTC
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, PlainSerializer


def _to_utc_z(value: AwareDatetime) -> str:
    """Время в UTC с суффиксом Z, без долей секунды: 2026-10-21T14:30:00Z."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Время события: на входе — любая дата со смещением, на выходе — всегда UTC с «Z»
UtcDatetime = Annotated[AwareDatetime, PlainSerializer(_to_utc_z, return_type=str)]


class Page[T](BaseModel):
    """Единая форма всех списков."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """Единая форма всех ошибок."""

    error: str
    detail: str
