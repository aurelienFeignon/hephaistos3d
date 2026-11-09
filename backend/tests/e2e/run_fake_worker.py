"""Vrai superviseur pour les tests de bout en bout : adaptateur factice et mesure GPU synthétique.

Ni CUDA ni NVML : ces parcours doivent tourner sur une machine sans carte graphique.
"""

from __future__ import annotations

import time

from hephaistos.adapters.registry import build_registry
from hephaistos.config import MIB, ConfigError, load_settings
from hephaistos.gpu.nvml import GpuSnapshot
from hephaistos.observability import configure_logging
from hephaistos.persistence.database import create_database_engine
from hephaistos.queue.broker import JobBroker
from hephaistos.storage.local import LocalStorage
from hephaistos.worker.supervisor import Supervisor

TOTAL_BYTES = 12 * 1024 * MIB


def synthetic_gpu() -> GpuSnapshot:
    return GpuSnapshot(
        name="GPU synthétique",
        total_bytes=TOTAL_BYTES,
        used_bytes=0,
        free_bytes=TOTAL_BYTES,
        reserved_bytes=0,
        utilization_percent=0,
        taken_at=time.monotonic(),
    )


def main() -> None:
    settings = load_settings()
    if not settings.enable_fake_adapter:
        raise ConfigError("ENABLE_FAKE_ADAPTER must be true for the end-to-end worker")
    engine = create_database_engine(settings.hephaistos_data_dir)
    configure_logging("worker")
    try:
        Supervisor(
            JobBroker(engine, settings),
            LocalStorage(settings.hephaistos_data_dir),
            registry=build_registry(enable_fake=True),
            gpu_probe=synthetic_gpu,
        ).run()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
