import hashlib
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from hephaistos.api.app import create_app
from hephaistos.config import Settings
from hephaistos.persistence.models import Artifact, Worker
from tests.api_client import TestClient
from tests.unit.test_queue import NOW, claim, get, job
from tests.unit.test_queue import queue as queue


@pytest.fixture
def client(queue, tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path, enable_fake_adapter=True)
    with TestClient(create_app(engine=queue.engine, settings=settings, now=lambda: NOW)) as client:
        yield client


def test_health_status_and_models_without_workers(client):
    assert client.get("/api/v1/system/health").json() == {"status": "ok"}
    status = client.get("/api/v1/system/status").json()
    assert status["database"]["ok"] is True
    assert status["queue"]["queued"] == 0
    assert status["gpu"]["free_vram_bytes"] is None
    models = client.get("/api/v1/models").json()
    assert {m["id"] for m in models} == {"idol", "fake"}
    assert all(m["available"] is False for m in models)
    assert client.get("/api/v1/models/fake/schema").json()["additionalProperties"] is False
    assert client.get("/api/v1/models/idol/presets").json()["balanced"]["values"]
    assert client.get("/api/v1/models/missing").json()["code"] == "MODEL_NOT_FOUND"


def worker(queue, **values):
    data = dict(
        id="worker",
        state="READY",
        gpu_free_bytes=10**10,
        started_at=NOW,
        last_heartbeat_at=NOW,
        adapters=[{"id": "fake", "available": True}],
        gpu={"name": "test", "total_bytes": 12 * 1024**3, "free_bytes": 10**10},
    )
    data.update(values)
    with Session(queue.engine) as session, session.begin():
        session.add(Worker(**data))


def test_worker_availability_expires(client, queue):
    worker(queue, last_heartbeat_at=NOW - timedelta(seconds=61))
    assert client.get("/api/v1/models/fake").json()["available"] is False
    assert client.get("/api/v1/system/status").json()["gpu"]["free_vram_bytes"] is None


def test_queue_reasons_and_position(client, queue):
    worker(queue)
    first = job(queue, created_at=NOW - timedelta(seconds=1))
    second = job(queue, not_before=NOW + timedelta(minutes=1))
    items = client.get("/api/v1/queue").json()["items"]
    assert [g["id"] for g in items] == [first, second]
    assert items[0]["position"] == 1
    assert items[1]["waiting_reason"] == "RETRY_BACKOFF"
    client.put("/api/v1/system/dispatch", json={"paused": True})
    assert client.get(f"/api/v1/generations/{first}").json()["waiting_reason"] == "DISPATCH_PAUSED"


