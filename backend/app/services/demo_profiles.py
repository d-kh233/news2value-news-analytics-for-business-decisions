"""Демо-профили из data/demo_profiles/*.json -> ручная часть профиля (manual_data).

Формат файла (один файл — одна компания):
    {"company_id": "U_67", "manual_data": {"name": "АО «Демо»", "key_clients": ["..."]}}
Ключи manual_data — поля CompanyProfile; проверяются той же схемой, что и тело PUT.
Ручная часть компании заменяется целиком: повторный запуск даёт тот же результат.
Файл с ошибкой или с несуществующей компанией пропускается, остальные загружаются.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.schemas.company import CompanyProfileUpdate
from app.services import company_service


@dataclass
class DemoReport:
    loaded: list[str] = field(default_factory=list)  # id компаний
    skipped: dict[str, str] = field(default_factory=dict)  # имя файла -> причина


def _read(path: Path) -> tuple[str, CompanyProfileUpdate]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("company_id"), str):
        raise ValueError("нужен объект с полем company_id (строка)")
    return data["company_id"], CompanyProfileUpdate.model_validate(data.get("manual_data") or {})


def load_dir(engine: Engine, directory: Path) -> DemoReport:
    report = DemoReport()
    for path in sorted(Path(directory).glob("*.json")):
        try:
            company_id, manual = _read(path)
        except (ValueError, ValidationError) as exc:  # JSONDecodeError — тоже ValueError
            report.skipped[path.name] = f"неверный файл: {exc}".splitlines()[0]
            continue
        try:
            with Session(engine) as session, session.begin():
                company_service.replace_manual(session, company_id, manual)
        except NotFoundError as exc:
            report.skipped[path.name] = exc.detail
            continue
        report.loaded.append(company_id)
    return report


def format_report(report: DemoReport) -> str:
    lines = [f"Загружено демо-профилей: {len(report.loaded)}"]
    lines += [f"  - {company_id}" for company_id in report.loaded]
    if report.skipped:
        lines.append(f"Пропущено файлов: {len(report.skipped)}")
        lines += [f"  - {name}: {reason}" for name, reason in report.skipped.items()]
    return "\n".join(lines)
