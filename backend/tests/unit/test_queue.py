from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
from alembic.config import Config
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from ulid import ULID

from alembic import command
from hephaistos.config import Settings
from hephaistos.domain.errors import ErrorCode
from hephaistos.persistence.database import create_database_engine
from hephaistos.persistence.models import Artifact, Attempt, Generation, SystemState
from hephaistos.queue.broker import ArtifactInfo, JobBroker, LeaseLost

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def queue(tmp_path: Path):
    engine = create_database_engine(tmp_path)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(config, "head")
    broker = JobBroker(engine, Settings())
    yield broker
    engine.dispose()


def job(queue, *, status="QUEUED", model="fake", **overrides):
    identifier = str(ULID())
    values = dict(
        id=identifier,
        status=status,
        model_id=model,
        model_version="1",
        params={"steps": 2},
        seed=42,
        source_filename="display.png",
        source_mime="image/png",
        source_width=1,
        source_height=1,
        source_has_alpha=False,
        source_sha256="a" * 64,
        created_at=NOW,
        queued_at=NOW,
        max_retries=3,
    )
    values.update(overrides)
    with Session(queue.engine) as session, session.begin():
        session.add(Generation(**values))
    return identifier


def get(queue, identifier):
    with Session(queue.engine) as session:
        return session.get(Generation, identifier)


def attempts(queue):
    with Session(queue.engine) as session:
        return session.scalars(select(Attempt).order_by(Attempt.number)).all()


def claim(queue, *, now=NOW, worker="worker"):
    return queue.claim(worker, {"fake"}, now=now, free_vram_bytes=10_000)


def outputs():
    return [
        ArtifactInfo(kind=k, mime=m, size_bytes=10, sha256="b" * 64)
        for k, m in [
            ("SPLAT_PLY", "application/octet-stream"),
            ("PREVIEW_PNG", "image/png"),
            ("CAMERA_JSON", "application/json"),
        ]
    ]


def test_database_pragmas_and_foreign_keys(queue):
    with queue.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_claim_filters_and_oldest_first(queue):
    job(queue, model="missing", created_at=NOW - timedelta(days=3))
    job(queue, not_before=NOW + timedelta(seconds=1))
    job(queue, deleting_at=NOW)
    first = job(queue, created_at=NOW - timedelta(days=1))
    job(queue)
    lease = claim(queue)
    assert lease.generation_id == first
    assert lease.params == {"steps": 2}
    assert get(queue, first).attempt_count == 1
    attempt = attempts(queue)[0]
    assert attempt.trigger == "INITIAL"
    assert attempt.free_vram_at_start_bytes == 10_000
    assert attempt.id == lease.attempt_id
    assert claim(queue) is None  # global RUNNING capacity


@pytest.mark.parametrize("limit", [1, 2])
def test_concurrent_workers_respect_capacity_and_single_claim(queue, limit):
    queue.settings = Settings(gpu_max_concurrent_jobs=limit)
    identifiers = {job(queue) for _ in range(5)}
    barrier = Barrier(5)

    def take(index):
        barrier.wait(timeout=10)
        # Independent engine/pool, as in distinct workers.
        engine = create_database_engine(Path(queue.engine.url.database).parent.parent)
        try:
            return claim(JobBroker(engine, queue.settings), worker=f"worker-{index}")
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=5) as executor:
        leases = [lease for lease in executor.map(take, range(5)) if lease is not None]
    assert len(leases) == limit
    assert len({lease.generation_id for lease in leases}) == limit
    assert {lease.generation_id for lease in leases} <= identifiers
    assert len(attempts(queue)) == limit


def test_dispatch_pause_and_empty_adapters(queue):
    identifier = job(queue)
    assert queue.claim("worker", set(), now=NOW, free_vram_bytes=0) is None
    with Session(queue.engine) as session, session.begin():
        session.add(SystemState(id=1, dispatch_paused=True, updated_at=NOW))
    assert claim(queue) is None
    assert get(queue, identifier).status == "QUEUED"
    with Session(queue.engine) as session, session.begin():
        session.execute(update(SystemState).values(dispatch_paused=False))
    assert claim(queue) is not None


