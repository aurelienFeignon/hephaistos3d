import time
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy.orm import Session

from hephaistos.adapters.base import GenerationOutput
from hephaistos.adapters.fake import FakeAdapter
from hephaistos.config import Settings
from hephaistos.domain.errors import ErrorCode
from hephaistos.gpu.nvml import MIB, GpuSnapshot
from hephaistos.persistence.models import SystemState, Worker
from hephaistos.storage.local import LocalStorage
from hephaistos.worker.inference import Cancelled, Failed, InferenceProcess, Succeeded
from hephaistos.worker.supervisor import Supervisor
from tests.unit.test_queue import NOW, attempts, get, job
from tests.unit.test_queue import queue as queue


class Clock:
    elapsed = 0.0

    def now(self):
        return NOW + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed


def gpu(free=10_000):
    return GpuSnapshot("simulated", 12_000 * MIB, (12_000 - free) * MIB, free * MIB, 0, 0, 0)


class Child:
    pid = 12345

    def __init__(self):
        self.alive = False
        self.loaded = Event()
        self.loaded.set()
        self.finish = Event()
        self.finish.set()
        self.outcome = None
        self.calls = 0

    def start(self, timeout_s=900):
        self.alive = True
        self.loaded.wait(5)
        return 0.1

    def is_alive(self):
        return self.alive

    def generate(self, request, *, on_event=None, timeout_s):
        self.calls += 1
        self.finish.wait(5)
        if self.outcome is not None:
            return self.outcome
        artifacts = {}
        for kind, name in [("splat", "splat.ply"), ("preview", "preview.png"), ("camera", "camera.json")]:
            path = request.output_dir / name
            path.write_bytes(b"complete")
            artifacts[kind] = path
        return Succeeded(GenerationOutput(artifacts, {"gaussians": 32}))

    def cancel(self):
        self.outcome = Cancelled()
        self.finish.set()

    def kill(self):
        self.alive = False
        self.outcome = Failed(ErrorCode.WORKER_CRASH, "killed")
        self.loaded.set()
        self.finish.set()

    def shutdown(self, grace_s=30):
        self.kill()
        return 0


@pytest.fixture
def rig(queue, tmp_path):
    clock = Clock()
    children = []

    def factory(model):
        child = Child()
        children.append(child)
        return child

    supervisor = Supervisor(
        queue,
        LocalStorage(tmp_path / "data"),
        registry={"fake": FakeAdapter()},
        gpu_probe=lambda: gpu(),
        process_factory=factory,
        clock=clock,
        worker_id="test-worker",
    )
    yield supervisor, clock, children
    supervisor.close()


def submit(supervisor, **overrides):
    identifier = job(
        supervisor.broker,
        model_version="fake-1",
        params={"steps": 2, "num_gaussians": 32, "step_delay_s": 0},
        **overrides,
    )
    path = supervisor.storage.source_dir(identifier)
    path.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (8, 8)).save(path / "input.png")
    return identifier


def until(supervisor, predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, "supervisor did not reach expected state"
        supervisor.tick()
        time.sleep(0.005)


def test_module_does_not_import_torch():
    from tests.unit.test_adapters_base import assert_does_not_import_torch

    assert_does_not_import_torch("hephaistos.worker.supervisor")


def test_success_reuses_child_and_unloads_after_idle(rig):
    supervisor, clock, children = rig
    identifier = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    assert (supervisor.storage.result_dir(identifier) / "splat.ply").read_bytes() == b"complete"
    second = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, second).status == "COMPLETED")
    assert len(children) == 1
    assert children[0].calls == 2
    clock.elapsed += 901
    supervisor.tick()
    assert not children[0].is_alive()
    with Session(supervisor.broker.engine) as session:
        worker = session.get(Worker, "test-worker")
        assert worker.state == "IDLE"
        assert worker.inference_pid is None


