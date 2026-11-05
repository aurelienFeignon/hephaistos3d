import hashlib
import io
from unittest.mock import patch

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from hephaistos.api.app import create_app
from hephaistos.config import MIB, Settings
from hephaistos.persistence.models import Generation
from hephaistos.storage.local import LocalStorage
from tests.api_client import TestClient
from tests.unit.test_api import worker
from tests.unit.test_queue import NOW, get
from tests.unit.test_queue import queue as queue


def image_bytes(fmt="PNG", size=(8, 6), exif=None):
    buf = io.BytesIO()
    image = Image.new("RGB", size, "red")
    image.save(buf, fmt, **({"exif": exif} if exif else {}))
    return buf.getvalue()


@pytest.fixture
def client(queue, tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path, enable_fake_adapter=True, upload_max_bytes=MIB)
    with TestClient(create_app(engine=queue.engine, settings=settings, now=lambda: NOW)) as client:
        yield client


def upload(client, data=None, content=None, filename="photo.png"):
    return client.post(
        "/api/v1/generations",
        data={"model_id": "fake", **(data or {})},
        files={"image": (filename, image_bytes() if content is None else content, "image/png")},
    )


def assert_empty(queue, tmp_path):
    with Session(queue.engine) as session:
        assert list(session.scalars(select(Generation))) == []
    for area in ("tmp", "sources"):
        assert not list((tmp_path / area).glob("*"))


def test_upload_creates_replayable_generation_and_promotes_files(client, queue, tmp_path):
    content = image_bytes()
    response = upload(client, {"preset": "fast", "params": '{"steps": 3}', "seed": "42"}, content)
    assert response.status_code == 201
    body = response.json()
    identifier = body["id"]
    assert response.headers["location"] == f"/api/v1/generations/{identifier}"
    assert body["status"] == "QUEUED"
    assert body["waiting_reason"] == "NO_WORKER"
    assert body["preset"] == "fast"
    assert body["params"]["steps"] == 3
    assert body["params"]["num_gaussians"] == 131072
    assert body["seed"] == 42
    assert body["created_at"] == body["queued_at"] == "2026-01-01T00:00:00Z"
    row = get(queue, identifier)
    assert row.owner_id == "local"
    assert row.source_sha256 == hashlib.sha256(content).hexdigest()
    assert row.model_version == "fake-1"
    path = tmp_path / "sources" / identifier
    assert (path / "original.png").read_bytes() == content
    with Image.open(path / "input.png") as normalized:
        assert normalized.size == (8, 6)
    assert client.get(body["source"]["url"]).content == content
    assert not list((tmp_path / "tmp").glob("*"))
    assert queue.claim("worker", {"fake"}, now=NOW, free_vram_bytes=10**10).generation_id == identifier


@pytest.mark.parametrize(
    "fmt,extension,mime", [("JPEG", "jpg", "image/jpeg"), ("PNG", "png", "image/png"), ("WEBP", "webp", "image/webp")]
)
def test_actual_format_wins_over_filename_and_declared_mime(client, tmp_path, fmt, extension, mime):
    response = upload(client, content=image_bytes(fmt), filename="misleading.gif")
    assert response.status_code == 201
    body = response.json()
    assert body["source"]["mime"] == mime
    assert (tmp_path / "sources" / body["id"] / f"original.{extension}").is_file()


def test_default_preset_and_random_seed(client):
    bodies = [upload(client).json(), upload(client, {"seed": "random"}).json()]
    for body in bodies:
        assert body["preset"] == "balanced"
        assert body["params"]["steps"] == 50
        assert 0 <= body["seed"] <= 2**31 - 1