def test_heartbeat_progress_and_expired_lease(queue):
    identifier = job(queue)
    lease = claim(queue)
    queue.heartbeat(lease, now=NOW + timedelta(seconds=30))
    queue.report_progress(lease, stage="sampling", current=1, total=2, now=NOW + timedelta(seconds=31))
    queue.report_progress(
        lease, stage="sampling", current=2, total=2, now=NOW + timedelta(seconds=31, milliseconds=500)
    )
    assert get(queue, identifier).progress_current == 1
    queue.report_progress(lease, stage="decode", now=NOW + timedelta(seconds=32))
    assert get(queue, identifier).progress_current is None
    with pytest.raises(LeaseLost):
        queue.heartbeat(lease, now=NOW + timedelta(seconds=91))
    callback = Mock()
    with pytest.raises(LeaseLost):
        queue.complete(lease, outputs(), promote=callback, now=NOW + timedelta(seconds=91))
    callback.assert_not_called()


def test_success_promotes_before_committing_and_rolls_back_failure(queue):
    identifier = job(queue)
    lease = claim(queue)
    callback = Mock(side_effect=OSError("disk"))
    with pytest.raises(OSError):
        queue.complete(lease, outputs(), promote=callback, now=NOW)
    assert get(queue, identifier).status == "RUNNING"
    assert attempts(queue)[0].outcome is None
    callback = Mock()
    queue.complete(lease, outputs(), promote=callback, now=NOW + timedelta(seconds=1))
    callback.assert_called_once()
    assert get(queue, identifier).status == "COMPLETED"
    assert get(queue, identifier).lease_owner is None
    assert attempts(queue)[0].outcome == "SUCCEEDED"
    with Session(queue.engine) as session:
        artifacts = session.scalars(select(Artifact)).all()
        assert len(artifacts) == 3
        assert all(a.relative_path.startswith(identifier + "/") for a in artifacts)
    with pytest.raises(LeaseLost):
        queue.complete(lease, outputs(), promote=callback, now=NOW)


@pytest.mark.parametrize(
    "code,expected,diagnosis",
    [
        (ErrorCode.GPU_OOM, "QUEUED", False),
        (ErrorCode.NO_OBJECT_DETECTED, "FAILED", False),
        (ErrorCode.INTERNAL, "FAILED", True),
    ],
)
def test_fail_classification(queue, code, expected, diagnosis):
    identifier = job(queue)
    lease = claim(queue)
    queue.fail(lease, code, "failure", now=NOW, error_trace="trace")
    generation = get(queue, identifier)
    assert generation.status == expected
    assert generation.needs_diagnosis is diagnosis
    assert attempts(queue)[0].error_trace == "trace"
    assert attempts(queue)[0].retryable is code.retryable
    if expected == "QUEUED":
        assert claim(queue, now=NOW + timedelta(seconds=29)) is None
        assert claim(queue, now=NOW + timedelta(seconds=30)) is not None
        assert attempts(queue)[1].trigger == "AUTO_RETRY"


def test_retries_exhaust_then_manual_retry_keeps_history(queue):
    identifier = job(queue, max_retries=1)
    first = claim(queue)
    queue.fail(first, ErrorCode.GPU_OOM, "oom", now=NOW)
    second = claim(queue, now=NOW + timedelta(seconds=30))
    queue.fail(second, ErrorCode.GPU_OOM, "oom", now=NOW + timedelta(seconds=31))
    assert get(queue, identifier).status == "FAILED"
    queue.retry(identifier, now=NOW + timedelta(seconds=32))
    third = claim(queue, now=NOW + timedelta(seconds=32))
    assert third.attempt_id not in {first.attempt_id, second.attempt_id}
    assert [a.number for a in attempts(queue)] == [1, 2, 3]
    assert attempts(queue)[-1].trigger == "MANUAL_RETRY"
    assert get(queue, identifier).attempt_count == 1


def test_interruption_does_not_consume_budget_and_resume_trigger(queue):
    identifier = job(queue)
    lease = claim(queue)
    queue.interrupt(lease, now=NOW + timedelta(seconds=1))
    assert get(queue, identifier).attempt_count == 0
    resumed = claim(queue, now=NOW + timedelta(seconds=2))
    assert resumed is not None
    assert attempts(queue)[0].outcome == "INTERRUPTED"
    assert attempts(queue)[1].trigger == "RESUME"


