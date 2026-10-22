"""Схемы уровня агента.

Карточка на выходе — формат бэкенда (backend/app/schemas/insight.py), её собирает
llm.backend_adapter.to_backend_insight; 6 блоков лежат в поле details.
Здесь только то, что вокруг неё: профиль, новость, тезис банка, трейс, итог прогона.
Формат id источников общий с llm/: N:<news_id> · A:<doc>:<chunk> · F:<КОЛОНКА> · G:<group_id>.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, Field

from llm.insight import company_facts
from llm.okved.sensitivity import load_map, okved2 as to_okved2
from llm.schemas import EventExtraction

_EMPTY = {"", "nan", "none", "null", "не определено"}


def _clean(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return None if v.lower() in _EMPTY else v
    return v


class CompanyProfile(BaseModel):
    company_id: str
    row: dict[str, Any]                 # колонки df_Uid_data_hack в ВЕРХНЕМ регистре + manual_data
    okved: Optional[str] = None
    okved2: Optional[str] = None
    industry: Optional[str] = None
    region: Optional[str] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any], company_id: Optional[str] = None) -> "CompanyProfile":
        """Из строки CSV, записи таблицы companies (нижний регистр) или профиля с manual_data."""
        norm: dict[str, Any] = {}
        for k, v in row.items():
            if k == "manual_data" or not isinstance(k, str):
                continue
            norm[k.strip().upper()] = _clean(v)
        for k, v in (row.get("manual_data") or {}).items():      # ручные данные не перетирают банковские
            if norm.get(k.upper()) in (None, ""):
                norm[k.upper()] = _clean(v)
        cid = company_id or norm.get("SYNTHETIC_U") or norm.get("COMPANY_ID") or norm.get("ID")
        if not cid:
            raise ValueError("в строке компании нет SYNTHETIC_U / company_id")
        norm["SYNTHETIC_U"] = cid
        okved = norm.get("MAINOKVED_CODE")
        o2 = to_okved2(str(okved)) if okved else None
        industry = (load_map().get(o2 or "") or {}).get("name") or norm.get("MAINOKVED_NAME") \
            or norm.get("INDUSTRY")
        return cls(company_id=str(cid), row=norm, okved=okved and str(okved), okved2=o2,
                   industry=industry, region=norm.get("REGION"))

    def facts(self) -> dict[str, str]:
        """Признаки с id (F:/G:), которые видит модель — та же функция, что в промпте интерпретации."""
        return {f["id"]: f["text"] for f in company_facts(self.row)}

    def short(self) -> str:
        return (f"{self.company_id}: {self.industry or 'отрасль н/д'} (ОКВЭД {self.okved or 'н/д'}), "
                f"регион {self.region or 'н/д'}")


class NewsItem(BaseModel):
    news_id: str
    title: str
    text: str
    published_at: date
    url: Optional[str] = None
    source: Optional[str] = None
    event: Optional[EventExtraction] = None     # None — новость ещё не размечена, агент разметит сам
    relevance_score: Optional[float] = None     # если источник новостей уже посчитал (0..1)

    @property
    def sid(self) -> str:
        return f"N:{self.news_id}"

    @property
    def published_iso(self) -> str:
        return f"{self.published_at.isoformat()}T00:00:00Z"

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "NewsItem":
        """Терпимо к именам полей: id/news_id, text/body, date/published_at. Разметка — в extraction
        (как EventExtraction) или прямо в корне (factor_moves, event_type…)."""
        g = lambda *keys, default=None: next((d[k] for k in keys if d.get(k) not in (None, "")), default)  # noqa: E731
        nid = str(g("news_id", "id", "uid"))
        text = g("text", "body", "content", default="")
        published = str(g("published_at", "date", "pub_date", default=date.today().isoformat()))[:10]
        ext = d.get("extraction") or (d if ("factor_moves" in d and "event_type" in d) else None)
        event = None
        if ext:
            first = re.split(r"(?<=[.!?])\s+", text.strip())[0] if text else ""
            event = EventExtraction.model_validate({
                "title": ext.get("title") or g("title", default=""), "summary": ext.get("summary") or first,
                **{k: v for k, v in ext.items() if k not in ("title", "summary", "news_id")},
                "news_id": nid, "url": g("url", "link"), "source": g("source", "publisher"),
                "published_at": published,
            })
        return cls(news_id=nid, title=g("title", "headline", default=""), text=text, published_at=published,
                   url=g("url", "link"), source=g("source", "publisher"), event=event,
                   relevance_score=g("relevance_score", "relevance"))


class BankThesis(BaseModel):
    thesis_id: str
    title: str
    thesis: str
    figure: str
    date: str
    published_at: str
    url: str = ""
    factors: list[str] = Field(default_factory=list)
    chunk: int = 0

    @property
    def sid(self) -> str:
        return f"A:{self.thesis_id}:{self.chunk}"

    def as_chunk(self) -> dict:
        """Формат bank_chunks для llm.insight.interpret."""
        return {"id": self.sid, "title": self.title, "date": self.date, "published_at": self.published_at,
                "url": self.url, "text": f"{self.thesis} ({self.figure})"}


class TraceStep(BaseModel):
    step: int
    name: str
    tool: Optional[str] = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    status: Literal["ok", "fallback", "error", "skipped"] = "ok"
    duration_ms: int = 0
    note: Optional[str] = None


class AgentResult(BaseModel):
    run_id: str
    company_id: str
    role: str
    period_days: int
    generated_at: datetime
    llm_mode: str
    profile: CompanyProfile
    plan: list[str]
    insights: list[dict[str, Any]]      # ровно схема backend Insight + details / role / generated_by
    trace: list[TraceStep]
    llm_usage: dict[str, Any] = Field(default_factory=dict)
