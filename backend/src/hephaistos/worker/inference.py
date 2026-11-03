"""Processus d'inférence et son pilotage par le superviseur (P1.6, architecture.md §5.2 et §5.6).

Le superviseur n'importe jamais torch : tout ce qui touche CUDA vit dans l'enfant, créé en `spawn`.
Rendre la VRAM, c'est terminer l'enfant ; le tuer (SIGKILL) reste toujours possible, c'est la
garantie de libération en temps borné.

Issue d'un job, côté superviseur : `Succeeded`, `Failed` (code classé, relançable ou non) ou
`Cancelled`. La mort de l'enfant devient WORKER_CRASH, un délai dépassé TIMEOUT.
"""

from __future__ import annotations

import multiprocessing as mp
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Any

from hephaistos.adapters.base import Cancelled as CancelRequested
from hephaistos.adapters.base import GenerationInput, GenerationOutput, LoadContext, Stage
from hephaistos.domain.errors import ErrorCode

EXIT_CUDA_COMPROMISED = 75  # EX_TEMPFAIL : contexte CUDA présumé compromis, un enfant neuf le remplacera
MAX_CONSECUTIVE_OOM = 3
_CUDA_EXIT_GRACE_S = 5.0


# --------------------------------------------------------------------------------------- messages


@dataclass(frozen=True)
class StageEvent:
    stage: Stage


@dataclass(frozen=True)
class StepEvent:
    index: int
    total: int


@dataclass(frozen=True)
class LogEvent:
    event: str
    fields: Mapping[str, Any] = field(default_factory=dict)


ProgressEvent = StageEvent | StepEvent | LogEvent


@dataclass(frozen=True)
class Succeeded:
    output: GenerationOutput


@dataclass(frozen=True)
class Failed:
    code: ErrorCode
    message: str

    @property
    def retryable(self) -> bool:
        return self.code.retryable


@dataclass(frozen=True)
class Cancelled:
    pass


Outcome = Succeeded | Failed | Cancelled


@dataclass(frozen=True)
class _Loaded:
    seconds: float


@dataclass(frozen=True)
class _LoadFailed:
    message: str


@dataclass(frozen=True)
class _Generate:
    request: GenerationInput


@dataclass(frozen=True)
class _Shutdown:
    pass


# --------------------------------------------------------------------------------------- superviseur


class InferenceProcess:
    """Un enfant d'inférence : chargé une fois, conservé entre les jobs, terminé à la demande."""

    def __init__(self, adapter_id: str, *, models_root: Path, options: Mapping[str, Any] | None = None) -> None:
        self.adapter_id = adapter_id
        self.models_root = models_root
        self.options = dict(options or {})
        self._ctx = mp.get_context("spawn")
        self._process: Any = None
        self._conn: Connection | None = None
        self._cancel: Event | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    def is_alive(self) -> bool:
        return bool(self._process and self._process.is_alive())

    def start(self, timeout_s: float = 900.0) -> float:
        """Crée l'enfant et attend le chargement du modèle ; rend sa durée. Lève RuntimeError sinon."""
        parent, child = self._ctx.Pipe(duplex=True)
        self._cancel = self._ctx.Event()
        args = (self.adapter_id, self.models_root, self.options, child, self._cancel)
        self._process = self._ctx.Process(target=_child_main, args=args, name=f"inference-{self.adapter_id}")
        self._process.start()
        child.close()
        self._conn = parent
        message = self._receive(time.monotonic() + timeout_s)
        if isinstance(message, _Loaded):
            return message.seconds
        self.kill()
        reason = message.message if isinstance(message, _LoadFailed) else "aucune réponse au chargement"
        raise RuntimeError(f"chargement de {self.adapter_id!r} impossible : {reason}")

    def generate(
        self, request: GenerationInput, *, on_event: Callable[[ProgressEvent], None] | None = None, timeout_s: float
    ) -> Outcome:
        if self._conn is None or self._cancel is None:
            raise RuntimeError("processus d'inférence non démarré : start() d'abord")
        self._cancel.clear()
        self._conn.send(_Generate(request))
        deadline = time.monotonic() + timeout_s
        while True:
            message = self._receive(deadline)
            if isinstance(message, StageEvent | StepEvent | LogEvent):
                if on_event:
                    on_event(message)
                continue
            if isinstance(message, Succeeded | Cancelled):
                return message
            if isinstance(message, Failed):
                if message.code is ErrorCode.CUDA_ERROR and self.wait_exit(_CUDA_EXIT_GRACE_S) is None:
                    self.kill()  # l'enfant devait sortir de lui-même : on ne lui laisse pas le GPU
                return message
            if message is _TIMED_OUT:
                self.kill()
                return Failed(ErrorCode.TIMEOUT, f"aucune issue en {timeout_s:.0f} s : processus d'inférence tué")
            return Failed(ErrorCode.WORKER_CRASH, f"processus d'inférence mort (code {self.wait_exit(5)})")

    def cancel(self) -> None:
        """Demande coopérative, vue au prochain hook ; `kill()` reste le recours."""
        if self._cancel is not None:
            self._cancel.set()

    def wait_exit(self, timeout_s: float) -> int | None:
        if self._process is None:
            return None
        self._process.join(timeout_s)
        return None if self._process.is_alive() else self._process.exitcode

    def shutdown(self, grace_s: float = 30.0) -> int | None:
        if self.is_alive() and self._conn is not None:
            try:
                self._conn.send(_Shutdown())
            except (BrokenPipeError, OSError):
                pass
        code = self.wait_exit(grace_s)
        if code is None:
            self.kill()
            code = self.wait_exit(5)
        return code

    def kill(self) -> None:
        if self.is_alive():
            self._process.kill()
            self._process.join(10)

    def _receive(self, deadline: float) -> object:
        """Message suivant, `_TIMED_OUT` à l'échéance, `_DEAD` si l'enfant meurt sans rien dire."""
        if self._conn is None:
            return _DEAD
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _TIMED_OUT
            try:
                if self._conn.poll(min(remaining, 0.2)):
                    return self._conn.recv()
            except (EOFError, OSError):
                return _DEAD
            if not self.is_alive() and not self._conn.poll(0):
                return _DEAD


