"""Синтетические новости (data/synthetic/news.json) в таблицу documents — демо без интернета.

У новости в файле вместо даты days_ago: дата публикации — «сегодня агента» (AGENT_TODAY или
сегодня по UTC) минус days_ago, 07:00 UTC. Поэтому новости всегда попадают в окно агента.
Повторная загрузка не создаёт дублей (ключ — url synthetic://news/…), а только сдвигает даты
и обновляет текст. Отрасли (okved:NN) размечаются так же, как при обычном сборе.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.ingestion.normalizer import content_hash
from app.ml_ports import tagging_port
from app.ml_ports.news_port import agent_today
from app.repositories import document_repo

SOURCE_NAME = "synthetic"
PUBLISH_TIME = time(7, 0, tzinfo=UTC)


@dataclass
class SyntheticNewsReport:
    new: int = 0
    updated: int = 0


def _values(item: dict[str, Any]) -> dict[str, Any]:
    day = agent_today() - timedelta(days=int(item["days_ago"]))
    tags = tagging_port.with_industry_tags(
        list(item.get("tags") or []), item["title"], item["text"], ref=item["id"]
    )
    return {
        "kind": "external_news",
        "source_name": item.get("source") or SOURCE_NAME,
        "url": item["url"],
        "title": item["title"],
        "text": item["text"],
        "published_at": datetime.combine(day, PUBLISH_TIME),
        "tags": tags,
        "status": "ok",
        "content_hash": content_hash(item["text"] + item["url"]),
    }


def load_news(engine: Engine, path: Path) -> SyntheticNewsReport:
    items = json.loads(path.read_text(encoding="utf-8"))
    report = SyntheticNewsReport()
    with Session(engine) as session, session.begin():
        for item in items:
            values = _values(item)
            existing = document_repo.find_by_url(session, values["url"])
            if existing is None:
                document_repo.insert_new(session, values)
                report.new += 1
            else:
                document_repo.replace(session, existing.id, values)
                report.updated += 1
    return report


def format_report(report: SyntheticNewsReport, path: Path) -> str:
    return (
        f"Синтетические новости из {path.name}: новых {report.new}, обновлено {report.updated} "
        f"(даты — от {agent_today().isoformat()})"
    )
