"""Lecture de l'état du GPU par NVML, sans créer de contexte CUDA.

Créer un contexte CUDA coûte plusieurs centaines de Mo de VRAM qu'on ne rend qu'en quittant le
processus : tout ce qui observe le GPU sans calculer dessus (arbitre, healthcheck, sondes) passe
par ici, jamais par torch.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pynvml

MIB = 1024 * 1024


@dataclass(frozen=True)
class GpuSnapshot:
    name: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    reserved_bytes: int
    utilization_percent: int
    taken_at: float

    @property
    def used_mib(self) -> int:
        return self.used_bytes // MIB

    @property
    def free_mib(self) -> int:
        return self.free_bytes // MIB


class Nvml:
    """Accès NVML à un périphérique. Utilisable comme gestionnaire de contexte."""

    def __init__(self, index: int = 0) -> None:
        self._index = index
        self._handle: object | None = None

    def __enter__(self) -> Nvml:
        pynvml.nvmlInit()
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(self._index)
        return self

    def __exit__(self, *exc: object) -> None:
        pynvml.nvmlShutdown()
        self._handle = None

    def snapshot(self) -> GpuSnapshot:
        if self._handle is None:
            raise RuntimeError("Nvml utilisé hors de son contexte")
        # v2 obligatoire : la v1 compte dans « used » la mémoire réservée par le pilote (362 Mio sur
        # la RTX 5070), ce qui faisait lire 1063 Mio à un GPU au repos où nvidia-smi en voit 703.
        memory = pynvml.nvmlDeviceGetMemoryInfo(self._handle, version=pynvml.nvmlMemory_v2)
        utilization = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
        name = pynvml.nvmlDeviceGetName(self._handle)
        return GpuSnapshot(
            name=name.decode() if isinstance(name, bytes) else str(name),
            total_bytes=int(memory.total),
            used_bytes=int(memory.used),
            free_bytes=int(memory.free),
            reserved_bytes=int(memory.reserved),
            utilization_percent=int(utilization.gpu),
            taken_at=time.time(),
        )


class PeakSampler(threading.Thread):
    """Échantillonne la VRAM utilisée du périphérique et retient le pic."""

    def __init__(self, nvml: Nvml, interval_s: float = 0.1) -> None:
        super().__init__(daemon=True)
        self._nvml = nvml
        self._interval = interval_s
        self._stop_event = threading.Event()
        self.peak_used_bytes = 0
        self.samples = 0

    def run(self) -> None:
        while not self._stop_event.is_set():
            used = self._nvml.snapshot().used_bytes
            self.peak_used_bytes = max(self.peak_used_bytes, used)
            self.samples += 1
            self._stop_event.wait(self._interval)

    def stop(self) -> int:
        self._stop_event.set()
        self.join()
        return self.peak_used_bytes
