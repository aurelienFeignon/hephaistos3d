"""Bounded multipart reception and recoverable PENDING → QUEUED publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import shutil
from collections.abc import AsyncGenerator
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, BinaryIO

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from hephaistos.adapters.base import AdapterError
from hephaistos.adapters.schema import effective_params
from hephaistos.api.admission import LEASE_SECONDS, Admission
from hephaistos.api.dependencies import Principal, current_principal
from hephaistos.api.errors import ResourceError
from hephaistos.api.schemas import GenerationView
from hephaistos.config import MIB
from hephaistos.imaging import ImageRejected, RejectReason, normalize_image
from hephaistos.persistence.models import Generation, SystemState, Worker, new_id
from hephaistos.queue.broker import utc
from hephaistos.storage.local import UnsafePath

if TYPE_CHECKING:
    from hephaistos.api.resources import Resources

CHUNK_BYTES = 64 * 1024
MULTIPART_OVERHEAD_BYTES = 256 * 1024
SEED_MAX = 2**31 - 1


def invalid(detail: str, field: str = "params") -> ResourceError:
    return ResourceError(
        422, "PARAMS_INVALID", detail, errors=[dict(loc=["body", field], msg=detail, type="value_error")]
    )


class BoundedMultipartParser(MultiPartParser):
    """Enforce the image limit before Starlette queues bytes for its spooled file."""

    def __init__(self, headers: Headers, stream: AsyncGenerator[bytes, None], service: Resources) -> None:
        super().__init__(headers, stream, max_files=1, max_fields=4, max_part_size=16 * 1024)
        self.service = service
        self.received = 0
        self.seen: set[str] = set()
        self.complete = False

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        name = self._current_part.field_name
        if name not in {"image", "model_id", "preset", "params", "seed"} or name in self.seen:
            raise MultiPartException("Unknown or duplicate form field")
        self.seen.add(name)
        if (name == "image") != (self._current_part.file is not None):
            raise MultiPartException("Only image must be a file")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self.received += end - start
            if self.received > self.service.settings.upload_max_bytes:
                raise ResourceError(413, "UPLOAD_TOO_LARGE", "Image exceeds UPLOAD_MAX_BYTES")
        super().on_part_data(data, start, end)

    def on_part_end(self) -> None:
        super().on_part_end()
        if self._current_part.field_name == "model_id":
            self.service.adapter(self._current_part.data.decode(self._charset, errors="replace"))

    def on_end(self) -> None:
        self.complete = True


async def receive(request: Request, service: Resources) -> FormData:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise ResourceError(415, "UNSUPPORTED_MEDIA_TYPE", "Expected multipart/form-data")

    async def bounded_stream() -> AsyncGenerator[bytes, None]:
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > service.settings.upload_max_bytes + MULTIPART_OVERHEAD_BYTES:
                raise ResourceError(413, "UPLOAD_TOO_LARGE", "Multipart request exceeds upload budget")
            yield chunk

    parser = BoundedMultipartParser(request.headers, bounded_stream(), service)
    try:
        form = await parser.parse()
    except MultiPartException as exc:
        raise ResourceError(422, "VALIDATION_ERROR", "Invalid multipart form") from exc
    if not parser.complete or not {"image", "model_id"} <= parser.seen:
        await form.close()
        raise ResourceError(422, "VALIDATION_ERROR", "A complete image and model_id are required")
    return form


def copy_image(source: BinaryIO, destination: Path, max_bytes: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with destination.open("xb") as handle:
        while chunk := source.read(CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise ResourceError(413, "UPLOAD_TOO_LARGE", "Image exceeds UPLOAD_MAX_BYTES")
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest()


def parse_params(value: str) -> dict[str, Any]:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"Non-finite number: {constant}")

    try:
        result = json.loads(value, parse_constant=reject_constant)
    except (ValueError, RecursionError) as exc:
        raise invalid("params must be a JSON object with finite values") from exc
    if not isinstance(result, dict):
        raise invalid("params must be a JSON object")
    return result


def parse_seed(value: str) -> int:
    if value == "random":
        return secrets.randbelow(SEED_MAX + 1)
    if re.fullmatch(r"[0-9]{1,10}", value) is None or int(value) > SEED_MAX:
        raise invalid(f"seed must be random or an integer from 0 to {SEED_MAX}", "seed")
    return int(value)


class IngestService:
    def __init__(self, resources: Resources) -> None:
        self.resources = resources

    def check_budget(self, required: int) -> None:
        if required == 0:
            return
        service = self.resources
        budget = service.settings.gpu_vram_limit_mb * MIB or None
        with Session(service.engine) as session:
            # The hardware budget remains useful when a worker is stopped/unavailable.
            totals = [w.gpu.get("total_bytes") for w in session.scalars(select(Worker))]
        known = [t for t in totals if isinstance(t, int) and not isinstance(t, bool) and t > 0]
        if known:
            total = max(known)
            budget = min(budget, total) if budget is not None else total
        if budget is not None and required > budget:
            raise ResourceError(422, "INSUFFICIENT_VRAM_BUDGET", "Model parameters exceed the GPU memory budget")

    def create(self, form: FormData, principal: Principal, reservation: str | None = None) -> str:
        service = self.resources
        model_id = str(form["model_id"])
        adapter = service.adapter(model_id)
        file = form["image"]
        if not isinstance(file, UploadFile):
            raise ResourceError(422, "VALIDATION_ERROR", "image must be a file")
        identifier = new_id()
        staged = service.storage.upload_dir(identifier)
        try:
            staged.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise ResourceError(503, "INGEST_FAILED", "Upload storage is unavailable") from exc
        retain_for_recovery = False
        try:
            received = staged / "received"
            sha256 = copy_image(file.file, received, service.settings.upload_max_bytes)
            try:
                info = normalize_image(received, staged / "input.png", max_pixels=service.settings.upload_max_pixels)
            except ImageRejected as exc:
                if exc.reason == RejectReason.TOO_MANY_PIXELS:
                    raise ResourceError(422, "IMAGE_TOO_LARGE", "Image exceeds UPLOAD_MAX_PIXELS") from exc
                code = "UNSUPPORTED_MEDIA_TYPE" if exc.reason == RejectReason.UNSUPPORTED_FORMAT else "IMAGE_UNREADABLE"
                raise ResourceError(415, code, "Unsupported or unreadable image") from exc
            if info.source_mime not in adapter.descriptor.input_formats:
                raise ResourceError(415, "UNSUPPORTED_MEDIA_TYPE", "Image format is not supported by this model")
            default = "balanced" if "balanced" in adapter.presets else next(iter(adapter.presets), None)
            preset = str(form["preset"]) if "preset" in form else default
            try:
                params = effective_params(adapter, preset, parse_params(str(form.get("params", "{}"))))
            except AdapterError as exc:
                raise invalid(exc.message) from exc
            seed = parse_seed(str(form.get("seed", "random")))
            self.check_budget(adapter.vram_required_bytes(params))
            received.rename(staged / service.storage.source_file(identifier, info.source_format).name)
            filename = (file.filename or "image").replace("\\", "/").rsplit("/", 1)[-1]
            filename = "".join(c for c in filename if c.isprintable())[:255] or "image"
            with Session(service.engine) as session:
                session.execute(text("BEGIN IMMEDIATE"))
                if reservation is not None:
                    state = session.get(SystemState, 1)
                    expiry = state.ingest_reservations.get(reservation) if state else None
                    if expiry is None or utc(datetime.fromisoformat(expiry)) <= utc(service.now()):
                        raise ResourceError(503, "UPLOAD_LEASE_LOST", "Upload admission lease expired")
                session.add(
                    Generation(
                        id=identifier,
                        owner_id=principal.id,
                        model_id=model_id,
                        model_version=adapter.descriptor.version,
                        status="PENDING",
                        preset=preset,
                        params=params.model_dump(mode="json"),
                        seed=seed,
                        source_filename=filename,
                        source_mime=info.source_mime,
                        source_sha256=sha256,
                        source_width=info.width,
                        source_height=info.height,
                        source_has_alpha=info.has_alpha,
                        created_at=utc(service.now()),
                        max_retries=service.settings.job_max_retries,
                    )
                )
                session.commit()
            # A crash or database failure after this point leaves PENDING for janitor recovery.
            retain_for_recovery = True
            self.finalize(identifier, info.source_format)
            retain_for_recovery = False
            return identifier
        except ResourceError:
            retain_for_recovery = False
            raise
        except OSError as exc:
            raise ResourceError(503, "INGEST_FAILED", "Upload storage is unavailable") from exc
        finally:
            if not retain_for_recovery:
                shutil.rmtree(staged.parent, ignore_errors=True)

    def finalize(self, identifier: str, image_format: str) -> None:
        service = self.resources
        with Session(service.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            generation = session.get(Generation, identifier)
            if generation is None or generation.deleting_at is not None or generation.status != "PENDING":
                raise ResourceError(409, "INVALID_TRANSITION", "Upload was cancelled or deleted during ingestion")
            values: dict[str, Any]
            try:
                service.storage.promote_sources(identifier, image_format)
            except (OSError, UnsafePath):
                values = dict(
                    status="FAILED",
                    finished_at=utc(service.now()),
                    error_code="INGEST_FAILED",
                    error_message="Could not publish the uploaded image",
                    needs_diagnosis=True,
                )
            else:
                values = dict(status="QUEUED", queued_at=utc(service.now()))
            changed = session.scalar(
                update(Generation)
                .where(Generation.id == identifier, Generation.status == "PENDING", Generation.deleting_at.is_(None))
                .values(**values)
                .returning(Generation.id)
            )
            if changed is None:
                raise ResourceError(409, "INVALID_TRANSITION", "Upload state changed")
            session.commit()


def register_upload(router: APIRouter, resources: Resources) -> None:
    ingest = IngestService(resources)
    admission = Admission(resources.engine, resources.settings)
    upload_schema = dict(
        type="object",
        required=["image", "model_id"],
        additionalProperties=False,
        properties=dict(
            image=dict(type="string", format="binary"),
            model_id=dict(type="string"),
            preset=dict(type="string"),
            params=dict(type="string", default="{}"),
            seed=dict(type="string", default="random"),
        ),
    )
    # Match the common problem schema without importing the application factory.
    problem_content = router.responses[422]["content"]

    @router.post(
        "/generations",
        response_model=GenerationView,
        status_code=201,
        tags=["generations"],
        responses={
            413: dict(description="Upload too large", content=problem_content),
            415: dict(description="Unsupported image or request media type", content=problem_content),
        },
        openapi_extra={
            "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": upload_schema}}}
        },
    )
    async def create(
        request: Request, response: Response, principal: Annotated[Principal, Depends(current_principal)]
    ) -> dict[str, Any]:
        reservation = await run_in_threadpool(admission.reserve, now=resources.now())

        async def renew() -> None:
            while True:
                await asyncio.sleep(LEASE_SECONDS / 3)
                await run_in_threadpool(admission.renew, reservation, now=resources.now())

        heartbeat = asyncio.create_task(renew())
        form: FormData | None = None
        try:
            form = await receive(request, resources)
            identifier = await run_in_threadpool(ingest.create, form, principal, reservation)
            request.state.generation_id = identifier
            with Session(resources.engine) as session:
                body = resources.body(session, resources.generation(session, identifier))
            response.headers["Location"] = f"/api/v1/generations/{identifier}"
            return body
        finally:
            if form is not None:
                await form.close()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, ResourceError):
                await heartbeat
            await run_in_threadpool(admission.release, reservation)
