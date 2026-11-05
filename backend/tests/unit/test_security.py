import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hephaistos.api.app import create_app
from hephaistos.config import Settings
from tests.unit.test_queue import NOW
from tests.unit.test_queue import queue as queue
from tests.unit.test_upload import image_bytes

TOKEN = "security-test-token-at-least-32-characters"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def client(queue, tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path, api_access_token=TOKEN, enable_fake_adapter=True)
    with TestClient(
        create_app(engine=queue.engine, settings=settings, now=lambda: NOW), base_url="http://localhost"
    ) as client:
        yield client


def test_token_required_for_reads_and_writes(client):
    assert client.get("/api/v1/system/health").status_code == 200
    assert client.get("/api/v1/models").status_code == 401
    assert client.get("/api/v1/docs").status_code == 401
    assert client.put("/api/v1/system/dispatch", json={"paused": True}).status_code == 401
    assert client.get("/api/v1/models", headers=AUTH).status_code == 200
    assert client.get("/api/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {**AUTH, "Host": "untrusted.example"},
        {**AUTH, "Host": "localhost.untrusted.example"},
        {**AUTH, "Origin": "https://untrusted.example"},
        {**AUTH, "Origin": "null"},
        {**AUTH, "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_host_and_cross_site_mutations_are_denied(client, headers):
    response = client.post(
        "/api/v1/generations", headers=headers, data={"model_id": "fake"}, files={"image": ("x.png", image_bytes())}
    )
    assert response.status_code in (400, 403)
    assert response.headers["content-type"] == "application/problem+json"


def test_allowed_origin_and_server_client_are_accepted(client):
    assert (
        client.put(
            "/api/v1/system/dispatch", headers={**AUTH, "Origin": "http://localhost:3030"}, json={"paused": True}
        ).status_code
        == 200
    )
    assert client.put("/api/v1/system/dispatch", headers=AUTH, json={"paused": False}).status_code == 200


def test_json_limit_precedes_validation(client):
    response = client.put("/api/v1/system/dispatch", headers=AUTH, json={"paused": True, "extra": "x" * 70000})
    assert response.status_code == 413
    assert response.json()["code"] == "REQUEST_TOO_LARGE"


def test_chunked_json_limit_without_content_length(client):
    def chunks():
        yield b'{"paused":true,"extra":"'
        for _ in range(80):
            yield b"x" * 1024
        yield b'"}'

    response = client.put(
        "/api/v1/system/dispatch", headers={**AUTH, "Content-Type": "application/json"}, content=chunks()
    )
    assert response.status_code == 413


def test_missing_token_fails_closed(queue, tmp_path):
    from hephaistos.config import ConfigError

    with pytest.raises(ConfigError, match="API_ACCESS_TOKEN"):
        create_app(engine=queue.engine, settings=Settings(hephaistos_data_dir=tmp_path, api_access_token=None))


def test_admission_slots_shared_between_api_instances(client, queue, tmp_path):
    from hephaistos.api.admission import Admission
    from hephaistos.api.errors import ResourceError

    settings = Settings(hephaistos_data_dir=tmp_path, upload_max_concurrent=1)
    first, second = Admission(queue.engine, settings), Admission(queue.engine, settings)
    lease = first.reserve(now=NOW)
    with pytest.raises(ResourceError) as exc:
        second.reserve(now=NOW)
    assert exc.value.code == "UPLOAD_CAPACITY_EXCEEDED"
    first.release(lease)
    second.release(second.reserve(now=NOW))


def test_queue_quota_counts_pending_upload_reservations(client, queue, tmp_path):
    from hephaistos.api.admission import Admission
    from hephaistos.api.errors import ResourceError

    admission = Admission(
        queue.engine, Settings(hephaistos_data_dir=tmp_path, upload_max_concurrent=2, queue_max_active=1)
    )
    lease = admission.reserve(now=NOW)
    with pytest.raises(ResourceError) as exc:
        admission.reserve(now=NOW)
    assert exc.value.code == "QUEUE_CAPACITY_EXCEEDED"
    admission.release(lease)


def test_storage_quota_reserves_normalized_and_future_outputs(client, queue, tmp_path):
    from hephaistos.api.admission import Admission
    from hephaistos.api.errors import ResourceError

    admission = Admission(queue.engine, Settings(hephaistos_data_dir=tmp_path, storage_max_bytes=2 * 1024**2))
    with pytest.raises(ResourceError) as exc:
        admission.reserve(now=NOW)
    assert exc.value.code == "STORAGE_QUOTA_EXCEEDED"


def test_upload_quota_rejects_before_parsing(client, queue, tmp_path):
    from hephaistos.api.admission import Admission

    admission = Admission(queue.engine, Settings(hephaistos_data_dir=tmp_path))
    lease = admission.reserve(now=NOW)
    try:
        response = client.post("/api/v1/generations", content=b"not multipart", headers=AUTH)
        assert response.status_code == 429
    finally:
        admission.release(lease)


def test_queue_is_paginated_with_constant_query_count(client, queue):
    from sqlalchemy import event

    from tests.unit.test_queue import job

    identifiers = sorted(job(queue) for _ in range(40))
    counts = []

    def count(*_):
        counts.append(1)

    event.listen(queue.engine, "before_cursor_execute", count)
    try:
        first = client.get("/api/v1/queue?limit=5", headers=AUTH).json()
        assert [g["id"] for g in first["items"]] == identifiers[:5]
        assert len(counts) <= 5
        counts.clear()
        second = client.get("/api/v1/queue", headers=AUTH, params={"limit": 5, "cursor": first["next_cursor"]}).json()
        assert [g["id"] for g in second["items"]] == identifiers[5:10]
        assert [g["position"] for g in second["items"]] == [6, 7, 8, 9, 10]
        assert len(counts) <= 6
    finally:
        event.remove(queue.engine, "before_cursor_execute", count)


def test_source_is_sanitized_by_default_original_is_explicit(client):
    import io

    from PIL import Image

    exif = Image.Exif()
    exif[0x010F] = "SECURITY_AUDIT_TEST"
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6)).save(buffer, "PNG", exif=exif)
    response = client.post(
        "/api/v1/generations",
        headers=AUTH,
        data={"model_id": "fake"},
        files={"image": ("test.png", buffer.getvalue(), "image/png")},
    )
    assert response.status_code == 201
    source = response.json()["source"]
    normalized = client.get(source["url"], headers=AUTH)
    original = client.get(source["original_url"], headers=AUTH)
    with Image.open(io.BytesIO(normalized.content)) as image:
        assert not image.getexif()
    with Image.open(io.BytesIO(original.content)) as image:
        assert image.getexif()[0x010F] == "SECURITY_AUDIT_TEST"
    assert original.headers["content-disposition"].startswith("attachment")


