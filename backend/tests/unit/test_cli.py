"""CLI de diagnostic `hephaistos generate` (P1.11), jouée avec le FakeAdapter."""

import json
from pathlib import Path

from PIL import Image

from hephaistos.cli import main
from tests.adapter_conformance import reference_image


def run(capsys, tmp_path, *args: str) -> tuple[int, dict, str]:
    code = main(["generate", "--models-root", str(tmp_path / "models"), "--out", str(tmp_path / "out"), *args])
    captured = capsys.readouterr()
    summary = json.loads(captured.out) if captured.out.strip() else {}
    return code, summary, captured.err


def fake_args(tmp_path, *extra: str) -> list[str]:
    image = reference_image(tmp_path / "in.png")
    return [
        "--model",
        "fake",
        "--image",
        str(image),
        "--param",
        "num_gaussians=1024",
        "--param",
        "step_delay_s=0",
        *extra,
    ]


def test_generation_writes_artifacts_and_reports_effective_params(capsys, tmp_path):
    code, summary, err = run(capsys, tmp_path, *fake_args(tmp_path, "--preset", "fast", "--seed", "5"))
    assert code == 0
    assert summary["status"] == "succeeded"
    assert summary["seed"] == 5
    assert summary["params"]["steps"] == 25  # préréglage « fast »
    assert summary["params"]["num_gaussians"] == 1024  # saisie
    for path in summary["artifacts"].values():
        assert (tmp_path / "out") in Path(path).parents
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    assert next(e["stage"] for e in events if e["event"] == "stage") == "preprocess"
    assert sum(e["event"] == "step" for e in events) == 25


def test_the_model_sees_the_normalized_image(capsys, tmp_path):
    code, summary, _ = run(capsys, tmp_path, *fake_args(tmp_path))
    assert code == 0
    with Image.open(summary["input"]) as image:
        assert image.format == "PNG"


def test_random_seed_is_reported(capsys, tmp_path):
    code, summary, _ = run(capsys, tmp_path, *fake_args(tmp_path))
    assert code == 0 and isinstance(summary["seed"], int)


def test_invalid_param_exits_2_and_names_the_field(capsys, tmp_path):
    code, _, err = run(capsys, tmp_path, *fake_args(tmp_path, "--param", "steps=0"))
    assert code == 2
    assert "steps" in err


def test_malformed_param_exits_2(capsys, tmp_path):
    code, _, err = run(capsys, tmp_path, *fake_args(tmp_path, "--param", "steps"))
    assert code == 2
    assert "clé=valeur" in err


def test_rejected_image_exits_2(capsys, tmp_path):
    gif = tmp_path / "anim.gif"
    Image.new("RGB", (8, 8)).save(gif, "GIF")
    code, _, err = run(capsys, tmp_path, "--model", "fake", "--image", str(gif))
    assert code == 2
    assert "unsupported_format" in err


def test_unknown_model_exits_2(capsys, tmp_path):
    code, _, err = run(capsys, tmp_path, "--model", "inconnu", "--image", str(reference_image(tmp_path / "i.png")))
    assert code == 2
    assert "inconnu" in err


def test_failed_generation_exits_1_with_its_code(capsys, tmp_path):
    code, summary, _ = run(capsys, tmp_path, *fake_args(tmp_path, "--param", "inject_failure=NO_OBJECT"))
    assert code == 1
    assert summary["status"] == "failed"
    assert summary["code"] == "NO_OBJECT_DETECTED"
    assert summary["retryable"] is False
