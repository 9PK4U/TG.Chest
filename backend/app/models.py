import time
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .config import get_settings
from .db import Base


def uid():
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    vault: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    bot_ai_policy: Mapped[str] = mapped_column(String(12), default="external")


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("folders.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7), default="#bba6f8")


class Entry(Base):
    __tablename__ = "entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    folder_id: Mapped[str | None] = mapped_column(ForeignKey("folders.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(12), default="note")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    index_status: Mapped[str] = mapped_column(String(20), default="pending")
    source: Mapped[str] = mapped_column(String(20), default="miniapp")
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ai_policy: Mapped[str] = mapped_column(String(12), default="external")
    capture_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    __table_args__ = (UniqueConstraint("user_id", "telegram_message_id"), Index("entries_user_updated", "user_id", "updated_at"))


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    entry_id: Mapped[str] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(BigInteger)
    storage: Mapped[str] = mapped_column(String(16), default="local")
    storage_path: Mapped[str] = mapped_column(Text, default="")
    stage_path: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    extracted_text: Mapped[str] = mapped_column(Text, default="")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    entry_id: Mapped[str] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector(get_settings().embedding_dimensions).with_variant(JSON(), "sqlite"), nullable=True)
    model: Mapped[str] = mapped_column(String(120), default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    locked_until: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error: Mapped[str] = mapped_column(String(120), default="")


class Capture(Base):
    """Encrypted immutable input + model proposal, retained independently of notes."""
    __tablename__ = "captures"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    policy: Mapped[str] = mapped_column(String(12))
    mode: Mapped[str] = mapped_column(String(12), default="organize")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    payload: Mapped[str] = mapped_column(Text)
    result_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIConfiguration(Base):
    __tablename__ = "ai_configurations"
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