def test_log_tail_reads_only_bounded_suffix(tmp_path):
    from hephaistos.api.resources import json_tail

    path = tmp_path / "huge.jsonl"
    path.write_bytes(b"x" * 100000 + b'\n{"event":"last"}\n{"partial":')
    assert json_tail(path, lines=1, max_bytes=64) == [{"event": "last"}]
    assert json_tail(path, lines=1, max_bytes=4) == []


def test_admission_recovers_expired_reservation(client, queue, tmp_path):
    from datetime import timedelta

    from hephaistos.api.admission import LEASE_SECONDS, Admission
    from hephaistos.api.errors import ResourceError

    admission = Admission(queue.engine, Settings(hephaistos_data_dir=tmp_path))
    first = admission.reserve(now=NOW)
    second = admission.reserve(now=NOW + timedelta(seconds=LEASE_SECONDS + 1))
    with pytest.raises(ResourceError, match="expired"):
        admission.renew(first, now=NOW + timedelta(seconds=LEASE_SECONDS + 1))
    admission.release(first)
    admission.release(second)


def test_rejected_upload_releases_reservation(client, queue):
    from sqlalchemy.orm import Session

    from hephaistos.persistence.models import SystemState

    response = client.post("/api/v1/generations", content=b"invalid", headers=AUTH)
    assert response.status_code == 415
    with Session(queue.engine) as session:
        assert session.get(SystemState, 1).ingest_reservations == {}


