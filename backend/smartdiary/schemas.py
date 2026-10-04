from datetime import datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(min_length=10, max_length=128)
    invitation: str = ""


class RecordWrite(BaseModel):
    id: UUID
    kind: Literal["text", "audio", "image", "link"] = "text"
    text: str = Field(default="", max_length=50000)
    url: str = Field(default="", max_length=4096)
    occurred_at: datetime
    recorded_at: datetime
    base_version: int = Field(default=0, ge=0)
    source_type: Literal["personal", "external"] = "personal"
    parent_record_id: UUID | None = None
    relation: Literal["followup", "result"] = "followup"

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value


class Preferences(BaseModel):
    timezone: str = "Asia/Shanghai"
    diary_time: str = "22:30"
    proactivity: Literal["quiet", "balanced", "companion"] = "balanced"
    proactive_limit: int = Field(default=1, ge=0, le=10)
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    notifications: bool = True

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        return value

    @field_validator("diary_time", "quiet_start", "quiet_end")
    @classmethod
    def valid_time(cls, value):
        time.fromisoformat(value)
        if len(value) != 5:
            raise ValueError("HH:mm required")
        return value


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    day_from: str | None = None
    day_to: str | None = None
    person: str | None = None
    source_type: Literal["personal", "external"] | None = None


class ChatWrite(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    record_id: UUID | None = None


class DiaryEdit(BaseModel):
    base_version: int = Field(ge=1)
    paragraph_id: str
    text: str = Field(max_length=20000)


class MemoryEdit(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    detail: str = Field(min_length=1, max_length=20000)
    people: list[str] = Field(default_factory=list, max_length=20)
    topics: list[str] = Field(default_factory=list, max_length=20)


class PersonEdit(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=30)


class PersonMerge(BaseModel):
    source_ids: list[UUID] = Field(min_length=1, max_length=20)


class ReminderWrite(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    due_at: datetime
    confirmed: bool = False

    @field_validator("due_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value
