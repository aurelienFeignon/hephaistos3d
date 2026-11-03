"""Processus d'inférence (P1.6) joué de bout en bout avec le FakeAdapter, dans un vrai enfant `spawn`."""

from pathlib import Path

import pytest

from hephaistos.adapters.base import GenerationInput, Stage
from hephaistos.adapters.fake import FakeParams
from hephaistos.domain.errors import ErrorCode
from hephaistos.worker.inference import (
    EXIT_CUDA_COMPROMISED,
    Cancelled,
    Failed,
    InferenceProcess,
    StageEvent,
    StepEvent,
    Succeeded,
)
from tests.adapter_conformance import reference_image
from tests.unit.test_adapters_base import assert_does_not_import_torch


@pytest.fixture
def process(tmp_path):
    proc = InferenceProcess("fake", models_root=tmp_path)
    proc.start()
    yield proc
    proc.kill()


def request(tmp_path: Path, name: str = "job", **params) -> GenerationInput:
    base = tmp_path / name
    (base / "out").mkdir(parents=True)
    values = {"steps": 12, "num_gaussians": 1024, "step_delay_s": 0.0, **params}
    return GenerationInput(reference_image(base / "in.png"), FakeParams(**values), seed=7, output_dir=base / "out")


def test_supervisor_module_does_not_import_torch():
    assert_does_not_import_torch("hephaistos.worker.inference")


def test_generation_relays_progress_and_returns_artifacts(process, tmp_path):
    events = []
    outcome = process.generate(request(tmp_path), on_event=events.append, timeout_s=60)
    assert isinstance(outcome, Succeeded)
    assert outcome.output.artifacts["splat"].is_file()
    assert [e.stage for e in events if isinstance(e, StageEvent)] == list(Stage)
    assert [(e.index, e.total) for e in events if isinstance(e, StepEvent)] == [(i, 12) for i in range(1, 13)]


def test_model_stays_loaded_between_jobs(process, tmp_path):
    pid = process.pid
    for name in ("a", "b"):
        assert isinstance(process.generate(request(tmp_path, name), timeout_s=60), Succeeded)
    assert process.pid == pid and process.is_alive()


def test_cancel_stops_the_job_and_keeps_the_process(process, tmp_path):
    def cancel_at_step_3(event):
        if isinstance(event, StepEvent) and event.index == 3:
            process.cancel()

    job = request(tmp_path, "a", step_delay_s=0.05)
    outcome = process.generate(job, on_event=cancel_at_step_3, timeout_s=60)
    assert isinstance(outcome, Cancelled)
    assert not any(job.output_dir.iterdir())
    # l'annulation ne déborde pas sur le job suivant
    assert isinstance(process.generate(request(tmp_path, "b"), timeout_s=60), Succeeded)


def test_oom_is_a_retryable_failure_and_the_process_survives(process, tmp_path):
    outcome = process.generate(request(tmp_path, inject_failure="GPU_OOM", fail_at_step=2), timeout_s=60)
    assert isinstance(outcome, Failed)
    assert outcome.code is ErrorCode.GPU_OOM and outcome.retryable
    assert process.is_alive()


def test_three_consecutive_ooms_end_the_process(process, tmp_path):
    for name in ("a", "b", "c"):
        process.generate(request(tmp_path, name, inject_failure="GPU_OOM", fail_at_step=2), timeout_s=60)
    assert process.wait_exit(timeout_s=30) == EXIT_CUDA_COMPROMISED


def test_a_success_resets_the_oom_count(process, tmp_path):
    for name, failure in (("a", "GPU_OOM"), ("b", "GPU_OOM"), ("c", None), ("d", "GPU_OOM"), ("e", "GPU_OOM")):
        process.generate(request(tmp_path, name, inject_failure=failure, fail_at_step=2), timeout_s=60)
    assert process.is_alive()


def test_cuda_error_reports_then_exits_75(process, tmp_path):
    outcome = process.generate(request(tmp_path, inject_failure="CUDA_ERROR", fail_at_step=2), timeout_s=60)
    assert isinstance(outcome, Failed)
    assert outcome.code is ErrorCode.CUDA_ERROR and outcome.retryable
    assert process.wait_exit(timeout_s=30) == EXIT_CUDA_COMPROMISED


def test_non_retryable_failure_keeps_the_process(process, tmp_path):
    outcome = process.generate(request(tmp_path, inject_failure="NO_OBJECT", fail_at_step=2), timeout_s=60)
    assert isinstance(outcome, Failed)
    assert outcome.code is ErrorCode.NO_OBJECT_DETECTED and not outcome.retryable
    assert process.is_alive()


def test_child_death_is_a_worker_crash(process, tmp_path):
    outcome = process.generate(request(tmp_path, inject_failure="CRASH", fail_at_step=2), timeout_s=60)
    assert isinstance(outcome, Failed)
    assert outcome.code is ErrorCode.WORKER_CRASH and outcome.retryable
    assert not process.is_alive()


def test_a_hung_child_times_out_and_is_killed(process, tmp_path):
    outcome = process.generate(request(tmp_path, inject_failure="HANG", fail_at_step=2), timeout_s=3)
    assert isinstance(outcome, Failed)
    assert outcome.code is ErrorCode.TIMEOUT and outcome.retryable
    assert not process.is_alive()


def test_shutdown_ends_the_process_cleanly(process):
    assert process.shutdown(grace_s=30) == 0
    assert not process.is_alive()


def test_load_failure_is_reported_at_start(tmp_path):
    proc = InferenceProcess("inconnu", models_root=tmp_path)
    with pytest.raises(RuntimeError, match="inconnu"):
        proc.start()
    assert not proc.is_alive()


def test_failed_health_check_after_a_job_exits_75(process, tmp_path):
    # §5.6 : le job a abouti, mais le contexte n'est plus sain ; on n'y fait pas tourner le suivant.
    outcome = process.generate(request(tmp_path, inject_failure="UNHEALTHY"), timeout_s=60)
    assert isinstance(outcome, Succeeded)
    assert process.wait_exit(timeout_s=30) == EXIT_CUDA_COMPROMISED
