# ADR-0015 — Un processus d'inférence par génération

- **Statut** : Accepté (2026-11-19) — remplace la conservation de l'enfant entre jobs de l'ADR-0003,
  et le déchargement après `MODEL_IDLE_UNLOAD_SECONDS` cité par l'ADR-0011 (« Rien n'est gardé au repos »)
- **Date** : 2026-11-19

## Contexte
L'ADR-0003 conservait le processus d'inférence entre deux jobs et ne le terminait qu'après
`MODEL_IDLE_UNLOAD_SECONDS` d'inactivité. P5.2 a montré que, sur la carte de 12 Go, le modèle était
de toute façon rechargé à chaque génération : chargé, Idol tient environ 7,3 Gio, la VRAM libre tombe
sous `GPU_MIN_FREE_VRAM_MB`, et le superviseur libère son propre enfant avant le contrôle suivant
(il ne distingue pas sa mémoire de celle des autres applications, ADR-0011).

Ce rechargement n'était donc qu'un **effet de bord** du seuil : sur une carte plus grande, ou avec un
seuil plus bas, le modèle serait resté résident jusqu'à 15 minutes. Or le GPU est partagé, et
Hephaistos doit à terme servir **plusieurs modèles** par le registre d'adaptateurs (ADR-0004) : un
modèle résident retiendrait la VRAM dont le suivant, ou une autre application, a besoin.

## Décision
- Le superviseur **termine le processus d'inférence à la fin de chaque génération**, quelle qu'en
  soit l'issue (réussite, échec, annulation, libération). Chaque prise crée un processus neuf, qui
  charge le modèle du job.
- Le réglage `MODEL_IDLE_UNLOAD_SECONDS` est **supprimé**, sans remplacement : il n'y a plus de
  modèle au repos à décharger. Une variable restée dans un `.env` est ignorée.
- La fin est **propre** : le superviseur laisse à l'enfant jusqu'à 10 s pour finir son contrôle de
  santé CUDA et décharger le modèle, puis le tue. Une sortie non nulle (contexte compromis) ou un
  `SIGKILL` compte comme une mort d'enfant : trois en dix minutes suspendent la distribution, comme avant.
- Le compteur de trois OOM consécutifs dans un même enfant disparaît : un enfant ne sert plus qu'un
  job, et la fragmentation qu'il visait ne peut plus s'accumuler. Un OOM reste un échec relançable,
  borné par `JOB_MAX_RETRIES`, et l'admission refuse de démarrer sous `GPU_MIN_FREE_VRAM_MB`.
- Inchangé : superviseur sans contexte CUDA, enfant en `spawn`, `SIGKILL` toujours possible,
  contrôle NVML à l'admission, profil Compose `gpu`.

## Conséquences
- Entre deux générations, Hephaistos tient **0 octet** de VRAM, par construction et non plus selon
  la taille de la carte. Le critère A5.3 se lit « 0 octet entre deux générations ».
- Coût accepté : un chargement par génération, **12 s médian** (11 à 29 s) mesuré en P5.2, soit
  environ 26 % d'une génération Équilibré. C'était déjà le coût réel sur cette carte.
- Le chargement entre désormais dans `JOB_TIMEOUT_SECONDS` à chaque génération (avant : la première
  seulement). Le défaut, 1 800 s, couvre largement 12-29 s de chargement et ~60 s de génération.
- Changer de modèle d'une génération à l'autre ne coûte rien de plus : c'est le cas général.
- L'état de worker `READY` (modèle chargé, sans job) n'est plus jamais produit. Il reste dans
  l'énumération en base et dans le contrat d'API pour ne pas exiger de migration. `workers[].model`
  vaut `LOADING` puis `RESIDENT` **pendant** une génération, `null` sinon.

## Alternatives écartées
- **Garder l'enfant et corriger l'admission** (`free + vram_required ≥ seuil` quand l'enfant est
  résident pour le même modèle) : supprime le rechargement, mais retient la VRAM jusqu'à 15 minutes
  sur un GPU partagé entre plusieurs modèles. Écartée le 2026-11-19.
- **Faire de `MODEL_IDLE_UNLOAD_SECONDS=0` « libérer après chaque job »** et garder les valeurs
  positives : garde une option de résidence qui n'a pas d'usage prévu, au prix d'un changement de
  sens du 0. Écartée le 2026-11-19.
