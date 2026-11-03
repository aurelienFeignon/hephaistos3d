from pathlib import Path

import pytest

from hephaistos.adapters.base import Availability
from hephaistos.adapters.idol import DINOV3, FLUX_VAE, IDOL_FILES, IdolAdapter
from hephaistos.adapters.registry import build_registry, check_installations
from tests.unit.test_adapters_base import assert_does_not_import_torch


def test_registry_declares_idol_and_fake_only_when_enabled():
    assert set(build_registry(enable_fake=False)) == {"idol"}
    assert set(build_registry(enable_fake=True)) == {"idol", "fake"}


def test_registry_keys_are_descriptor_ids():
    for adapter_id, adapter in build_registry(enable_fake=True).items():
        assert adapter.descriptor.id == adapter_id


@pytest.mark.parametrize("module", ["hephaistos.adapters.registry", "hephaistos.adapters.idol"])
def test_registry_and_idol_do_not_import_torch(module):
    # A1.1 : l'API construit le registre sans torch.
    assert_does_not_import_torch(module)


def test_a_failing_check_is_reported_unavailable_not_raised(tmp_path):
    class Broken:
        descriptor = IdolAdapter.descriptor

        def check_installation(self, models_root: Path) -> Availability:
            raise PermissionError("lecture refusée")

    result = check_installations({"idol": Broken()}, tmp_path)  # type: ignore[dict-item]
    assert not result["idol"].available
    assert "lecture refusée" in (result["idol"].reason or "")


# ------------------------------------------------------------------------- IdolAdapter.check_installation


def install(root: Path, *, skip: str | None = None, refs: dict[str, str] | None = None) -> Path:
    """Arborescence minimale qu'attend Idol hors ligne : fichiers vides, révisions épinglées."""
    for name in IDOL_FILES:
        if name != skip:
            (root / "idol").mkdir(parents=True, exist_ok=True)
            (root / "idol" / name).write_bytes(b"x")
    for repo in (DINOV3, FLUX_VAE):
        cache = root / "huggingface" / "hub" / repo.cache_dir
        snapshot = cache / "snapshots" / repo.revision
        for name in repo.files:
            if name != skip:
                (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
                (snapshot / name).write_bytes(b"x")
        (cache / "refs").mkdir(parents=True, exist_ok=True)
        (cache / "refs" / "main").write_text((refs or {}).get(repo.repo_id, repo.revision))
    return root


def test_complete_installation_is_available(tmp_path):
    assert IdolAdapter().check_installation(install(tmp_path)) == Availability.ok()


@pytest.mark.parametrize(
    "missing", ["denoiser.safetensors", "model.safetensors", "vae/diffusion_pytorch_model.safetensors"]
)
def test_missing_weight_file_is_named(tmp_path, missing):
    availability = IdolAdapter().check_installation(install(tmp_path, skip=missing))
    assert not availability.available
    assert missing in (availability.reason or "")


def test_refs_main_on_another_revision_is_refused(tmp_path):
    # Hors ligne, transformers résout le cache par refs/main : une autre révision chargerait
    # d'autres poids, ou rien.
    root = install(tmp_path, refs={DINOV3.repo_id: "0" * 40})
    availability = IdolAdapter().check_installation(root)
    assert not availability.available
    assert "refs/main" in (availability.reason or "")


def test_missing_refs_main_is_refused(tmp_path):
    root = install(tmp_path)
    (root / "huggingface" / "hub" / FLUX_VAE.cache_dir / "refs" / "main").unlink()
    availability = IdolAdapter().check_installation(root)
    assert not availability.available
    assert "refs/main" in (availability.reason or "")


LOCAL_MODELS = Path(__file__).resolve().parents[3] / "var" / "models"


@pytest.mark.skipif(not LOCAL_MODELS.is_dir(), reason="poids non installés sur cette machine")
def test_installed_weights_on_this_host_are_available():
    assert IdolAdapter().check_installation(LOCAL_MODELS) == Availability.ok()


def test_idol_vram_need_is_the_device_level_measure_and_fits_the_admission_default():
    # P1.10 : pic mesuré par NVML (contexte CUDA et noyaux compilés compris), identique pour les trois
    # préréglages. Le seuil d'admission par défaut (.env.example, GPU_MIN_FREE_VRAM_MB) doit le couvrir.
    from hephaistos.adapters.idol import IdolParams

    mib = 1024 * 1024
    for num_gaussians in (65536, 262144, 1048576):
        need = IdolAdapter().vram_required_bytes(IdolParams(num_gaussians=num_gaussians))
        assert need == 7624 * mib
        assert need <= 8192 * mib
