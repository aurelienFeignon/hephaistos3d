"""Progression d'Idol par hooks de module (ADR-0005, architecture.md §6.1), sans torch ni GPU.

Les faux modules n'offrent que ce que la progression emploie : `register_forward_pre_hook` et un
appel qui déclenche les hooks, comme `nn.Module.__call__`.
"""

from types import SimpleNamespace

import pytest

from hephaistos.adapters.base import Cancelled, Stage
from hephaistos.adapters.idol.progress import HOOKED_MODULES, IdolProgress, pipeline_shape_problems
from tests.adapter_conformance import RecordingHooks
from tests.unit.test_adapters_base import assert_does_not_import_torch


class FakeModule:
    def __init__(self) -> None:
        self.hooks: list = []

    def register_forward_pre_hook(self, hook):
        self.hooks.append(hook)
        return SimpleNamespace(remove=lambda: self.hooks.remove(hook))

    def __call__(self, *args):
        for hook in list(self.hooks):
            hook(self, args)


def fake_pipeline(optimize: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        optimize=optimize,
        image_encoder=SimpleNamespace(dinov3=FakeModule()),
        denoiser=FakeModule(),
        decoder=FakeModule(),
        decoder_gs=FakeModule(),
    )


def run_like_idol(pipeline, steps: int, calls_per_step: int = 1) -> None:
    """Ordre des appels du SDK : DINOv3, puis le débruiteur à chaque pas, puis les décodeurs."""
    pipeline.image_encoder.dinov3()
    for _ in range(steps * calls_per_step):
        pipeline.denoiser()
    pipeline.decoder()
    pipeline.decoder()  # le décodeur octree est appelé à chaque niveau
    pipeline.decoder_gs()


def test_progress_module_does_not_import_torch():
    assert_does_not_import_torch("hephaistos.adapters.idol.progress")


def test_stages_and_steps_follow_the_sdk_calls():
    pipeline, hooks = fake_pipeline(), RecordingHooks()
    with IdolProgress(pipeline, steps=5, cfg_strength=3.0, hooks=hooks):
        run_like_idol(pipeline, steps=5)
    assert hooks.stages == [Stage.PREPROCESS, Stage.ENCODE, Stage.SAMPLING, Stage.DECODE]
    assert hooks.steps == [(i, 5) for i in range(1, 6)]


def test_unbatched_guidance_counts_two_calls_per_step():
    # optimize=False et cfg>0 : le SDK appelle le débruiteur deux fois par pas.
    pipeline, hooks = fake_pipeline(optimize=False), RecordingHooks()
    with IdolProgress(pipeline, steps=4, cfg_strength=3.0, hooks=hooks):
        run_like_idol(pipeline, steps=4, calls_per_step=2)
    assert hooks.steps == [(i, 4) for i in range(1, 5)]


def test_no_guidance_is_one_call_per_step_even_unbatched():
    pipeline, hooks = fake_pipeline(optimize=False), RecordingHooks()
    with IdolProgress(pipeline, steps=4, cfg_strength=0.0, hooks=hooks):
        run_like_idol(pipeline, steps=4)
    assert hooks.steps == [(i, 4) for i in range(1, 5)]


def test_more_calls_than_expected_stops_publishing_steps_and_logs():
    # Garde de cohérence : un compteur faux ne s'affiche jamais.
    pipeline, hooks = fake_pipeline(), RecordingHooks()
    with IdolProgress(pipeline, steps=3, cfg_strength=3.0, hooks=hooks):
        run_like_idol(pipeline, steps=5)
    assert hooks.steps == [(1, 3), (2, 3), (3, 3)]
    logged = [value for kind, value in hooks.events if kind == "log"]
    assert any(event == "step_count_mismatch" for event, _ in logged)


def test_fewer_calls_than_expected_is_logged_at_the_end():
    pipeline, hooks = fake_pipeline(), RecordingHooks()
    with IdolProgress(pipeline, steps=5, cfg_strength=3.0, hooks=hooks):
        run_like_idol(pipeline, steps=3)
    logged = [value for kind, value in hooks.events if kind == "log"]
    assert ("step_count_mismatch", {"denoiser_calls": 3, "expected_calls": 5}) in logged


def test_cancel_is_raised_from_the_next_hook():
    pipeline, hooks = fake_pipeline(), RecordingHooks(cancel_at_step=2)
    with pytest.raises(Cancelled), IdolProgress(pipeline, steps=5, cfg_strength=3.0, hooks=hooks):
        run_like_idol(pipeline, steps=5)
    assert max(index for index, _ in hooks.steps) == 2


def test_hooks_are_removed_even_after_a_cancel():
    pipeline, hooks = fake_pipeline(), RecordingHooks(cancel_at_step=1)
    with pytest.raises(Cancelled), IdolProgress(pipeline, steps=5, cfg_strength=3.0, hooks=hooks):
        run_like_idol(pipeline, steps=5)
    modules = (pipeline.image_encoder.dinov3, pipeline.denoiser, pipeline.decoder, pipeline.decoder_gs)
    assert all(module.hooks == [] for module in modules)


# ------------------------------------------------------------------------- garde de forme (A1.7)


def test_the_real_layout_has_no_problem():
    assert pipeline_shape_problems(fake_pipeline(), module_type=FakeModule) == []


@pytest.mark.parametrize("path", HOOKED_MODULES)
def test_a_missing_hooked_module_is_named(path):
    pipeline = fake_pipeline()
    owner_path, _, attribute = path.rpartition(".")
    owner = pipeline
    for part in filter(None, owner_path.split(".")):
        owner = getattr(owner, part)
    delattr(owner, attribute)
    assert any(path in problem for problem in pipeline_shape_problems(pipeline, module_type=FakeModule))


@pytest.mark.parametrize("path", HOOKED_MODULES)
def test_a_hooked_attribute_that_is_not_a_module_is_named(path):
    pipeline = fake_pipeline()
    owner_path, _, attribute = path.rpartition(".")
    owner = pipeline
    for part in filter(None, owner_path.split(".")):
        owner = getattr(owner, part)
    setattr(owner, attribute, object())
    assert any(path in problem for problem in pipeline_shape_problems(pipeline, module_type=FakeModule))


def test_hooked_modules_are_the_four_of_the_architecture():
    assert set(HOOKED_MODULES) == {"image_encoder.dinov3", "denoiser", "decoder", "decoder_gs"}
