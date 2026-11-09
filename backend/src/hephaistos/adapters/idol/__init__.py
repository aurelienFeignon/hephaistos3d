"""Adaptateur Idol (Sperid Labs) — image 2D → Gaussian Splatting.

Ce module n'importe jamais torch (l'API l'emploie) : descripteur, paramètres, préréglages, contrôle
d'installation et classement des erreurs. Le chargement importe `runtime`, qui n'existe que dans le
processus d'inférence.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hephaistos.adapters.base import (
    AdapterError,
    Availability,
    GenerationInput,
    GenerationOutput,
    GpuRequirements,
    LoadContext,
    ModelDescriptor,
    Preset,
    RunHooks,
)
from hephaistos.domain.errors import ErrorCode

SDK_COMMIT = "4702a7f"
WEIGHTS_REVISION = "2731c2291fc65a797c5cb627a5e5526fc1490902"

# Lus par IdolPipeline(weights_dir) dans <models_root>/idol.
IDOL_FILES = (
    "config.json",
    "denoiser.safetensors",
    "decoder.safetensors",
    "decoder_gs.safetensors",
    "birefnet.safetensors",
)


@dataclass(frozen=True)
class HubRepo:
    """Encodeur qu'Idol désigne par identifiant de dépôt : lu dans le cache Hugging Face, hors ligne."""

    repo_id: str
    revision: str
    files: tuple[str, ...]

    @property
    def cache_dir(self) -> str:
        return "models--" + self.repo_id.replace("/", "--")


DINOV3 = HubRepo(
    "facebook/dinov3-vith16plus-pretrain-lvd1689m",
    "c807c9eeea853df70aec4069e6f56b28ddc82acc",
    ("config.json", "preprocessor_config.json", "model.safetensors"),
)
FLUX_VAE = HubRepo(
    "black-forest-labs/FLUX.2-dev",
    "26afe3a78bb242c0a8bb181dcc8937bb16e5c66c",
    ("vae/config.json", "vae/diffusion_pytorch_model.safetensors"),
)

# Pic du processus d'inférence mesuré par NVML (P1.10, ADR-0014, RTX 5070, mode full) : contexte CUDA
# et noyaux compilés compris, que max_memory_reserved (6,84 Gio) ne voit pas. Identique pour les trois
# préréglages : le pic vient des poids et de l'échantillonnage, pas du nombre de gaussiennes.
MEASURED_PEAK_BYTES = 7624 * 1024 * 1024


# Domaines : docs/specifications.md §3.2 — curseurs de la démo officielle (idol-sdk 4702a7f, demo/app.py),
# seules valeurs que les éditeurs publient ; défauts : ceux du SDK. Pas de docstring : Pydantic la
# publierait dans le schéma.
class IdolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["synthetic", "real"] = Field(
        "synthetic",
        title="Type d'image",
        description="Rendu ou image générée d'un objet seul, ou photo réelle (recadrage plus large).",
        json_schema_extra={"x-ui": {"widget": "radio", "order": 1, "labels": {"synthetic": "Rendu", "real": "Photo"}}},
    )
    num_gaussians: Literal[65536, 131072, 262144, 524288, 1048576] = Field(
        262144,
        title="Gaussiennes",
        json_schema_extra={"x-ui": {"widget": "select", "order": 2, "advanced": True}},
    )
    steps: int = Field(
        50,
        ge=10,
        le=100,
        title="Pas d'échantillonnage",
        json_schema_extra={"x-ui": {"widget": "slider", "order": 3, "group": "sampling", "advanced": True}},
    )
    cfg_strength: float = Field(
        3.0,
        ge=0.0,
        le=10.0,
        multiple_of=0.1,
        title="Guidage",
        json_schema_extra={"x-ui": {"widget": "slider", "order": 4, "group": "sampling", "advanced": True}},
    )
    rescale_t: float = Field(
        3.0,
        ge=1.0,
        le=6.0,
        multiple_of=0.1,
        title="Décalage temporel",
        description="Concentre les pas vers le bruit fort.",
        json_schema_extra={"x-ui": {"widget": "slider", "order": 5, "group": "sampling", "advanced": True}},
    )
    crop_margin: float | None = Field(
        None,
        ge=1.0,
        le=2.5,
        title="Marge de recadrage",
        description="Vide : selon le type d'image (1,2 rendu, 1,7 photo).",
        json_schema_extra={"x-ui": {"widget": "number", "order": 6, "advanced": True}},
    )


