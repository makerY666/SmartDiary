import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


def uid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="text")
    text: Mapped[str] = mapped_column(Text, default="")
    original_text: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    error: Mapped[str] = mapped_column(String(200), default="")
    source_type: Mapped[str] = mapped_column(String(20), default="personal")
    superseded_by: Mapped[str] = mapped_column(String(36), default="")


class RecordRevision(Base):
    __tablename__ = "record_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    record_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(20), default="accepted")


class RecordLink(Base):
    __tablename__ = "record_links"
    __table_args__ = (UniqueConstraint("user_id", "child_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    child_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    relation: Mapped[str] = mapped_column(String(20), default="followup")


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    preserve_text: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    extracted_text: Mapped[str] = mapped_column(Text, default="")


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id"), index=True)
    record_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="event")
    people: Mapped[list] = mapped_column(JSON, default=list)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source_start: Mapped[int] = mapped_column(Integer, default=0)
    source_end: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    vector: Mapped[list | None] = mapped_column(Vector(1024).with_variant(JSON, "sqlite"), nullable=True)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class Diary(Base):
    __tablename__ = "diaries"
    __table_args__ = (UniqueConstraint("user_id", "day", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    paragraphs: Mapped[list] = mapped_column(JSON, default=list)
    timeline: Mapped[list] = mapped_column(JSON, default=list)
    pending: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Change(Base):
    __tablename__ = "changes"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    dedupe_key: Mapped[str] = mapped_column(String(180), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str] = mapped_column(String(200), default="")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    proactive: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)


class Usage(Base):
    __tablename__ = "usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    month: Mapped[str] = mapped_column(String(7), index=True)
    reserved_yuan: Mapped[float] = mapped_column(Float, default=0)
    spent_yuan: Mapped[float] = mapped_column(Float, default=0)
    state: Mapped[str] = mapped_column(String(20), default="reserved")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class Tombstone(Base):
    __tablename__ = "tombstones"
    __table_args__ = (UniqueConstraint("user_id", "record_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    record_id: Mapped[str] = mapped_column(String(36), index=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
