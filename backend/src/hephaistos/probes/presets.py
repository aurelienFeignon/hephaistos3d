"""Sonde de calibrage des préréglages (P1.10, specifications.md §3.3).

Charge l'adaptateur une fois dans un processus d'inférence, absorbe la compilation par une
génération de chauffe, puis joue chaque préréglage sur chaque image, `--repeat` fois.

Critères :
  Rapide  : durée ≤ 60 % de celle d'Équilibré (médianes, par image)
  Qualité : pic de VRAM d'Hephaistos ≤ VRAM totale - 1,5 Go

    python -m hephaistos.probes.presets --image /refs/nb-armchair.webp:synthetic \\
        --image /refs/photo-exif6.jpg:real --repeat 2 --out /data/probe/presets

Le pic d'Hephaistos se lit par NVML (périphérique - niveau avant l'enfant) : il comprend le contexte
CUDA et ce qui échappe à l'allocateur de torch (noyaux compilés), contrairement à max_reserved.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from hephaistos.adapters.base import GenerationInput
from hephaistos.adapters.registry import build_registry
from hephaistos.adapters.schema import effective_params
from hephaistos.gpu.nvml import MIB, Nvml, PeakSampler
from hephaistos.imaging import normalize_image
from hephaistos.worker.inference import InferenceProcess, Succeeded

FAST_MAX_RATIO = 0.60
QUALITY_MARGIN_MIB = 1536


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="idol")
    parser.add_argument("--image", action="append", required=True, help="CHEMIN[:synthetic|real]")
    parser.add_argument("--preset", action="append", default=None, help="défaut : tous")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--models-root", type=Path, default=Path("/models"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    adapter = build_registry(enable_fake=True)[args.model]
    presets = args.preset or list(adapter.presets)
    args.out.mkdir(parents=True, exist_ok=True)
    images = []
    for spec in args.image:
        path, _, mode = spec.partition(":")
        dest = args.out / f"{Path(path).stem}.png"
        normalize_image(Path(path), dest, max_pixels=40_000_000)
        images.append((Path(path).stem, dest, mode or "synthetic"))

    report: dict[str, Any] = {"model": args.model, "runs": []}
    with Nvml() as nvml:
        snapshot = nvml.snapshot()
        baseline_mib, total_mib = snapshot.used_mib, snapshot.total_bytes // MIB
        report |= {"device_total_mib": total_mib, "baseline_used_mib": baseline_mib}
        process = InferenceProcess(args.model, models_root=args.models_root)
        report["load_seconds"] = process.start()
        try:
            warmup = _run(process, adapter, "balanced", images[0], args, "warmup", nvml)
            report["warmup"] = warmup
            for name, image, mode in images:
                for preset in presets:
                    for repeat in range(1, args.repeat + 1):
                        run = _run(
                            process, adapter, preset, (name, image, mode), args, f"{name}-{preset}-{repeat}", nvml
                        )
                        run["hephaistos_peak_mib"] = run["device_peak_mib"] - baseline_mib
                        report["runs"].append(run)
                        print(json.dumps(run), file=sys.stderr, flush=True)
        finally:
            process.shutdown()

    report["verdict"] = _verdict(report["runs"], total_mib)
    (args.out / f"presets-{int(time.time())}.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["verdict"], indent=2))
    return 0 if all(v["ok"] for v in report["verdict"].values()) else 1


def _run(
    process: InferenceProcess,
    adapter: Any,
    preset: str,
    image: tuple[str, Path, str],
    args: argparse.Namespace,
    run: str,
    nvml: Nvml,
) -> dict[str, Any]:
    name, path, mode = image
    out = args.out / run
    out.mkdir(parents=True, exist_ok=True)
    overrides = {"mode": mode} if "mode" in adapter.params_model.model_fields else {}
    params = effective_params(adapter, preset, overrides)
    sampler = PeakSampler(nvml)
    sampler.start()
    started = time.perf_counter()
    outcome = process.generate(GenerationInput(path, params, seed=args.seed, output_dir=out), timeout_s=1800)
    seconds = time.perf_counter() - started
    peak = sampler.stop() // MIB
    if not isinstance(outcome, Succeeded):
        raise RuntimeError(f"{run} : {outcome}")
    return {"run": run, "image": name, "preset": preset, "params": params.model_dump(), "wall_seconds": seconds,
            "device_peak_mib": peak, **outcome.output.metrics}  # fmt: skip


def _verdict(runs: list[dict[str, Any]], total_mib: int) -> dict[str, Any]:
    def median(image: str, preset: str, key: str) -> float:
        return float(statistics.median(r[key] for r in runs if r["image"] == image and r["preset"] == preset))

    verdict: dict[str, Any] = {}
    for image in sorted({r["image"] for r in runs}):
        ratio = median(image, "fast", "seconds") / median(image, "balanced", "seconds")
        verdict[f"fast/{image}"] = {
            "ratio_to_balanced": round(ratio, 3),
            "max": FAST_MAX_RATIO,
            "ok": ratio <= FAST_MAX_RATIO,
        }
    quality_peak = max(r["hephaistos_peak_mib"] for r in runs if r["preset"] == "quality")
    budget = total_mib - QUALITY_MARGIN_MIB
    verdict["quality/vram"] = {"peak_mib": quality_peak, "budget_mib": budget, "ok": quality_peak <= budget}
    return verdict


if __name__ == "__main__":
    sys.exit(main())
