"""Singleton dispatch state; defaults are applied only when the row is first created."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from hephaistos.config import Settings
from hephaistos.persistence.models import SystemState


class DispatchState(BaseModel):
    model_config = ConfigDict(frozen=True)

    paused: bool
    paused_by: Literal["user", "config", "system"] | None
    reason: str | None
    since: datetime | None


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def snapshot(row: SystemState) -> DispatchState:
    since = row.paused_at
    if since is not None:
        since = as_utc(since)
    return DispatchState(paused=row.dispatch_paused, paused_by=row.paused_by, reason=row.paused_reason, since=since)


class DispatchService:
    def __init__(self, engine: Engine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with Session(self.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def _ensure(self, session: Session, now: datetime) -> SystemState:
        now = as_utc(now)
        row = session.get(SystemState, 1)
        if row is None:
            paused = self.settings.dispatch_paused
            row = SystemState(
                id=1,
                dispatch_paused=paused,
                paused_by="config" if paused else None,
                paused_reason="DISPATCH_PAUSED" if paused else None,
                paused_at=now if paused else None,
                updated_at=now,
            )
            session.add(row)
            session.flush()
        return row

    def initialize(self, *, now: datetime) -> DispatchState:
        with self._transaction() as session:
            return snapshot(self._ensure(session, now))

    def get(self, *, now: datetime) -> DispatchState:
        # Existing rows are read without a write lock, including during promotion.
        with Session(self.engine) as session:
            row = session.get(SystemState, 1)
            if row is not None:
                return snapshot(row)
        return self.initialize(now=now)

    def set(self, *, paused: bool, reason: str | None = None, now: datetime) -> DispatchState:
        now = as_utc(now)
        reason = (reason.strip() or None) if paused and reason is not None else None
        with self._transaction() as session:
            row = self._ensure(session, now)
            actor = "user" if paused else None
            if row.dispatch_paused != paused or row.paused_reason != reason or row.paused_by != actor:
                row.dispatch_paused = paused
                row.paused_by = actor
                row.paused_reason = reason
                row.paused_at = now if paused else None
                row.updated_at = now
            return snapshot(row)
