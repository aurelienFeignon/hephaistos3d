# ADR-0011 — Partage du GPU : contrôle à l'admission, sans préemption

- **Statut** : Accepté (2026-11-03)
- **Date** : 2026-11-03

## Contexte
Le GPU d'Hephaistos n'est pas forcément dédié : l'utilisateur peut y faire tourner d'autres
charges (services d'inférence, entraînements, calculs lancés à la main). Hephaistos ne les connaît
pas et ne doit pas en dépendre. Idol annonce 7,2 Go de pic : sur une carte de 12 Go, une génération
et une autre charge lourde ne tiennent pas ensemble.

Une première conception cédait le GPU en cours de génération : le superviseur estimait l'usage
« étranger » (VRAM du périphérique moins la part d'Hephaistos) et tuait le processus d'inférence
quand il croissait. Elle a été essayée en P0 puis abandonnée (voir « Alternatives écartées »).

## Décision
- **Aucune préemption.** Hephaistos ne cède jamais le GPU en cours de génération. Une génération
  démarrée va à son terme, sauf annulation explicite par l'utilisateur.
- **Contrôle à l'admission** : le superviseur ne prend un job que si la VRAM libre, lue par NVML
  (API v2), atteint `GPU_MIN_FREE_VRAM_MB` (défaut 8192). Sinon le job attend, jusqu'à
  `GPU_WAIT_TIMEOUT_SECONDS` (0 = indéfiniment), puis échoue en `GPU_UNAVAILABLE`.
- **Plafond optionnel** `GPU_VRAM_LIMIT_MB` (défaut 0 = aucun) : le processus d'inférence appelle
  `torch.cuda.set_per_process_memory_fraction`. Au-delà, l'OOM reste dans Hephaistos (erreur
  `GPU_OOM` du job) ; il n'affecte pas les autres processus.
- **Rien n'est gardé au repos** : le processus d'inférence se termine après
  `MODEL_IDLE_UNLOAD_SECONDS` (ADR-0003, option B), ce qui rend toute la VRAM, contexte compris.
- **Contrôle manuel** : `worker` est dans le profil Compose `gpu`. `docker compose up -d` ne touche
  pas au GPU ; `docker compose stop worker` rend toute la VRAM. Geste recommandé avant une autre
  charge lourde.
- **Suspension** de la distribution (API, UI, `DISPATCH_PAUSED`) : plus aucune prise ; la
  génération en cours se termine.
- Un OOM pendant une génération reste une erreur du job, relancée seulement parce que `GPU_OOM`
  est classé transitoire (ADR-0013).

## Conséquences
- ⚠️ Une autre charge qui démarre **pendant** une génération ne dispose que de la VRAM restante :
  elle peut subir un OOM si aucun plafond n'est fixé. C'est à l'opérateur de choisir : fixer
  `GPU_VRAM_LIMIT_MB` pour lui laisser une marge, ou arrêter le worker avant cette charge.
- Le comportement ne dépend d'aucune estimation : un seul relevé NVML de VRAM libre, à l'admission.
- Hephaistos ne lit ni ne modifie aucun autre service de l'hôte.

## Alternatives écartées
- **Préemption** (détecter la montée de l'usage étranger, annuler puis tuer le processus
  d'inférence, attendre un délai, reprendre). Rejetée pour deux raisons :
  - **L'estimation de l'usage étranger par NVML est structurellement fausse.** Mesuré en P0 : la
    VRAM publiée par le processus d'inférence arrive en retard sur le relevé NVML. Une allocation
    de 1,3 Go entre deux relevés et ~11 s sans publication pendant le prétraitement ont produit
    2 140 Mio d'« usage étranger » fantôme, et une préemption sans aucune autre charge. En mode
    `torch.compile`, 1,9 Go échappent en outre à l'allocateur de torch.
  - **Un projet générique n'a pas à arbitrer pour des services qu'il ne connaît pas.** La priorité
    entre charges relève de l'opérateur.
- **Verrou partagé** pris par les autres charges : exige de modifier des services tiers.
- MIG : non pris en charge par les GeForce.
- MPS avec limites de mémoire : démon et reconfiguration des autres conteneurs.
- `EXCLUSIVE_PROCESS` : un service résident bloquerait Hephaistos ; modification système.
