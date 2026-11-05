import io
import json
import socket
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from hephaistos.config import Settings
from hephaistos.healthcheck import check_api, check_janitor, check_worker
from hephaistos.persistence.models import Worker
from tests.unit.test_queue import NOW
from tests.unit.test_queue import queue as queue


def test_api_checks_real_database_health_contract():
    response = io.BytesIO(b'{"status":"ok"}')
    response.status = 200
    with patch("hephaistos.healthcheck.urlopen", return_value=response) as request:
        assert check_api() is True
    assert request.call_args.kwargs["timeout"] == 2


@pytest.mark.parametrize("body", [b"not json", b'{"status":"failed"}', b"[]"])
def test_invalid_api_response_is_unhealthy(body):
    response = io.BytesIO(body)
    response.status = 200
    with patch("hephaistos.healthcheck.urlopen", return_value=response):
        assert check_api() is False


def test_unreachable_api_is_unhealthy():
    with patch("hephaistos.healthcheck.urlopen", side_effect=OSError("not listening")):
        assert check_api() is False


def test_worker_health_only_uses_own_container(queue, tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path)
    with Session(queue.engine) as session, session.begin():
        session.add(
            Worker(id="another-host:1:old", state="READY", gpu_free_bytes=0, started_at=NOW, last_heartbeat_at=NOW)
        )
    assert check_worker(settings, now=NOW) is False
    with Session(queue.engine) as session, session.begin():
        session.add(
            Worker(
                id=f"{socket.gethostname()}:1:self",
                state="READY",
                gpu_free_bytes=0,
                started_at=NOW,
                last_heartbeat_at=NOW,
            )
        )
    assert check_worker(settings, now=NOW) is True


@pytest.mark.parametrize("state", ["DEGRADED", "STOPPING"])
def test_worker_nonserving_states_are_unhealthy(queue, tmp_path, state):
    with Session(queue.engine) as session, session.begin():
        session.add(
            Worker(
                id=f"{socket.gethostname()}:1:self",
                state=state,
                gpu_free_bytes=0,
                started_at=NOW,
                last_heartbeat_at=NOW,
            )
        )
    assert check_worker(Settings(hephaistos_data_dir=tmp_path), now=NOW) is False


def test_worker_heartbeat_expires(queue, tmp_path):
    with Session(queue.engine) as session, session.begin():
        session.add(
            Worker(
                id=f"{socket.gethostname()}:1:self",
                state="BUSY",
                gpu_free_bytes=0,
                started_at=NOW,
                last_heartbeat_at=NOW - timedelta(seconds=61),
            )
        )
    assert check_worker(Settings(hephaistos_data_dir=tmp_path), now=NOW) is False


def test_missing_database_is_not_created(tmp_path):
    assert check_worker(Settings(hephaistos_data_dir=tmp_path), now=NOW) is False
    assert not (tmp_path / "db").exists()


@pytest.mark.parametrize("age,ok,expected", [(0, True, True), (61, True, False), (0, False, False), (-60, True, False)])
def test_janitor_requires_fresh_successful_cycle(tmp_path, age, ok, expected):
    (tmp_path / "janitor-status.json").write_text(
        json.dumps(dict(at=(NOW - timedelta(seconds=age)).isoformat(), ok=ok))
    )
    assert check_janitor(Settings(hephaistos_data_dir=tmp_path), now=NOW) is expected


def test_malformed_or_missing_janitor_health_is_unhealthy(tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path)
    assert check_janitor(settings, now=NOW) is False
    (tmp_path / "janitor-status.json").write_text("{")
    assert check_janitor(settings, now=NOW) is False


def test_stop_grace_must_exceed_supervisor_grace(monkeypatch):
    from hephaistos.config import ConfigError, load_settings

    monkeypatch.setenv("WORKER_SHUTDOWN_GRACE_SECONDS", "90")
    monkeypatch.setenv("WORKER_STOP_GRACE_SECONDS", "90")
    with pytest.raises(ConfigError, match="WORKER_STOP_GRACE_SECONDS"):
        load_settings()


def test_healthcheck_module_never_imports_torch():
    from tests.unit.test_adapters_base import assert_does_not_import_torch

    assert_does_not_import_torch("hephaistos.healthcheck")


def test_healthcheck_cli_reports_missing_services_without_creating_data(tmp_path):
    import os
    import subprocess
    import sys

    environment = dict(os.environ, HEPHAISTOS_DATA_DIR=str(tmp_path))
    for service in ("worker", "janitor"):
        result = subprocess.run(
            [sys.executable, "-m", "hephaistos.healthcheck", service], env=environment, capture_output=True, text=True
        )
        assert result.returncode == 1
    assert list(tmp_path.iterdir()) == []
