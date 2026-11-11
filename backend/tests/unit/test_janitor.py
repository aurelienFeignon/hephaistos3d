import hashlib
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from ulid import ULID

from hephaistos.api.app import create_app
from hephaistos.config import Settings
from hephaistos.janitor.service import Janitor
from hephaistos.persistence.models import Artifact, Attempt, Generation, Worker
from hephaistos.storage.deletion import delete_generation
from hephaistos.storage.local import LocalStorage
from tests.api_client import TestClient
from tests.unit.test_queue import NOW, claim, get, job
from tests.unit.test_queue import queue as queue


@pytest.fixture
def janitor(queue, tmp_path):
    return Janitor(queue.engine, Settings(hephaistos_data_dir=tmp_path), now=lambda: NOW)


def sources(janitor, identifier, *, staged=False):
    path = janitor.storage.upload_dir(identifier) if staged else janitor.storage.source_dir(identifier)
    path.mkdir(parents=True)
    (path / "original.png").write_bytes(b"original")
    (path / "input.png").write_bytes(b"normalized")
    with Session(janitor.engine) as session, session.begin():
        session.execute(
            update(Generation)
            .where(Generation.id == identifier)
            .values(source_sha256=hashlib.sha256(b"original").hexdigest())
        )
    return path


def old_tree(path):
    stamp = (NOW - timedelta(days=2)).timestamp()
    for entry in [*path.rglob("*"), path]:
        os.utime(entry, (stamp, stamp), follow_symlinks=False)


def test_expired_lease_is_abandoned_and_requeued(janitor, queue):
    identifier = job(queue)
    lease = claim(queue, now=NOW - timedelta(minutes=2))
    assert lease is not None
    report = janitor.run_once()
    assert report.recovered_leases == 1
    row = get(queue, identifier)
    assert row.status == "QUEUED" and row.error_code == "WORKER_LOST"
    with Session(queue.engine) as session:
        assert session.scalar(select(Attempt)).outcome == "ABANDONED"
    assert janitor.run_once().recovered_leases == 0


def test_live_lease_is_untouched(janitor, queue):
    identifier = job(queue)
    claim(queue)
    assert janitor.run_once().recovered_leases == 0
    assert get(queue, identifier).status == "RUNNING"


@pytest.mark.parametrize("staged", [False, True])
def test_pending_complete_sources_recovered(janitor, queue, staged):
    identifier = job(queue, status="PENDING", queued_at=None, created_at=NOW - timedelta(minutes=6))
    sources(janitor, identifier, staged=staged)
    assert janitor.run_once().recovered_pending == 1
    row = get(queue, identifier)
    assert row.status == "QUEUED" and row.queued_at.replace(tzinfo=NOW.tzinfo) == NOW
    assert janitor.storage.source_file(identifier, "PNG").is_file()
    assert janitor.run_once().recovered_pending == 0


def test_pending_missing_or_incomplete_sources_fails(janitor, queue):
    identifier = job(queue, status="PENDING", queued_at=None, created_at=NOW - timedelta(minutes=6))
    path = sources(janitor, identifier)
    (path / "input.png").unlink()
    janitor.run_once()
    row = get(queue, identifier)
    assert row.status == "FAILED" and row.error_code == "INGEST_FAILED"
    assert row.needs_diagnosis is True


def test_recent_pending_and_cancelled_are_never_revived(janitor, queue):
    recent = job(queue, status="PENDING", queued_at=None, created_at=NOW - timedelta(minutes=4))
    cancelled = job(queue, status="CANCELLED", created_at=NOW - timedelta(minutes=6))
    sources(janitor, recent)
    sources(janitor, cancelled)
    janitor.run_once()
    assert get(queue, recent).status == "PENDING"
    assert get(queue, cancelled).status == "CANCELLED"


