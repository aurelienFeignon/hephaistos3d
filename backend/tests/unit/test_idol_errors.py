"""Classement des erreurs d'Idol (P1.8, data-model.md §4), sans torch.

Le classement lit les classes de torch dans `sys.modules` (jamais d'import) : on y installe des
modules factices aux mêmes chemins que torch 2.14 (vérifiés dans l'image worker le 2026-11-03).
"""

import sys
import types

import pytest
from pydantic import ValidationError

from hephaistos.adapters.base import AdapterError
from hephaistos.adapters.idol import IdolAdapter, IdolParams
from hephaistos.domain.errors import ErrorCode


class OutOfMemoryError(RuntimeError): ...


class AcceleratorError(RuntimeError): ...


class TorchDynamoException(RuntimeError): ...


class BackendCompilerFailed(TorchDynamoException): ...


class InductorError(RuntimeError): ...


@pytest.fixture
def fake_torch(monkeypatch):
    torch = types.ModuleType("torch")
    torch.OutOfMemoryError = OutOfMemoryError
    torch.AcceleratorError = AcceleratorError
    dynamo_exc = types.ModuleType("torch._dynamo.exc")
    dynamo_exc.TorchDynamoException = TorchDynamoException
    inductor_exc = types.ModuleType("torch._inductor.exc")
    inductor_exc.InductorError = InductorError
    for name, module in (("torch", torch), ("torch._dynamo.exc", dynamo_exc), ("torch._inductor.exc", inductor_exc)):
        monkeypatch.setitem(sys.modules, name, module)


def classify(exc: BaseException) -> ErrorCode:
    return IdolAdapter().classify_error(exc).code


@pytest.mark.usefixtures("fake_torch")
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (OutOfMemoryError("CUDA out of memory. Tried to allocate 480.00 MiB"), ErrorCode.GPU_OOM),
        (AcceleratorError("CUDA error: an illegal memory access was encountered"), ErrorCode.CUDA_ERROR),
        (RuntimeError("CUDA error: unspecified launch failure"), ErrorCode.CUDA_ERROR),
        (BackendCompilerFailed("backend='inductor' raised CUDA error"), ErrorCode.COMPILE_ERROR),
        (InductorError("CppCompileError: C++ compile error"), ErrorCode.COMPILE_ERROR),
        (ValueError("no object found in the image"), ErrorCode.NO_OBJECT_DETECTED),
        (RuntimeError("shape mismatch"), ErrorCode.INTERNAL),
    ],
)
def test_classification(exc, code):
    assert classify(exc) is code


def test_without_torch_loaded_a_cuda_message_is_still_a_cuda_error(monkeypatch):
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert classify(RuntimeError("CUDA error: device-side assert triggered")) is ErrorCode.CUDA_ERROR
    assert classify(RuntimeError("autre")) is ErrorCode.INTERNAL


def test_classification_never_imports_torch():
    from tests.unit.test_adapters_base import assert_does_not_import_torch

    assert_does_not_import_torch("hephaistos.adapters.idol")


def test_validation_error_is_params_invalid():
    with pytest.raises(ValidationError) as caught:
        IdolParams.model_validate({"steps": 0})
    assert classify(caught.value) is ErrorCode.PARAMS_INVALID


def test_classified_error_passes_through():
    error = AdapterError(ErrorCode.MODEL_UNAVAILABLE, "poids absents")
    assert IdolAdapter().classify_error(error) is error


@pytest.mark.usefixtures("fake_torch")
def test_classification_keeps_the_original_message():
    error = IdolAdapter().classify_error(OutOfMemoryError("Tried to allocate 480.00 MiB"))
    assert "480.00 MiB" in error.message
