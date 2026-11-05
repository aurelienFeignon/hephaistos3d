import io
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from ulid import ULID

from hephaistos.observability import AttemptJournal, JSONFormatter, logging_context
from hephaistos.queue.broker import Lease
from hephaistos.storage.local import LocalStorage
from hephaistos.worker.inference import LogEvent, StageEvent, StepEvent
from tests.unit.test_queue import NOW, attempts, get
from tests.unit.test_queue import queue as queue
from tests.unit.test_supervisor import rig as rig
from tests.unit.test_supervisor import submit, until


def rows(supervisor, identifier, number=1):
    return [json.loads(line) for line in supervisor.storage.log_file(identifier, number).read_text().splitlines()]


def journal(tmp_path):
    lease = Lease(str(ULID()), str(ULID()), "worker", "fake", "fake-1", {}, 42, 1)
    return AttemptJournal(LocalStorage(tmp_path), lease, now=lambda: NOW), lease


def test_journal_appends_valid_json_and_protects_correlation(tmp_path):
    writer, lease = journal(tmp_path)
    writer.write("started")
    writer.write("adapter_log", fields={"generation_id": "spoof", "event": "spoof", "text": "line1\nline2"})
    path = writer.storage.log_file(lease.generation_id, 1)
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(events) == 2
    assert all(e["generation_id"] == lease.generation_id and e["attempt_id"] == lease.attempt_id for e in events)
    assert all(e["worker_id"] == "worker" and e["attempt"] == 1 and e["model_id"] == "fake" for e in events)
    assert events[0]["timestamp"] == "2026-01-01T00:00:00Z"
    assert events[1]["fields"]["fields"]["text"] == "line1\nline2"
    assert events[1]["event"] == "adapter_log"