def test_admission_concurrent_reservers_never_exceed_capacity(client, queue, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from hephaistos.api.admission import Admission
    from hephaistos.api.errors import ResourceError

    admission = Admission(queue.engine, Settings(hephaistos_data_dir=tmp_path))
    barrier = Barrier(4)

    def reserve():
        barrier.wait()
        try:
            return admission.reserve(now=NOW)
        except ResourceError as error:
            assert error.code == "UPLOAD_CAPACITY_EXCEEDED"
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        reservations = list(executor.map(lambda _: reserve(), range(4)))
    admitted = [value for value in reservations if value]
    assert len(admitted) == 1
    admission.release(admitted[0])


def test_body_timeout_before_parsing_releases_upload_slot(queue, tmp_path):
    from sqlalchemy.orm import Session
    from starlette.types import Message

    from hephaistos.persistence.models import SystemState

    settings = Settings(hephaistos_data_dir=tmp_path, api_access_token=TOKEN, api_request_timeout_seconds=0.01)
    app = create_app(engine=queue.engine, settings=settings, now=lambda: NOW)

    async def run():
        messages = []

        async def slow_receive():
            await asyncio.sleep(0.05)
            return {"type": "http.request", "body": b"x", "more_body": True}

        async def send(message: Message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/generations",
                "raw_path": b"/api/v1/generations",
                "query_string": b"",
                "headers": [
                    (b"host", b"localhost"),
                    (b"authorization", AUTH["Authorization"].encode()),
                    (b"content-type", b"multipart/form-data; boundary=test"),
                ],
                "server": ("localhost", 80),
                "client": ("127.0.0.1", 1),
                "root_path": "",
            },
            slow_receive,
            send,
        )
        assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 408

    with TestClient(app, base_url="http://localhost"):
        asyncio.run(run())
        with Session(queue.engine) as session:
            assert session.get(SystemState, 1).ingest_reservations == {}


def test_disk_margin_is_checked_before_accepting_upload(client, queue, tmp_path):
    from hephaistos.api.admission import Admission
    from hephaistos.api.errors import ResourceError

    admission = Admission(queue.engine, Settings(hephaistos_data_dir=tmp_path))
    with patch.object(admission.storage, "free_bytes", return_value=1), pytest.raises(ResourceError) as error:
        admission.reserve(now=NOW)
    assert error.value.code == "STORAGE_SPACE_INSUFFICIENT"


@pytest.mark.parametrize("blocked_by", ["queue", "disk"])
def test_manual_retry_cannot_bypass_admission(queue, tmp_path, blocked_by):
    from hephaistos.api.admission import Admission
    from tests.unit.test_queue import get, job

    identifier = job(queue, status="CANCELLED")
    settings = Settings(hephaistos_data_dir=tmp_path, api_access_token=TOKEN, queue_max_active=1)
    with TestClient(
        create_app(engine=queue.engine, settings=settings, now=lambda: NOW), base_url="http://localhost"
    ) as client:
        if blocked_by == "queue":
            job(queue)
            response = client.post(f"/api/v1/generations/{identifier}/retry", headers=AUTH)
            assert response.status_code == 429
        else:
            with patch.object(Admission, "usage", return_value=(0, 0)):
                response = client.post(f"/api/v1/generations/{identifier}/retry", headers=AUTH)
            assert response.status_code == 507
    assert get(queue, identifier).status == "CANCELLED"


def test_manual_retry_accepts_capacity_without_upload_lease(client, queue):
    from sqlalchemy.orm import Session

    from hephaistos.persistence.models import SystemState
    from tests.unit.test_queue import job

    identifier = job(queue, status="CANCELLED")
    assert client.post(f"/api/v1/generations/{identifier}/retry", headers=AUTH).status_code == 200
    with Session(queue.engine) as session:
        assert session.get(SystemState, 1).ingest_reservations == {}
