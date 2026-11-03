"""Adaptateur Idol chargé dans le processus de test : A1.3, A1.4, A1.5, A1.7 (development-plan.md).

Module à part : Idol y reste chargé (fixture de module) et doit être libéré avant que
`test_idol_process.py` n'en charge un autre dans un processus d'inférence — deux copies ne tiennent
pas sur une carte de 12 Go.
"""

import pytest
from PIL import Image

from hephaistos.adapters.base import GenerationInput, LoadContext
from hephaistos.adapters.idol import IdolAdapter, IdolParams
from hephaistos.adapters.idol.progress import pipeline_shape_problems
from hephaistos.domain.errors import ErrorCode
from tests.adapter_conformance import AdapterConformance, ConformanceCase, RecordingHooks
from tests.gpu.conftest import MODELS_ROOT, normalized_reference

pytestmark = pytest.mark.gpu

FAST = {"steps": 12, "num_gaussians": 65536}  # 12 pas : l'annulation au pas 10 doit tomber avant le 12


@pytest.fixture(scope="module")
def case():
    return ConformanceCase(
        make_adapter=IdolAdapter,
        params=FAST,
        models_root=MODELS_ROOT,
        expected_gaussians=65536,
        make_image=lambda path: normalized_reference("nb-armchair.webp", path),
    )


@pytest.fixture(scope="module")
def idol_loaded(case):
    """Un seul chargement pour tout le module : ~30 s et une compilation."""
    adapter = case.make_adapter()
    adapter.load(LoadContext(models_root=case.models_root))
    yield adapter
    adapter.unload()


class TestIdolConformance(AdapterConformance):
    """A1.3 (étapes et pas), A1.4 (annulation au pas 10), artefacts valides : la suite commune."""

    @pytest.fixture
    def loaded(self, idol_loaded):
        return idol_loaded

    def test_hooked_modules_are_real_modules(self, loaded, monkeypatch):
        # A1.7 : la garde passe sur le vrai pipeline, et voit qu'un sous-module disparaît (mutation).
        import torch

        pipeline = loaded._runtime.pipeline
        assert pipeline_shape_problems(pipeline, module_type=torch.nn.Module) == []
        monkeypatch.setattr(pipeline, "denoiser", object())
        assert any("denoiser" in p for p in pipeline_shape_problems(pipeline, module_type=torch.nn.Module))

    def test_uniform_image_has_no_object(self, loaded, tmp_path):
        # A1.5
        blank = tmp_path / "uni.png"
        Image.new("RGB", (512, 512), (128, 128, 128)).save(blank)
        (tmp_path / "out").mkdir()
        request = GenerationInput(blank, IdolParams(**FAST), seed=1, output_dir=tmp_path / "out")
        with pytest.raises(ValueError, match="no object found") as caught:
            loaded.generate(request, RecordingHooks())
        error = loaded.classify_error(caught.value)
        assert error.code is ErrorCode.NO_OBJECT_DETECTED
        assert not error.retryable
