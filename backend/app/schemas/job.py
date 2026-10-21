from typing import Literal

from pydantic import BaseModel

JobStatus = Literal["pending", "running", "done", "failed"]


class Job(BaseModel):
    id: str
    status: JobStatus
    error: str | None = None


class JobAccepted(BaseModel):
    """Ответ 202: задача принята и выполняется в фоне."""

    job_id: str
