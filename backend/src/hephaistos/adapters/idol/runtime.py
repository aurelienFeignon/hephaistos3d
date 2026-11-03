"""Exécution d'Idol : n'est importé que dans le processus d'inférence (torch, CUDA, SDK Idol).

Repris de la sonde du Gate P0 (`hephaistos.probes.idol`), éprouvée sur RTX 5070 : 18 générations
dans un processus, annulation au pas 10, neutralité des hooks.
"""

from __future__ import annotations

import gc
import json
import os
import time
from pathlib import Path
from typing import Any

import torch
from idol import IdolPipeline
from PIL import Image

from hephaistos.adapters.base import (
    AdapterError,
    Cancelled,
    GenerationInput,
    GenerationOutput,
    LoadContext,
    RunHooks,
    Stage,
)
from hephaistos.adapters.idol import IdolParams
from hephaistos.adapters.idol.progress import IdolProgress, pipeline_shape_problems
from hephaistos.domain.errors import ErrorCode
from hephaistos.storage.atomic import write_atomically

MIB = 1024 * 1024


class IdolRuntime:
    def __init__(self, ctx: LoadContext) -> None:
        if not torch.cuda.is_available():
            raise AdapterError(ErrorCode.MODEL_UNAVAILABLE, "aucun GPU CUDA visible")
        exec_mode = ctx.options.get("exec_mode", "full")
        vram_limit_mb = int(ctx.options.get("vram_limit_mb", 0))
        torch.cuda.init()
        if vram_limit_mb > 0:
            # Au-delà, l'OOM reste dans ce processus et n'atteint pas les autres charges (G0.10).
            total = torch.cuda.get_device_properties(0).total_memory
            torch.cuda.set_per_process_memory_fraction(max(0.05, min(1.0, vram_limit_mb * MIB / total)))
        self.pipeline: Any = IdolPipeline(
            str(ctx.models_root / "idol"),
            device="cuda",
            dinov3_dtype=ctx.options.get("dinov3_dtype", "bf16"),
            optimize=exec_mode != "eager",
        )
        if problems := pipeline_shape_problems(self.pipeline, module_type=torch.nn.Module):
            raise AdapterError(ErrorCode.MODEL_UNAVAILABLE, "SDK Idol incompatible : " + " ; ".join(problems))

    def generate(self, request: GenerationInput, params: IdolParams, hooks: RunHooks) -> GenerationOutput:
        with Image.open(request.image) as source:
            image = source.convert("RGBA" if source.mode == "RGBA" else "RGB")
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        try:
            progress = IdolProgress(self.pipeline, steps=params.steps, cfg_strength=params.cfg_strength, hooks=hooks)
            with progress:
                output = self.pipeline(
                    image,
                    mode=params.mode,
                    seed=request.seed,
                    steps=params.steps,
                    cfg_strength=params.cfg_strength,
                    rescale_t=params.rescale_t,
                    num_gaussians=params.num_gaussians,
                    crop_margin=params.crop_margin,
                )
                torch.cuda.synchronize()
        except (Cancelled, torch.OutOfMemoryError):
            torch.cuda.empty_cache()  # rend les tenseurs de la génération interrompue avant le job suivant
            raise

        hooks.stage(Stage.EXPORT)
        out = request.output_dir
        artifacts = {
            "splat": _replace_atomically(out / "splat.ply", lambda path: output.gaussian.save_ply(str(path))),
            "preview": write_atomically(out / "preview.png", lambda f: output.preprocessed_image.save(f, "PNG")),
            "camera": write_atomically(out / "camera.json", lambda f: f.write(json.dumps(output.camera).encode())),
        }
        metrics = {
            "seconds": time.perf_counter() - started,
            "gaussians": int(output.gaussian.xyz.shape[0]),
            "max_allocated_bytes": torch.cuda.max_memory_allocated(),
            "max_reserved_bytes": torch.cuda.max_memory_reserved(),
            "denoiser_calls": progress.denoiser_calls,
            "expected_denoiser_calls": progress.expected_calls,
        }
        return GenerationOutput(artifacts, metrics)

    def health_check(self) -> None:
        """§5.6 : après chaque job, le contexte doit encore répondre ; sinon le processus sort en 75."""
        torch.cuda.synchronize()
        probe = torch.ones(16, device="cuda")
        if float((probe * 2).sum().item()) != 32.0:
            raise RuntimeError("CUDA error: le contrôle après job rend un résultat faux")

    def unload(self) -> None:
        self.pipeline = None
        gc.collect()
        torch.cuda.empty_cache()


def _replace_atomically(dest: Path, write: Any) -> Path:
    """Pour une API qui écrit par chemin (save_ply) : fichier temporaire puis renommage."""
    tmp = dest.with_name(f".{dest.name}.tmp")
    try:
        write(tmp)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest
