from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий базовый класс моделей: по нему Alembic видит все таблицы."""
