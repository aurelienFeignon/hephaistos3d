"""Adaptateur Idol (Sperid Labs) — image 2D → Gaussian Splatting.

État : descripteur, paramètres, préréglages et contrôle d'installation (P1.5). Le chargement et la
génération (P1.6, P1.7) viendront dans le processus d'inférence, seul endroit où torch est importé.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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

# max_memory_reserved mesuré au Gate P0 (RTX 5070, mode full), à 262 144 comme à 1 048 576 gaussiennes.
MEASURED_PEAK_RESERVED_BYTES = 7_344_226_304


# Domaines : docs/specifications.md §3.2 — curseurs de la démo officielle (idol-sdk 4702a7f, demo/app.py),
# seules valeurs que les éditeurs publient ; défauts : ceux du SDK. Pas de docstring : Pydantic la
# publierait dans le schéma.
class IdolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["synthetic", "real"] = Field(
        "synthetic",
        title="Type d'image",
        description="Rendu ou image générée d'un objet seul, ou photo réelle (recadrage plus large).",
        json_schema_extra={"x-ui": {"widget": "radio", "order": 1}},
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
    # Équilibré = défauts publiés par les éditeurs ; Rapide et Qualité sont des hypothèses (P1.10).
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
        return MEASURED_PEAK_RESERVED_BYTES

    def load(self, ctx: LoadContext) -> None:
        raise NotImplementedError("chargement d'Idol : P1.6")

    def generate(self, request: GenerationInput, hooks: RunHooks) -> GenerationOutput:
        raise NotImplementedError("génération Idol : P1.7")

    def unload(self) -> None:
        raise NotImplementedError("déchargement d'Idol : P1.6")

    def classify_error(self, exc: BaseException) -> AdapterError:
        # Les erreurs CUDA et OOM de torch (P1.8) se classent dans le processus d'inférence.
        if isinstance(exc, AdapterError):
            return exc
        if isinstance(exc, ValidationError):
            return AdapterError(ErrorCode.PARAMS_INVALID, str(exc))
        if isinstance(exc, ValueError) and str(exc).startswith("no object found"):
            return AdapterError(ErrorCode.NO_OBJECT_DETECTED, str(exc))
        return AdapterError(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}")


def _hub_problems(hub: Path, repo: HubRepo) -> list[str]:
    cache = hub / repo.cache_dir
    refs_main = cache / "refs" / "main"
    if not refs_main.is_file():
        return [f"{repo.repo_id} : refs/main absent"]
    if (pinned := refs_main.read_text().strip()) != repo.revision:
        return [f"{repo.repo_id} : refs/main vaut {pinned[:12]}, attendu {repo.revision[:12]}"]
    snapshot = cache / "snapshots" / repo.revision
    return [f"{repo.repo_id} : {name} absent" for name in repo.files if not (snapshot / name).is_file()]
