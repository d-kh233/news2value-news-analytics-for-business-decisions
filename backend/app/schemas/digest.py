from typing import Literal

from pydantic import BaseModel

DigestPeriod = Literal["day", "week"]


class Digest(BaseModel):
    company_id: str
    period: DigestPeriod
    summary: str
    insight_ids: list[str]
