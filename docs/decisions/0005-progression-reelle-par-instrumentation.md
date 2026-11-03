# ADR-0005 — Progression et annulation par hooks de module PyTorch

- **Statut** : Accepté (2026-11-02) — révisé avant acceptation (ajustement 6)
- **Date** : 2026-11-02

## Contexte
Le SDK Idol n'expose aucun callback. La première proposition remplaçait `idol.sampler.tqdm`, un
symbole interne : fragile. L'exigence interdit toute progression simulée.

## Décision
- Hooks `register_forward_pre_hook` (API publique PyTorch) sur `image_encoder.dinov3`, `denoiser`,
  `decoder`, `decoder_gs`. Aucun symbole du SDK n'est remplacé ni enveloppé.
- `preprocess` est borné par l'entrée de DINOv3, car BiRefNet appelle `self.forward()` directement
  et échappe aux hooks (vérifié dans le code).
- Un pas = un appel au débruiteur (deux si guidage sans lot) ; contrôle de cohérence en fin de
  génération ; en cas d'écart, seules les étapes sont publiées.
- L'annulation lève une exception depuis un hook ; le SDK restaure ses réglages globaux (`finally`).
- Filet : si l'annulation n'est pas vue (étape non découpable, hook manquant), le superviseur
  termine le processus d'inférence par `SIGKILL` (ADR-0003).

## Conséquences
- La dépendance porte sur quatre **noms d'attributs** du pipeline, gardés par un test de forme sans
  GPU, et non plus sur le corps d'une fonction du SDK.
- Neutralité vérifiée en P0 : sortie avec hooks contre sortie sans hooks à graine égale, rapportée à
  la variabilité naturelle.

## Alternatives écartées
- Remplacer `idol.sampler.tqdm` : dépend d'un détail d'implémentation.
- Forker le SDK pour un callback : maintenance contre un amont très jeune.
- Réimplémenter `IdolPipeline.__call__` : duplique la logique d'orchestration.
- Proposer un callback en amont : souhaitable en plus, sans en dépendre.
