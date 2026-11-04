"""Single-worker orchestration without CUDA imports; inference runs in a spawn child.

The control loop stays responsive during silent load/encode/decode. Its background
thread only drives the inference pipe; database writes remain on the control thread.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import shutil
import signal
import socket
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session
from ulid import ULID

from hephaistos.adapters.base import GenerationInput, ModelAdapter
from hephaistos.adapters.registry import check_installations
from hephaistos.config import MIB, ConfigError
from hephaistos.domain.errors import ErrorCode
from hephaistos.gpu.nvml import GpuSnapshot, Nvml
from hephaistos.persistence.models import SystemState, Worker
from hephaistos.queue.broker import ArtifactInfo, JobBroker, Lease, LeaseLost
from hephaistos.storage.local import LocalStorage
from hephaistos.worker.inference import (
    Cancelled,
    Failed,
    InferenceProcess,
    Outcome,
    ProgressEvent,
    StageEvent,
    StepEvent,
    Succeeded,
)

log = logging.getLogger(__name__)


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class Inference(Protocol):
    @property
    def pid(self) -> int | None: ...
    def start(self, timeout_s: float = 900.0) -> float: ...
    def is_alive(self) -> bool: ...
    def generate(
        self, request: GenerationInput, *, on_event: Callable[[ProgressEvent], None] | None = None, timeout_s: float
    ) -> Outcome: ...
    def cancel(self) -> None: ...
    def kill(self) -> None: ...
    def shutdown(self, grace_s: float = 30.0) -> int | None: ...


def probe_gpu() -> GpuSnapshot:
    with Nvml() as nvml:
        return nvml.snapshot()


class Supervisor:
    def __init__(
        self,
        broker: JobBroker,
        storage: LocalStorage,
        *,
        registry: dict[str, ModelAdapter],
        gpu_probe: Callable[[], GpuSnapshot] = probe_gpu,
        process_factory: Callable[[str], Inference] | None = None,
        clock: Clock | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.broker, self.storage, self.registry = broker, storage, registry
        self.settings = broker.settings
        self.clock = clock or SystemClock()
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{ULID()}"
        self.gpu_probe = gpu_probe
        self.process_factory = process_factory or self._make_process
        self.installations = check_installations(registry, self.settings.hephaistos_models_root)
        self.state = "IDLE"
        self.waiting_reason: str | None = None
        self.child: Inference | None = None
        self.child_model: str | None = None
        self.child_started_at: datetime | None = None
        self.lease: Lease | None = None
        self._future: Future[float | Outcome] | None = None
        self._phase = ""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference-control")
        self._events: queue.SimpleQueue[tuple[str, ProgressEvent, float]] = queue.SimpleQueue()
        self._started_at = self.clock.now()
        self._last_job = self.clock.monotonic()
        self._heartbeat_at = self.clock.monotonic()
        self._published_at = float("-inf")
        self._published_state = ""
        self._job_deadline = 0.0
        self._stage: str | None = None
        self._stage_at = 0.0
        self._durations: dict[str, int] = {}
        self._stop_at: float | None = None
        self._cancel_at: float | None = None
        self._gpu_retry_at = float("-inf")
        self._gpu: GpuSnapshot | None = None
        self._deaths: list[datetime] = []
        self._degraded = False
        self._closed = False

    def _make_process(self, model: str) -> InferenceProcess:
        return InferenceProcess(
            model,
            models_root=self.settings.hephaistos_models_root,
            options={
                "exec_mode": self.settings.idol_exec_mode,
                "dinov3_dtype": self.settings.idol_dinov3_dtype,
                "vram_limit_mb": self.settings.gpu_vram_limit_mb,
            },
        )

    def request_stop(self) -> None:
        if self._stop_at is None:
            self._stop_at = self.clock.monotonic()

    def _publish(self, *, force: bool = False) -> None:
        now = self.clock.monotonic()
        if (
            not force
            and self.state == self._published_state
            and now - self._published_at < self.settings.job_lease_seconds / 3
        ):
            return
        values: dict[str, Any] = dict(
            state=self.state,
            waiting_reason=self.waiting_reason if self.state == "WAITING_GPU" else None,
            gpu_free_bytes=self._gpu.free_bytes if self._gpu else 0,
            inference_pid=self.child.pid if self.child else None,
            inference_started_at=self.child_started_at,
            child_deaths_window=[d.isoformat() for d in self._deaths],
            adapters=[
                dict(id=k, version=self.registry[k].descriptor.version, available=v.available, reason=v.reason)
                for k, v in self.installations.items()
            ],
            gpu=dict(name=self._gpu.name, total_bytes=self._gpu.total_bytes, free_bytes=self._gpu.free_bytes)
            if self._gpu
            else {},
            current_generation_id=self.lease.generation_id if self.lease else None,
            started_at=self._started_at,
            last_heartbeat_at=self.clock.now(),
        )
        with Session(self.broker.engine) as session, session.begin():
            worker = session.get(Worker, self.worker_id)
            if worker is None:
                session.add(Worker(id=self.worker_id, **values))
            else:
                for key, value in values.items():
                    setattr(worker, key, value)
        self._published_state, self._published_at = self.state, now

    def _death(self) -> None:
        now = self.clock.now()
        self._deaths = [death for death in self._deaths if (now - death).total_seconds() < 600]
        self._deaths.append(now)
        if len(self._deaths) >= 3:
            self._degraded = True
            with Session(self.broker.engine) as session:
                session.execute(text("BEGIN IMMEDIATE"))
                state = session.get(SystemState, 1)
                if state is None:
                    state = SystemState(id=1, updated_at=now)
                    session.add(state)
                state.dispatch_paused = True
                state.paused_by = "system"
                state.paused_reason = "Worker degraded after repeated child deaths"
                state.paused_at = state.updated_at = now
                session.commit()

    def _drop_child(self, *, unexpected: bool = False) -> None:
        if self.child:
            self.child.shutdown(grace_s=0)
        self.child = None
        self.child_model = None
        self.child_started_at = None
        if unexpected:
            self._death()

    def _cleanup_attempt(self, lease: Lease) -> None:
        path = self.storage.attempt_dir(lease.generation_id, lease.attempt_id)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass

    def _release(self) -> None:
        lease = self.lease
        self.lease = None
        self._future = None
        self._phase = ""
        self._cancel_at = None
        self._last_job = self.clock.monotonic()
        if lease:
            try:
                self._cleanup_attempt(lease)
            except OSError:
                log.exception("Could not clean attempt staging; janitor will retry")
        self.state = "DEGRADED" if self._degraded else "READY" if self.child else "IDLE"
        self._publish(force=True)

    def _lost(self) -> None:
        self._drop_child()
        self._release()

    def _artifacts(self, outcome: Succeeded) -> list[ArtifactInfo]:
        assert self.lease is not None
        staged = self.storage.attempt_dir(self.lease.generation_id, self.lease.attempt_id)
        result = []
        for key, kind, name, mime in [
            ("splat", "SPLAT_PLY", "splat.ply", "application/octet-stream"),
            ("preview", "PREVIEW_PNG", "preview.png", "image/png"),
            ("camera", "CAMERA_JSON", "camera.json", "application/json"),
        ]:
            path = staged / name
            if outcome.output.artifacts.get(key) != path or path.is_symlink() or not path.is_file():
                raise ValueError(f"Invalid {key} artifact")
            with path.open("rb") as handle:
                sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
            metadata = dict(outcome.output.metrics) if key == "splat" else {}
            result.append(ArtifactInfo(kind, mime, path.stat().st_size, sha256, metadata))
        return result

    def _promote(self, lease: Lease) -> None:
        # Called exclusively inside broker.complete's lease-validated write transaction.
        # The broker refuses pre-existing authoritative artifact rows.
        destination = self.storage.result_dir(lease.generation_id)
        if destination.exists():
            shutil.rmtree(destination)
        self.storage.promote_results(lease.generation_id, lease.attempt_id)

    def _record_stage(self, at: float) -> None:
        if self._stage is not None:
            self._durations[self._stage] = self._durations.get(self._stage, 0) + max(
                0, round((at - self._stage_at) * 1000)
            )
            self._stage_at = at

    def _finish(self, outcome: Outcome) -> None:
        assert self.lease is not None
        lease = self.lease
        now = self.clock.now()
        try:
            self._record_stage(self.clock.monotonic())
            peak = outcome.output.metrics.get("max_allocated_bytes") if isinstance(outcome, Succeeded) else None
            self.broker.record_metrics(
                lease,
                now=now,
                stage_durations_ms=self._durations,
                peak_vram_bytes=int(peak) if peak is not None else None,
            )
            if self.broker.heartbeat(lease, now=now):
                self.broker.finish_cancel(lease, now=now)
            elif isinstance(outcome, Succeeded):
                self.broker.complete(lease, self._artifacts(outcome), promote=lambda: self._promote(lease), now=now)
            elif isinstance(outcome, Cancelled):
                self.broker.finish_cancel(lease, now=now)
            else:
                self.broker.fail(lease, outcome.code, outcome.message, now=now, error_trace=outcome.error_trace)
        except LeaseLost:
            self._drop_child()
            try:
                self.broker.finish_cancel(lease, now=now)
            except LeaseLost:
                pass
        except Exception as exc:  # noqa: BLE001 — unknown failures are diagnosed, never auto-retried
            self.broker.fail(lease, ErrorCode.INTERNAL, str(exc), now=now, error_trace=traceback.format_exc())
        self._release()

    def _relay(self, attempt_id: str) -> Callable[[ProgressEvent], None]:
        def receive(event: ProgressEvent) -> None:
            self._events.put((attempt_id, event, self.clock.monotonic()))

        return receive

    def _active(self) -> None:
        assert self.lease is not None and self._future is not None and self.child is not None
        now = self.clock.monotonic()
        if self._stop_at is not None and now - self._stop_at >= self.settings.worker_shutdown_grace_seconds:
            self._drop_child()
            try:
                self.broker.interrupt(self.lease, now=self.clock.now())
            except LeaseLost:
                pass
            self._release()
            return
        if now - self._heartbeat_at >= self.settings.job_lease_seconds / 3:
            try:
                if self.broker.heartbeat(self.lease, now=self.clock.now()) and self._cancel_at is None:
                    self._cancel_at = now
            except LeaseLost:
                self._lost()
                return
            self._heartbeat_at = now
        if self._cancel_at is not None:
            self.child.cancel()
            if now - self._cancel_at >= self.settings.worker_shutdown_grace_seconds:
                self._drop_child()
                self.broker.finish_cancel(self.lease, now=self.clock.now())
                self._release()
                return
        while not self._events.empty():
            attempt_id, event, event_at = self._events.get()
            if attempt_id != self.lease.attempt_id:
                continue
            try:
                if isinstance(event, StageEvent):
                    self._record_stage(event_at)
                    self._stage, self._stage_at = event.stage.value, event_at
                if isinstance(event, StageEvent) and event.stage.value != "sampling":
                    self.broker.report_progress(self.lease, stage=event.stage.value, now=self.clock.now())
                elif isinstance(event, StepEvent):
                    self.broker.report_progress(
                        self.lease, stage="sampling", current=event.index, total=event.total, now=self.clock.now()
                    )
            except LeaseLost:
                self._lost()
                return
        if now >= self._job_deadline:
            self._drop_child(unexpected=True)
            self._finish(Failed(ErrorCode.TIMEOUT, "Job deadline exceeded"))
            return
        if not self._future.done():
            self._publish()
            return
        try:
            result = self._future.result()
            if self._phase == "loading":
                assert isinstance(result, float)
                self.state = "BUSY"
                lease = self.lease
                adapter = self.registry[lease.model_id]
                staged = self.storage.attempt_dir(lease.generation_id, lease.attempt_id)
                staged.mkdir(parents=True, exist_ok=True)
                request = GenerationInput(
                    self.storage.source_dir(lease.generation_id) / "input.png",
                    adapter.params_model.model_validate(lease.params),
                    lease.seed,
                    staged,
                )
                self._phase = "generating"
                self._future = self._executor.submit(
                    self.child.generate,
                    request,
                    on_event=self._relay(lease.attempt_id),
                    timeout_s=max(0.01, self._job_deadline - now),
                )
                self._publish(force=True)
                return
            assert isinstance(result, Succeeded | Failed | Cancelled)
            if isinstance(result, Failed) and result.code in {
                ErrorCode.CUDA_ERROR,
                ErrorCode.WORKER_CRASH,
                ErrorCode.TIMEOUT,
            }:
                self._drop_child(unexpected=True)
            self._finish(result)
        except LeaseLost:
            self._lost()
        except Exception as exc:  # noqa: BLE001 — thread/load failures become classified job failures
            self._drop_child(unexpected=True)
            self.broker.fail(
                self.lease,
                getattr(exc, "code", ErrorCode.INTERNAL),
                str(exc),
                now=self.clock.now(),
                error_trace=traceback.format_exc(),
            )
            self._release()

    def tick(self) -> None:
        try:
            self._tick()
        except ConfigError:
            raise
        except LeaseLost:
            self._lost()
        except Exception as exc:
            if self.lease is None:
                raise
            self._drop_child(unexpected=True)
            self.broker.fail(
                self.lease, ErrorCode.INTERNAL, str(exc), now=self.clock.now(), error_trace=traceback.format_exc()
            )
            self._release()

    def _tick(self) -> None:
        if self._closed:
            return
        if self.lease:
            self._active()
            return
        if self.child and not self.child.is_alive():
            self._drop_child(unexpected=True)
        if self._stop_at is not None:
            self._drop_child()
            self.state = "STOPPING"
            self._publish()
            return
        if self._degraded:
            self.state = "DEGRADED"
            self._publish()
            return
        if (
            self.child
            and self.settings.model_idle_unload_seconds > 0
            and self.clock.monotonic() - self._last_job >= self.settings.model_idle_unload_seconds
        ):
            self._drop_child()
        if self.clock.monotonic() < self._gpu_retry_at:
            self._publish()
            return
        try:
            self._gpu = self.gpu_probe()
        except Exception:  # noqa: BLE001 — absent GPU is a recoverable worker state
            self._gpu = None
            self.state = "NO_GPU"
            self._gpu_retry_at = self.clock.monotonic() + 30
            self.broker.expire_gpu_wait(
                {key for key, value in self.installations.items() if value.available}, now=self.clock.now()
            )
            self._publish()
            return
        if self.settings.gpu_vram_limit_mb * MIB > self._gpu.total_bytes:
            raise ConfigError("GPU_VRAM_LIMIT_MB exceeds GPU total memory")
        with Session(self.broker.engine) as session:
            system = session.get(SystemState, 1)
            paused = system.dispatch_paused if system else self.settings.dispatch_paused
        self.waiting_reason = None
        if paused:
            self.state = "WAITING_GPU"
            self.waiting_reason = "DISPATCH_PAUSED"
        elif self._gpu.free_bytes < self.settings.gpu_min_free_vram_mb * MIB:
            self.state = "WAITING_GPU"
            self.waiting_reason = "VRAM_INSUFFICIENT"
            self.broker.expire_gpu_wait(
                {key for key, value in self.installations.items() if value.available}, now=self.clock.now()
            )
            # Release resident memory before another admission check, never mid-job.
            if self.child:
                self._drop_child()
        else:
            available = {key for key, value in self.installations.items() if value.available}
            lease = self.broker.claim(
                self.worker_id, available, now=self.clock.now(), free_vram_bytes=self._gpu.free_bytes
            )
            if lease:
                self.lease = lease
                self._stage = None
                self._durations = {}
                self._heartbeat_at = self.clock.monotonic()
                self._job_deadline = self.clock.monotonic() + self.settings.job_timeout_seconds
                adapter = self.registry[lease.model_id]
                try:
                    adapter.params_model.model_validate(lease.params)
                except ValueError as exc:
                    self.broker.fail(lease, ErrorCode.PARAMS_INVALID, str(exc), now=self.clock.now())
                    self._release()
                    return
                if lease.model_version != adapter.descriptor.version:
                    self.broker.fail(
                        lease,
                        ErrorCode.MODEL_UNAVAILABLE,
                        "Adapter version differs from queued version",
                        now=self.clock.now(),
                    )
                    self._release()
                    return
                if self.child_model != lease.model_id:
                    self._drop_child()
                if self.child is None:
                    self.child = self.process_factory(lease.model_id)
                    self.child_model = lease.model_id
                    self.child_started_at = self.clock.now()
                    self.state = "LOADING"
                    self._future = self._executor.submit(
                        self.child.start, timeout_s=float(self.settings.job_timeout_seconds)
                    )
                    self._phase = "loading"
                else:
                    # Reuse the same launch path after an already completed load.
                    self._future = Future()
                    self._future.set_result(0.0)
                    self._phase = "loading"
                self._publish(force=True)
                return
            self.state = "READY" if self.child else "IDLE"
        self._publish()

    def close(self) -> None:
        if self._closed:
            return
        self.request_stop()
        if self.lease:
            self._drop_child()
            try:
                self.broker.interrupt(self.lease, now=self.clock.now())
            except LeaseLost:
                pass
            self._release()
        self._drop_child()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.state = "STOPPING"
        self._publish(force=True)
        self._closed = True

    def run(self) -> None:
        """Install SIGTERM/SIGINT handlers on the main thread and restore them on exit."""
        previous = {sig: signal.signal(sig, lambda *_: self.request_stop()) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            while self._stop_at is None or self.lease is not None:
                self.tick()
                time.sleep(0.2)
        finally:
            self.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main() -> None:
    import argparse

    from hephaistos.adapters.registry import build_registry
    from hephaistos.config import load_settings
    from hephaistos.persistence.database import create_database_engine

    argparse.ArgumentParser(description="Run the inference supervisor (schema must already be migrated)").parse_args()
    settings = load_settings()
    engine = create_database_engine(settings.hephaistos_data_dir)
    logging.basicConfig(level=logging.INFO)
    try:
        Supervisor(
            JobBroker(engine, settings),
            LocalStorage(settings.hephaistos_data_dir),
            registry=build_registry(enable_fake=settings.enable_fake_adapter),
        ).run()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
