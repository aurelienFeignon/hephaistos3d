"""Sonde d'inférence Idol du Gate P0 (G0.2 → G0.6, G0.9, G0.10).

L'inférence tourne dans un PROCESSUS ENFANT (spawn) : le parent n'ouvre jamais de contexte CUDA,
vérifie la VRAM libre par NVML avant de créer l'enfant, et mesure le pic du périphérique — c'est
le prototype du superviseur (architecture.md §5). Une génération démarrée n'est jamais interrompue
pour céder le GPU : seule une annulation explicite l'arrête.

La progression est lue par des hooks de module PyTorch (API publique `register_forward_pre_hook`)
posés sur les sous-modules du pipeline ; aucun symbole du SDK n'est remplacé.

Exemples :
  python -m hephaistos.probes.idol --image /refs/nb-armchair.webp:synthetic --gaussians 262144
  python -m hephaistos.probes.idol --image a.webp --exec-mode eager --equivalence
  python -m hephaistos.probes.idol --image a.webp --cancel-at-step 10
  python -m hephaistos.probes.idol --image a.webp --vram-limit-mb 5000

Code de sortie : 0 succès, 1 échec, 3 VRAM libre insuffisante (sonde reportée).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Event
from pathlib import Path
from typing import Any

from hephaistos.gpu.nvml import MIB, Nvml, PeakSampler
from hephaistos.probes.ply import check_ply

EXEC_MODES = ("full", "no_compile", "eager")


@dataclass(frozen=True)
class ImageSpec:
    path: Path
    mode: str  # 'synthetic' | 'real'

    @classmethod
    def parse(cls, value: str) -> ImageSpec:
        path, _, mode = value.partition(":")
        return cls(Path(path), mode or "synthetic")


# --------------------------------------------------------------------------------------------- enfant


class Cancelled(Exception):
    """Levée depuis un hook pour interrompre une génération annulée."""


class ProgressHooks:
    """Étapes et pas d'échantillonnage observés par hooks de module, sans toucher au SDK.

    preprocess : de l'appel jusqu'au premier passage dans DINOv3
    encode     : DINOv3 + VAE
    sampling   : un appel au débruiteur par pas (deux si guidage sans lot)
    decode     : décodeur octree puis décodeur gaussien
    """

    def __init__(self, pipeline: Any, steps: int, cfg_strength: float, emit: Any, cancel: Event) -> None:
        self.steps = steps
        self.per_step = 2 if (cfg_strength > 0 and not pipeline.optimize) else 1
        self.expected_calls = steps * self.per_step
        self.denoiser_calls = 0
        self.stage_started: dict[str, float] = {}
        self._emit = emit
        self._cancel = cancel
        self._handles = [
            pipeline.image_encoder.dinov3.register_forward_pre_hook(lambda _m, _a: self._enter("encode")),
            pipeline.denoiser.register_forward_pre_hook(self._on_denoiser),
            pipeline.decoder.register_forward_pre_hook(lambda _m, _a: self._enter("decode")),
            pipeline.decoder_gs.register_forward_pre_hook(lambda _m, _a: self._enter("decode")),
        ]
        self._enter("preprocess")

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def _enter(self, stage: str) -> None:
        self._check_cancel()
        if stage not in self.stage_started:
            self.stage_started[stage] = time.perf_counter()
            self._emit({"event": "stage", "stage": stage})

    def _on_denoiser(self, _module: Any, _args: Any) -> None:
        self._enter("sampling")
        self.denoiser_calls += 1
        if self.denoiser_calls % self.per_step == 0:
            import torch

            step = self.denoiser_calls // self.per_step
            self._emit({"event": "step", "step": step, "of": self.steps, "reserved": torch.cuda.memory_reserved()})

    def close(self) -> None:
        for handle in self._handles:
            handle.remove()


def _child(args: argparse.Namespace, conn: Connection, cancel: Event) -> None:
    def emit(message: dict[str, Any]) -> None:
        conn.send({**message, "t": time.time()})

    try:
        _child_run(args, emit, cancel)
    except Exception as exc:  # noqa: BLE001 — tout échec est rapporté au parent
        import traceback

        emit({"event": "error", "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()})
    finally:
        emit({"event": "exit"})
        conn.close()


def _child_run(args: argparse.Namespace, emit: Any, cancel: Event) -> None:
    if args.exec_mode != "full":
        os.environ["TORCHDYNAMO_DISABLE"] = "1"  # torch.compile devient inopérant ; doit précéder l'import

    with Nvml() as nvml:
        used_before_cuda = nvml.snapshot().used_bytes
        import torch

        torch.cuda.init()
        torch.zeros(1, device="cuda")
        snap = nvml.snapshot()
    # Plafond optionnel, au-dessus duquel l'OOM reste dans ce processus (G0.10) ; 0 = aucun.
    cap_fraction = 1.0
    if args.vram_limit_mb > 0:
        total = torch.cuda.get_device_properties(0).total_memory
        cap_fraction = max(0.05, min(1.0, args.vram_limit_mb * MIB / total))
        torch.cuda.set_per_process_memory_fraction(cap_fraction)
    emit(
        {
            "event": "cuda_ready",
            "context_overhead_bytes": snap.used_bytes - used_before_cuda - torch.cuda.memory_reserved(),
            "cap_fraction": cap_fraction,
            "torch": torch.__version__,
            "capability": list(torch.cuda.get_device_capability(0)),
        }
    )

    import idol.models.layers as idol_layers
    from idol import IdolPipeline
    from PIL import Image, ImageOps

    start = time.perf_counter()
    pipeline = IdolPipeline(
        str(args.weights), device="cuda", dinov3_dtype=args.dinov3_dtype, optimize=args.exec_mode != "eager"
    )
    emit({"event": "loaded", "seconds": time.perf_counter() - start, "reserved": torch.cuda.memory_reserved()})
    attn_backend_at_rest = idol_layers.ATTN_BACKEND

    def load_image(spec: ImageSpec) -> Image.Image:
        image = ImageOps.exif_transpose(Image.open(spec.path))  # Idol ignore l'orientation EXIF
        return image if image.mode == "RGBA" else image.convert("RGB")

    def generate(spec: ImageSpec, gaussians: int, hooks_on: bool, seed: int) -> tuple[Any, dict[str, Any]]:
        image = load_image(spec)
        torch.cuda.reset_peak_memory_stats()
        hooks = ProgressHooks(pipeline, args.steps, args.cfg_strength, emit, cancel) if hooks_on else None
        start = time.perf_counter()
        try:
            output = pipeline(
                image,
                mode=spec.mode,
                seed=seed,
                steps=args.steps,
                cfg_strength=args.cfg_strength,
                num_gaussians=gaussians,
            )
            torch.cuda.synchronize()
        finally:
            if hooks is not None:
                hooks.close()
        seconds = time.perf_counter() - start
        metrics: dict[str, Any] = {
            "seconds": seconds,
            "max_allocated": torch.cuda.max_memory_allocated(),
            "max_reserved": torch.cuda.max_memory_reserved(),
        }
        if hooks is not None:
            marks = sorted(hooks.stage_started.items(), key=lambda kv: kv[1])
            ends = [t for _, t in marks[1:]] + [start + seconds]
            metrics["stage_seconds"] = {name: end - t for (name, t), end in zip(marks, ends, strict=True)}
            metrics["denoiser_calls"] = hooks.denoiser_calls
            metrics["expected_calls"] = hooks.expected_calls
        return output, metrics

    run_index = 0
    for spec in args.image:
        for gaussians in args.gaussians:
            for repeat in range(args.repeat):
                run_index += 1
                name = f"{spec.path.stem}-{gaussians}-{repeat + 1}"
                try:
                    output, metrics = generate(spec, gaussians, hooks_on=True, seed=args.seed)
                except Cancelled:
                    cancel.clear()
                    torch.cuda.empty_cache()
                    emit(
                        {
                            "event": "cancelled",
                            "run": name,
                            "attn_backend_restored": idol_layers.ATTN_BACKEND == attn_backend_at_rest,
                            "tf32_matmul": torch.backends.cuda.matmul.allow_tf32,
                            "reserved_after": torch.cuda.memory_reserved(),
                        }
                    )
                    continue
                out_dir = args.out / name
                out_dir.mkdir(parents=True, exist_ok=True)
                output.gaussian.save_ply(str(out_dir / "splat.ply"))
                output.preprocessed_image.save(out_dir / "preview.png")
                (out_dir / "camera.json").write_text(json.dumps(output.camera, indent=2))
                emit({"event": "result", "run": name, "index": run_index, "gaussians": gaussians, **metrics})

    if args.equivalence:
        spec, gaussians = args.image[0], args.gaussians[0]
        a, _ = generate(spec, gaussians, hooks_on=True, seed=args.seed)
        b, _ = generate(spec, gaussians, hooks_on=False, seed=args.seed)
        c, _ = generate(spec, gaussians, hooks_on=False, seed=args.seed)

        def max_diff(x: Any, y: Any) -> float:
            fields = ("xyz", "features_dc", "scaling", "rotation", "opacity")
            return max(float((getattr(x.gaussian, f) - getattr(y.gaussian, f)).abs().max()) for f in fields)

        emit({"event": "equivalence", "hooks_vs_plain": max_diff(a, b), "plain_vs_plain": max_diff(b, c)})


# --------------------------------------------------------------------------------------------- parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", type=ImageSpec.parse, action="append", required=True, help="CHEMIN[:synthetic|real]")
    parser.add_argument("--weights", type=Path, default=Path("/models/idol"))
    parser.add_argument("--out", type=Path, default=Path("/data/probe"))
    parser.add_argument("--exec-mode", choices=EXEC_MODES, default=os.environ.get("IDOL_EXEC_MODE", "full"))
    parser.add_argument("--dinov3-dtype", choices=["bf16", "fp32"], default="bf16")
    parser.add_argument("--gaussians", type=int, action="append", help="répétable ; défaut 262144")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--cfg-strength", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--equivalence", action="store_true", help="compare hooks posés / absents à graine égale")
    parser.add_argument("--cancel-at-step", type=int, default=None, help="annule la 1re génération à ce pas")
    parser.add_argument(
        "--min-free-mb",
        type=int,
        default=int(os.environ.get("GPU_MIN_FREE_VRAM_MB", "8192")),
        help="VRAM libre exigée avant de créer le processus d'inférence",
    )
    parser.add_argument(
        "--vram-limit-mb",
        type=int,
        default=int(os.environ.get("GPU_VRAM_LIMIT_MB", "0")),
        help="plafond de VRAM du processus d'inférence ; 0 = aucun",
    )
    args = parser.parse_args(argv)
    args.gaussians = args.gaussians or [262144]

    if os.environ.get("HF_HUB_OFFLINE") != "1":
        print("avertissement : HF_HUB_OFFLINE n'est pas à 1", file=sys.stderr)

    report: dict[str, Any] = {"exec_mode": args.exec_mode, "events": []}
    with Nvml() as nvml:
        at_start = nvml.snapshot()
        report["used_at_start_mib"] = at_start.used_mib
        report["free_at_start_mib"] = at_start.free_mib
        if at_start.free_mib < args.min_free_mb:
            report["refused"] = "vram_insufficient"
            print(json.dumps(report, indent=2))
            return 3  # G0.10 : aucun processus enfant, aucun contexte CUDA créé

        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        cancel = ctx.Event()
        child = ctx.Process(target=_child, args=(args, child_conn, cancel), name="idol-probe")
        sampler = PeakSampler(nvml)
        sampler.start()
        child.start()
        child_conn.close()

        while True:
            if parent_conn.poll(0.25):
                try:
                    message = parent_conn.recv()
                except EOFError:
                    break
                report["events"].append(message)
                print(json.dumps(message), file=sys.stderr)
                if message["event"] == "step" and message["step"] == args.cancel_at_step:
                    args.cancel_at_step = None
                    cancel.set()
                if message["event"] == "exit":
                    break
            if not child.is_alive() and not parent_conn.poll():
                break

        child.join(timeout=30)
        report["child_exitcode"] = child.exitcode
        report["peak_device_used_mib"] = sampler.stop() // MIB
        # Toute la VRAM de l'inférence, contexte compris, doit être rendue à la fin de l'enfant.
        report["device_used_after_exit_mib"] = nvml.snapshot().used_mib

    results = [e for e in report["events"] if e["event"] == "result"]
    report["ply"] = []
    for result in results:
        expected = (result["gaussians"] // 32) * 32
        ply_report = check_ply(args.out / result["run"] / "splat.ply", expected_vertices=expected)
        report["ply"].append({"run": result["run"], "ok": ply_report.ok, "problems": ply_report.problems})

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"report-{int(time.time())}.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "events"}, indent=2, default=str))
    errors = [e for e in report["events"] if e["event"] == "error"]
    return 1 if errors or not all(p["ok"] for p in report["ply"]) else 0


if __name__ == "__main__":
    sys.exit(main())