def test_journal_refuses_symlink_and_is_best_effort(tmp_path):
    writer, lease = journal(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("keep")
    path = writer.storage.log_file(lease.generation_id, 1)
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    assert writer.write("test") is False
    assert outside.read_text() == "keep"


def test_journal_io_error_does_not_escape(tmp_path):
    writer, _ = journal(tmp_path)
    with patch("hephaistos.observability.os.open", side_effect=OSError("full disk")):
        assert writer.write("test") is False


def test_formatter_keeps_trace_on_one_physical_line():
    record = logging.LogRecord("test", logging.ERROR, "", 0, "failed", (), None)
    record.generation_id = "generation"
    try:
        raise RuntimeError("line one\nline two")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    line = JSONFormatter("worker").format(record)
    assert "\n" not in line
    value = json.loads(line)
    assert value["service"] == "worker" and value["level"] == "error"
    assert value["generation_id"] == "generation" and "RuntimeError" in value["exception"]


def test_stdlib_and_structlog_share_json_stdout():
    import structlog

    stream = io.StringIO()
    with logging_context("test", stream=stream):
        logging.getLogger("hephaistos.tests").info("plain", extra={"generation_id": "one"})
        structlog.get_logger("hephaistos.tests").info("structured", generation_id="two")
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [e["event"] for e in events] == ["plain", "structured"]
    assert [e["generation_id"] for e in events] == ["one", "two"]
    assert all(e["service"] == "test" and e["level"] == "info" and e["timestamp"] for e in events)


def test_supervisor_records_completed_attempt(rig):
    supervisor, _, _ = rig
    identifier = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    events = rows(supervisor, identifier)
    assert events[0]["event"] == "attempt_started"
    assert events[-1]["event"] == "attempt_finished"
    assert events[-1]["fields"]["outcome"] == "SUCCEEDED"
    assert all(e["generation_id"] == identifier for e in events)
    assert all(e["attempt_id"] == attempts(supervisor.broker)[0].id for e in events)


def test_supervisor_relays_logs_and_discards_old_attempt_events(rig):
    from hephaistos.adapters.base import Stage

    supervisor, _, _ = rig
    identifier = submit(supervisor)
    supervisor.tick()
    lease = supervisor.lease
    supervisor._events.put(("old-attempt", LogEvent("stale", {}), 0))
    supervisor._events.put((lease.attempt_id, StageEvent(Stage.PREPROCESS), 0))
    supervisor._events.put((lease.attempt_id, StepEvent(1, 2), 0))
    supervisor._events.put((lease.attempt_id, LogEvent("adapter_test", {"value": 2}), 0))
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    events = rows(supervisor, identifier)
    assert any(e["event"] == "stage" for e in events)
    assert any(e["event"] == "step" for e in events)
    adapter = next(e for e in events if e["event"] == "adapter_log")
    assert adapter["fields"]["adapter_event"] == "adapter_test"
    assert not any("stale" in json.dumps(e) for e in events)


def test_logging_failure_never_changes_success(rig):
    supervisor, _, _ = rig
    identifier = submit(supervisor)
    with patch.object(AttemptJournal, "write", return_value=False):
        until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    assert get(supervisor.broker, identifier).status == "COMPLETED"


def test_api_emits_correlated_access_logs(queue, tmp_path, capsys):
    from hephaistos.api.app import create_app
    from hephaistos.config import Settings
    from tests.api_client import TestClient
    from tests.unit.test_queue import job

    identifier = job(queue)
    with TestClient(create_app(engine=queue.engine, settings=Settings(hephaistos_data_dir=tmp_path))) as client:
        response = client.get(f"/api/v1/generations/{identifier}?secret=never-log-this")
        assert response.status_code == 200
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    request = next(e for e in events if e["event"] == "http_request")
    assert request["generation_id"] == identifier and request["status"] == 200
    assert request["route"] == "/api/v1/generations/{identifier}"
    assert "never-log-this" not in json.dumps(request)
    assert request["request_id"]


def test_last_events_are_drained_after_future_completion(rig):
    import time
    from unittest.mock import Mock

    supervisor, _, _ = rig
    identifier = submit(supervisor)
    until(supervisor, lambda: supervisor._phase == "generating")
    deadline = time.monotonic() + 5
    while not supervisor._future.done():
        assert time.monotonic() < deadline
        time.sleep(0.005)
    events = supervisor._events
    events.put((supervisor.lease.attempt_id, LogEvent("last_message"), 0))
    proxy = Mock(wraps=events)
    proxy.empty.side_effect = [True, False, True]
    supervisor._events = proxy
    supervisor.tick()
    assert get(supervisor.broker, identifier).status == "COMPLETED"
    assert any(
        e["event"] == "adapter_log" and e["fields"]["adapter_event"] == "last_message"
        for e in rows(supervisor, identifier)
    )


@pytest.mark.parametrize("code, retryable", [("INTERNAL", False), ("GPU_OOM", True)])
def test_failed_attempt_keeps_code_and_trace(rig, code, retryable):
    from hephaistos.domain.errors import ErrorCode
    from hephaistos.worker.inference import Failed

    supervisor, _, children = rig
    identifier = submit(supervisor)
    supervisor.tick()
    children[0].outcome = Failed(ErrorCode(code), "diagnostic", "Traceback\n  details")
    until(supervisor, lambda: supervisor.lease is None)
    final = rows(supervisor, identifier)[-1]
    assert final["event"] == "attempt_finished"
    assert final["fields"]["outcome"] == "FAILED"
    assert final["fields"]["code"] == code and final["fields"]["retryable"] is retryable
    assert final["fields"]["error_trace"] == "Traceback\n  details"


def test_cancel_records_actual_database_outcome(rig):
    supervisor, clock, _ = rig
    identifier = submit(supervisor)
    supervisor.tick()
    supervisor.broker.cancel(identifier, now=clock.now())
    until(supervisor, lambda: supervisor.lease is None)
    assert rows(supervisor, identifier)[-1]["fields"]["outcome"] == "CANCELLED"


def test_shutdown_records_interruption(rig):
    supervisor, _, _ = rig
    identifier = submit(supervisor)
    supervisor.tick()
    supervisor.close()
    assert rows(supervisor, identifier)[-1]["fields"]["outcome"] == "INTERRUPTED"


def test_manual_retry_keeps_separate_attempt_journals(rig):
    from hephaistos.domain.errors import ErrorCode
    from hephaistos.worker.inference import Failed

    supervisor, clock, children = rig
    identifier = submit(supervisor)
    supervisor.tick()
    children[0].outcome = Failed(ErrorCode.INTERNAL, "diagnose")
    until(supervisor, lambda: supervisor.lease is None)
    first = supervisor.storage.log_file(identifier, 1).read_bytes()
    children[0].outcome = None
    supervisor.broker.retry(identifier, now=clock.now())
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    assert supervisor.storage.log_file(identifier, 1).read_bytes() == first
    assert rows(supervisor, identifier, 2)[-1]["fields"]["outcome"] == "SUCCEEDED"
    assert rows(supervisor, identifier, 2)[0]["attempt_id"] != rows(supervisor, identifier)[0]["attempt_id"]


def test_janitor_recovery_logs_the_abandoned_attempt_even_if_reclaimed(queue, tmp_path):
    from datetime import timedelta

    from hephaistos.config import Settings
    from hephaistos.janitor.service import Janitor
    from tests.unit.test_queue import claim, job

    identifier = job(queue)
    lease = claim(queue, now=NOW - timedelta(minutes=2))
    janitor = Janitor(queue.engine, Settings(hephaistos_data_dir=tmp_path), now=lambda: NOW)
    recover = janitor.broker.recover_expired

    def recover_then_claim(identifier, *, now):
        result = recover(identifier, now=now)
        assert queue.claim("new-worker", {"fake"}, now=NOW + timedelta(seconds=31), free_vram_bytes=10**10)
        return result

    with patch.object(janitor.broker, "recover_expired", recover_then_claim):
        janitor.run_once(maintenance=False)
    events = [json.loads(line) for line in janitor.storage.log_file(identifier, 1).read_text().splitlines()]
    assert events[-1]["event"] == "attempt_recovered"
    assert events[-1]["attempt_id"] == lease.attempt_id
    assert events[-1]["fields"]["outcome"] == "ABANDONED"


def test_nonfinite_and_nonserializable_fields_still_produce_json(tmp_path):
    writer, lease = journal(tmp_path)
    assert writer.write("diagnostic", value=float("nan"), path=Path("/example"))
    body = json.loads(writer.storage.log_file(lease.generation_id, 1).read_text())
    assert body["fields"]


def test_journal_is_available_through_http_logs(rig):
    from hephaistos.api.app import create_app
    from hephaistos.config import Settings
    from tests.api_client import TestClient

    supervisor, clock, _ = rig
    identifier = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    settings = Settings(hephaistos_data_dir=supervisor.storage.root, enable_fake_adapter=True)
    with TestClient(create_app(engine=supervisor.broker.engine, settings=settings, now=clock.now)) as client:
        response = client.get(f"/api/v1/generations/{identifier}/logs?attempt=1&tail=1")
        assert response.status_code == 200
        assert response.json()[0]["fields"]["outcome"] == "SUCCEEDED"


def test_real_fake_process_journal_contains_progress(rig):
    from hephaistos.worker.inference import InferenceProcess

    supervisor, _, _ = rig
    supervisor.process_factory = lambda model: InferenceProcess(model, models_root=Path("/tmp"))
    identifier = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    events = rows(supervisor, identifier)
    assert any(e["event"] == "stage" for e in events)
    assert any(e["event"] == "step" for e in events)
    assert events[-1]["fields"]["outcome"] == "SUCCEEDED"


def test_setup_is_idempotent_and_restores_host_handlers():
    from hephaistos.observability import configure_logging

    root = logging.getLogger()
    original = list(root.handlers)
    stream = io.StringIO()
    with logging_context("test", stream=stream):
        configure_logging("test", stream=stream)
        logging.getLogger("hephaistos.tests").info("one_event")
    assert len(stream.getvalue().splitlines()) == 1
    assert root.handlers == original
