"""API v1 resources. Reads never import CUDA or probe model installations."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import Engine, and_, func, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from hephaistos.adapters.base import ModelAdapter
from hephaistos.adapters.registry import build_registry
from hephaistos.api.admission import Admission
from hephaistos.api.errors import ResourceError
from hephaistos.api.schemas import (
    ArtifactView,
    AttemptView,
    GenerationPage,
    GenerationView,
    Health,
    ModelView,
    PresetView,
    QueueView,
    SystemStatus,
)
from hephaistos.api.uploads import register_upload
from hephaistos.config import MIB, Settings
from hephaistos.domain.errors import ErrorCode
from hephaistos.domain.lifecycle import TERMINAL
from hephaistos.persistence.models import Artifact, Attempt, Generation, SystemState, Worker
from hephaistos.queue.broker import JobBroker, LeaseLost, utc
from hephaistos.storage.deletion import DeletionConflict, delete_generation
from hephaistos.storage.local import LocalStorage, UnsafePath
from hephaistos.system.dispatch import DispatchService

Status = Literal["PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]


class ProtectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protected: StrictBool


def stamp(value: datetime | None) -> str | None:
    return utc(value).isoformat().replace("+00:00", "Z") if value else None


def artifact_body(row: Artifact) -> dict[str, Any]:
    return dict(
        id=row.id,
        generation_id=row.generation_id,
        kind=row.kind,
        mime=row.mime,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        metadata=row.metadata_,
        created_at=stamp(row.created_at),
        download_url=f"/api/v1/artifacts/{row.id}/download",
    )


def encode_cursor(row: Generation) -> str:
    return base64.urlsafe_b64encode(json.dumps([stamp(row.created_at), row.id]).encode()).decode()


def decode_cursor(value: str) -> tuple[datetime, str]:
    try:
        if len(value) > 512:
            raise ValueError("Cursor too long")
        data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        if not isinstance(data, list) or len(data) != 2 or not all(isinstance(x, str) for x in data):
            raise ValueError("Invalid cursor")
        from hephaistos.storage.local import validate_id

        date = datetime.fromisoformat(data[0])
        if date.tzinfo is None:
            raise ValueError("UTC timestamp required")
        return utc(date), validate_id(data[1])
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ResourceError(422, "VALIDATION_ERROR", "Invalid pagination cursor") from exc


@dataclass
class ReadContext:
    paused: bool
    workers: list[Worker]
    artifacts: dict[str, list[Artifact]] = field(default_factory=dict)


def json_tail(path: Path, *, lines: int, max_bytes: int) -> list[dict[str, Any]]:
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        offset = max(0, size - max_bytes)
        stream.seek(offset)
        data = stream.read(max_bytes)
    if offset:
        boundary = data.find(b"\n")
        data = data[boundary + 1 :] if boundary >= 0 else b""
    if data and not data.endswith(b"\n"):
        data = data[: data.rfind(b"\n") + 1]
    events = []
    for line in data.splitlines()[-lines:]:
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


class Resources:
    def __init__(self, engine: Engine, settings: Settings, now: Callable[[], datetime]) -> None:
        self.engine, self.settings, self.now = engine, settings, now
        self.storage = LocalStorage(settings.hephaistos_data_dir)
        self.registry = build_registry(enable_fake=settings.enable_fake_adapter)
        self.broker = JobBroker(engine, settings)
        self.dispatch = DispatchService(engine, settings)

    def generation(self, session: Session, identifier: str) -> Generation:
        row = session.get(Generation, identifier)
        if row is None or row.deleting_at is not None:
            raise ResourceError(404, "GENERATION_NOT_FOUND", "Generation not found")
        return row

    def workers(self, session: Session) -> list[Worker]:
        return list(
            session.scalars(
                select(Worker)
                .where(
                    Worker.last_heartbeat_at > utc(self.now()) - timedelta(seconds=self.settings.job_lease_seconds),
                    Worker.state != "STOPPING",
                )
                .order_by(Worker.id)
            )
        )

    def available_workers(self, session: Session, model_id: str) -> list[Worker]:
        return [
            w
            for w in self.workers(session)
            if w.state not in {"NO_GPU", "DEGRADED"}
            and any(a.get("id") == model_id and a.get("available") is True for a in w.adapters)
        ]

    def read_context(self, session: Session, rows: list[Generation]) -> ReadContext:
        state = session.get(SystemState, 1)
        context = ReadContext(state.dispatch_paused if state else self.settings.dispatch_paused, self.workers(session))
        if rows:
            for artifact in session.scalars(
                select(Artifact).where(Artifact.generation_id.in_([g.id for g in rows])).order_by(Artifact.id)
            ):
                context.artifacts.setdefault(artifact.generation_id, []).append(artifact)
        return context

    def waiting_reason(self, row: Generation, context: ReadContext) -> str | None:
        if row.status != "QUEUED":
            return None
        if context.paused:
            return "DISPATCH_PAUSED"
        if row.not_before and utc(row.not_before) > utc(self.now()):
            return "RETRY_BACKOFF"
        workers = [
            w
            for w in context.workers
            if w.state not in {"NO_GPU", "DEGRADED"}
            and any(a.get("id") == row.model_id and a.get("available") is True for a in w.adapters)
        ]
        if not workers:
            return "NO_WORKER"
        if max(w.gpu_free_bytes for w in workers) < self.settings.gpu_min_free_vram_mb * MIB:
            return "VRAM_INSUFFICIENT"
        return None

    def body(self, session: Session, row: Generation, context: ReadContext | None = None) -> dict[str, Any]:
        context = context or self.read_context(session, [row])
        error = None
        if row.error_code:
            try:
                retryable = ErrorCode(row.error_code).retryable
            except ValueError:
                retryable = False
            error = dict(
                code=row.error_code, message=row.error_message, retryable=retryable, needs_diagnosis=row.needs_diagnosis
            )
        # Même règle que la rétention du janitor : terminale, non protégée, rétention active.
        auto_delete_after = None
        days = self.settings.storage_retention_days
        if days and row.status in TERMINAL and not row.protected and row.finished_at:
            auto_delete_after = utc(row.finished_at) + timedelta(days=days)
        elapsed = None
        if row.started_at:
            elapsed = max(0, int((utc(row.finished_at or self.now()) - utc(row.started_at)).total_seconds() * 1000))
        return dict(
            id=row.id,
            model_id=row.model_id,
            model_version=row.model_version,
            status=row.status,
            waiting_reason=self.waiting_reason(row, context),
            preset=row.preset,
            params=row.params,
            seed=row.seed,
            source=dict(
                filename=row.source_filename,
                mime=row.source_mime,
                width=row.source_width,
                height=row.source_height,
                has_alpha=row.source_has_alpha,
                url=f"/api/v1/generations/{row.id}/source",
                original_url=f"/api/v1/generations/{row.id}/source?original=true",
                normalized_mime="image/png",
            ),
            progress=dict(
                stage=row.progress_stage,
                current=row.progress_current,
                total=row.progress_total,
                updated_at=stamp(row.progress_updated_at),
            ),
            created_at=stamp(row.created_at),
            queued_at=stamp(row.queued_at),
            started_at=stamp(row.started_at),
            finished_at=stamp(row.finished_at),
            elapsed_ms=elapsed,
            attempt_count=row.attempt_count,
            max_retries=row.max_retries,
            next_attempt_at=stamp(row.not_before),
            error=error,
            protected=row.protected,
            auto_delete_after=stamp(auto_delete_after),
            artifacts=[artifact_body(a) for a in context.artifacts.get(row.id, [])],
        )

    def adapter(self, identifier: str) -> ModelAdapter:
        if identifier not in self.registry:
            raise ResourceError(404, "MODEL_NOT_FOUND", "Model not found")
        return self.registry[identifier]

    def model(self, session: Session, identifier: str) -> dict[str, Any]:
        adapter = self.adapter(identifier)
        d = adapter.descriptor
        available = bool(self.available_workers(session, identifier))
        gpu = None
        if d.gpu:
            gpu = dict(
                vram_required_bytes=adapter.vram_required_bytes(adapter.params_model()),
                min_compute_capability=".".join(map(str, d.gpu.min_compute_capability)),
            )
        return dict(
            id=d.id,
            name=d.name,
            version=d.version,
            available=available,
            unavailable_reason=None if available else "No active worker for this model",
            capabilities=sorted(d.capabilities),
            input_formats=sorted(d.input_formats),
            output_kinds=sorted(d.output_kinds),
            gpu=gpu,
            default_preset="balanced" if "balanced" in adapter.presets else next(iter(adapter.presets), None),
        )

    def artifact(self, session: Session, identifier: str) -> Artifact:
        row = session.get(Artifact, identifier)
        parent = session.get(Generation, row.generation_id) if row else None
        if row is None or parent is None or parent.deleting_at is not None:
            raise ResourceError(404, "ARTIFACT_NOT_FOUND", "Artifact not found")
        return row

    @staticmethod
    def file(path: Path) -> Path:
        if not path.is_file():
            raise FileNotFoundError(path)
        return path


def register_resources(router: APIRouter, engine: Engine, settings: Settings, now: Callable[[], datetime]) -> None:
    service = Resources(engine, settings, now)
    register_upload(router, service)

    @router.get("/system/health", response_model=Health, tags=["system"], openapi_extra={"security": []})
    def health() -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise ResourceError(503, "DATABASE_UNAVAILABLE", "Database unavailable") from exc
        return {"status": "ok"}

    @router.get("/system/status", response_model=SystemStatus, tags=["system"])
    def status() -> dict[str, Any]:
        with Session(engine) as session:
            workers = service.workers(session)
            counts = dict(
                session.execute(
                    select(Generation.status, func.count())
                    .where(Generation.deleting_at.is_(None))
                    .group_by(Generation.status)
                ).all()
            )
            size = session.scalar(select(func.coalesce(func.sum(Artifact.size_bytes), 0))) or 0
            return dict(
                version="0.1.0",
                database={"ok": True},
                queue=dict(
                    queued=counts.get("QUEUED", 0),
                    running=counts.get("RUNNING", 0),
                    gpu_slots=settings.gpu_max_concurrent_jobs,
                ),
                dispatch=service.dispatch.get(now=now()).model_dump(mode="json"),
                gpu=dict(
                    free_vram_bytes=max((w.gpu_free_bytes for w in workers if w.state != "NO_GPU"), default=None),
                    min_free_vram_bytes=settings.gpu_min_free_vram_mb * MIB,
                    vram_limit_bytes=settings.gpu_vram_limit_mb * MIB or None,
                    waiting_reason=next((w.waiting_reason for w in workers if w.waiting_reason), None),
                ),
                workers=[
                    dict(
                        id=w.id,
                        state=w.state,
                        waiting_reason=w.waiting_reason,
                        last_heartbeat_at=stamp(w.last_heartbeat_at),
                        adapters=w.adapters,
                        gpu=dict(
                            name=w.gpu.get("name"),
                            compute_capability=w.gpu.get("compute_capability"),
                            vram_total_bytes=w.gpu.get("total_bytes"),
                            vram_free_bytes=w.gpu_free_bytes,
                        ),
                    )
                    for w in workers
                ],
                storage=dict(results_bytes=size, free_bytes=service.storage.free_bytes()),
            )

    @router.get("/models", response_model=list[ModelView], tags=["models"])
    def models() -> list[dict[str, Any]]:
        with Session(engine) as session:
            return [service.model(session, identifier) for identifier in service.registry]

    @router.get("/models/{model_id}", response_model=ModelView, tags=["models"])
    def model(model_id: str) -> dict[str, Any]:
        with Session(engine) as session:
            return service.model(session, model_id)

    @router.get("/models/{model_id}/schema", tags=["models"])
    def schema(model_id: str) -> dict[str, Any]:
        return service.adapter(model_id).params_model.model_json_schema()

    @router.get("/models/{model_id}/presets", response_model=dict[str, PresetView], tags=["models"])
    def presets(model_id: str) -> dict[str, Any]:
        return {key: dict(label=p.label, values=dict(p.values)) for key, p in service.adapter(model_id).presets.items()}

    @router.get("/generations", response_model=GenerationPage, tags=["generations"])
    def generations(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: str | None = None,
        status: Annotated[list[Status] | None, Query()] = None,
        protected: bool | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        query = select(Generation).where(Generation.deleting_at.is_(None))
        if status:
            query = query.where(Generation.status.in_(status))
        if protected is not None:
            query = query.where(Generation.protected == protected)
        if model_id is not None:
            query = query.where(Generation.model_id == model_id)
        if cursor:
            date, identifier = decode_cursor(cursor)
            query = query.where(
                or_(Generation.created_at < date, and_(Generation.created_at == date, Generation.id < identifier))
            )
        with Session(engine) as session:
            rows = list(
                session.scalars(query.order_by(Generation.created_at.desc(), Generation.id.desc()).limit(limit + 1))
            )
            context = service.read_context(session, rows[:limit])
            return dict(
                items=[service.body(session, g, context) for g in rows[:limit]],
                next_cursor=encode_cursor(rows[limit - 1]) if len(rows) > limit else None,
            )

    @router.get("/generations/{identifier}", response_model=GenerationView, tags=["generations"])
    def generation(identifier: str) -> dict[str, Any]:
        with Session(engine) as session:
            return service.body(session, service.generation(session, identifier))

    @router.patch("/generations/{identifier}", response_model=GenerationView, tags=["generations"])
    def protect(identifier: str, body: ProtectionUpdate) -> dict[str, Any]:
        with Session(engine) as session, session.begin():
            changed = session.scalar(
                update(Generation)
                .where(Generation.id == identifier, Generation.deleting_at.is_(None))
                .values(protected=body.protected)
                .returning(Generation.id)
            )
            if changed is None:
                raise ResourceError(404, "GENERATION_NOT_FOUND", "Generation not found")
            return service.body(session, service.generation(session, identifier))

    def action(identifier: str, operation: str, response: Response) -> dict[str, Any]:
        with Session(engine) as session:
            service.generation(session, identifier)
        try:
            if operation == "cancel":
                running = service.broker.cancel(identifier, now=now())
                response.status_code = 202 if running else 200
            else:
                service.broker.retry(
                    identifier, now=now(), admit=lambda session: Admission(engine, settings).check(session, now=now())
                )
        except LeaseLost as exc:
            raise ResourceError(409, "INVALID_TRANSITION", "Generation state does not allow this action") from exc
        return generation(identifier)

    @router.post(
        "/generations/{identifier}/cancel",
        response_model=GenerationView,
        tags=["generations"],
        responses={202: {"description": "Cancellation requested", "model": GenerationView}},
    )
    def cancel(identifier: str, response: Response) -> dict[str, Any]:
        return action(identifier, "cancel", response)

    @router.post("/generations/{identifier}/retry", response_model=GenerationView, tags=["generations"])
    def retry(identifier: str, response: Response) -> dict[str, Any]:
        return action(identifier, "retry", response)

    @router.delete("/generations/{identifier}", status_code=204, tags=["generations"])
    def delete(identifier: str) -> Response:
        with Session(engine) as session:
            service.generation(session, identifier)
        try:
            delete_generation(sessionmaker(engine), service.storage, identifier, now=now())
        except DeletionConflict as exc:
            raise ResourceError(409, "GENERATION_RUNNING", "Cancel the running generation first") from exc
        return Response(status_code=204)

    @router.get("/generations/{identifier}/source", response_class=FileResponse, tags=["generations"])
    def source(identifier: str, original: bool = False) -> FileResponse:
        with Session(engine) as session:
            row = service.generation(session, identifier)
            try:
                fmt = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[row.source_mime]
                path = service.file(
                    service.storage.source_file(row.id, fmt)
                    if original
                    else service.storage._check(service.storage.source_dir(row.id) / "input.png")
                )
            except (UnsafePath, FileNotFoundError, KeyError) as exc:
                raise ResourceError(404, "SOURCE_NOT_FOUND", "Source file not found") from exc
            return FileResponse(
                path,
                media_type=row.source_mime if original else "image/png",
                filename=path.name,
                content_disposition_type="attachment" if original else "inline",
            )

    @router.get("/generations/{identifier}/attempts", response_model=list[AttemptView], tags=["generations"])
    def attempts(
        identifier: str,
        response: Response,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, Any]]:
        with Session(engine) as session:
            service.generation(session, identifier)
            rows = list(
                session.scalars(
                    select(Attempt)
                    .where(Attempt.generation_id == identifier, Attempt.number > after)
                    .order_by(Attempt.number)
                    .limit(limit + 1)
                )
            )
            if len(rows) > limit:
                response.headers["X-Next-Attempt"] = str(rows[limit - 1].number)
            return [
                dict(
                    id=a.id,
                    generation_id=a.generation_id,
                    number=a.number,
                    trigger=a.trigger,
                    worker_id=a.worker_id,
                    started_at=stamp(a.started_at),
                    finished_at=stamp(a.finished_at),
                    outcome=a.outcome,
                    error_code=a.error_code,
                    error_message=a.error_message,
                    retryable=a.retryable,
                    stage_durations_ms=a.stage_durations_ms,
                    peak_vram_bytes=a.peak_vram_bytes,
                    free_vram_at_start_bytes=a.free_vram_at_start_bytes,
                )
                for a in rows[:limit]
            ]

    @router.get("/generations/{identifier}/logs", tags=["generations"])
    def logs(
        identifier: str, attempt: Annotated[int, Query(ge=1)], tail: Annotated[int, Query(ge=1, le=10000)] = 500
    ) -> list[dict[str, Any]]:
        with Session(engine) as session:
            service.generation(session, identifier)
            row = session.scalar(select(Attempt).where(Attempt.generation_id == identifier, Attempt.number == attempt))
            if row is None:
                raise ResourceError(404, "ATTEMPT_NOT_FOUND", "Attempt not found")
            try:
                path = service.storage.log_file(identifier, attempt)
                return json_tail(path, lines=tail, max_bytes=settings.logs_tail_max_bytes)
            except (UnsafePath, FileNotFoundError) as exc:
                raise ResourceError(404, "LOG_NOT_FOUND", "Attempt log not found") from exc

    @router.get("/generations/{identifier}/artifacts", response_model=list[ArtifactView], tags=["generations"])
    def artifacts(identifier: str) -> list[dict[str, Any]]:
        with Session(engine) as session:
            service.generation(session, identifier)
            return [
                artifact_body(a)
                for a in session.scalars(
                    select(Artifact).where(Artifact.generation_id == identifier).order_by(Artifact.id)
                )
            ]

    @router.get("/artifacts/{identifier}", response_model=ArtifactView, tags=["artifacts"])
    def artifact(identifier: str) -> dict[str, Any]:
        with Session(engine) as session:
            return artifact_body(service.artifact(session, identifier))

    @router.get("/artifacts/{identifier}/download", response_class=FileResponse, tags=["artifacts"])
    def download(identifier: str) -> FileResponse:
        with Session(engine) as session:
            row = service.artifact(session, identifier)
            try:
                name = {"SPLAT_PLY": "splat.ply", "PREVIEW_PNG": "preview.png", "CAMERA_JSON": "camera.json"}[row.kind]
                # Never trust relative_path from SQL as a filesystem path.
                path = service.file(service.storage._check(service.storage.result_dir(row.generation_id) / name))
            except (UnsafePath, FileNotFoundError, KeyError) as exc:
                raise ResourceError(404, "ARTIFACT_NOT_FOUND", "Artifact file not found") from exc
            return FileResponse(
                path,
                media_type=row.mime,
                filename=f"hephaistos-{row.generation_id}-{name}",
                headers={"ETag": f'"{row.sha256}"'},
            )

    @router.get("/queue", response_model=QueueView, tags=["queue"])
    def queue(limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None) -> dict[str, Any]:
        query = select(Generation).where(Generation.deleting_at.is_(None), Generation.status.in_(["QUEUED", "RUNNING"]))
        date, identifier = decode_cursor(cursor) if cursor else (None, None)
        if date is not None:
            query = query.where(
                or_(Generation.created_at > date, and_(Generation.created_at == date, Generation.id > identifier))
            )
        with Session(engine) as session:
            rows = list(session.scalars(query.order_by(Generation.created_at, Generation.id).limit(limit + 1)))
            position = 0
            if date is not None:
                position = (
                    session.scalar(
                        select(func.count())
                        .select_from(Generation)
                        .where(
                            Generation.status == "QUEUED",
                            Generation.deleting_at.is_(None),
                            or_(
                                Generation.created_at < date,
                                and_(Generation.created_at == date, Generation.id <= identifier),
                            ),
                        )
                    )
                    or 0
                )
            context = service.read_context(session, rows[:limit])
            items = []
            for row in rows[:limit]:
                if row.status == "QUEUED":
                    position += 1
                items.append(
                    dict(service.body(session, row, context), position=position if row.status == "QUEUED" else None)
                )
            return dict(items=items, next_cursor=encode_cursor(rows[limit - 1]) if len(rows) > limit else None)
