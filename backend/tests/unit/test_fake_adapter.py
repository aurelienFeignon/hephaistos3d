import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from hephaistos.adapters.base import AdapterError, GenerationInput, LoadContext
from hephaistos.adapters.fake import FakeAdapter, FakeCudaError, FakeOutOfMemoryError
from hephaistos.domain.errors import ErrorCode
from tests.adapter_conformance import AdapterConformance, ConformanceCase, RecordingHooks, reference_image
from tests.unit.test_adapters_base import assert_does_not_import_torch

BACKEND_ROOT = Path(__file__).resolve().parents[2]  # les sous-processus importent `tests.*` depuis là
FAST = {"steps": 14, "num_gaussians": 4096, "step_delay_s": 0.0}


@pytest.fixture
def case(tmp_path):
    return ConformanceCase(make_adapter=FakeAdapter, params=FAST, models_root=tmp_path, expected_gaussians=4096)


class TestFakeConformance(AdapterConformance):
    pass


def test_fake_module_does_not_import_torch():
    assert_does_not_import_torch("hephaistos.adapters.fake")


def test_descriptor_declares_no_gpu():
    descriptor = FakeAdapter().descriptor
    assert descriptor.id == "fake"
    assert descriptor.gpu is None
    assert FakeAdapter().vram_required_bytes(FakeAdapter.params_model()) == 0


def test_params_reject_unknown_and_out_of_range_fields():
    with pytest.raises(ValidationError):
        FakeAdapter.params_model.model_validate({"steps": 0})
    with pytest.raises(ValidationError):
        FakeAdapter.params_model.model_validate({"inconnu": 1})


def _generate(tmp_path, **params):
    adapter = FakeAdapter()
    adapter.load(LoadContext(models_root=tmp_path))
    out = tmp_path / "out"
    out.mkdir()
    request = GenerationInput(
        reference_image(tmp_path / "in.png"),
        adapter.params_model.model_validate({**FAST, **params}),
        seed=1,
        output_dir=out,
    )
    return adapter, adapter.generate(request, RecordingHooks()), out


@pytest.mark.parametrize(
    ("failure", "raised", "code"),
    [
        ("GPU_OOM", FakeOutOfMemoryError, ErrorCode.GPU_OOM),
        ("NO_OBJECT", ValueError, ErrorCode.NO_OBJECT_DETECTED),
        ("CUDA_ERROR", FakeCudaError, ErrorCode.CUDA_ERROR),
    ],
)
def test_injected_failure_is_classified(tmp_path, failure, raised, code):
    adapter = FakeAdapter()
    with pytest.raises(raised) as caught:
        _generate(tmp_path, inject_failure=failure, fail_at_step=3)
    classified = adapter.classify_error(caught.value)
    assert classified.code is code
    assert classified.retryable is code.retryable


def test_injected_failure_leaves_no_artifact(tmp_path):
    with pytest.raises(FakeOutOfMemoryError):
        _generate(tmp_path, inject_failure="GPU_OOM", fail_at_step=3)
    assert not any((tmp_path / "out").iterdir())


def test_unknown_exception_is_internal_and_not_retryable():
    error = FakeAdapter().classify_error(RuntimeError("inattendu"))
    assert error.code is ErrorCode.INTERNAL
    assert not error.retryable


def test_classified_error_passes_through_unchanged():
    original = AdapterError(ErrorCode.PARAMS_INVALID, "x")
    assert FakeAdapter().classify_error(original) is original


def test_validation_error_is_params_invalid():
    with pytest.raises(ValidationError) as caught:
        FakeAdapter.params_model.model_validate({"steps": 0})
    assert FakeAdapter().classify_error(caught.value).code is ErrorCode.PARAMS_INVALID


def test_injected_crash_kills_the_process(tmp_path):
    # CRASH simule la mort du processus d'inférence (WORKER_CRASH côté superviseur).
    script = textwrap.dedent(f"""
        from pathlib import Path
        from tests.unit.test_fake_adapter import _generate
        _generate(Path({str(tmp_path)!r}), inject_failure="CRASH", fail_at_step=2)
    """)
    result = subprocess.run([sys.executable, "-c", script], check=False, timeout=60, cwd=BACKEND_ROOT)
    assert result.returncode not in (0, 1)


def test_injected_hang_ignores_cancellation(tmp_path):
    # HANG simule un processus d'inférence bloqué : seul un SIGKILL du superviseur l'arrête.
    script = textwrap.dedent(f"""
        from pathlib import Path
        from tests.unit.test_fake_adapter import _generate
        _generate(Path({str(tmp_path)!r}), inject_failure="HANG", fail_at_step=2)
    """)
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([sys.executable, "-c", script], check=False, timeout=3, cwd=BACKEND_ROOT)
