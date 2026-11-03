"""Cycle de vie d'une génération (P2.3, data-model.md §2-§3) : machine à états et politique de relance."""

from datetime import timedelta

import pytest

from hephaistos.domain.errors import ErrorCode
from hephaistos.domain.lifecycle import (
    TERMINAL,
    Event,
    Fail,
    InvalidTransition,
    Requeue,
    Status,
    after_failure,
    after_interruption,
    next_status,
)

S, E = Status, Event

# data-model.md §3, ligne à ligne
ALLOWED = {
    (S.PENDING, E.INGESTED): S.QUEUED,
    (S.PENDING, E.INGEST_FAILED): S.FAILED,
    (S.PENDING, E.CANCEL): S.CANCELLED,
    (S.QUEUED, E.CLAIM): S.RUNNING,
    (S.QUEUED, E.CANCEL): S.CANCELLED,
    (S.QUEUED, E.GPU_WAIT_EXPIRED): S.FAILED,
    (S.RUNNING, E.SUCCEED): S.COMPLETED,
    (S.RUNNING, E.FAIL): S.FAILED,
    (S.RUNNING, E.REQUEUE): S.QUEUED,
    (S.RUNNING, E.CANCEL): S.CANCELLED,
    (S.FAILED, E.MANUAL_RETRY): S.QUEUED,
    (S.CANCELLED, E.MANUAL_RETRY): S.QUEUED,
}


@pytest.mark.parametrize("status", list(Status))
@pytest.mark.parametrize("event", list(Event))
def test_transition_table_is_exhaustive(status, event):
    # Toute paire (statut, événement) est soit dans la table, soit refusée : aucune autre issue.
    if (status, event) in ALLOWED:
        assert next_status(status, event) is ALLOWED[(status, event)]
    else:
        with pytest.raises(InvalidTransition):
            next_status(status, event)


def test_completed_is_final():
    assert TERMINAL == {S.COMPLETED, S.FAILED, S.CANCELLED}
    for event in Event:
        with pytest.raises(InvalidTransition):
            next_status(S.COMPLETED, event)


def test_invalid_transition_names_status_and_event():
    with pytest.raises(InvalidTransition, match=r"COMPLETED.*CANCEL"):
        next_status(S.COMPLETED, E.CANCEL)


# ------------------------------------------------------------------------- relances (D8, A2.4, A2.5, A2.13)

POLICY = {"max_retries": 3, "backoff_s": 30, "backoff_max_s": 900}


def test_transient_errors_are_retried_with_doubling_backoff_then_fail():
    # A2.4 : 3 relances espacées de 30, 60, 120 s, puis FAILED.
    decisions = [after_failure(ErrorCode.GPU_OOM, attempt_count=n, **POLICY) for n in (1, 2, 3, 4)]
    assert decisions[:3] == [Requeue(timedelta(seconds=s)) for s in (30, 60, 120)]
    assert decisions[3] == Fail(needs_diagnosis=False)


def test_backoff_is_capped():
    policy = {**POLICY, "max_retries": 10, "backoff_max_s": 100}
    assert after_failure(ErrorCode.TIMEOUT, attempt_count=4, **policy) == Requeue(timedelta(seconds=100))


@pytest.mark.parametrize("code", [c for c in ErrorCode if not c.retryable and c is not ErrorCode.INTERNAL])
def test_deterministic_errors_fail_at_once(code):
    # A2.5 : une seule tentative.
    assert after_failure(code, attempt_count=1, **POLICY) == Fail(needs_diagnosis=False)


def test_unclassified_error_fails_and_needs_diagnosis_never_retried():
    # A2.13, D8 : même avec tout le budget restant.
    assert after_failure(ErrorCode.INTERNAL, attempt_count=1, **POLICY) == Fail(needs_diagnosis=True)


def test_zero_retries_fails_the_first_transient_error():
    assert after_failure(ErrorCode.CUDA_ERROR, attempt_count=1, **{**POLICY, "max_retries": 0}) == Fail(
        needs_diagnosis=False
    )


def test_abandoned_attempt_consumes_the_budget():
    # Bail expiré : WORKER_LOST, compté comme une tentative.
    assert after_failure(ErrorCode.WORKER_LOST, attempt_count=4, **POLICY) == Fail(needs_diagnosis=False)


def test_interruption_does_not_consume_the_budget():
    # Arrêt propre du worker : la tentative ne compte pas, la génération repart sans délai.
    assert after_interruption(attempt_count=3) == 2
    assert after_interruption(attempt_count=0) == 0
