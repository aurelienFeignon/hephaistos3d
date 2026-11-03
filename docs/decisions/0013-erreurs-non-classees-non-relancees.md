# ADR-0013 — Erreurs non classées : diagnostic avant relance

- **Statut** : Accepté (2026-11-02, D8)
- **Date** : 2026-11-02

## Contexte
Les relances automatiques ne doivent viser que les erreurs transitoires identifiées. Une erreur
inconnue peut être déterministe : la relancer gaspille du GPU et masque le défaut.

## Décision
- Liste fermée de codes transitoires : `GPU_OOM`, `CUDA_ERROR`, `WORKER_CRASH`, `WORKER_LOST`,
  `TIMEOUT`.
- Toute exception non classée devient `INTERNAL` : `FAILED`, `needs_diagnosis = vrai`, trace complète
  conservée, relance manuelle seulement.
- Une erreur récurrente est classée dans le code après diagnostic, test à l'appui.

## Conséquences
- L'interface signale explicitement les échecs à diagnostiquer.
- Une panne transitoire inédite demande une relance manuelle jusqu'à son classement.

## Alternatives écartées
- Relancer l'inconnu dans la limite du budget (proposition initiale) : refusée par D8.