@pytest.mark.parametrize(
    "fields",
    [
        {"params": "["},
        {"params": "[]"},
        {"params": "null"},
        {"params": '{"steps":0}'},
        {"params": '{"unknown":1}'},
        {"params": '{"step_delay_s": NaN}'},
        {"preset": "absent"},
        {"seed": "-1"},
        {"seed": "2147483648"},
        {"seed": "1.5"},
        {"seed": "true"},
    ],
)
def test_invalid_parameters_leave_no_rows_or_files(client, queue, tmp_path, fields):
    response = upload(client, fields)
    assert response.status_code == 422
    assert response.json()["code"] == "PARAMS_INVALID"
    assert response.json()["errors"]
    assert_empty(queue, tmp_path)


@pytest.mark.parametrize(
    "content,code", [(b"%PDF-1.7", "IMAGE_UNREADABLE"), (image_bytes("GIF"), "UNSUPPORTED_MEDIA_TYPE")]
)
def test_invalid_image_is_415_before_parameter_validation(client, queue, tmp_path, content, code):
    response = upload(client, {"params": "["}, content)
    assert response.status_code == 415
    assert response.json()["code"] == code
    assert response.headers["content-type"] == "application/problem+json"
    assert_empty(queue, tmp_path)


def test_unknown_model_before_image_validation(client, queue, tmp_path):
    response = upload(client, {"model_id": "missing"}, b"not an image")
    assert response.status_code == 404
    assert response.json()["code"] == "MODEL_NOT_FOUND"
    assert_empty(queue, tmp_path)


def test_pixel_limit_before_parameters(queue, tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path, enable_fake_adapter=True, upload_max_pixels=47)
    with TestClient(create_app(engine=queue.engine, settings=settings, now=lambda: NOW)) as client:
        response = upload(client, {"params": "["})
    assert response.status_code == 422
    assert response.json()["code"] == "IMAGE_TOO_LARGE"
    assert_empty(queue, tmp_path)


def test_upload_limit_without_content_length(client, queue, tmp_path):
    boundary = "test-boundary"
    start = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\nfake\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="test.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode()

    def chunks():
        yield start
        for _ in range(20):
            yield b"x" * 65536
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/api/v1/generations", content=chunks(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    assert response.status_code == 413
    assert response.json()["code"] == "UPLOAD_TOO_LARGE"
    assert_empty(queue, tmp_path)


def test_budget_rejects_against_configured_limit(queue, tmp_path):
    settings = Settings(hephaistos_data_dir=tmp_path, gpu_vram_limit_mb=1024)
    with TestClient(create_app(engine=queue.engine, settings=settings, now=lambda: NOW)) as client:
        response = upload(client, {"model_id": "idol"})
    assert response.status_code == 422
    assert response.json()["code"] == "INSUFFICIENT_VRAM_BUDGET"
    assert_empty(queue, tmp_path)


def test_budget_uses_total_not_free_vram(client, queue):
    worker(queue, gpu_free_bytes=1)
    response = upload(client, {"model_id": "idol"})
    assert response.status_code == 201
    assert response.json()["status"] == "QUEUED"
    assert response.json()["waiting_reason"] == "NO_WORKER"


def test_budget_rejects_against_known_total_vram(client, queue, tmp_path):
    worker(queue, gpu={"total_bytes": MIB, "name": "small"})
    response = upload(client, {"model_id": "idol"})
    assert response.status_code == 422
    assert response.json()["code"] == "INSUFFICIENT_VRAM_BUDGET"
    assert_empty(queue, tmp_path)


def test_upload_during_pause_remains_queued(client):
    client.put("/api/v1/system/dispatch", json={"paused": True})
    body = upload(client).json()
    assert body["status"] == "QUEUED"
    assert body["waiting_reason"] == "DISPATCH_PAUSED"