def test_pagination_ties_filters_and_deleted_rows(client, queue):
    ids = [job(queue) for _ in range(4)]
    job(queue, deleting_at=NOW)
    page = client.get("/api/v1/generations", params={"limit": 2}).json()
    assert [g["id"] for g in page["items"]] == sorted(ids, reverse=True)[:2]
    other = client.get("/api/v1/generations", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [g["id"] for g in other["items"]] == sorted(ids, reverse=True)[2:]
    assert other["next_cursor"] is None
    assert client.get("/api/v1/generations?status=FAILED&status=CANCELLED").json()["items"] == []
    assert client.get("/api/v1/generations?protected=true").json()["items"] == []
    for query in ("limit=0", "limit=101", "cursor=bad", "status=bogus"):
        response = client.get("/api/v1/generations?" + query)
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


def test_generation_actions_and_attempts(client, queue):
    identifier = job(queue)
    route = f"/api/v1/generations/{identifier}"
    assert client.patch(route, json={"protected": True}).json()["protected"] is True
    assert client.patch(route, json={"protected": "false"}).status_code == 422
    assert client.patch(route, json={"status": "COMPLETED"}).status_code == 422
    assert client.post(route + "/retry").status_code == 409
    assert client.post(route + "/cancel").json()["status"] == "CANCELLED"
    assert client.post(route + "/retry").json()["status"] == "QUEUED"
    lease = claim(queue)
    assert lease.generation_id == identifier
    assert client.post(route + "/cancel").status_code == 202
    assert get(queue, identifier).cancel_requested_at is not None
    assert client.delete(route).status_code == 409
    attempts = client.get(route + "/attempts").json()
    assert attempts[0]["number"] == 1
    assert "log_path" not in attempts[0]
    assert "error_trace" not in attempts[0]


def test_missing_and_deleting_resources(client, queue):
    identifier = job(queue, deleting_at=NOW)
    for suffix in ("", "/source", "/attempts", "/artifacts", "/logs?attempt=1"):
        response = client.get(f"/api/v1/generations/{identifier}" + suffix)
        assert response.status_code == 404
        assert response.json()["code"] == "GENERATION_NOT_FOUND"
    assert client.get("/api/v1/artifacts/missing").json()["code"] == "ARTIFACT_NOT_FOUND"


def test_auto_delete_after_follows_the_retention_rule(client, queue):
    """P4.8 : seuil après lequel le janitor supprime, null si rien ne sera supprimé automatiquement."""
    finished = NOW - timedelta(days=2)
    ended = {status: job(queue, status=status, finished_at=finished) for status in ("COMPLETED", "FAILED", "CANCELLED")}
    kept = job(queue, status="COMPLETED", finished_at=finished, protected=True)
    running = job(queue, status="RUNNING", started_at=NOW)
    queued = job(queue)

    def after(identifier):
        return client.get(f"/api/v1/generations/{identifier}").json()["auto_delete_after"]

    # Défaut : STORAGE_RETENTION_DAYS = 30.
    for identifier in ended.values():
        assert after(identifier) == (finished + timedelta(days=30)).isoformat().replace("+00:00", "Z")
    assert after(kept) is None
    assert after(running) is None
    assert after(queued) is None
    listed = {item["id"]: item["auto_delete_after"] for item in client.get("/api/v1/generations").json()["items"]}
    assert listed[kept] is None and listed[ended["COMPLETED"]] is not None
    # Lever la protection rend la date : la réponse du PATCH la porte déjà.
    assert client.patch(f"/api/v1/generations/{kept}", json={"protected": False}).json()["auto_delete_after"]


def test_auto_delete_after_is_null_when_retention_is_disabled(queue, tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path, enable_fake_adapter=True, storage_retention_days=0)
    identifier = job(queue, status="COMPLETED", finished_at=NOW)
    with TestClient(create_app(engine=queue.engine, settings=settings, now=lambda: NOW)) as client:
        assert client.get(f"/api/v1/generations/{identifier}").json()["auto_delete_after"] is None


def test_protected_manual_deletion(client, queue):
    identifier = job(queue, protected=True)
    assert client.delete(f"/api/v1/generations/{identifier}").status_code == 204
    assert get(queue, identifier) is None


def test_download_range_etag_and_safe_path(client, queue, tmp_path):
    identifier = job(queue, status="COMPLETED")
    path = tmp_path / "results" / identifier / "splat.ply"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"0123456789")
    with Session(queue.engine) as session, session.begin():
        artifact = Artifact(
            generation_id=identifier,
            kind="SPLAT_PLY",
            relative_path=f"results/{identifier}/splat.ply",
            mime="application/octet-stream",
            sha256="a" * 64,
            size_bytes=10,
            created_at=NOW,
        )
        session.add(artifact)
        session.flush()
        aid = artifact.id
    route = f"/api/v1/artifacts/{aid}"
    assert client.get(route).json()["sha256"] == "a" * 64
    response = client.get(route + "/download", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["etag"] == '"' + "a" * 64 + '"'
    assert "attachment;" in response.headers["content-disposition"]
    path.unlink()
    path.symlink_to("/etc/passwd")
    assert client.get(route + "/download").status_code == 404


@pytest.mark.parametrize(
    ("kind", "name", "mime", "content"),
    [
        ("SPLAT_PLY", "splat.ply", "application/octet-stream", b"ply\nformat binary_little_endian 1.0\n\x00\xff"),
        ("PREVIEW_PNG", "preview.png", "image/png", b"\x89PNG\r\n\x1a\n\x00"),
        ("CAMERA_JSON", "camera.json", "application/json", b'{"yaw": 0.0}'),
    ],
)
def test_download_is_the_stored_file_named_by_the_server(client, queue, tmp_path, kind, name, mime, content):
    """A4.4 : octets servis = fichier stocké, ETag = son SHA-256, nom construit par le serveur."""
    identifier = job(queue, status="COMPLETED")
    path = tmp_path / "results" / identifier / name
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with Session(queue.engine) as session, session.begin():
        artifact = Artifact(
            generation_id=identifier,
            kind=kind,
            relative_path=f"results/{identifier}/{name}",
            mime=mime,
            sha256=digest,
            size_bytes=len(content),
            created_at=NOW,
        )
        session.add(artifact)
        session.flush()
        aid = artifact.id
    response = client.get(f"/api/v1/artifacts/{aid}/download")
    assert response.status_code == 200
    assert response.content == path.read_bytes()
    assert response.headers["etag"] == f'"{hashlib.sha256(response.content).hexdigest()}"'
    assert response.headers["content-disposition"] == f'attachment; filename="hephaistos-{identifier}-{name}"'
    assert response.headers["content-type"].startswith(mime)


def test_source_and_logs(client, queue, tmp_path):
    identifier = job(queue)
    route = f"/api/v1/generations/{identifier}"
    path = tmp_path / "sources" / identifier / "original.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"image")
    (path.parent / "input.png").write_bytes(b"normalized")
    response = client.get(route + "/source")
    assert response.content == b"normalized"
    assert client.get(route + "/source?original=true").content == b"image"
    assert response.headers["content-disposition"].startswith("inline;")
    claim(queue)
    log = tmp_path / "logs" / identifier / "attempt-1.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text('{"event":"one"}\n{"event":"two"}\n')
    assert client.get(route + "/logs?attempt=1&tail=1").json() == [{"event": "two"}]
    assert client.get(route + "/logs?attempt=2").json()["code"] == "ATTEMPT_NOT_FOUND"


def test_model_availability_and_vram_wait(client, queue):
    worker(queue, gpu_free_bytes=1)
    assert client.get("/api/v1/models/fake").json()["available"] is True
    identifier = job(queue)
    body = client.get(f"/api/v1/generations/{identifier}").json()
    assert body["waiting_reason"] == "VRAM_INSUFFICIENT"
    assert body["created_at"] == "2026-01-01T00:00:00Z"
    assert body["source"]["filename"] == "display.png"
    assert body["progress"]["stage"] is None


def test_failure_detail_and_elapsed_time(client, queue):
    identifier = job(
        queue,
        status="FAILED",
        error_code="INTERNAL",
        error_message="diagnose",
        needs_diagnosis=True,
        started_at=NOW - timedelta(seconds=2),
        finished_at=NOW,
    )
    body = client.get(f"/api/v1/generations/{identifier}").json()
    assert body["error"] == dict(code="INTERNAL", message="diagnose", retryable=False, needs_diagnosis=True)
    assert body["elapsed_ms"] == 2000
    assert body["waiting_reason"] is None


def test_pagination_survives_deleted_anchor(client, queue):
    ids = sorted([job(queue) for _ in range(3)], reverse=True)
    first = client.get("/api/v1/generations?limit=1").json()
    assert client.delete(f"/api/v1/generations/{ids[0]}").status_code == 204
    next_page = client.get("/api/v1/generations", params={"cursor": first["next_cursor"]}).json()
    assert [r["id"] for r in next_page["items"]] == ids[1:]


@pytest.mark.parametrize(
    "path",
    [
        "/system/health",
        "/system/status",
        "/models",
        "/models/idol",
        "/models/idol/schema",
        "/models/idol/presets",
        "/generations",
        "/generations/missing",
        "/generations/missing/source",
        "/generations/missing/attempts",
        "/generations/missing/logs?attempt=1",
        "/generations/missing/artifacts",
        "/artifacts/missing",
        "/artifacts/missing/download",
        "/queue",
    ],
)
def test_principal_guards_resources(client, path):
    from fastapi import HTTPException

    from hephaistos.api.dependencies import current_principal

    def deny():
        raise HTTPException(403, "Denied")

    client.app.dependency_overrides[current_principal] = deny
    response = client.get("/api/v1" + path)
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


def test_openapi_has_typed_resources_and_problem_responses(client):
    spec = client.get("/api/v1/openapi.json").json()
    schema = spec["paths"]["/api/v1/generations/{identifier}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["$ref"].endswith("/GenerationView")
    validation = spec["paths"]["/api/v1/generations"]["get"]["responses"]["422"]["content"]
    assert "application/problem+json" in validation
    assert "application/json" not in validation
    assert spec["components"]["schemas"]["GenerationView"]["properties"]["source"]["$ref"].endswith("/SourceView")
