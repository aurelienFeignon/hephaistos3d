"""Outil de diagnostic : une génération de bout en bout, sans base ni API (P1.11).

    hephaistos generate --model idol --image photo.jpg --preset balanced --out /tmp/essai \\
        [--param steps=30] [--seed 42] [--exec-mode full] [--vram-limit-mb 0]

Chemin identique à celui du worker : normalisation de l'image, paramètres effectifs, processus
d'inférence séparé. Événements en JSON sur stderr, résumé JSON sur stdout.
Sortie : 0 réussite, 1 échec de la génération, 2 entrée refusée (image, paramètres, modèle).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hephaistos.adapters.base import AdapterError, GenerationInput
from hephaistos.adapters.registry import build_registry
from hephaistos.adapters.schema import effective_params
from hephaistos.imaging import ImageRejected, normalize_image
from hephaistos.worker.inference import Failed, InferenceProcess, ProgressEvent, Succeeded

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
SEED_MAX = 2**31 - 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hephaistos", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="une génération de diagnostic")
    generate.add_argument("--model", required=True)
    generate.add_argument("--image", type=Path, required=True)
    generate.add_argument("--out", type=Path, required=True)
    generate.add_argument("--preset", default=None)
    generate.add_argument("--param", action="append", default=[], metavar="CLÉ=VALEUR")
    generate.add_argument("--seed", type=int, default=None, help="aléatoire si absente")
    generate.add_argument("--models-root", type=Path, default=Path(os.environ.get("HEPHAISTOS_MODELS_ROOT", "/models")))
    generate.add_argument(
        "--exec-mode", choices=["full", "no_compile", "eager"], default=os.environ.get("IDOL_EXEC_MODE", "full")
    )
    generate.add_argument("--vram-limit-mb", type=int, default=int(os.environ.get("GPU_VRAM_LIMIT_MB", "0")))
    generate.add_argument("--max-pixels", type=int, default=int(os.environ.get("UPLOAD_MAX_PIXELS", "40000000")))
    generate.add_argument("--timeout", type=float, default=1800.0)
    return _generate(parser.parse_args(argv))


def _generate(args: argparse.Namespace) -> int:
    adapter = build_registry(enable_fake=True).get(args.model)
    if adapter is None:
        return _refuse(f"modèle {args.model!r} inconnu")
    try:
        overrides = dict(_parse_param(raw) for raw in args.param)
        params = effective_params(adapter, args.preset, overrides)
    except ValueError as exc:
        return _refuse(str(exc))
    except AdapterError as exc:
        return _refuse(exc.message)
    availability = adapter.check_installation(args.models_root)
    if not availability.available:
        return _refuse(f"{args.model} indisponible : {availability.reason}")

    output_dir = args.out
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized = output_dir / "input.png"
    try:
        normalize_image(args.image, normalized, max_pixels=args.max_pixels)
    except ImageRejected as exc:
        return _refuse(f"image refusée ({exc.reason}) : {exc.detail}")

    seed = args.seed if args.seed is not None else secrets.randbelow(SEED_MAX + 1)
    options = {"exec_mode": args.exec_mode, "vram_limit_mb": args.vram_limit_mb}
    process = InferenceProcess(args.model, models_root=args.models_root, options=options)
    try:
        _event({"event": "loaded", "seconds": process.start()})
        request = GenerationInput(normalized, params, seed=seed, output_dir=output_dir)
        outcome = process.generate(request, on_event=_progress, timeout_s=args.timeout)
    except RuntimeError as exc:
        return _refuse(str(exc))
    finally:
        process.shutdown()

    summary: dict[str, Any] = {
        "model": args.model,
        "input": str(normalized),
        "seed": seed,
        "params": params.model_dump(),
    }
    if isinstance(outcome, Succeeded):
        summary |= {"status": "succeeded", "artifacts": outcome.output.artifacts, "metrics": outcome.output.metrics}
        code = EXIT_OK
    elif isinstance(outcome, Failed):
        summary |= {
            "status": "failed",
            "code": outcome.code,
            "message": outcome.message,
            "retryable": outcome.retryable,
        }
        code = EXIT_FAILED
    else:
        summary |= {"status": "cancelled"}
        code = EXIT_FAILED
    print(json.dumps(summary, default=str, ensure_ascii=False, indent=2))
    return code


def _parse_param(raw: str) -> tuple[str, Any]:
    key, sep, value = raw.partition("=")
    if not sep or not key:
        raise ValueError(f"--param {raw!r} : forme attendue clé=valeur")
    try:
        return key, json.loads(value)  # 30 → int, 2.5 → float, null → None
    except json.JSONDecodeError:
        return key, value  # chaîne nue : NO_OBJECT, real…


def _progress(event: ProgressEvent) -> None:
    _event({"event": type(event).__name__.removesuffix("Event").lower(), **asdict(event)})


def _event(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str, ensure_ascii=False), file=sys.stderr, flush=True)


def _refuse(message: str) -> int:
    print(f"hephaistos : {message}", file=sys.stderr)
    return EXIT_REFUSED
