"""SQLite job broker: serialized admission, conditional writes and attempt-fenced leases.

Every write starts BEGIN IMMEDIATE. GPU admission belongs to the supervisor before
claim(). Expired RUNNING rows keep their capacity until explicit recovery. Promotion
runs under the same database write lock as completion, after lease validation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence, Set
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session

from hephaistos.config import Settings
from hephaistos.domain.errors import ErrorCode
from hephaistos.domain.lifecycle import Fail, Requeue, after_failure, after_interruption
from hephaistos.persistence.models import Artifact, Attempt, Generation, SystemState


class LeaseLost(Exception):
    """A stale lease or conflicting state rejected the requested write."""


@dataclass(frozen=True)
class Lease:
    generation_id: str
    attempt_id: str
    worker_id: str
    model_id: str
    model_version: str
    params: dict[str, Any]
    seed: int
    number: int


@dataclass(frozen=True)
class ArtifactInfo:
    kind: str
    mime: str
    size_bytes: int
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


def utc(value: datetime) -> datetime:
    # SQLite returns naive UTC timestamps; public inputs must already represent UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class JobBroker:
    def __init__(self, engine: Engine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    @staticmethod
    def _write(session: Session, generation: Generation, **values: Any) -> None:
        changed = session.scalar(
            update(Generation)
            .where(
                Generation.id == generation.id,
                Generation.status == generation.status,
                Generation.deleting_at.is_(None),
            )
            .values(**values)
            .returning(Generation.id)
        )
        if changed is None:
            raise LeaseLost(generation.id)

    @staticmethod
    def _owned(session: Session, lease: Lease, now: datetime) -> tuple[Generation, Attempt]:
        generation = session.get(Generation, lease.generation_id)
        attempt = session.get(Attempt, lease.attempt_id)
        if (
            generation is None
            or attempt is None
            or generation.status != "RUNNING"
            or generation.deleting_at is not None
            or generation.lease_owner != lease.worker_id
            or generation.lease_expires_at is None
            or utc(generation.lease_expires_at) <= utc(now)
            or attempt.generation_id != lease.generation_id
            or attempt.worker_id != lease.worker_id
            or attempt.outcome is not None
        ):
            raise LeaseLost(lease.generation_id)
        return generation, attempt

    @staticmethod
    def _clear_lease() -> dict[str, Any]:
        return dict(
            lease_owner=None,
            lease_expires_at=None,
            progress_stage=None,
            progress_current=None,
            progress_total=None,
            progress_updated_at=None,
            cancel_requested_at=None,
        )

    def claim(self, worker_id: str, model_ids: Set[str], *, now: datetime, free_vram_bytes: int) -> Lease | None:
        if not model_ids:
            return None
        now = utc(now)
        with self._transaction() as session:
            state = session.get(SystemState, 1)
            paused = state.dispatch_paused if state is not None else self.settings.dispatch_paused
            if paused:
                return None
            running = session.scalar(select(func.count()).select_from(Generation).where(Generation.status == "RUNNING"))
            if running is not None and running >= self.settings.gpu_max_concurrent_jobs:
                return None
            generation = session.scalar(
                select(Generation)
                .where(
                    Generation.status == "QUEUED",
                    Generation.deleting_at.is_(None),
                    Generation.model_id.in_(model_ids),
                    (Generation.not_before.is_(None) | (Generation.not_before <= now)),
                )
                .order_by(Generation.created_at, Generation.id)
                .limit(1)
            )
            if generation is None:
                return None
            previous = session.scalar(
                select(Attempt).where(Attempt.generation_id == generation.id).order_by(Attempt.number.desc()).limit(1)
            )
            attempt = Attempt(
                generation_id=generation.id,
                number=1 if previous is None else previous.number + 1,
                trigger=generation.next_attempt_trigger,
                worker_id=worker_id,
                started_at=now,
                free_vram_at_start_bytes=free_vram_bytes,
                log_path=f"{generation.id}/attempt-{1 if previous is None else previous.number + 1}.jsonl",
            )
            self._write(
                session,
                generation,
                status="RUNNING",
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=self.settings.job_lease_seconds),
                started_at=generation.started_at or now,
                attempt_count=generation.attempt_count + 1,
                not_before=None,
                **{k: v for k, v in self._clear_lease().items() if k not in {"lease_owner", "lease_expires_at"}},
            )
            session.add(attempt)
            session.flush()
            return Lease(
                generation.id,
                attempt.id,
                worker_id,
                generation.model_id,
                generation.model_version,
                generation.params,
                generation.seed,
                attempt.number,
            )

    def heartbeat(self, lease: Lease, *, now: datetime) -> bool:
        """Extend a valid lease and return whether cooperative cancellation was requested."""
        with self._transaction() as session:
            generation, _ = self._owned(session, lease, now)
            cancelled = generation.cancel_requested_at is not None
            self._write(
                session, generation, lease_expires_at=utc(now) + timedelta(seconds=self.settings.job_lease_seconds)
            )
            return cancelled

    def report_progress(
        self, lease: Lease, *, stage: str, now: datetime, current: int | None = None, total: int | None = None
    ) -> None:
        if stage not in {"preprocess", "encode", "sampling", "decode", "export"}:
            raise ValueError("Invalid progress stage")
        if stage == "sampling" and (current is None or total is None or not 0 <= current <= total or total < 1):
            raise ValueError("Invalid sampling progress")
        if stage != "sampling":
            current = total = None
        with self._transaction() as session:
            generation, _ = self._owned(session, lease, now)
            if generation.progress_updated_at is not None and utc(now) - utc(
                generation.progress_updated_at
            ) < timedelta(seconds=1):
                return
            self._write(
                session,
                generation,
                progress_stage=stage,
                progress_current=current,
                progress_total=total,
                progress_updated_at=utc(now),
            )

    def complete(
        self, lease: Lease, artifacts: Sequence[ArtifactInfo], *, promote: Callable[[], object], now: datetime
    ) -> None:
        names = {"SPLAT_PLY": "splat.ply", "PREVIEW_PNG": "preview.png", "CAMERA_JSON": "camera.json"}
        if {a.kind for a in artifacts} != set(names) or len(artifacts) != len(names):
            raise ValueError("Expected exactly one of each result artifact")
        with self._transaction() as session:
            generation, attempt = self._owned(session, lease, now)
            if generation.cancel_requested_at is not None:
                raise LeaseLost("Cancellation requested; acknowledge before completing")
            for artifact in artifacts:
                session.add(
                    Artifact(
                        generation_id=generation.id,
                        kind=artifact.kind,
                        relative_path=f"{generation.id}/{names[artifact.kind]}",
                        mime=artifact.mime,
                        size_bytes=artifact.size_bytes,
                        sha256=artifact.sha256,
                        metadata_=artifact.metadata,
                        created_at=utc(now),
                    )
                )
            self._write(
                session,
                generation,
                status="COMPLETED",
                finished_at=utc(now),
                error_code=None,
                error_message=None,
                needs_diagnosis=False,
                **self._clear_lease(),
            )
            attempt.outcome = "SUCCEEDED"
            attempt.finished_at = utc(now)
            # Detect SQL/constraint errors before touching the filesystem. Readers still
            # see RUNNING until the transaction commits after successful promotion.
            session.flush()
            promote()

    def _failure(
        self,
        session: Session,
        generation: Generation,
        attempt: Attempt,
        code: ErrorCode,
        message: str,
        *,
        now: datetime,
        error_trace: str | None,
        outcome: str,
    ) -> None:
        decision = after_failure(
            code,
            attempt_count=generation.attempt_count,
            max_retries=generation.max_retries,
            backoff_s=self.settings.job_retry_backoff_seconds,
            backoff_max_s=self.settings.job_retry_backoff_max_seconds,
        )
        retry = isinstance(decision, Requeue)
        self._write(
            session,
            generation,
            status="QUEUED" if retry else "FAILED",
            next_attempt_trigger="RECOVERY" if outcome == "ABANDONED" else "AUTO_RETRY",
            queued_at=utc(now) if retry else generation.queued_at,
            not_before=utc(now) + decision.delay if isinstance(decision, Requeue) else None,
            finished_at=None if retry else utc(now),
            error_code=code.value,
            error_message=message,
            needs_diagnosis=isinstance(decision, Fail) and decision.needs_diagnosis,
            **self._clear_lease(),
        )
        attempt.outcome = outcome
        attempt.finished_at = utc(now)
        attempt.error_code = code.value
        attempt.error_message = message
        attempt.error_trace = error_trace
        attempt.retryable = code.retryable

    def fail(
        self, lease: Lease, code: ErrorCode, message: str, *, now: datetime, error_trace: str | None = None
    ) -> None:
        with self._transaction() as session:
            generation, attempt = self._owned(session, lease, now)
            self._failure(
                session, generation, attempt, code, message, now=now, error_trace=error_trace, outcome="FAILED"
            )

    def interrupt(self, lease: Lease, *, now: datetime) -> None:
        with self._transaction() as session:
            generation, attempt = self._owned(session, lease, now)
            self._write(
                session,
                generation,
                status="QUEUED",
                queued_at=utc(now),
                not_before=None,
                attempt_count=after_interruption(generation.attempt_count),
                next_attempt_trigger="RESUME",
                **self._clear_lease(),
            )
            attempt.outcome = "INTERRUPTED"
            attempt.finished_at = utc(now)

    def recover_expired(self, identifier: str, *, now: datetime) -> bool:
        with self._transaction() as session:
            generation = session.get(Generation, identifier)
            if (
                generation is None
                or generation.status != "RUNNING"
                or generation.deleting_at is not None
                or generation.lease_expires_at is None
                or utc(generation.lease_expires_at) > utc(now)
            ):
                return False
            attempt = session.scalar(
                select(Attempt)
                .where(Attempt.generation_id == identifier, Attempt.outcome.is_(None))
                .order_by(Attempt.number.desc())
                .limit(1)
            )
            if attempt is None:
                raise LeaseLost("RUNNING generation has no open attempt")
            self._failure(
                session,
                generation,
                attempt,
                ErrorCode.WORKER_LOST,
                "Lease expired",
                now=now,
                error_trace=None,
                outcome="ABANDONED",
            )
            return True

    def retry(self, identifier: str, *, now: datetime) -> None:
        with self._transaction() as session:
            generation = session.get(Generation, identifier)
            if generation is None or generation.status not in {"FAILED", "CANCELLED"}:
                raise LeaseLost(identifier)
            self._write(
                session,
                generation,
                status="QUEUED",
                queued_at=utc(now),
                finished_at=None,
                not_before=None,
                attempt_count=0,
                next_attempt_trigger="MANUAL_RETRY",
                max_retries=self.settings.job_max_retries,
                error_code=None,
                error_message=None,
                needs_diagnosis=False,
                **self._clear_lease(),
            )

    def cancel(self, identifier: str, *, now: datetime) -> None:
        with self._transaction() as session:
            generation = session.get(Generation, identifier)
            if generation is None or generation.status not in {"PENDING", "QUEUED", "RUNNING"}:
                raise LeaseLost(identifier)
            if generation.status == "RUNNING":
                self._write(session, generation, cancel_requested_at=utc(now))
            else:
                self._write(session, generation, status="CANCELLED", finished_at=utc(now), **self._clear_lease())

    def finish_cancel(self, lease: Lease, *, now: datetime) -> None:
        with self._transaction() as session:
            generation, attempt = self._owned(session, lease, now)
            if generation.cancel_requested_at is None:
                raise LeaseLost("Cancellation was not requested")
            self._write(session, generation, status="CANCELLED", finished_at=utc(now), **self._clear_lease())
            attempt.outcome = "CANCELLED"
            attempt.finished_at = utc(now)