_TIMED_OUT = object()
_DEAD = object()


# --------------------------------------------------------------------------------------- enfant


class _PipeHooks:
    def __init__(self, conn: Connection, cancel: Event) -> None:
        self._conn = conn
        self._cancel = cancel

    def stage(self, stage: Stage) -> None:
        self._conn.send(StageEvent(stage))

    def step(self, index: int, total: int) -> None:
        self._conn.send(StepEvent(index, total))

    def log(self, event: str, **fields: Any) -> None:
        self._conn.send(LogEvent(event, fields))

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise CancelRequested()


def _child_main(adapter_id: str, models_root: Path, options: dict[str, Any], conn: Connection, cancel: Event) -> None:
    import sys

    from hephaistos.adapters.registry import build_registry

    adapter = build_registry(enable_fake=True).get(adapter_id)
    if adapter is None:
        conn.send(_LoadFailed(f"adaptateur {adapter_id!r} inconnu"))
        sys.exit(1)
    started = time.perf_counter()
    try:
        adapter.load(LoadContext(models_root=models_root, options=options))
    except Exception as exc:  # noqa: BLE001 — rapporté au superviseur, qui décide
        conn.send(_LoadFailed(adapter.classify_error(exc).message))
        sys.exit(1)
    conn.send(_Loaded(time.perf_counter() - started))

    hooks = _PipeHooks(conn, cancel)
    consecutive_oom = 0
    while True:
        message = conn.recv()
        if isinstance(message, _Shutdown):
            adapter.unload()
            sys.exit(0)
        outcome: Outcome
        try:
            outcome = Succeeded(adapter.generate(message.request, hooks))
        except CancelRequested:
            outcome = Cancelled()
        except Exception as exc:  # noqa: BLE001 — toute erreur est classée ; l'inconnue devient INTERNAL
            error = adapter.classify_error(exc)
            outcome = Failed(error.code, error.message)
        consecutive_oom = (
            consecutive_oom + 1 if isinstance(outcome, Failed) and outcome.code is ErrorCode.GPU_OOM else 0
        )
        conn.send(outcome)

        if isinstance(outcome, Failed) and outcome.code is ErrorCode.CUDA_ERROR:
            sys.exit(EXIT_CUDA_COMPROMISED)
        if consecutive_oom >= MAX_CONSECUTIVE_OOM:
            sys.exit(EXIT_CUDA_COMPROMISED)
        try:
            adapter.health_check()
        except Exception:  # noqa: BLE001 — contexte présumé compromis, quel que soit le motif
            sys.exit(EXIT_CUDA_COMPROMISED)
