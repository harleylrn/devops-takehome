from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SubmitJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator("prompt")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class JobMessage(SubmitJob):
    job_id: UUID


class JobState(BaseModel):
    job_id: UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    result: str | None = None
    error: str | None = None


class AcceptedJob(BaseModel):
    job_id: UUID
