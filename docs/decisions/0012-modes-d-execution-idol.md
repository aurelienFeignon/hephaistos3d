# ADR-0012 — Modes d'exécution d'Idol

- **Statut** : Accepté (2026-11-02, ajustement 5) ; **défaut `full` fixé le 2026-11-03** (G0.5)
- **Date** : 2026-11-02

## Contexte
`IdolPipeline(optimize=True)` active à la fois `torch.compile`, le guidage en lot, l'attention cuDNN
et TF32. Sur Blackwell sm_120, `torch.compile` et l'attention cuDNN ne sont pas encore démontrés.

## Décision
`IDOL_EXEC_MODE` :
- `full` : `optimize=True` ;
- `no_compile` : `optimize=True` et `TORCHDYNAMO_DISABLE=1` posé avant l'import de torch —
  `torch.compile` devient inopérant, les autres optimisations restent ;
- `eager` : `optimize=False`.
Pas de repli automatique entre modes.

**Défaut : `full`** (2026-11-03, après G0.5). Mesuré sur RTX 5070 (sm_120) : les trois modes
fonctionnent ; l'échantillonnage prend ~42 s en `full` contre ~53 s en `eager`, soit ~10 s de
moins par génération. Le surcoût de compilation (~30 s) ne se paie qu'à la première génération
d'un processus d'inférence. En `full`, ~1,9 Go de VRAM échappent à l'allocateur de torch
(noyaux compilés) : sans effet depuis l'abandon de la préemption (ADR-0011), mais à compter dans
`GPU_MIN_FREE_VRAM_MB`.

## Conséquences
- Le mode intermédiaire isole le coût et le risque de la compilation des autres optimisations.
- Un échec de compilation est un défaut de configuration (`COMPILE_ERROR`, non relancé), visible.

## Alternatives écartées
- Repli automatique vers `eager` : masquerait une dégradation de performance d'environ ×1,7.