def test_recovery_fences_old_attempt_even_with_same_worker(queue):
    identifier = job(queue)
    first = claim(queue)
    assert not queue.recover_expired(identifier, now=NOW + timedelta(seconds=59))
    assert queue.recover_expired(identifier, now=NOW + timedelta(seconds=60))
    assert attempts(queue)[0].outcome == "ABANDONED"
    second = claim(queue, now=NOW + timedelta(seconds=90))
    assert second.attempt_id != first.attempt_id
    assert attempts(queue)[1].trigger == "RECOVERY"
    callback = Mock()
    with pytest.raises(LeaseLost):
        queue.complete(first, outputs(), promote=callback, now=NOW + timedelta(seconds=91))
    callback.assert_not_called()
    queue.heartbeat(second, now=NOW + timedelta(seconds=91))


@pytest.mark.parametrize("status", ["PENDING", "QUEUED", "RUNNING"])
def test_cancellation(queue, status):
    identifier = job(queue, status="QUEUED" if status == "RUNNING" else status)
    lease = claim(queue) if status == "RUNNING" else None
    queue.cancel(identifier, now=NOW)
    if lease:
        assert get(queue, identifier).status == "RUNNING"
        assert queue.heartbeat(lease, now=NOW) is True
        queue.finish_cancel(lease, now=NOW)
        assert attempts(queue)[0].outcome == "CANCELLED"
    assert get(queue, identifier).status == "CANCELLED"


def test_deleting_generation_cannot_be_retried(queue):
    identifier = job(queue, status="FAILED", deleting_at=NOW)
    with pytest.raises(LeaseLost):
        queue.retry(identifier, now=NOW)


def test_only_one_worker_can_claim_a_single_job(queue):
    queue.settings = Settings(gpu_max_concurrent_jobs=5)
    identifier = job(queue)
    barrier = Barrier(5)

    def take(index):
        barrier.wait(timeout=10)
        return claim(queue, worker=f"worker-{index}")

    with ThreadPoolExecutor(max_workers=5) as executor:
        leases = [lease for lease in executor.map(take, range(5)) if lease is not None]
    assert len(leases) == 1
    assert leases[0].generation_id == identifier
    assert len(attempts(queue)) == 1


def test_retry_schedule_30_60_120_then_failure(queue):
    identifier = job(queue)
    now = NOW
    for delay in [30, 60, 120]:
        lease = claim(queue, now=now)
        queue.fail(lease, ErrorCode.GPU_OOM, "oom", now=now)
        assert claim(queue, now=now + timedelta(seconds=delay - 1)) is None
        now += timedelta(seconds=delay)
    lease = claim(queue, now=now)
    queue.fail(lease, ErrorCode.GPU_OOM, "oom", now=now)
    assert get(queue, identifier).status == "FAILED"
    assert len(attempts(queue)) == 4


def test_wrong_worker_or_attempt_cannot_write(queue):
    from dataclasses import replace

    job(queue)
    lease = claim(queue)
    for stale in [replace(lease, worker_id="other"), replace(lease, attempt_id=str(ULID()))]:
        for write in [
            lambda stale=stale: queue.heartbeat(stale, now=NOW),
            lambda stale=stale: queue.fail(stale, ErrorCode.INTERNAL, "error", now=NOW),
            lambda stale=stale: queue.interrupt(stale, now=NOW),
            lambda stale=stale: queue.report_progress(stale, stage="decode", now=NOW),
        ]:
            with pytest.raises(LeaseLost):
                write()
    assert attempts(queue)[0].outcome is None


def test_recovery_exhausts_budget_and_is_idempotent(queue):
    identifier = job(queue, max_retries=0)
    claim(queue)
    expired = NOW + timedelta(seconds=60)
    assert queue.recover_expired(identifier, now=expired)
    assert not queue.recover_expired(identifier, now=expired)
    assert get(queue, identifier).status == "FAILED"
    assert get(queue, identifier).error_code == "WORKER_LOST"


def test_cancel_blocks_promotion_and_unrequested_cancel_is_refused(queue):
    identifier = job(queue)
    lease = claim(queue)
    with pytest.raises(LeaseLost):
        queue.finish_cancel(lease, now=NOW)
    queue.cancel(identifier, now=NOW)
    callback = Mock()
    with pytest.raises(LeaseLost):
        queue.complete(lease, outputs(), promote=callback, now=NOW)
    callback.assert_not_called()


