"""Registre des adaptateurs, déclaré en code (ADR-0004).

La découverte porte sur l'installation, pas sur le code : chaque worker appelle
`check_installations` au démarrage et publie le résultat dans son heartbeat.
"""

from __future__ import annotations

from pathlib import Path

from hephaistos.adapters.base import Availability, ModelAdapter
from hephaistos.adapters.fake import FakeAdapter
from hephaistos.adapters.idol import IdolAdapter


def build_registry(*, enable_fake: bool) -> dict[str, ModelAdapter]:
    adapters: list[ModelAdapter] = [IdolAdapter()]
    if enable_fake:
        adapters.append(FakeAdapter())
    return {adapter.descriptor.id: adapter for adapter in adapters}


def check_installations(registry: dict[str, ModelAdapter], models_root: Path) -> dict[str, Availability]:
    """Ne lève jamais : un contrôle qui échoue rend l'adaptateur indisponible, avec la raison."""
    result: dict[str, Availability] = {}
    for adapter_id, adapter in registry.items():
        try:
            result[adapter_id] = adapter.check_installation(models_root)
        except Exception as exc:  # noqa: BLE001 — la raison est publiée, le worker démarre quand même
            result[adapter_id] = Availability.missing(f"contrôle en échec : {type(exc).__name__}: {exc}")
    return result