def test_retention_preserves_protected_active_and_recent(janitor, queue):
    expired = NOW - timedelta(days=31)
    deleted = job(queue, status="COMPLETED", finished_at=expired)
    protected = job(queue, status="FAILED", finished_at=expired, protected=True)
    running = job(queue, status="RUNNING", finished_at=expired)
    queued = job(queue, finished_at=expired)
    recent = job(queue, status="COMPLETED", finished_at=NOW)
    sources(janitor, deleted)
    assert janitor.run_once().deleted_generations == 1
    assert get(queue, deleted) is None
    assert not janitor.storage.source_dir(deleted).exists()
    for identifier in (protected, running, queued, recent):
        assert get(queue, identifier) is not None


def test_announced_auto_delete_after_is_when_the_janitor_deletes(janitor, queue):
    """P4.8 : la date publiée par l'API est le seuil réel de la rétention, à la seconde près."""
    due = job(queue, status="COMPLETED", finished_at=NOW - timedelta(days=30, seconds=1))
    early = job(queue, status="COMPLETED", finished_at=NOW - timedelta(days=30) + timedelta(seconds=1))
    with TestClient(create_app(engine=queue.engine, settings=janitor.settings, now=lambda: NOW)) as client:
        announced = {
            identifier: datetime.fromisoformat(
                client.get(f"/api/v1/generations/{identifier}").json()["auto_delete_after"]
            )
            for identifier in (due, early)
        }
    assert announced[due] < NOW < announced[early]
    janitor.run_once()
    assert get(queue, due) is None
    assert get(queue, early) is not None


def test_zero_retention_disables_automatic_deletion(janitor, queue):
    janitor.settings = Settings(hephaistos_data_dir=janitor.storage.root, storage_retention_days=0)
    identifier = job(queue, status="FAILED", finished_at=NOW - timedelta(days=1000))
    janitor.run_once()
    assert get(queue, identifier) is not None


def test_retention_rechecks_protection_at_marker_commit(janitor, queue):
    identifier = job(queue, status="COMPLETED", finished_at=NOW - timedelta(days=31), protected=True)
    assert (
        delete_generation(sessionmaker(queue.engine), janitor.storage, identifier, now=NOW, retention_before=NOW)
        is False
    )
    assert get(queue, identifier).deleting_at is None


def test_failed_delete_is_replayed_next_cycle(janitor, queue):
    identifier = job(queue, status="COMPLETED", deleting_at=NOW, protected=True)
    path = sources(janitor, identifier)
    with patch.object(LocalStorage, "remove_generation", side_effect=PermissionError("blocked")):
        report = janitor.run_once()
    assert report.errors and get(queue, identifier).deleting_at is not None
    assert path.exists()
    assert janitor.run_once().deleted_generations == 1
    assert get(queue, identifier) is None and not path.exists()


def test_cleanup_only_old_orphans_and_inactive_tmp(janitor, queue):
    orphan = str(ULID())
    old = sources(janitor, orphan)
    old_tree(old)
    fresh = sources(janitor, str(ULID()))
    live = job(queue, status="RUNNING")
    active = janitor.storage.attempt_dir(live, str(ULID()))
    active.mkdir(parents=True)
    old_tree(active.parent)
    inactive = job(queue, status="COMPLETED", finished_at=NOW)
    abandoned = janitor.storage.attempt_dir(inactive, str(ULID()))
    abandoned.mkdir(parents=True)
    old_tree(abandoned.parent)
    owned = sources(janitor, inactive)
    old_tree(owned)
    report = janitor.run_once()
    assert report.removed_paths == 2
    assert not old.exists() and not abandoned.parent.exists()
    assert fresh.exists() and active.exists() and owned.exists()


def test_new_child_prevents_orphan_cleanup(janitor):
    path = sources(janitor, str(ULID()))
    old_tree(path)
    stamp = NOW.timestamp()
    os.utime(path / "input.png", (stamp, stamp))
    janitor.run_once()
    assert path.exists()


