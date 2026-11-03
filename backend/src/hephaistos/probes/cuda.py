"""Sonde CUDA du Gate P0 (G0.1, G0.5) — n'exécute que des opérations de quelques Mo.

Refuse de s'exécuter si le GPU a moins de --min-free-mb de VRAM libre.

Usage : python -m hephaistos.probes.cuda [--exec-mode full|no_compile|eager]
Code de sortie : 0 succès, 1 échec bloquant, 3 VRAM libre insuffisante (sonde reportée).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from hephaistos.gpu.nvml import MIB, Nvml

EXPECTED_CAPABILITY = (12, 0)


def _timed(name: str, fn: Callable[[], Any], results: dict[str, Any]) -> None:
    start = time.perf_counter()
    try:
        detail = fn()
        results[name] = {"ok": True, "seconds": round(time.perf_counter() - start, 3), "detail": detail}
    except Exception as exc:  # noqa: BLE001 — la sonde rapporte toute erreur, c'est son rôle
        results[name] = {
            "ok": False,
            "seconds": round(time.perf_counter() - start, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exec-mode", choices=["full", "no_compile", "eager"], default="full")
    parser.add_argument("--min-free-mb", type=int, default=512)
    args = parser.parse_args(argv)

    if args.exec_mode != "full":
        os.environ["TORCHDYNAMO_DISABLE"] = "1"  # doit précéder l'import de torch

    with Nvml() as nvml:
        before = nvml.snapshot()
    if before.free_mib < args.min_free_mb:
        print(
            json.dumps({"refused": "vram_insufficient", "free_mib": before.free_mib, "min_free_mb": args.min_free_mb})
        )
        return 3

    import torch
    import torch.nn.functional as F
    from torch.nn.attention import SDPBackend, sdpa_kernel

    report: dict[str, Any] = {
        "exec_mode": args.exec_mode,
        "gpu_before": {"name": before.name, "total_mib": before.total_bytes // MIB, "used_mib": before.used_mib},
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "cudnn": torch.backends.cudnn.version(),
        "cuda_available": torch.cuda.is_available(),
    }
    if not torch.cuda.is_available():
        print(json.dumps(report, indent=2))
        return 1

    capability = torch.cuda.get_device_capability(0)
    report.update(
        device=torch.cuda.get_device_name(0),
        capability=list(capability),
        arch_list=torch.cuda.get_arch_list(),
        bf16_supported=torch.cuda.is_bf16_supported(),
    )
    device = torch.device("cuda")
    tests: dict[str, Any] = {}

    def matmul() -> float:
        a = torch.randn(1024, 1024, device=device, dtype=torch.float16)
        return float((a @ a).float().abs().mean())

    def attention(backend: SDPBackend | None) -> Callable[[], str]:
        def run() -> str:
            q = torch.randn(1, 16, 1024, 64, device=device, dtype=torch.float16)
            if backend is None:
                out = F.scaled_dot_product_attention(q, q, q)
            else:
                with sdpa_kernel(backend):
                    out = F.scaled_dot_product_attention(q, q, q)
            torch.cuda.synchronize()
            return str(tuple(out.shape))

        return run

    def compiled() -> float:
        fn = torch.compile(lambda x: torch.sin(x) + x * 2, dynamic=False)
        x = torch.randn(4096, device=device)
        return float((fn(x) - (torch.sin(x) + x * 2)).abs().max())

    def deform_conv() -> str:
        from torchvision.ops import deform_conv2d

        x = torch.randn(1, 4, 16, 16, device=device)
        weight = torch.randn(4, 4, 3, 3, device=device)
        offset = torch.zeros(1, 2 * 9, 16, 16, device=device)
        return str(tuple(deform_conv2d(x, offset, weight, padding=1).shape))

    _timed("matmul_fp16", matmul, tests)
    _timed("sdpa_default", attention(None), tests)
    _timed("sdpa_cudnn", attention(SDPBackend.CUDNN_ATTENTION), tests)
    if args.exec_mode == "full":
        _timed("torch_compile", compiled, tests)
    _timed("torchvision_deform_conv2d", deform_conv, tests)
    report["tests"] = tests
    with Nvml() as nvml:
        after = nvml.snapshot()
    report["context_overhead_mib"] = (after.used_bytes - before.used_bytes - torch.cuda.memory_reserved()) // MIB

    print(json.dumps(report, indent=2))
    blocking = capability != EXPECTED_CAPABILITY or not tests["matmul_fp16"]["ok"] or not tests["sdpa_default"]["ok"]
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