def test_low_vram_and_paused_dispatch_never_start_child(rig):
    supervisor, _clock, children = rig
    identifier = submit(supervisor)
    supervisor.gpu_probe = lambda: gpu(100)
    supervisor.tick()
    assert get(supervisor.broker, identifier).status == "QUEUED"
    assert children == []
    assert supervisor.state == "WAITING_GPU"
    supervisor.gpu_probe = lambda: gpu()
    with Session(supervisor.broker.engine) as session, session.begin():
        session.add(SystemState(id=1, dispatch_paused=True, updated_at=NOW))
    supervisor.tick()
    assert children == []


def test_no_gpu_retries_after_thirty_seconds(rig):
    supervisor, clock, children = rig
    calls = []

    def missing():
        calls.append(1)
        raise RuntimeError("missing GPU")

    supervisor.gpu_probe = missing
    submit(supervisor)
    supervisor.tick()
    supervisor.tick()
    assert supervisor.state == "NO_GPU"
    assert len(calls) == 1
    clock.elapsed = 30
    supervisor.tick()
    assert len(calls) == 2
    assert not children


def test_heartbeat_continues_during_silent_loading(rig):
    supervisor, clock, _children = rig
    identifier = submit(supervisor)
    child = Child()
    child.loaded.clear()
    supervisor.process_factory = lambda model: child
    supervisor.tick()
    clock.elapsed = 21
    supervisor.tick()
    assert get(supervisor.broker, identifier).lease_expires_at.replace(tzinfo=NOW.tzinfo) > NOW + timedelta(seconds=60)
    child.loaded.set()
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")


def test_stop_interrupts_after_grace_without_consuming_retry_budget(rig):
    supervisor, clock, _children = rig
    identifier = submit(supervisor)
    child = Child()
    child.finish.clear()
    supervisor.process_factory = lambda model: child
    until(supervisor, lambda: child.calls == 1)
    supervisor.request_stop()
    for elapsed in [21, 42, 63, 84, 91]:
        clock.elapsed = elapsed
        supervisor.tick()
    assert not child.alive
    assert get(supervisor.broker, identifier).status == "QUEUED"
    assert get(supervisor.broker, identifier).attempt_count == 0
    assert attempts(supervisor.broker)[0].outcome == "INTERRUPTED"


def test_stale_lease_kills_child_without_publishing(rig):
    supervisor, clock, _children = rig
    identifier = submit(supervisor)
    child = Child()
    child.finish.clear()
    supervisor.process_factory = lambda model: child
    until(supervisor, lambda: child.calls == 1)
    clock.elapsed = 61
    supervisor.broker.recover_expired(identifier, now=clock.now())
    supervisor.tick()
    assert not child.alive
    assert not supervisor.storage.result_dir(identifier).exists()
    assert attempts(supervisor.broker)[0].outcome == "ABANDONED"


def test_cooperative_cancel_is_forwarded(rig):
    supervisor, clock, _children = rig
    identifier = submit(supervisor)
    child = Child()
    child.finish.clear()
    supervisor.process_factory = lambda model: child
    until(supervisor, lambda: child.calls == 1)
    supervisor.broker.cancel(identifier, now=clock.now())
    clock.elapsed = 21
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "CANCELLED")
    assert attempts(supervisor.broker)[0].outcome == "CANCELLED"


def test_orphan_results_are_replaced_only_by_valid_completion(rig):
    supervisor, _clock, _children = rig
    identifier = submit(supervisor)
    orphan = supervisor.storage.result_dir(identifier)
    orphan.mkdir(parents=True)
    (orphan / "splat.ply").write_bytes(b"orphan from crashed attempt")
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    assert (orphan / "splat.ply").read_bytes() == b"complete"


