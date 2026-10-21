from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime

InsightType = Literal["risk", "opportunity", "event"]
SourceKind = Literal["external", "bank_analytics"]


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
