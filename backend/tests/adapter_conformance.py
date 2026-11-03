"""Suite de conformité commune à tous les adaptateurs (ADR-0004).

Chaque adaptateur fournit un `ConformanceCase` ; les mêmes tests jouent sur `FakeAdapter` (sans GPU)
et sur `IdolAdapter` (marqueur « gpu »). Un adaptateur qui passe cette suite se branche sur le
worker sans autre adaptation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from hephaistos.adapters.base import Cancelled, GenerationInput, LoadContext, ModelAdapter, Stage
from hephaistos.probes.ply import check_ply


@dataclass
class RecordingHooks:
    """Hooks d'exécution qui enregistrent tout ; annule au pas `cancel_at_step` s'il est fixé."""

    cancel_at_step: int | None = None
    events: list[tuple[str, Any]] = field(default_factory=list)
    _cancel_requested: bool = False

    def stage(self, stage: Stage) -> None:
        self.events.append(("stage", stage))

    def step(self, index: int, total: int) -> None:
        self.events.append(("step", (index, total)))
        if index == self.cancel_at_step:
            self._cancel_requested = True

    def log(self, event: str, **fields: Any) -> None:
        self.events.append(("log", (event, fields)))

    def check_cancel(self) -> None:
        if self._cancel_requested:
            raise Cancelled()

    @property
    def stages(self) -> list[Stage]:
        return [value for kind, value in self.events if kind == "stage"]

    @property
    def steps(self) -> list[tuple[int, int]]:
        return [value for kind, value in self.events if kind == "step"]


@dataclass
class ConformanceCase:
    make_adapter: Callable[[], ModelAdapter]
    params: dict[str, Any]  # au moins 12 pas, pour l'annulation au pas 10
    models_root: Path
    expected_gaussians: int


def reference_image(path: Path) -> Path:
    image = Image.new("RGB", (64, 64), (20, 20, 20))
    for x in range(16, 48):
        for y in range(16, 48):
            image.putpixel((x, y), (200, 120, 40))
    image.save(path, "PNG")
    return path


class AdapterConformance:
    """À hériter dans un module de test qui définit la fixture `case`."""

    @pytest.fixture
    def loaded(self, case: ConformanceCase):
        adapter = case.make_adapter()
        adapter.load(LoadContext(models_root=case.models_root))
        yield adapter
        adapter.unload()

    def _request(self, adapter: ModelAdapter, case: ConformanceCase, tmp_path: Path) -> GenerationInput:
        out = tmp_path / "out"
        out.mkdir(parents=True)
        params = adapter.params_model.model_validate(case.params)
        return GenerationInput(reference_image(tmp_path / "in.png"), params, seed=42, output_dir=out)

    def test_installation_is_reported(self, case: ConformanceCase):
        assert case.make_adapter().check_installation(case.models_root).available

    def test_stages_are_published_in_order(self, loaded, case, tmp_path):
        hooks = RecordingHooks()
        loaded.generate(self._request(loaded, case, tmp_path), hooks)
        assert hooks.stages == list(Stage)

    def test_every_step_is_published_in_order(self, loaded, case, tmp_path):
        hooks = RecordingHooks()
        loaded.generate(self._request(loaded, case, tmp_path), hooks)
        total = case.params["steps"]
        assert hooks.steps == [(i, total) for i in range(1, total + 1)]
        first_step = hooks.events.index(("step", (1, total)))
        assert hooks.events.index(("stage", Stage.SAMPLING)) < first_step
        assert first_step < hooks.events.index(("stage", Stage.DECODE))

    def test_artifacts_are_valid_and_inside_the_output_dir(self, loaded, case, tmp_path):
        request = self._request(loaded, case, tmp_path)
        output = loaded.generate(request, RecordingHooks())
        assert {"splat", "preview", "camera"} <= set(output.artifacts)
        for path in output.artifacts.values():
            assert path.parent == request.output_dir and path.is_file()
        report = check_ply(output.artifacts["splat"], expected_vertices=case.expected_gaussians)
        assert report.ok, report.problems

    def test_cancel_at_step_10_stops_before_step_12_without_artifacts(self, loaded, case, tmp_path):
        # A1.4
        request = self._request(loaded, case, tmp_path)
        hooks = RecordingHooks(cancel_at_step=10)
        with pytest.raises(Cancelled):
            loaded.generate(request, hooks)
        assert max(index for index, _ in hooks.steps) < 12
        assert not any(request.output_dir.iterdir())

    def test_next_generation_succeeds_after_a_cancel(self, loaded, case, tmp_path):
        with pytest.raises(Cancelled):
            loaded.generate(self._request(loaded, case, tmp_path / "a"), RecordingHooks(cancel_at_step=10))
        output = loaded.generate(self._request(loaded, case, tmp_path / "b"), RecordingHooks())
        assert output.artifacts["splat"].is_file()

    def test_presets_validate_against_the_params_model(self, case: ConformanceCase):
        adapter = case.make_adapter()
        assert adapter.presets
        for preset in adapter.presets.values():
            adapter.params_model.model_validate(dict(preset.values))
