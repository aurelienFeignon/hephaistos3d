"""Tests sur GPU réel (marqueur « gpu », exclus par défaut). Se jouent dans l'image de test du worker :

    docker compose --profile gpu-test run --rm worker-test

Poids sous HEPHAISTOS_MODELS_ROOT (défaut /models), images de référence sous HEPHAISTOS_REFS
(défaut /refs) : nb-armchair.webp, fig-explorer.webp, photo-exif6.jpg.
"""

import os
from pathlib import Path

import pytest

from hephaistos.imaging import normalize_image

MODELS_ROOT = Path(os.environ.get("HEPHAISTOS_MODELS_ROOT", "/models"))
REFS = Path(os.environ.get("HEPHAISTOS_REFS", "/refs"))
REFERENCE_IMAGES = {"nb-armchair.webp": "synthetic", "fig-explorer.webp": "synthetic", "photo-exif6.jpg": "real"}


def pytest_collection_modifyitems(config, items):
    missing = [name for name in REFERENCE_IMAGES if not (REFS / name).is_file()]
    if not (MODELS_ROOT / "idol").is_dir() or missing:
        skip = pytest.mark.skip(reason=f"poids ou images de référence absents ({MODELS_ROOT}, {REFS})")
        for item in items:
            if "tests/gpu/" in str(item.fspath):
                item.add_marker(skip)


def normalized_reference(name: str, dest: Path) -> Path:
    normalize_image(REFS / name, dest, max_pixels=40_000_000)
    return dest
