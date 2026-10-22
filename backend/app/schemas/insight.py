from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime

InsightType = Literal["risk", "opportunity", "event"]
SourceKind = Literal["external", "bank_analytics"]
# Роль читателя: меняет отбор и акценты инсайтов (тексты ролей — llm/insight.py)
Role = Literal["owner", "ceo", "cfo", "treasurer", "accountant", "procurement"]


class Source(BaseModel):
    title: str
    url: str
    published_at: UtcDatetime
    source_kind: SourceKind


class Insight(BaseModel):
    id: str
    company_id: str
    title: str
    summary: str
    type: InsightType
    importance: int = Field(ge=0, le=100)
    impact_on_company: str
    recommended_action: str
    sources: list[Source]
    created_at: UtcDatetime
    # --- необязательные поля, которые заполняет агент (в моках их нет) ---
    # 6 блоков с разметкой «факт / банк / ИИ» и ссылками на источники (схема llm/schemas.py Insight)
    # + details.agent: релевантность, слагаемые скоринга, заметки самопроверки, run_id
    details: dict[str, Any] | None = None
    used_profile_fields: list[str] = Field(default_factory=list)  # F:/P:/G: — признаки компании
    role: Role | None = None
    generated_by: Literal["llm", "rules"] | None = None
    prompt_version: str | None = None
