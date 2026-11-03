"""Interface d'un adaptateur de modèle (ADR-0004, architecture.md §6).

Ce module est importé par l'API, qui n'a pas torch : aucun adaptateur n'importe torch au niveau
module, seulement dans `load` et ce qui s'exécute dans le processus d'inférence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from hephaistos.domain.errors import ErrorCode


class Stage(StrEnum):
    """Étapes d'une génération, dans l'ordre d'exécution."""

    PREPROCESS = "preprocess"
    ENCODE = "encode"
    SAMPLING = "sampling"
    DECODE = "decode"
    EXPORT = "export"


class Cancelled(Exception):
    """Levée par `RunHooks.check_cancel()` : la génération s'arrête sans rien promouvoir."""


class AdapterError(Exception):
    """Erreur classée : son code décide seul si elle est relançable."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.code.retryable


@dataclass(frozen=True)
class GpuRequirements:
    min_compute_capability: tuple[int, int]


@dataclass(frozen=True)
class ModelDescriptor:
    id: str
    name: str
    version: str  # adaptateur + commit du SDK + révision des poids
    capabilities: frozenset[str]
    input_formats: frozenset[str]
    output_kinds: frozenset[str]
    gpu: GpuRequirements | None  # None : l'adaptateur n'utilise pas le GPU


@dataclass(frozen=True)
class Preset:
    label: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str | None = None  # pourquoi l'adaptateur est indisponible, lisible par un humain

    @classmethod
    def ok(cls) -> Availability:
        return cls(True)

    @classmethod
    def missing(cls, reason: str) -> Availability:
        return cls(False, reason)


@dataclass(frozen=True)
class LoadContext:
    models_root: Path
    options: Mapping[str, Any] = field(default_factory=dict)  # réglages de chargement (ex. mode d'exécution)


@dataclass(frozen=True)
class GenerationInput:
    image: Path  # image normalisée (PNG), jamais le fichier téléversé
    params: BaseModel  # paramètres effectifs, déjà validés par `params_model`
    seed: int
    output_dir: Path


@dataclass(frozen=True)
class GenerationOutput:
    artifacts: Mapping[str, Path]  # type d'artefact (ex. "splat", "preview") → fichier dans output_dir
    metrics: Mapping[str, float | int] = field(default_factory=dict)


class RunHooks(Protocol):
    def stage(self, stage: Stage) -> None: ...
    def step(self, index: int, total: int) -> None: ...
    def log(self, event: str, **fields: Any) -> None: ...
    def check_cancel(self) -> None:
        """Lève `Cancelled` si l'annulation a été demandée."""
        ...


class ModelAdapter(Protocol):
    descriptor: ModelDescriptor
    params_model: type[BaseModel]
    presets: Mapping[str, Preset]

    def check_installation(self, models_root: Path) -> Availability: ...
    def vram_required_bytes(self, params: BaseModel) -> int: ...
    def load(self, ctx: LoadContext) -> None: ...
    def generate(self, request: GenerationInput, hooks: RunHooks) -> GenerationOutput: ...
    def unload(self) -> None: ...
    def classify_error(self, exc: BaseException) -> AdapterError: ...
