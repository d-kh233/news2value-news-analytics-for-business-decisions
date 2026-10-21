"""Выгрузка схемы API в docs/openapi.json.

Запуск из backend/: uv run python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402

target = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Схема записана в {target}")
