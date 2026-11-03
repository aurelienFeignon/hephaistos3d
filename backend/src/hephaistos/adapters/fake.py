"""Adaptateur factice : démonstration et tests sans GPU (ADR-0004, architecture.md §6.1).

Mêmes étapes et mêmes messages qu'Idol, pas temporisés, PLY synthétique valide. Les échecs
injectables reproduisent ce que le worker doit savoir traiter : OOM, absence d'objet, erreur CUDA,
mort du processus (CRASH) et blocage qui ignore l'annulation (HANG).
"""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO, Literal

import numpy as np
from PIL import Image
from plyfile import PlyData, PlyElement
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hephaistos.adapters.base import (
    AdapterError,
    Availability,
    GenerationInput,
    GenerationOutput,
    LoadContext,
    ModelDescriptor,
    Preset,
    RunHooks,
    Stage,
)
from hephaistos.domain.errors import ErrorCode

PLY_PROPERTIES = (
    "x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
    "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
)  # fmt: skip


class FakeOutOfMemoryError(RuntimeError):
    """Équivalent factice de torch.cuda.OutOfMemoryError."""


class FakeCudaError(RuntimeError):
    """Équivalent factice d'une erreur CUDA non récupérable."""


class FakeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: int = Field(50, ge=1, le=200, title="Pas", json_schema_extra={"x-ui": {"widget": "slider", "order": 1}})
    num_gaussians: int = Field(
        262144,
        ge=32,
        le=1048576,
        multiple_of=32,
        title="Gaussiennes",
        json_schema_extra={"x-ui": {"widget": "number", "order": 2}},
    )
    step_delay_s: float = Field(
        0.05,
        ge=0,
        le=5,
        title="Durée d'un pas",
        json_schema_extra={"x-ui": {"widget": "number", "order": 3, "unit": "s", "advanced": True}},
    )
    inject_failure: Literal["GPU_OOM", "NO_OBJECT", "CUDA_ERROR", "CRASH", "HANG"] | None = Field(
        None,
        title="Échec injecté",
        json_schema_extra={"x-ui": {"widget": "select", "order": 4, "group": "test", "advanced": True}},
    )
    fail_at_step: int = Field(
        1,
        ge=1,
        title="Échec au pas",
        json_schema_extra={"x-ui": {"widget": "number", "order": 5, "group": "test", "advanced": True}},
    )


class FakeAdapter:
    descriptor = ModelDescriptor(
        id="fake",
        name="Factice",
        version="fake-1",
        capabilities=frozenset({"seeded", "step_progress", "cancellable"}),
        input_formats=frozenset({"image/jpeg", "image/png", "image/webp"}),
        output_kinds=frozenset({"splat", "preview", "camera"}),
        gpu=None,
    )
    params_model: type[BaseModel] = FakeParams
    presets: Mapping[str, Preset] = {
        "fast": Preset("Rapide", {"steps": 25, "num_gaussians": 131072}),
        "balanced": Preset("Équilibré", {"steps": 50, "num_gaussians": 262144}),
        "quality": Preset("Qualité", {"steps": 50, "num_gaussians": 1048576}),
    }

    def __init__(self) -> None:
        self._loaded = False

    def check_installation(self, models_root: Path) -> Availability:
        return Availability.ok()

    def vram_required_bytes(self, params: BaseModel) -> int:
        return 0

    def load(self, ctx: LoadContext) -> None:
        self._loaded = True

    def unload(self) -> None:
        self._loaded = False

    def generate(self, request: GenerationInput, hooks: RunHooks) -> GenerationOutput:
        if not self._loaded:
            raise AdapterError(ErrorCode.MODEL_UNAVAILABLE, "adaptateur non chargé")
        params = request.params
        if not isinstance(params, FakeParams):
            raise AdapterError(ErrorCode.PARAMS_INVALID, f"paramètres {type(params).__name__}, attendu FakeParams")
        started = time.perf_counter()

        hooks.stage(Stage.PREPROCESS)
        with Image.open(request.image) as image:
            preview = image.convert("RGB").resize((256, 256))
        hooks.check_cancel()
        hooks.stage(Stage.ENCODE)
        hooks.check_cancel()

        hooks.stage(Stage.SAMPLING)
        for index in range(1, params.steps + 1):
            hooks.check_cancel()
            if params.inject_failure and index == params.fail_at_step:
                _fail(params.inject_failure)
            time.sleep(params.step_delay_s)
            hooks.step(index, params.steps)

        hooks.stage(Stage.DECODE)
        splats = _synthetic_splats(params.num_gaussians, request.seed)
        hooks.check_cancel()

        hooks.stage(Stage.EXPORT)
        out = request.output_dir
        ply = PlyData([PlyElement.describe(splats, "vertex")])
        camera = json.dumps({"yaw": 0.0, "pitch": 0.0, "radius": 3.0, "fov": 30.0}).encode()
        artifacts = {
            "splat": _write_atomically(out / "splat.ply", ply.write),
            "preview": _write_atomically(out / "preview.png", lambda f: preview.save(f, "PNG")),
            "camera": _write_atomically(out / "camera.json", lambda f: f.write(camera)),
        }
        metrics = {"seconds": time.perf_counter() - started, "gaussians": params.num_gaussians}
        return GenerationOutput(artifacts, metrics)

    def classify_error(self, exc: BaseException) -> AdapterError:
        if isinstance(exc, AdapterError):
            return exc
        if isinstance(exc, ValidationError):
            return AdapterError(ErrorCode.PARAMS_INVALID, str(exc))
        if isinstance(exc, FakeOutOfMemoryError):
            return AdapterError(ErrorCode.GPU_OOM, str(exc))
        if isinstance(exc, FakeCudaError):
            return AdapterError(ErrorCode.CUDA_ERROR, str(exc))
        if isinstance(exc, ValueError) and str(exc).startswith("no object found"):
            return AdapterError(ErrorCode.NO_OBJECT_DETECTED, str(exc))
        return AdapterError(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}")


def _fail(kind: str) -> None:
    if kind == "GPU_OOM":
        raise FakeOutOfMemoryError("CUDA out of memory (factice)")
    if kind == "CUDA_ERROR":
        raise FakeCudaError("CUDA error: an illegal memory access was encountered (factice)")
    if kind == "NO_OBJECT":
        raise ValueError("no object found in the image (factice)")
    if kind == "CRASH":
        os.kill(os.getpid(), signal.SIGKILL)
    if kind == "HANG":
        while True:  # ignore l'annulation : seul le superviseur peut l'arrêter
            time.sleep(3600)


def _synthetic_splats(count: int, seed: int) -> np.ndarray:
    """Sphère de gaussiennes : 17 propriétés float32, positions dans [-0.5, 0.5]³."""
    rng = np.random.default_rng(seed)
    data = np.zeros(count, dtype=[(name, "f4") for name in PLY_PROPERTIES])
    direction = rng.normal(size=(count, 3))
    direction /= np.linalg.norm(direction, axis=1, keepdims=True)
    for axis, name in enumerate(("x", "y", "z")):
        data[name] = 0.4 * direction[:, axis]
    for channel in ("f_dc_0", "f_dc_1", "f_dc_2"):
        data[channel] = rng.uniform(-1, 1, count)
    data["opacity"] = 2.0
    for scale in ("scale_0", "scale_1", "scale_2"):
        data[scale] = -5.0
    data["rot_0"] = 1.0
    return data


def _write_atomically(dest: Path, write: Callable[[BinaryIO], object]) -> Path:
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        with tmp.open("wb") as handle:
            write(handle)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest
