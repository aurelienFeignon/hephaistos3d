"""Idol par le processus d'inférence et la CLI : A1.6, A1.2 (development-plan.md)."""

import json
import subprocess
from pathlib import Path

import pytest

from hephaistos.adapters.base import GenerationInput
from hephaistos.adapters.idol import IdolParams
from hephaistos.gpu.nvml import Nvml
from hephaistos.probes.ply import check_ply
from hephaistos.worker.inference import InferenceProcess, Succeeded
from tests.gpu.conftest import MODELS_ROOT, REFERENCE_IMAGES, REFS, normalized_reference

pytestmark = pytest.mark.gpu

FAST = {"steps": 12, "num_gaussians": 65536}


def test_inference_process_releases_all_vram(tmp_path):
    # A1.6 : après libération, le processus n'existe plus et la VRAM revient à son niveau de départ.
    with Nvml() as nvml:
        before = nvml.snapshot().used_mib
        process = InferenceProcess("idol", models_root=MODELS_ROOT)
        process.start()
        out = tmp_path / "out"
        out.mkdir()
        image = normalized_reference("nb-armchair.webp", tmp_path / "in.png")
        outcome = process.generate(GenerationInput(image, IdolParams(**FAST), seed=1, output_dir=out), timeout_s=600)
        assert isinstance(outcome, Succeeded)
        assert process.shutdown(grace_s=60) == 0
        after = nvml.snapshot().used_mib
    assert not process.is_alive()
    assert abs(after - before) <= 100, f"VRAM {before} → {after} Mio"


@pytest.mark.parametrize("name", sorted(REFERENCE_IMAGES))
def test_cli_produces_valid_artifacts_for_each_reference_image(name, tmp_path):
    # A1.2 : la CLI, de bout en bout, sur les trois images de référence.
    out = tmp_path / "out"
    command = [
        "hephaistos", "generate", "--model", "idol", "--image", str(REFS / name), "--out", str(out),
        "--models-root", str(MODELS_ROOT), "--preset", "balanced", "--seed", "42",
        "--param", f"mode={REFERENCE_IMAGES[name]}",
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, timeout=1200, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    summary = json.loads(result.stdout)
    report = check_ply(Path(summary["artifacts"]["splat"]), expected_vertices=summary["params"]["num_gaussians"])
    assert report.ok, report.problems
    assert summary["metrics"]["denoiser_calls"] == summary["metrics"]["expected_denoiser_calls"]
