"""Portable relational schema (data-model.md §1)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ulid import ULID


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(ULID())


def enum_type(name: str, *values: str) -> Enum:
    return Enum(*values, name=name, native_enum=False, create_constraint=True)


class Generation(Base):
    __tablename__ = "generation"
    __table_args__ = (
        CheckConstraint(
            "next_attempt_trigger IN ('INITIAL', 'AUTO_RETRY', 'MANUAL_RETRY', 'RECOVERY', 'RESUME')",
            name="ck_generation_next_attempt_trigger",
        ),
        Index("ix_generation_claim", "status", "not_before", "created_at"),
        Index("ix_generation_finished_at", "finished_at"),
        Index("ix_generation_lease_expires_at", "lease_expires_at"),
    )
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    owner_id: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        enum_type("generation_status", "PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
    )
    next_attempt_trigger: Mapped[str] = mapped_column(String(16), default="INITIAL", server_default="INITIAL")
    preset: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    seed: Mapped[int] = mapped_column(BigInteger)
    source_filename: Mapped[str] = mapped_column(Text)
    source_mime: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(Text)
    source_width: Mapped[int] = mapped_column(Integer)
    source_height: Mapped[int] = mapped_column(Integer)
    source_has_alpha: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_stage: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    progress_current: Mapped[int | None] = mapped_column(Integer)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer)
    needs_diagnosis: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    protected: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    retry_of: Mapped[str | None] = mapped_column(String(26))


class Attempt(Base):
    __tablename__ = "attempt"
    __table_args__ = (UniqueConstraint("generation_id", "number", name="uq_attempt_generation_number"),)
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(String(26), ForeignKey("generation.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(
        enum_type("attempt_trigger", "INITIAL", "AUTO_RETRY", "MANUAL_RETRY", "RECOVERY", "RESUME")
    )
    worker_id: Mapped[str] = mapped_column(Text)
    log_path: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(
        enum_type("attempt_outcome", "SUCCEEDED", "FAILED", "ABANDONED", "INTERRUPTED", "CANCELLED")
    )
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    error_trace: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    stage_durations_ms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    peak_vram_bytes: Mapped[int | None] = mapped_column(BigInteger)
    free_vram_at_start_bytes: Mapped[int | None] = mapped_column(BigInteger)


class Artifact(Base):
    __tablename__ = "artifact"
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(String(26), ForeignKey("generation.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(enum_type("artifact_kind", "SPLAT_PLY", "PREVIEW_PNG", "CAMERA_JSON"))
    relative_path: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Worker(Base):
    __tablename__ = "worker"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(
        enum_type("worker_state", "NO_GPU", "IDLE", "LOADING", "READY", "BUSY", "WAITING_GPU", "DEGRADED", "STOPPING")
    )
    waiting_reason: Mapped[str | None] = mapped_column(Text)
    gpu_free_bytes: Mapped[int] = mapped_column(BigInteger)
    inference_pid: Mapped[int | None] = mapped_column(Integer)
    inference_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    child_deaths_window: Mapped[list[str]] = mapped_column(JSON, default=list)
    adapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    gpu: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    current_generation_id: Mapped[str | None] = mapped_column(String(26))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemState(Base):
    __tablename__ = "system_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_system_state_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingest_reservations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, server_default="{}")
    dispatch_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    paused_reason: Mapped[str | None] = mapped_column(Text)
    paused_by: Mapped[str | None] = mapped_column(enum_type("paused_by", "user", "config", "system"))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