def test_exif_is_normalized_but_original_is_preserved(client, tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6
    content = image_bytes(exif=exif.tobytes())
    body = upload(client, content=content).json()
    assert (body["source"]["width"], body["source"]["height"]) == (6, 8)
    path = tmp_path / "sources" / body["id"]
    assert (path / "original.png").read_bytes() == content
    with Image.open(path / "input.png") as normalized:
        assert normalized.size == (6, 8)
        assert not normalized.getexif()


def test_failed_promotion_records_ingest_failure(client, queue, tmp_path):
    with patch.object(LocalStorage, "promote_sources", side_effect=OSError("private filesystem detail")):
        response = upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == "INGEST_FAILED"
    assert body["error"]["retryable"] is False
    assert "private filesystem detail" not in response.text
    assert body["queued_at"] is None
    assert body["finished_at"] == "2026-01-01T00:00:00Z"
    assert not list((tmp_path / "sources").glob("*"))


def test_openapi_declares_multipart_and_created_response(client):
    route = client.get("/api/v1/openapi.json").json()["paths"]["/api/v1/generations"]["post"]
    schema = route["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert {"image", "model_id"} == set(schema["required"])
    assert schema["properties"]["image"]["format"] == "binary"
    assert route["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/GenerationView")
    assert "application/problem+json" in route["responses"]["413"]["content"]


@pytest.mark.parametrize("interruption", ["cancel", "delete", "mark_deleting"])
def test_finalization_cannot_revive_cancelled_or_deleted_generation(client, queue, tmp_path, interruption):
    from sqlalchemy import update

    from hephaistos.api.uploads import IngestService

    finalize = IngestService.finalize
    ids = []

    def interrupt(service, identifier, image_format):
        ids.append(identifier)
        route = f"/api/v1/generations/{identifier}"
        # PENDING is visible, but cannot be claimed before its files are published.
        assert get(queue, identifier).status == "PENDING"
        assert queue.claim("worker", {"fake"}, now=NOW, free_vram_bytes=10**10) is None
        if interruption == "cancel":
            assert client.post(route + "/cancel").status_code == 200
        elif interruption == "delete":
            assert client.delete(route).status_code == 204
        else:
            with Session(queue.engine) as session, session.begin():
                session.execute(update(Generation).where(Generation.id == identifier).values(deleting_at=NOW))
        return finalize(service, identifier, image_format)

    with patch.object(IngestService, "finalize", interrupt):
        response = upload(client)
    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"
    row = get(queue, ids[0])
    if interruption == "delete":
        assert row is None
    elif interruption == "cancel":
        assert row.status == "CANCELLED"
    else:
        assert row.deleting_at is not None and row.status == "PENDING"
    assert not (tmp_path / "sources" / ids[0]).exists()
    assert not (tmp_path / "tmp" / ids[0]).exists()


@pytest.mark.parametrize("limit", [MIB, 20 * MIB])
def test_streaming_parser_stops_early_and_closes_spool(queue, tmp_path, limit):
    import asyncio
    import tempfile

    from starlette.datastructures import Headers

    from hephaistos.api.errors import ResourceError
    from hephaistos.api.resources import Resources
    from hephaistos.api.uploads import BoundedMultipartParser

    received = []
    spools = []

    def spool(**kwargs):
        file = tempfile.SpooledTemporaryFile(**kwargs)
        spools.append(file)
        return file

    async def stream():
        yield (b'--bound\r\nContent-Disposition: form-data; name="image"; filename="test.png"\r\n\r\n')
        for index in range(limit // 65536 + 2):
            received.append(index)
            yield b"x" * 65536
        pytest.fail("Stream should have been rejected before the end")

    resources = Resources(queue.engine, Settings(hephaistos_data_dir=tmp_path, upload_max_bytes=limit), lambda: NOW)
    parser = BoundedMultipartParser(
        Headers({"Content-Type": "multipart/form-data; boundary=bound"}), stream(), resources
    )
    with patch("starlette.formparsers.SpooledTemporaryFile", spool), pytest.raises(ResourceError) as exc:
        asyncio.run(parser.parse())
    assert exc.value.status == 413
    assert len(received) == limit // 65536 + 1
    assert spools and all(f.closed for f in spools)


def test_file_size_limit_is_inclusive(client, queue, tmp_path):
    content = image_bytes()
    content += b"\x00" * (MIB - len(content))
    assert upload(client, content=content).status_code == 201
    assert upload(client, content=content + b"\x00").status_code == 413
    with Session(queue.engine) as session:
        assert len(list(session.scalars(select(Generation)))) == 1


@pytest.mark.parametrize(
    "data,files",
    [
        ([("model_id", "fake"), ("model_id", "idol")], [("image", ("x.png", image_bytes()))]),
        ({"model_id": "fake", "extra": "x"}, {"image": ("x.png", image_bytes())}),
        ({"model_id": "fake"}, [("image", ("x.png", image_bytes())), ("image", ("y.png", image_bytes()))]),
        ({"model_id": "fake"}, {"wrong": ("x.png", image_bytes())}),
        ({"model_id": "fake", "image": "not a file"}, {"other": ("x.png", image_bytes())}),
    ],
)
def test_invalid_multipart_fields_are_rejected(client, queue, tmp_path, data, files):
    # httpx accepts repeated form fields through a dict value list.
    if isinstance(data, list):
        data = {"model_id": ["fake", "idol"]}
    response = client.post("/api/v1/generations", data=data, files=files)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert_empty(queue, tmp_path)


def test_disconnected_incomplete_upload_is_rejected(client, queue, tmp_path):
    body = b'--bound\r\nContent-Disposition: form-data; name="image"; filename="x.png"\r\n\r\npartial'
    response = client.post(
        "/api/v1/generations", content=body, headers={"Content-Type": "multipart/form-data; boundary=bound"}
    )
    assert response.status_code == 422
    assert_empty(queue, tmp_path)


def test_filename_is_display_metadata_only(client, tmp_path):
    body = upload(client, filename="../../outside.png").json()
    assert body["source"]["filename"] == "outside.png"
    assert (tmp_path / "sources" / body["id"] / "original.png").is_file()
    assert not (tmp_path / "outside.png").exists()


def test_unpublished_ingestion_failure_cannot_be_retried_as_inference(client, queue):
    with patch.object(LocalStorage, "promote_sources", side_effect=OSError("disk failure")):
        body = upload(client).json()
    assert body["status"] == "FAILED"
    response = client.post(f"/api/v1/generations/{body['id']}/retry")
    assert response.status_code == 409
    assert queue.claim("worker", {"fake"}, now=NOW, free_vram_bytes=10**10) is None


def test_disk_failure_before_pending_is_problem_json(client, queue, tmp_path):
    with patch("hephaistos.api.uploads.copy_image", side_effect=OSError("sensitive disk path")):
        response = upload(client)
    assert response.status_code == 503
    assert response.json()["code"] == "INGEST_FAILED"
    assert response.headers["content-type"] == "application/problem+json"
    assert "sensitive disk path" not in response.text
    assert_empty(queue, tmp_path)


def test_hundred_million_pixel_header_is_rejected_without_decoding(client, queue, tmp_path):
    import struct
    import zlib

    content = bytearray(image_bytes())
    content[16:24] = struct.pack(">II", 10000, 10000)
    content[29:33] = struct.pack(">I", zlib.crc32(content[12:29]))
    with pytest.warns(Image.DecompressionBombWarning):
        response = upload(client, content=bytes(content))
    assert response.status_code == 422
    assert response.json()["code"] == "IMAGE_TOO_LARGE"
    assert_empty(queue, tmp_path)


def test_no_multipart_and_missing_boundary_are_rejected(client, queue, tmp_path):
    assert client.post("/api/v1/generations", json={"model_id": "fake"}).status_code == 415
    response = client.post("/api/v1/generations", content=b"anything", headers={"Content-Type": "multipart/form-data"})
    assert response.status_code == 422
    assert_empty(queue, tmp_path)


def test_authentication_runs_before_body_consumption(client):
    from fastapi import HTTPException

    from hephaistos.api.dependencies import current_principal

    def deny():
        raise HTTPException(403, "Denied")

    client.app.dependency_overrides[current_principal] = deny
    assert upload(client).status_code == 403
