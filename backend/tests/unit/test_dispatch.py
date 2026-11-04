from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hephaistos.api.app import create_app
from hephaistos.api.dependencies import current_principal
from hephaistos.config import Settings
from hephaistos.persistence.models import SystemState
from hephaistos.system.dispatch import DispatchService
from tests.unit.test_queue import NOW, attempts, claim, get, job
from tests.unit.test_queue import queue as queue
from tests.unit.test_supervisor import rig as rig


@pytest.fixture
def client(queue):
    with TestClient(create_app(engine=queue.engine, settings=queue.settings, now=lambda: NOW)) as client:
        yield client


def test_get_initial_state_is_persisted_once(client, queue):
    expected = {"paused": False, "paused_by": None, "reason": None, "since": None}
    assert client.get("/api/v1/system/dispatch").json() == expected
    assert client.get("/api/v1/system/dispatch").json() == expected
    with Session(queue.engine) as session:
        assert session.scalar(select(func.count()).select_from(SystemState)) == 1


def test_config_pause_initializes_once_and_does_not_override_manual_resume(queue):
    settings = Settings(dispatch_paused=True)
    with TestClient(create_app(engine=queue.engine, settings=settings, now=lambda: NOW)) as client:
        state = client.get("/api/v1/system/dispatch").json()
        assert state == {
            "paused": True,
            "paused_by": "config",
            "reason": "DISPATCH_PAUSED",
            "since": "2026-01-01T00:00:00Z",
        }
        response = client.put("/api/v1/system/dispatch", json={"paused": False})
        assert response.status_code == 200
    with TestClient(create_app(engine=queue.engine, settings=settings, now=lambda: NOW)) as restarted:
        assert restarted.get("/api/v1/system/dispatch").json()["paused"] is False


def test_pause_persists_reason_and_resume_clears_metadata(client, queue):
    body = {"paused": True, "reason": "  maintenance  "}
    response = client.put("/api/v1/system/dispatch", json=body)
    assert response.status_code == 200
    state = response.json()
    assert state == {"paused": True, "paused_by": "user", "reason": "maintenance", "since": "2026-01-01T00:00:00Z"}
    assert client.get("/api/v1/system/dispatch").json() == state
    response = client.put("/api/v1/system/dispatch", json={"paused": False, "reason": "ignored on resume"})
    assert response.json() == {"paused": False, "paused_by": None, "reason": None, "since": None}


def test_repeated_pause_is_idempotent(queue):
    service = DispatchService(queue.engine, queue.settings)
    first = service.set(paused=True, reason="maintenance", now=NOW)
    second = service.set(paused=True, reason="maintenance", now=NOW + timedelta(minutes=5))
    assert second == first
    with Session(queue.engine) as session:
        assert session.get(SystemState, 1).updated_at.replace(tzinfo=NOW.tzinfo) == NOW


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"paused": "false"},
        {"paused": 1},
        {"paused": None},
        {"paused": True, "reason": 10},
        {"paused": True, "reason": "x" * 1001},
        {"paused": True, "unknown": 1},
    ],
)
def test_invalid_request_is_problem_json(client, payload):
    response = client.put("/api/v1/system/dispatch", json=payload)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert body["type"] and body["title"] and body["detail"] and body["errors"]
    assert client.get("/api/v1/system/dispatch").json()["paused"] is False


def test_principal_dependency_guards_both_routes(queue):
    app = create_app(engine=queue.engine, settings=queue.settings)

    def denied():
        raise HTTPException(status_code=403, detail="Denied")

    app.dependency_overrides[current_principal] = denied
    with TestClient(app) as client:
        for response in [
            client.get("/api/v1/system/dispatch"),
            client.put("/api/v1/system/dispatch", json={"paused": True}),
        ]:
            assert response.status_code == 403
            assert response.headers["content-type"] == "application/problem+json"


def test_pause_blocks_new_claims_but_running_generation_completes(client, queue):
    from tests.unit.test_queue import outputs

    running = job(queue)
    lease = claim(queue)
    waiting = job(queue)
    assert client.put("/api/v1/system/dispatch", json={"paused": True}).status_code == 200
    assert get(queue, running).status == "RUNNING"
    assert get(queue, running).cancel_requested_at is None
    queue.complete(lease, outputs(), promote=lambda: None, now=NOW)
    assert get(queue, running).status == "COMPLETED"
    assert claim(queue) is None
    assert get(queue, waiting).status == "QUEUED"
    assert client.put("/api/v1/system/dispatch", json={"paused": False}).status_code == 200
    assert claim(queue).generation_id == waiting
    assert len(attempts(queue)) == 2


def test_concurrent_first_writes_leave_one_coherent_system_row(queue):
    barrier = Barrier(4)

    def write(index):
        barrier.wait(timeout=10)
        return DispatchService(queue.engine, queue.settings).set(paused=True, reason=f"reason-{index}", now=NOW)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(write, range(4)))
    assert all(result.paused and result.paused_by == "user" for result in results)
    with Session(queue.engine) as session:
        assert session.scalar(select(func.count()).select_from(SystemState)) == 1
        row = session.get(SystemState, 1)
        assert row.paused_by == "user"
        assert row.paused_reason in {f"reason-{i}" for i in range(4)}
        assert row.paused_at is not None


def test_openapi_uses_v1_path_and_dispatch_schema(client):
    schema = client.get("/api/v1/openapi.json").json()
    assert set(schema["paths"]["/api/v1/system/dispatch"]) == {"get", "put"}
    update = schema["components"]["schemas"]["DispatchUpdate"]
    assert update["required"] == ["paused"]
    assert update["additionalProperties"] is False


def test_resume_reactivates_degraded_supervisor(client, rig):
    from hephaistos.domain.errors import ErrorCode
    from hephaistos.worker.inference import Failed
    from tests.unit.test_supervisor import Child, submit, until

    supervisor, _clock, _children = rig

    def crashing(model):
        child = Child()
        child.outcome = Failed(ErrorCode.WORKER_CRASH, "injected")
        return child

    supervisor.process_factory = crashing
    for _ in range(3):
        identifier = submit(supervisor)
        until(
            supervisor,
            lambda identifier=identifier: (
                get(supervisor.broker, identifier).attempt_count == 1 and supervisor.lease is None
            ),
        )
    supervisor.tick()
    assert supervisor.state == "DEGRADED"
    assert client.get("/api/v1/system/dispatch").json()["paused_by"] == "system"
    assert client.put("/api/v1/system/dispatch", json={"paused": False}).status_code == 200
    supervisor.process_factory = lambda model: Child()
    identifier = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    assert supervisor.state == "READY"


def test_openapi_validation_response_matches_problem_content_type(client):
    operation = client.get("/api/v1/openapi.json").json()["paths"]["/api/v1/system/dispatch"]["put"]
    assert set(operation["responses"]["422"]["content"]) == {"application/problem+json"}


def test_dispatch_api_does_not_import_torch():
    from tests.unit.test_adapters_base import assert_does_not_import_torch

    assert_does_not_import_torch("hephaistos.api.app")


def test_pause_timestamp_keeps_same_instant_after_database_roundtrip(queue):
    from datetime import timezone

    service = DispatchService(queue.engine, queue.settings)
    local_time = NOW.astimezone(timezone(timedelta(hours=2)))
    written = service.set(paused=True, now=local_time)
    read = service.get(now=NOW)
    assert written.since == read.since == NOW
