"""Recover interrupted operations and collect old data under database fences."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from hephaistos.config import Settings
from hephaistos.persistence.models import Artifact, Generation, Worker
from hephaistos.queue.broker import JobBroker, LeaseLost, utc
from hephaistos.storage.deletion import DeletionConflict, delete_generation
from hephaistos.storage.local import LocalStorage, UnsafePath, validate_id

LOG = structlog.get_logger()
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


@dataclass
class CycleReport:
    recovered_leases: int = 0
    recovered_pending: int = 0
    deleted_generations: int = 0
    removed_paths: int = 0
    purged_workers: int = 0
    errors: list[str] = field(default_factory=list)


class Janitor:
    def __init__(self, engine: Engine, settings: Settings, *, now: Callable[[], datetime] | None = None) -> None:
        self.engine, self.settings = engine, settings
        self.now = now or (lambda: datetime.now(UTC))
        self.storage = LocalStorage(settings.hephaistos_data_dir)
        self.broker = JobBroker(engine, settings)

    @staticmethod
    def _guard(report: CycleReport, label: str, operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except (OSError, ValueError, SQLAlchemyError, LeaseLost, DeletionConflict) as exc:
            report.errors.append(label)
            LOG.warning("janitor_operation_failed", operation=label, error_type=type(exc).__name__)
            return False

    def _pending(self, identifier: str, now: datetime) -> bool:
        with Session(self.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(Generation, identifier)
            if (
                row is None
                or row.status != "PENDING"
                or row.deleting_at is not None
                or utc(row.created_at) >= now - timedelta(minutes=5)
            ):
                return False
            mime_formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
            fmt = mime_formats.get(row.source_mime)
            ready = False
            try:
                if fmt:
                    original = self.storage.source_file(identifier, fmt).name
                    published = self.storage.source_dir(identifier)
                    staged = self.storage.upload_dir(identifier)
                    folder = published if published.exists() else staged
                    expected = {original, "input.png"}
                    self.storage._check(folder)
                    ready = folder.is_dir() and {p.name for p in folder.iterdir()} == expected
                    if ready:
                        for name in expected:
                            path = self.storage._check(folder / name)
                            if not path.is_file() or path.stat().st_size == 0:
                                ready = False
                        if ready:
                            with (folder / original).open("rb") as source:
                                ready = hashlib.file_digest(source, "sha256").hexdigest() == row.source_sha256
                    if ready and folder == staged:
                        self.storage.promote_sources(identifier, fmt)
            except (OSError, UnsafePath):
                ready = False
            values: dict[str, Any]
            if ready:
                values = dict(
                    status="QUEUED", queued_at=now, error_code=None, error_message=None, needs_diagnosis=False
                )
            else:
                values = dict(
                    status="FAILED",
                    finished_at=now,
                    error_code="INGEST_FAILED",
                    error_message="Interrupted upload is incomplete or could not be published",
                    needs_diagnosis=True,
                )
            session.execute(
                update(Generation)
                .where(Generation.id == identifier, Generation.status == "PENDING", Generation.deleting_at.is_(None))
                .values(**values)
            )
            session.commit()
            return True

    def _old(self, path: Path, cutoff: float) -> bool:
        self.storage._check(path)
        if not path.is_dir() or path.stat().st_mtime >= cutoff:
            return False
        for directory, directories, files in os.walk(path, followlinks=False):
            for name in directories + files:
                child = self.storage._check(Path(directory) / name)
                if child.stat().st_mtime >= cutoff:
                    return False
        return True

    def _collect(self, area: str, path: Path, now: datetime) -> bool:
        hidden = path.name.startswith(".")
        if hidden:
            parts = path.name.split(".")
            if len(parts) < 3 or not parts[2] or area not in {"sources", "results"}:
                return False
            identifier = parts[1]
        else:
            identifier = path.name
        try:
            validate_id(identifier)
        except UnsafePath:
            return False  # Leave unknown names outside the managed layout alone.
        with Session(self.engine) as session:
            # Serialize against claim, publication, ingestion, retry and deletion markers.
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(Generation, identifier)
            if row is not None:
                if row.status in {"PENDING", "RUNNING"} or row.deleting_at is not None:
                    return False
                if not hidden and area not in {"tmp", "results"}:
                    return False
                if area == "results" and not hidden:
                    referenced = session.scalar(
                        select(Artifact.id).where(Artifact.generation_id == identifier).limit(1)
                    )
                    if referenced is not None:
                        return False
            if not self._old(path, (now - timedelta(hours=24)).timestamp()):
                return False
            LOG.info("janitor_remove_path", relative_path=str(path.relative_to(self.storage.root)))
            shutil.rmtree(path)
            session.commit()
            return True

    def _purge_workers(self, now: datetime) -> int:
        with Session(self.engine) as session, session.begin():
            removed = list(
                session.scalars(
                    delete(Worker)
                    .where(
                        Worker.last_heartbeat_at < now - timedelta(days=7),
                        ~Worker.id.in_(
                            select(Generation.lease_owner).where(
                                Generation.status == "RUNNING", Generation.lease_owner.is_not(None)
                            )
                        ),
                    )
                    .returning(Worker.id)
                )
            )
            return len(removed)

    def run_once(self, *, maintenance: bool = True) -> CycleReport:
        now = utc(self.now())
        report = CycleReport()
        with Session(self.engine) as session:
            expired = list(
                session.scalars(
                    select(Generation.id).where(
                        Generation.status == "RUNNING",
                        Generation.lease_expires_at <= now,
                        Generation.deleting_at.is_(None),
                    )
                )
            )
            pending = list(
                session.scalars(
                    select(Generation.id).where(
                        Generation.status == "PENDING",
                        Generation.created_at < now - timedelta(minutes=5),
                        Generation.deleting_at.is_(None),
                    )
                )
            )
            deleting = list(session.scalars(select(Generation.id).where(Generation.deleting_at.is_not(None))))
        for identifier in expired:
            report.recovered_leases += bool(
                self._guard(
                    report,
                    f"lease:{identifier}",
                    partial(self.broker.recover_expired, identifier, now=now),
                )
            )
        for identifier in pending:
            report.recovered_pending += bool(
                self._guard(report, f"pending:{identifier}", partial(self._pending, identifier, now))
            )
        for identifier in deleting:
            report.deleted_generations += bool(
                self._guard(
                    report,
                    f"delete:{identifier}",
                    partial(delete_generation, sessionmaker(self.engine), self.storage, identifier, now=now),
                )
            )
        if maintenance:
            if self.settings.storage_retention_days:
                cutoff = now - timedelta(days=self.settings.storage_retention_days)
                with Session(self.engine) as session:
                    candidates = list(
                        session.scalars(
                            select(Generation.id).where(
                                Generation.status.in_(TERMINAL),
                                Generation.protected.is_(False),
                                Generation.finished_at < cutoff,
                                Generation.deleting_at.is_(None),
                            )
                        )
                    )
                for identifier in candidates:
                    report.deleted_generations += bool(
                        self._guard(
                            report,
                            f"retention:{identifier}",
                            partial(
                                delete_generation,
                                sessionmaker(self.engine),
                                self.storage,
                                identifier,
                                now=now,
                                retention_before=cutoff,
                            ),
                        )
                    )
            for area in ("sources", "results", "logs", "tmp"):

                def collect_area(area: str = area) -> None:
                    folder = self.storage._check(self.storage.root / area)
                    if folder.exists():
                        for path in list(folder.iterdir()):
                            report.removed_paths += bool(
                                self._guard(report, f"path:{area}/{path.name}", partial(self._collect, area, path, now))
                            )

                self._guard(report, f"area:{area}", collect_area)
            report.purged_workers = self._guard(report, "workers", lambda: self._purge_workers(now)) or 0
        return report
