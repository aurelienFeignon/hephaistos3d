"""Progression d'Idol lue par hooks de module PyTorch, sans toucher au SDK (ADR-0005, architecture §6.1).

| Hook sur                         | Donne                                              |
|----------------------------------|----------------------------------------------------|
| (construction)                   | début de `preprocess`                              |
| `image_encoder.dinov3`           | fin de `preprocess`, début de `encode`             |
| `denoiser`                       | début de `sampling`, un pas par appel (deux si guidage sans lot) |
| `decoder`, `decoder_gs`          | début de `decode`                                  |

BiRefNet appelle `self.forward()` directement et échappe aux hooks : `preprocess` se borne donc à
l'entrée de DINOv3. N'importe pas torch : n'emploie que `register_forward_pre_hook`.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from hephaistos.adapters.base import RunHooks, Stage

# Sous-modules du pipeline sur lesquels la progression repose (architecture §6.1).
HOOKED_MODULES = ("image_encoder.dinov3", "denoiser", "decoder", "decoder_gs")


def pipeline_shape_problems(pipeline: Any, *, module_type: type) -> list[str]:
    """Garde de forme (A1.7) : une mise à jour du SDK qui renomme ou remplace un sous-module accroché
    fausserait la progression en silence ; on refuse de charger plutôt. `module_type` vaut
    `torch.nn.Module` en production : ce module n'importe pas torch."""
    problems = []
    for path in HOOKED_MODULES:
        target: Any = pipeline
        for part in path.split("."):
            target = getattr(target, part, None)
        if target is None:
            problems.append(f"{path} absent du pipeline")
        elif not isinstance(target, module_type):
            problems.append(f"{path} n'est pas un {module_type.__name__} ({type(target).__name__})")
    return problems


class IdolProgress:
    """À employer en `with` : les hooks sont posés à l'entrée et toujours retirés à la sortie."""

    def __init__(self, pipeline: Any, *, steps: int, cfg_strength: float, hooks: RunHooks) -> None:
        self._pipeline = pipeline
        self._hooks = hooks
        self.steps = steps
        self.per_step = 2 if (cfg_strength > 0 and not pipeline.optimize) else 1
        self.expected_calls = steps * self.per_step
        self.denoiser_calls = 0
        self._stage: Stage | None = None
        self._publishing_steps = True
        self._handles: list[Any] = []

    def __enter__(self) -> IdolProgress:
        p = self._pipeline
        self._handles = [
            p.image_encoder.dinov3.register_forward_pre_hook(lambda _m, _a: self._enter(Stage.ENCODE)),
            p.denoiser.register_forward_pre_hook(self._on_denoiser),
            p.decoder.register_forward_pre_hook(lambda _m, _a: self._enter(Stage.DECODE)),
            p.decoder_gs.register_forward_pre_hook(lambda _m, _a: self._enter(Stage.DECODE)),
        ]
        self._enter(Stage.PREPROCESS)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        for handle in self._handles:
            handle.remove()
        self._handles = []
        if exc_type is None and self.denoiser_calls != self.expected_calls and self._publishing_steps:
            self._log_mismatch()

    def _enter(self, stage: Stage) -> None:
        self._hooks.check_cancel()
        if stage != self._stage:
            self._stage = stage
            self._hooks.stage(stage)

    def _on_denoiser(self, _module: Any, _args: Any) -> None:
        self._enter(Stage.SAMPLING)
        self.denoiser_calls += 1
        if not self._publishing_steps:
            return
        if self.denoiser_calls > self.expected_calls:
            # Le SDK appelle le débruiteur plus qu'attendu : le compteur serait faux, on cesse de
            # publier des pas (les étapes continuent) et on le dit.
            self._publishing_steps = False
            self._log_mismatch()
        elif self.denoiser_calls % self.per_step == 0:
            self._hooks.step(self.denoiser_calls // self.per_step, self.steps)

    def _log_mismatch(self) -> None:
        self._hooks.log("step_count_mismatch", denoiser_calls=self.denoiser_calls, expected_calls=self.expected_calls)
