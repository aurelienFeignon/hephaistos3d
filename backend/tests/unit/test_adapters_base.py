import subprocess
import sys

import pytest

from hephaistos.adapters.base import AdapterError, Cancelled, Stage
from hephaistos.domain.errors import ErrorCode

# Tableau de docs/data-model.md §4 : un code relançable à tort ferait boucler un job (D8).
RETRYABLE = {"GPU_OOM", "CUDA_ERROR", "WORKER_LOST", "TIMEOUT", "WORKER_CRASH"}
NOT_RETRYABLE = {
    "INPUT_INVALID",
    "NO_OBJECT_DETECTED",
    "PARAMS_INVALID",
    "MODEL_UNAVAILABLE",
    "INGEST_FAILED",
    "GPU_UNAVAILABLE",
    "COMPILE_ERROR",
    "INTERNAL",
}


def test_error_codes_are_exactly_those_of_the_data_model():
    assert {code.value for code in ErrorCode} == RETRYABLE | NOT_RETRYABLE


@pytest.mark.parametrize("name", sorted(RETRYABLE))
def test_retryable_codes(name):
    assert ErrorCode(name).retryable


@pytest.mark.parametrize("name", sorted(NOT_RETRYABLE))
def test_non_retryable_codes(name):
    assert not ErrorCode(name).retryable


def test_adapter_error_takes_retryable_from_its_code():
    error = AdapterError(ErrorCode.GPU_OOM, "CUDA out of memory")
    assert error.code is ErrorCode.GPU_OOM
    assert error.message == "CUDA out of memory"
    assert error.retryable
    assert not AdapterError(ErrorCode.NO_OBJECT_DETECTED, "no object found").retryable


def test_adapter_error_is_an_exception_with_readable_text():
    error = AdapterError(ErrorCode.PARAMS_INVALID, "steps hors bornes")
    assert isinstance(error, Exception)
    assert str(error) == "PARAMS_INVALID: steps hors bornes"


def test_cancelled_is_not_an_adapter_error():
    # Une annulation est une issue voulue, jamais une erreur classée ni relancée.
    assert not issubclass(Cancelled, AdapterError)


def test_stages_in_execution_order():
    assert [stage.value for stage in Stage] == ["preprocess", "encode", "sampling", "decode", "export"]


# Processus neuf : un torch déjà importé par un autre test masquerait le défaut. L'intercepteur
# note toute TENTATIVE d'import, car torch est absent de l'environnement de développement : sans
# lui, un `import torch` sous `try/except ImportError` passerait inaperçu.
TORCH_GUARD = """
import importlib.abc, sys
attempts = []
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "torch" or name.startswith("torch."):
            attempts.append(name)
        return None
sys.meta_path.insert(0, Guard())
import {module}
sys.exit(1 if attempts else 0)
"""


def assert_does_not_import_torch(module: str) -> None:
    result = subprocess.run([sys.executable, "-c", TORCH_GUARD.format(module=module)], check=False)
    assert result.returncode == 0, f"{module} tente d'importer torch"


@pytest.mark.parametrize("module", ["hephaistos.adapters.base", "hephaistos.domain.errors"])
def test_import_does_not_load_torch(module):
    # A1.1 : l'API importe ces modules et n'embarque pas torch.
    assert_does_not_import_torch(module)
