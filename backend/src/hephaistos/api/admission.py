"""SQLite-fenced upload slots and conservative space reservations across API processes."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from hephaistos.api.errors import ResourceError
from hephaistos.config import Settings
from hephaistos.persistence.models import Generation, SystemState, new_id
from hephaistos.queue.broker import utc
from hephaistos.storage.local import LocalStorage

LEASE_SECONDS = 120


class Admission:
    def __init__(self, engine: Engine, settings: Settings) -> None:
        self.engine, self.settings = engine, settings
        self.storage = LocalStorage(settings.hephaistos_data_dir)

    @contextmanager
    def transaction(self) -> Iterator[tuple[Session, SystemState]]:
        with Session(self.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(SystemState, 1)
            if row is None:
                raise ResourceError(503, "DATABASE_UNAVAILABLE", "System state is not initialized")
            yield session, row
            session.commit()

    @property
    def job_budget(self) -> int:
        # Keep both copies across EXDEV; allow RGBA PNG worst case plus container headers/logs.
        return 2 * (
            self.settings.upload_max_bytes
            + self.settings.upload_max_pixels * 4
            + self.settings.generation_result_budget_bytes
            + 4 * 1024**2
        )

    def usage(self) -> tuple[int, int]:
        used = 0
        for area in ("db", "sources", "results", "logs", "tmp"):
            root = self.storage._check(self.storage.root / area)
            for directory, _, files in os.walk(root, followlinks=False):
                for name in files:
                    path = self.storage._check(Path(directory) / name)
                    try:
                        used += path.stat().st_size
                    except FileNotFoundError:
                        pass  # A completed delete or worker rename raced the accounting scan.
        return used, self.storage.free_bytes()

    def check(self, session: Session, *, now: datetime, upload: bool = False) -> dict[str, str]:
        """Caller must hold the SQLite write lock until creating or requeuing its job."""
        now = utc(now)
        row = session.get(SystemState, 1)
        if row is None:
            raise ResourceError(503, "DATABASE_UNAVAILABLE", "System state is not initialized")
        live = {
            key: expiry for key, expiry in row.ingest_reservations.items() if utc(datetime.fromisoformat(expiry)) > now
        }
        if upload and len(live) >= self.settings.upload_max_concurrent:
            raise ResourceError(429, "UPLOAD_CAPACITY_EXCEEDED", "All upload slots are occupied")
        active = (
            session.scalar(
                select(func.count())
                .select_from(Generation)
                .where(Generation.status.in_(["PENDING", "QUEUED", "RUNNING"]), Generation.deleting_at.is_(None))
            )
            or 0
        )
        if active + len(live) >= self.settings.queue_max_active:
            raise ResourceError(429, "QUEUE_CAPACITY_EXCEEDED", "Active generation quota reached")
        used, free = self.usage()
        budget = (active + len(live) + 1) * self.job_budget
        if used + budget > self.settings.storage_max_bytes:
            raise ResourceError(507, "STORAGE_QUOTA_EXCEEDED", "Storage quota cannot cover this generation")
        if free < self.settings.storage_min_free_bytes + budget:
            raise ResourceError(507, "STORAGE_SPACE_INSUFFICIENT", "Insufficient unreserved free disk space")
        return live

    def reserve(self, *, now: datetime) -> str:
        now = utc(now)
        with self.transaction() as (session, row):
            live = self.check(session, now=now, upload=True)
            identifier = new_id()
            live[identifier] = (now + timedelta(seconds=LEASE_SECONDS)).isoformat()
            row.ingest_reservations = live
            return identifier

    def renew(self, identifier: str, *, now: datetime) -> None:
        with self.transaction() as (_session, row):
            leases = dict(row.ingest_reservations)
            expiry = leases.get(identifier)
            if expiry is None or utc(datetime.fromisoformat(expiry)) <= utc(now):
                raise ResourceError(503, "UPLOAD_LEASE_LOST", "Upload admission lease expired")
            leases[identifier] = (utc(now) + timedelta(seconds=LEASE_SECONDS)).isoformat()
            row.ingest_reservations = leases

    def release(self, identifier: str) -> None:
        with self.transaction() as (_session, row):
            leases = dict(row.ingest_reservations)
            leases.pop(identifier, None)
            row.ingest_reservations = leases
