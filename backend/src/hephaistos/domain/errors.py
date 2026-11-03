"""Codes d'erreur d'une génération (docs/data-model.md §4).

Un code dit s'il est relançable ; aucun autre endroit ne le décide. Une erreur non classée devient
INTERNAL, jamais relancée automatiquement (D8) : elle se diagnostique d'abord.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INPUT_INVALID = "INPUT_INVALID"
    NO_OBJECT_DETECTED = "NO_OBJECT_DETECTED"
    PARAMS_INVALID = "PARAMS_INVALID"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    INGEST_FAILED = "INGEST_FAILED"
    GPU_OOM = "GPU_OOM"
    CUDA_ERROR = "CUDA_ERROR"
    WORKER_LOST = "WORKER_LOST"
    TIMEOUT = "TIMEOUT"
    WORKER_CRASH = "WORKER_CRASH"
    GPU_UNAVAILABLE = "GPU_UNAVAILABLE"
    COMPILE_ERROR = "COMPILE_ERROR"
    INTERNAL = "INTERNAL"

    @property
    def retryable(self) -> bool:
        return self in _RETRYABLE


_RETRYABLE = frozenset(
    {ErrorCode.GPU_OOM, ErrorCode.CUDA_ERROR, ErrorCode.WORKER_LOST, ErrorCode.TIMEOUT, ErrorCode.WORKER_CRASH}
)
