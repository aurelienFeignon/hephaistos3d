"""Cycle de vie d'une génération (data-model.md §2-§3) — pur, sans base.

`next_status` dit où mène un événement ; toute autre combinaison lève `InvalidTransition` (HTTP 409).
La persistance applique chaque transition en mise à jour conditionnelle (`WHERE status = ?`).
`after_failure` décide seule d'une relance : le code d'erreur et le budget, rien d'autre (D8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from hephaistos.domain.errors import ErrorCode


class Status(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset({Status.COMPLETED, Status.FAILED, Status.CANCELLED})


class Event(StrEnum):
    INGESTED = "INGESTED"  # fichiers promus dans sources/
    INGEST_FAILED = "INGEST_FAILED"
    CLAIM = "CLAIM"  # prise atomique par un worker
    SUCCEED = "SUCCEED"  # artefacts promus
    FAIL = "FAIL"  # erreur déterministe ou budget épuisé (voir after_failure)
    REQUEUE = "REQUEUE"  # relance automatique, bail expiré ou arrêt propre du worker
    GPU_WAIT_EXPIRED = "GPU_WAIT_EXPIRED"  # attente GPU au-delà de GPU_WAIT_TIMEOUT_SECONDS
    CANCEL = "CANCEL"  # immédiat hors RUNNING ; coopératif (constaté par le worker) en RUNNING
    MANUAL_RETRY = "MANUAL_RETRY"  # budget remis à zéro


_TRANSITIONS: dict[tuple[Status, Event], Status] = {
    (Status.PENDING, Event.INGESTED): Status.QUEUED,
    (Status.PENDING, Event.INGEST_FAILED): Status.FAILED,
    (Status.PENDING, Event.CANCEL): Status.CANCELLED,
    (Status.QUEUED, Event.CLAIM): Status.RUNNING,
    (Status.QUEUED, Event.CANCEL): Status.CANCELLED,
    (Status.QUEUED, Event.GPU_WAIT_EXPIRED): Status.FAILED,
    (Status.RUNNING, Event.SUCCEED): Status.COMPLETED,
    (Status.RUNNING, Event.FAIL): Status.FAILED,
    (Status.RUNNING, Event.REQUEUE): Status.QUEUED,
    (Status.RUNNING, Event.CANCEL): Status.CANCELLED,
    (Status.FAILED, Event.MANUAL_RETRY): Status.QUEUED,
    (Status.CANCELLED, Event.MANUAL_RETRY): Status.QUEUED,
}


class InvalidTransition(Exception):
    def __init__(self, status: Status, event: Event) -> None:
        super().__init__(f"transition refusée : {status} ne connaît pas l'événement {event}")
        self.status = status
        self.event = event


def next_status(status: Status, event: Event) -> Status:
    try:
        return _TRANSITIONS[(status, event)]
    except KeyError:
        raise InvalidTransition(status, event) from None


@dataclass(frozen=True)
class Requeue:
    delay: timedelta  # not_before = maintenant + delay


@dataclass(frozen=True)
class Fail:
    needs_diagnosis: bool  # erreur non classée : trace conservée, relance manuelle seulement (D8)


def after_failure(
    code: ErrorCode, *, attempt_count: int, max_retries: int, backoff_s: int, backoff_max_s: int
) -> Requeue | Fail:
    """Après l'échec de la tentative n° `attempt_count` : relance si le code est transitoire et que le
    budget le permet (`attempt_count ≤ max_retries`), avec un délai qui double à chaque tentative."""
    if code is ErrorCode.INTERNAL:
        return Fail(needs_diagnosis=True)
    if not code.retryable or attempt_count > max_retries:
        return Fail(needs_diagnosis=False)
    delay = min(backoff_s * 2 ** (attempt_count - 1), backoff_max_s)
    return Requeue(timedelta(seconds=delay))


def after_interruption(attempt_count: int) -> int:
    """Arrêt propre du worker (tentative INTERRUPTED) : elle ne compte pas dans le budget."""
    return max(0, attempt_count - 1)