def test_real_spawn_fake_end_to_end(rig):
    supervisor, _clock, _children = rig
    supervisor.process_factory = lambda model: InferenceProcess(model, models_root=Path("/tmp"))
    identifier = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "COMPLETED")
    assert attempts(supervisor.broker)[0].outcome == "SUCCEEDED"
    assert (supervisor.storage.result_dir(identifier) / "camera.json").is_file()


def test_three_child_crashes_degrade_worker_and_pause_dispatch(rig):
    supervisor, _clock, _children = rig

    def factory(model):
        child = Child()
        child.outcome = Failed(ErrorCode.WORKER_CRASH, "injected crash")
        return child

    supervisor.process_factory = factory
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
    with Session(supervisor.broker.engine) as session:
        state = session.get(SystemState, 1)
        assert state.dispatch_paused is True
        assert state.paused_by == "system"
        assert len(session.get(Worker, "test-worker").child_deaths_window) == 3


def test_unknown_error_needs_diagnosis_without_automatic_retry(rig):
    supervisor, _clock, _children = rig

    class BrokenChild(Child):
        def generate(self, request, *, on_event=None, timeout_s):
            raise RuntimeError("unexpected control error")

    supervisor.process_factory = lambda model: BrokenChild()
    identifier = submit(supervisor)
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "FAILED")
    generation = get(supervisor.broker, identifier)
    assert generation.error_code == "INTERNAL"
    assert generation.needs_diagnosis is True
    assert "unexpected control error" in attempts(supervisor.broker)[0].error_trace


def test_gpu_wait_timeout_creates_no_attempt(rig):
    supervisor, clock, children = rig
    settings = Settings(gpu_wait_timeout_seconds=30)
    supervisor.settings = supervisor.broker.settings = settings
    supervisor.gpu_probe = lambda: gpu(100)
    identifier = submit(supervisor)
    supervisor.tick()
    clock.elapsed = 30
    supervisor.tick()
    assert get(supervisor.broker, identifier).status == "FAILED"
    assert get(supervisor.broker, identifier).error_code == "GPU_UNAVAILABLE"
    assert attempts(supervisor.broker) == []
    assert children == []


def test_silent_job_timeout_kills_child_and_requeues(rig):
    supervisor, clock, _children = rig
    settings = Settings(job_timeout_seconds=10)
    supervisor.settings = supervisor.broker.settings = settings
    child = Child()
    child.finish.clear()
    supervisor.process_factory = lambda model: child
    identifier = submit(supervisor)
    until(supervisor, lambda: child.calls == 1)
    clock.elapsed = 10
    supervisor.tick()
    assert not child.alive
    assert get(supervisor.broker, identifier).status == "QUEUED"
    assert get(supervisor.broker, identifier).error_code == "TIMEOUT"


def test_graceful_stop_accepts_completion_without_claiming_next_job(rig):
    supervisor, _clock, _children = rig
    first = submit(supervisor)
    child = Child()
    child.finish.clear()
    supervisor.process_factory = lambda model: child
    until(supervisor, lambda: child.calls == 1)
    second = submit(supervisor)
    supervisor.request_stop()
    child.finish.set()
    until(supervisor, lambda: get(supervisor.broker, first).status == "COMPLETED")
    supervisor.tick()
    assert get(supervisor.broker, second).status == "QUEUED"
    assert child.calls == 1
    assert supervisor.state == "STOPPING"


def test_impossible_vram_limit_refuses_worker_before_claim(rig):
    from hephaistos.config import ConfigError

    supervisor, _clock, children = rig
    settings = Settings(gpu_vram_limit_mb=13_000)
    supervisor.settings = supervisor.broker.settings = settings
    identifier = submit(supervisor)
    with pytest.raises(ConfigError, match="GPU_VRAM_LIMIT_MB"):
        supervisor.tick()
    assert get(supervisor.broker, identifier).status == "QUEUED"
    assert children == []


