"""Разметка уже собранных документов по отраслям (теги okved:NN).

Новые документы размечаются при сборе (ingestion/runner.py). Этот сервис нужен для тех, что
собраны раньше, и после правки словаря в llm/okved/tagging.py: пересчитывает okved:* у всех
документов со статусом ok, остальные теги не трогает. Повторный запуск ничего не меняет.
"""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.ml_ports import tagging_port
from app.repositories import document_repo


@dataclass
class TagReport:
    total: int = 0  # документов с текстом
    changed: int = 0  # у скольких теги поменялись
    tagged: int = 0  # у скольких есть хотя бы одна отрасль
    by_tag: Counter = field(default_factory=Counter)


def tag_documents(engine: Engine) -> TagReport:
    report = TagReport()
    with Session(engine) as session, session.begin():
        for doc in document_repo.ok_documents(session):
            report.total += 1
            tags = tagging_port.with_industry_tags(list(doc.tags or []), doc.title, doc.text)
            industry = [t for t in tags if t.startswith("okved:")]
            if industry:
                report.tagged += 1
                report.by_tag.update(industry)
            if tags != list(doc.tags or []):
                document_repo.set_tags(session, doc.id, tags)
                report.changed += 1
    return report


def format_report(report: TagReport) -> str:
    lines = [
        f"Разметка отраслей ({tagging_port.tagger_version()}): с текстом {report.total}, "
        f"с отраслью {report.tagged}, теги изменились у {report.changed}"
    ]
    for tag, n in report.by_tag.most_common():
        lines.append(f"  {tag}: {n}")
    return "\n".join(lines)