class IdolAdapter:
    descriptor = ModelDescriptor(
        id="idol",
        name="Idol",
        version=f"idol-sdk {SDK_COMMIT} · poids {WEIGHTS_REVISION[:7]}",
        capabilities=frozenset({"seeded", "step_progress", "cancellable"}),
        input_formats=frozenset({"image/jpeg", "image/png", "image/webp"}),
        output_kinds=frozenset({"splat", "preview", "camera"}),
        gpu=GpuRequirements(min_compute_capability=(8, 0)),  # bf16 (DINOv3) ; seul sm_120 est testé
    )
    params_model: type[BaseModel] = IdolParams
    # Équilibré = défauts publiés par les éditeurs ; Rapide et Qualité calibrés en P1.10 (ADR-0014).
    presets: Mapping[str, Preset] = {
        "fast": Preset("Rapide", {"steps": 25, "num_gaussians": 131072}),
        "balanced": Preset("Équilibré", {"steps": 50, "cfg_strength": 3.0, "rescale_t": 3.0, "num_gaussians": 262144}),
        "quality": Preset("Qualité", {"steps": 50, "num_gaussians": 1048576}),
    }

    def check_installation(self, models_root: Path) -> Availability:
        problems = [f"idol/{name} absent" for name in IDOL_FILES if not (models_root / "idol" / name).is_file()]
        for repo in (DINOV3, FLUX_VAE):
            problems += _hub_problems(models_root / "huggingface" / "hub", repo)
        return Availability.missing(" ; ".join(problems)) if problems else Availability.ok()

    def vram_required_bytes(self, params: BaseModel) -> int:
        return MEASURED_PEAK_BYTES

    def __init__(self) -> None:
        self._runtime: Any = None  # IdolRuntime, chargé dans le processus d'inférence seulement

    def load(self, ctx: LoadContext) -> None:
        # Doit précéder le premier import de torch : torch.compile devient inopérant (ADR-0012).
        if ctx.options.get("exec_mode", "full") == "no_compile":
            os.environ["TORCHDYNAMO_DISABLE"] = "1"
        # Poids hors ligne (ADR-0010) : cache HF sous models_root, sauf configuration explicite.
        os.environ.setdefault("HF_HOME", str(ctx.models_root / "huggingface"))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from hephaistos.adapters.idol.runtime import IdolRuntime

        self._runtime = IdolRuntime(ctx)

    def generate(self, request: GenerationInput, hooks: RunHooks) -> GenerationOutput:
        if self._runtime is None:
            raise AdapterError(ErrorCode.MODEL_UNAVAILABLE, "Idol n'est pas chargé")
        if not isinstance(request.params, IdolParams):
            raise AdapterError(
                ErrorCode.PARAMS_INVALID, f"paramètres {type(request.params).__name__}, attendu IdolParams"
            )
        result: GenerationOutput = self._runtime.generate(request, request.params, hooks)
        return result

    def unload(self) -> None:
        if self._runtime is not None:
            self._runtime.unload()
            self._runtime = None

    def health_check(self) -> None:
        if self._runtime is not None:
            self._runtime.health_check()

    def classify_error(self, exc: BaseException) -> AdapterError:
        if isinstance(exc, AdapterError):
            return exc
        if isinstance(exc, ValidationError):
            return AdapterError(ErrorCode.PARAMS_INVALID, str(exc))
        if isinstance(exc, ValueError) and str(exc).startswith("no object found"):
            return AdapterError(ErrorCode.NO_OBJECT_DETECTED, str(exc))
        if (code := _torch_error_code(exc)) is not None:
            return AdapterError(code, f"{type(exc).__name__}: {exc}")
        return AdapterError(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}")


def _torch_error_code(exc: BaseException) -> ErrorCode | None:
    """Classes de torch lues dans sys.modules : ce module ne doit jamais importer torch (A1.1).
    Si torch n'est pas chargé, aucune de ses exceptions n'a pu être levée, sauf par son message."""
    torch = sys.modules.get("torch")
    if torch is not None and isinstance(exc, torch.OutOfMemoryError):
        return ErrorCode.GPU_OOM
    compile_errors = tuple(
        getattr(sys.modules.get(module), name, None)
        for module, name in (("torch._dynamo.exc", "TorchDynamoException"), ("torch._inductor.exc", "InductorError"))
    )
    if isinstance(exc, tuple(c for c in compile_errors if isinstance(c, type))):
        return ErrorCode.COMPILE_ERROR
    accelerator = getattr(torch, "AcceleratorError", None)
    if (isinstance(accelerator, type) and isinstance(exc, accelerator)) or "CUDA error" in str(exc):
        return ErrorCode.CUDA_ERROR
    return None


def _hub_problems(hub: Path, repo: HubRepo) -> list[str]:
    cache = hub / repo.cache_dir
    refs_main = cache / "refs" / "main"
    if not refs_main.is_file():
        return [f"{repo.repo_id} : refs/main absent"]
    if (pinned := refs_main.read_text().strip()) != repo.revision:
        return [f"{repo.repo_id} : refs/main vaut {pinned[:12]}, attendu {repo.revision[:12]}"]
    snapshot = cache / "snapshots" / repo.revision
    return [f"{repo.repo_id} : {name} absent" for name in repo.files if not (snapshot / name).is_file()]
