"""Разметка документов по отраслям: теги okved:NN из llm/okved/tagging.py.

Сама разметка — в llm/ (временно сделана бэкендом в ветке feat/llm-fixes-daria, см.
docs/llm-fixes-handoff.md). Здесь только стык: путь к пакету llm и слияние со старыми тегами.
Старые теги источника (general, economy, bank_analytics…) не трогаются, okved:* пересчитываются.
"""

from typing import Any

from app.ml_ports.audit import audited


def _tagging() -> Any:
    from app.ml_ports.agent_port import ensure_repo_on_path

    ensure_repo_on_path()
    from llm.okved import tagging

    return tagging


def _refs(*args: Any, ref: str | None = None, **_: Any) -> tuple[None, dict[str, Any]]:
    return None, {"document": ref} if ref else {}


@audited("llm.okved.industry_tags", _refs)
def industry_tags(title: str, text: str, *, ref: str | None = None) -> list[str]:
    """Теги отраслей новости: ["okved:41", ...] (может быть пусто)."""
    return _tagging().industry_tags(title, text)


@audited("llm.okved.industry_tags", _refs)
def with_industry_tags(
    tags: list[str], title: str, text: str, *, ref: str | None = None
) -> list[str]:
    """Теги документа после разметки: старые без okved:* плюс новые okved:*.

    ref — id документа для журнала audit_log (doc-N или начало хеша ещё не сохранённого)."""
    t = _tagging()
    return t.merge_tags(tags, t.industry_tags(title, text))


def tagger_version() -> str:
    return _tagging().TAGGER_VERSION


def normalize_industry(value: str) -> str:
    """Параметр фильтра industry: «41», «okved:41», « OKVED:41 » -> «okved:41»; иное — как есть."""
    v = value.strip().lower()
    if v.isdigit():
        return f"okved:{v.zfill(2)}"
    return v