def test_invalid_progress_and_artifacts_do_not_change_running_job(queue):
    identifier = job(queue)
    lease = claim(queue)
    for stage, current, total in [("bad", None, None), ("sampling", None, None), ("sampling", 3, 2)]:
        with pytest.raises(ValueError):
            queue.report_progress(lease, stage=stage, current=current, total=total, now=NOW)
    callback = Mock()
    with pytest.raises(ValueError):
        queue.complete(lease, outputs()[:1], promote=callback, now=NOW)
    callback.assert_not_called()
    assert get(queue, identifier).progress_stage is None
    assert get(queue, identifier).status == "RUNNING"


def test_migration_defaults_to_configured_data_directory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HEPHAISTOS_DATA_DIR", str(tmp_path))
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_database_engine(tmp_path)
    try:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                == "20261104_queue_trigger"
            )
    finally:
        engine.dispose()


def test_manual_retry_after_interrupted_then_cancelled_is_not_resume(queue):
    identifier = job(queue)
    lease = claim(queue)
    queue.interrupt(lease, now=NOW)
    queue.cancel(identifier, now=NOW)
    queue.retry(identifier, now=NOW)
    claim(queue, now=NOW)
    assert attempts(queue)[-1].trigger == "MANUAL_RETRY"


def test_trigger_migration_preserves_existing_history_and_checks_values(tmp_path: Path):
    from sqlalchemy import MetaData, Table
    from sqlalchemy.exc import IntegrityError

    engine = create_database_engine(tmp_path)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(config, "511f1d25ba38")
    metadata = MetaData()
    generation = Table("generation", metadata, autoload_with=engine)
    attempt = Table("attempt", metadata, autoload_with=engine)
    identifier = str(ULID())
    with engine.begin() as connection:
        connection.execute(
            generation.insert().values(
                id=identifier,
                status="FAILED",
                model_id="fake",
                model_version="1",
                params={},
                seed=1,
                source_filename="image.png",
                source_mime="image/png",
                source_width=1,
                source_height=1,
                source_sha256="a" * 64,
                source_has_alpha=False,
                created_at=NOW,
                max_retries=3,
            )
        )
        connection.execute(
            attempt.insert().values(
                id=str(ULID()),
                generation_id=identifier,
                number=1,
                trigger="INITIAL",
                worker_id="worker",
                started_at=NOW,
                outcome="FAILED",
                stage_durations_ms={},
                log_path="log",
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM attempt").scalar() == 1
        assert connection.exec_driver_sql("SELECT next_attempt_trigger FROM generation").scalar() == "INITIAL"
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("UPDATE generation SET next_attempt_trigger = 'invalid'")
        connection.rollback()
    command.downgrade(config, "511f1d25ba38")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM attempt").scalar() == 1
    engine.dispose()


def test_completion_is_invisible_to_readers_until_promotion(queue):
    identifier = job(queue)
    lease = claim(queue)

    def promote():
        # WAL readers see the old committed state while the broker holds its write lock.
        assert get(queue, identifier).status == "RUNNING"
        with Session(queue.engine) as session:
            assert session.scalar(select(Artifact)) is None

    queue.complete(lease, outputs(), promote=promote, now=NOW)
    assert get(queue, identifier).status == "COMPLETED"


def test_sql_failure_prevents_file_promotion(queue):
    from dataclasses import replace

    from sqlalchemy.exc import IntegrityError

    identifier = job(queue)
    lease = claim(queue)
    artifacts = outputs()
    artifacts[0] = replace(artifacts[0], mime=None)
    callback = Mock()
    with pytest.raises(IntegrityError):
        queue.complete(lease, artifacts, promote=callback, now=NOW)
    callback.assert_not_called()
    assert get(queue, identifier).status == "RUNNING"
    assert attempts(queue)[0].outcome is None


def test_expired_lease_with_cancellation_is_not_requeued(queue):
    identifier = job(queue)
    claim(queue)
    queue.cancel(identifier, now=NOW)
    assert queue.recover_expired(identifier, now=NOW + timedelta(seconds=60))
    assert get(queue, identifier).status == "CANCELLED"
    assert attempts(queue)[0].outcome == "CANCELLED"