def test_persisted_artifact_prevents_orphan_cleanup(rig):
    from hephaistos.persistence.models import Artifact

    supervisor, _clock, _children = rig
    identifier = submit(supervisor)
    destination = supervisor.storage.result_dir(identifier)
    destination.mkdir(parents=True)
    original = destination / "splat.ply"
    original.write_bytes(b"authoritative")
    with Session(supervisor.broker.engine) as session, session.begin():
        session.add(
            Artifact(
                generation_id=identifier,
                kind="SPLAT_PLY",
                relative_path=f"{identifier}/splat.ply",
                mime="application/octet-stream",
                size_bytes=13,
                sha256="a" * 64,
                created_at=NOW,
            )
        )
    until(
        supervisor,
        lambda identifier=identifier: (
            get(supervisor.broker, identifier).attempt_count == 1 and supervisor.lease is None
        ),
    )
    assert original.read_bytes() == b"authoritative"


def test_real_fake_failure_preserves_error_trace(rig):
    supervisor, _clock, _children = rig
    supervisor.process_factory = lambda model: InferenceProcess(model, models_root=Path("/tmp"))
    identifier = job(
        supervisor.broker,
        model_version="fake-1",
        params={"steps": 2, "num_gaussians": 32, "step_delay_s": 0, "inject_failure": "NO_OBJECT"},
    )
    source = supervisor.storage.source_dir(identifier)
    source.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (8, 8)).save(source / "input.png")
    until(supervisor, lambda: get(supervisor.broker, identifier).status == "FAILED")
    assert get(supervisor.broker, identifier).error_code == "NO_OBJECT_DETECTED"
    assert "Traceback" in attempts(supervisor.broker)[0].error_trace
    assert attempts(supervisor.broker)[0].stage_durations_ms


def test_sigterm_stops_real_worker_and_spawn_child(queue, tmp_path):
    import os
    import signal
    import subprocess
    import sys

    from PIL import Image

    from hephaistos.storage.local import LocalStorage

    identifier = job(
        queue,
        model_version="fake-1",
        params={"steps": 200, "num_gaussians": 32, "step_delay_s": 0.05, "inject_failure": "HANG"},
    )
    storage = LocalStorage(tmp_path / "data")
    source = storage.source_dir(identifier)
    source.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(source / "input.png")
    script = tmp_path / "run_worker.py"
    script.write_text("""
from pathlib import Path
import sys
from hephaistos.adapters.fake import FakeAdapter
from hephaistos.config import Settings
from hephaistos.gpu.nvml import GpuSnapshot, MIB
from hephaistos.persistence.database import create_database_engine
from hephaistos.queue.broker import JobBroker
from hephaistos.storage.local import LocalStorage
from hephaistos.worker.supervisor import Supervisor

if __name__ == '__main__':
    root = Path(sys.argv[1])
    settings = Settings(worker_shutdown_grace_seconds=1, job_lease_seconds=15, job_timeout_seconds=30)
    engine = create_database_engine(root)
    snapshot = lambda: GpuSnapshot('simulated', 12000*MIB, 0, 10000*MIB, 0, 0, 0)
    try:
        Supervisor(JobBroker(engine, settings), LocalStorage(root / 'data'), registry={'fake': FakeAdapter()},
                   gpu_probe=snapshot, worker_id='signal-worker').run()
    finally:
        engine.dispose()
""")
    # Use a real clock in this test: SIGTERM is sent only once the real child is running.
    process = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 10
        child_pid = None
        while time.monotonic() < deadline:
            with Session(queue.engine) as session:
                worker = session.get(Worker, "signal-worker")
                if worker and worker.state == "BUSY" and worker.inference_pid:
                    child_pid = worker.inference_pid
                    break
            assert process.poll() is None
            time.sleep(0.02)
        assert child_pid is not None
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout.decode() + stderr.decode()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        assert get(queue, identifier).status == "QUEUED"
        assert get(queue, identifier).attempt_count == 0
        assert attempts(queue)[0].outcome == "INTERRUPTED"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