def test_symlink_is_never_followed(janitor, tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    (external / "keep").write_text("keep")
    path = janitor.storage.source_dir(str(ULID()))
    path.parent.mkdir(parents=True)
    path.symlink_to(external, target_is_directory=True)
    old_tree(external)
    report = janitor.run_once()
    assert report.errors
    assert (external / "keep").read_text() == "keep"
    assert path.is_symlink()


def test_old_unreferenced_results_and_hidden_staging_are_removed(janitor, queue):
    identifier = job(queue, status="FAILED", finished_at=NOW)
    path = janitor.storage.result_dir(identifier)
    path.mkdir(parents=True)
    (path / "splat.ply").write_bytes(b"orphan")
    old_tree(path)
    hidden = path.parent / f".{identifier}.interrupted"
    hidden.mkdir()
    old_tree(hidden)
    report = janitor.run_once()
    assert report.removed_paths == 2
    assert not path.exists() and not hidden.exists()


def test_referenced_results_survive_cleanup(janitor, queue):
    identifier = job(queue, status="COMPLETED", finished_at=NOW)
    path = janitor.storage.result_dir(identifier)
    path.mkdir(parents=True)
    (path / "splat.ply").write_bytes(b"kept")
    old_tree(path)
    with Session(queue.engine) as session, session.begin():
        session.add(
            Artifact(
                generation_id=identifier,
                kind="SPLAT_PLY",
                relative_path="unused",
                mime="application/octet-stream",
                sha256="a" * 64,
                size_bytes=4,
                created_at=NOW,
            )
        )
    janitor.run_once()
    assert path.exists()


def test_stale_workers_purged_but_running_owner_preserved(janitor, queue):
    for identifier, days in [("old", 8), ("owner", 8), ("recent", 6)]:
        with Session(queue.engine) as session, session.begin():
            session.add(
                Worker(
                    id=identifier,
                    state="IDLE",
                    gpu_free_bytes=0,
                    started_at=NOW - timedelta(days=days),
                    last_heartbeat_at=NOW - timedelta(days=days),
                )
            )
    job(queue, status="RUNNING", lease_owner="owner")
    assert janitor.run_once().purged_workers == 1
    with Session(queue.engine) as session:
        assert {w.id for w in session.scalars(select(Worker))} == {"owner", "recent"}


def test_pending_bad_hash_is_never_enqueued(janitor, queue):
    identifier = job(queue, status="PENDING", queued_at=None, created_at=NOW - timedelta(minutes=6))
    path = sources(janitor, identifier)
    (path / "original.png").write_bytes(b"corrupted")
    janitor.run_once()
    assert get(queue, identifier).status == "FAILED"


def test_pending_promotion_failure_records_diagnosis(janitor, queue):
    identifier = job(queue, status="PENDING", queued_at=None, created_at=NOW - timedelta(minutes=6))
    sources(janitor, identifier, staged=True)
    with patch.object(LocalStorage, "promote_sources", side_effect=PermissionError("blocked")):
        janitor.run_once()
    row = get(queue, identifier)
    assert row.status == "FAILED" and row.error_code == "INGEST_FAILED" and row.needs_diagnosis


def test_pending_deletion_marker_wins_over_recovery(janitor, queue):
    identifier = job(queue, status="PENDING", queued_at=None, created_at=NOW - timedelta(minutes=6), deleting_at=NOW)
    sources(janitor, identifier)
    report = janitor.run_once()
    assert report.recovered_pending == 0 and report.deleted_generations == 1
    assert get(queue, identifier) is None


def test_pending_recovery_rechecks_state_after_selection(janitor, queue):
    identifier = job(queue, status="CANCELLED", created_at=NOW - timedelta(minutes=6))
    sources(janitor, identifier)
    assert janitor._pending(identifier, NOW) is False
    assert get(queue, identifier).status == "CANCELLED"


def test_retention_rechecks_active_state_after_selection(janitor, queue):
    identifier = job(queue, status="QUEUED", finished_at=NOW - timedelta(days=31))
    assert (
        delete_generation(sessionmaker(queue.engine), janitor.storage, identifier, now=NOW, retention_before=NOW)
        is False
    )
    assert get(queue, identifier).deleting_at is None


def test_fast_cycles_skip_expensive_maintenance(janitor, queue):
    identifier = job(queue, status="COMPLETED", finished_at=NOW - timedelta(days=31))
    janitor.run_once(maintenance=False)
    assert get(queue, identifier) is not None
    janitor.run_once()
    assert get(queue, identifier) is None


def test_loop_recovers_frequently_and_schedules_maintenance(janitor):
    import json
    from threading import Event

    from hephaistos.janitor.runner import run_loop
    from hephaistos.janitor.service import CycleReport

    elapsed = [0.0]
    calls = []
    stop = Event()

    def cycle(*, maintenance):
        calls.append(maintenance)
        if len(calls) == 3:
            stop.set()
        return CycleReport()

    def wait(seconds):
        assert seconds == 20
        elapsed[0] += 86400
        return stop.is_set()

    with patch.object(janitor, "run_once", cycle), patch.object(stop, "wait", wait):
        run_loop(janitor, stop, monotonic=lambda: elapsed[0])
    assert calls == [True, True, True]
    state = json.loads((janitor.storage.root / "janitor-status.json").read_text())
    assert state["ok"] is True and state["error_count"] == 0


def test_loop_separates_recovery_ticks_from_daily_maintenance(janitor):
    from threading import Event

    from hephaistos.janitor.runner import run_loop
    from hephaistos.janitor.service import CycleReport

    elapsed = [0.0]
    calls = []
    stop = Event()

    def cycle(*, maintenance):
        calls.append(maintenance)
        if len(calls) == 3:
            stop.set()
        return CycleReport()

    def wait(seconds):
        elapsed[0] += seconds
        return stop.is_set()

    with patch.object(janitor, "run_once", cycle), patch.object(stop, "wait", wait):
        run_loop(janitor, stop, monotonic=lambda: elapsed[0])
    assert calls == [True, False, False]


def test_failed_cycle_does_not_publish_success_heartbeat(janitor):
    from threading import Event

    from sqlalchemy.exc import SQLAlchemyError

    from hephaistos.janitor.runner import run_loop

    stop = Event()

    def failed(*, maintenance):
        stop.set()
        raise SQLAlchemyError("unavailable")

    with patch.object(janitor, "run_once", failed), patch.object(stop, "wait", return_value=True):
        run_loop(janitor, stop)
    assert not (janitor.storage.root / "janitor-status.json").exists()


def test_heartbeat_marks_partial_cycle_failure(janitor):
    import json

    from hephaistos.janitor.runner import heartbeat
    from hephaistos.janitor.service import CycleReport

    heartbeat(janitor, CycleReport(errors=["failed operation"]))
    state = json.loads((janitor.storage.root / "janitor-status.json").read_text())
    assert state["ok"] is False and state["error_count"] == 1


def test_entrypoint_help_does_not_load_cuda():
    import subprocess
    import sys

    code = "import sys; import hephaistos.janitor.__main__; assert 'torch' not in sys.modules"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0
    result = subprocess.run([sys.executable, "-m", "hephaistos.janitor", "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "--once" in result.stdout


def test_zero_day_retention_simulated_with_cutoff_preserves_protected_and_running(janitor, queue):
    # An injected cutoff simulates immediate retention; setting retention_days=0 disables it.
    expired = NOW - timedelta(seconds=1)
    identifiers = [
        job(queue, status="COMPLETED", finished_at=expired),
        job(queue, status="COMPLETED", finished_at=expired, protected=True),
        job(queue, status="RUNNING", finished_at=expired),
    ]
    paths = [sources(janitor, identifier) for identifier in identifiers]
    removed = [
        delete_generation(sessionmaker(queue.engine), janitor.storage, identifier, now=NOW, retention_before=NOW)
        for identifier in identifiers
    ]
    assert removed == [True, False, False]
    assert get(queue, identifiers[0]) is None and not paths[0].exists()
    for identifier, path in zip(identifiers[1:], paths[1:], strict=True):
        assert get(queue, identifier) is not None and path.exists()
        assert get(queue, identifier).deleting_at is None
