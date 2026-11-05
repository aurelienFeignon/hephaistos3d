"""Public response schemas, shared with generated API clients."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from hephaistos.system.dispatch import DispatchState


class Health(BaseModel):
    status: Literal["ok"]


class ArtifactView(BaseModel):
    id: str
    generation_id: str
    kind: Literal["SPLAT_PLY", "PREVIEW_PNG", "CAMERA_JSON"]
    mime: str
    sha256: str
    size_bytes: int
    metadata: dict[str, Any]
    created_at: datetime
    download_url: str


class SourceView(BaseModel):
    filename: str
    mime: str
    width: int
    height: int
    has_alpha: bool
    url: str


class ProgressView(BaseModel):
    stage: str | None
    current: int | None
    total: int | None
    updated_at: datetime | None


class ErrorView(BaseModel):
    code: str
    message: str | None
    retryable: bool
    needs_diagnosis: bool


class GenerationView(BaseModel):
    id: str
    model_id: str
    model_version: str
    status: Literal["PENDING", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    waiting_reason: str | None
    preset: str | None
    params: dict[str, Any]
    seed: int
    source: SourceView
    progress: ProgressView
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    elapsed_ms: int | None
    attempt_count: int
    max_retries: int
    next_attempt_at: datetime | None
    error: ErrorView | None
    protected: bool
    artifacts: list[ArtifactView]


class GenerationPage(BaseModel):
    items: list[GenerationView]
    next_cursor: str | None


class QueueItem(GenerationView):
    position: int | None


class QueueView(BaseModel):
    items: list[QueueItem]


class ModelGpu(BaseModel):
    vram_required_bytes: int
    min_compute_capability: str


class ModelView(BaseModel):
    id: str
    name: str
    version: str
    available: bool
    unavailable_reason: str | None
    capabilities: list[str]
    input_formats: list[str]
    output_kinds: list[str]
    gpu: ModelGpu | None
    default_preset: str | None


class PresetView(BaseModel):
    label: str
    values: dict[str, Any]


class AttemptView(BaseModel):
    id: str
    generation_id: str
    number: int
    trigger: str
    worker_id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str | None
    error_code: str | None
    error_message: str | None
    retryable: bool | None
    stage_durations_ms: dict[str, Any]
    peak_vram_bytes: int | None
    free_vram_at_start_bytes: int | None


class DatabaseStatus(BaseModel):
    ok: bool


class QueueStatus(BaseModel):
    queued: int
    running: int
    gpu_slots: int


class GpuStatus(BaseModel):
    free_vram_bytes: int | None
    min_free_vram_bytes: int
    vram_limit_bytes: int | None
    waiting_reason: str | None


class WorkerGpu(BaseModel):
    name: str | None
    compute_capability: str | None
    vram_total_bytes: int | None
    vram_free_bytes: int


class WorkerView(BaseModel):
    id: str
    state: str
    waiting_reason: str | None
    last_heartbeat_at: datetime
    adapters: list[dict[str, Any]]
    gpu: WorkerGpu


class StorageStatus(BaseModel):
    results_bytes: int
    free_bytes: int


class SystemStatus(BaseModel):
    version: str
    database: DatabaseStatus
    queue: QueueStatus
    dispatch: DispatchState
    gpu: GpuStatus
    workers: list[WorkerView]
    storage: StorageStatus
