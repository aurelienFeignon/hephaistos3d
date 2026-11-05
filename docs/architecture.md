# Architecture technique — Hephaistos 3D

> Statut : **validée le 2026-11-02** (option B, profil `gpu`), avec les ajustements demandés
> (partage du GPU, suspension, redémarrage après erreur CUDA, mode sans `torch.compile`,
> progression sans monkey-patching). Révisée le 2026-11-03 : plus de préemption, contrôle à
> l'admission (ADR-0011). Décisions : `development-plan.md` §2.

## 1. Principes

1. **Le métier ignore le modèle.** Domaine, API, file et stockage ne connaissent qu'une interface
   d'adaptateur. Rien d'Idol hors de `adapters/idol/`.
2. **Une seule source de vérité pour l'état** d'une génération : la base de données.
3. **L'API n'importe jamais PyTorch.** Image légère, testable sans GPU.
4. **Le GPU se partage à l'admission** (D2). Hephaistos ne prend un job que si la VRAM libre suffit,
   ne cède jamais le GPU en cours de génération, et n'y garde rien quand il ne génère pas.
5. **Tout est testable sans GPU** grâce à un adaptateur factice qui suit le même contrat.
6. **Pas de composant sans besoin démontré.**

## 2. Vue d'ensemble

```
             navigateur (localhost ; LAN sur décision explicite)
                   │  un seul port publié : 127.0.0.1:3030
                   ▼
        ┌─────────────────────┐
        │ frontend (Next.js)  │  sert l'UI ; proxifie /api/v1/* → api:8000
        └──────────┬──────────┘
                   │ réseau Docker interne
                   ▼
        ┌─────────────────────┐        ┌──────────────────────┐
        │ api (FastAPI)       │        │ janitor              │
        │ sans torch          │        │ baux expirés,        │
        └──────────┬──────────┘        │ rétention, purges    │
                   │                   └──────────┬───────────┘
                   ▼                              ▼
        ┌────────────────────────────────────────────────────┐
        │ SQLite (WAL) — générations, tentatives, artefacts,  │
        │                file (baux), workers, état système   │
        └────────────────────────────────────────────────────┘
                   ▲
                   │ claim / heartbeat / progression / résultat
        ┌──────────┴───────────────────────────────────────┐
        │ worker  (× GPU_WORKERS)                           │
        │  ┌──────────────────────────┐   Pipe   ┌────────────────────────┐
        │  │ superviseur              │◄────────►│ processus d'inférence  │
        │  │ PAS de contexte CUDA     │          │ torch + modèle         │
        │  │ file, admission NVML,    │  SIGKILL │ créé à la demande,     │
        │  │ suspension               │─────────►│ terminé pour libérer   │
        │  └──────────────────────────┘          └────────────────────────┘
        └──────────────────────────────────────────────────┘
     volumes (var/) : db · sources · results · tmp · logs · models (ro) · cache
```

| Service | Image | GPU | Rôle |
|---|---|---|---|
| `frontend` | Node 24, sortie `standalone` de Next | non | UI studio, proxy de l'API |
| `api` | cible `api` (sans torch) | non | REST v1, validation, uploads, téléchargements, suspension |
| `worker` | cible `worker` (torch, Idol, gcc), **profil `gpu`** | **oui** | superviseur + processus d'inférence ; démarré seulement avec `--profile gpu` |
| `janitor` | cible `api` | non | reprise des baux, rétention, suppressions en échec |
| `migrate` | cible `api` | non | `alembic upgrade head`, ponctuel |
| `worker-probe` | cible `worker`, profil `probe` | oui | sondes du Gate P0, **sans réseau**, jamais démarré par défaut |

## 3. Arborescence du dépôt

```
hephaistos3d/
├─ compose.yaml                  à la racine : `docker compose` sans option depuis le dépôt
├─ .env.example
├─ backend/                      uv init --package (paquet « hephaistos »)
│  ├─ pyproject.toml             extras : [idol] → torch cu132, idol@4702a7f ; groupe dev
│  ├─ uv.lock
│  ├─ alembic.ini, alembic/      alembic init
│  ├─ src/hephaistos/
│  │  ├─ config.py               pydantic-settings, validation au démarrage
│  │  ├─ domain/                 statuts, transitions, classification des erreurs, relances
│  │  ├─ adapters/               base.py · registry.py · fake/ · idol/
│  │  ├─ gpu/                    nvml.py (observation sans CUDA) · admission.py
│  │  ├─ persistence/            modèles SQLAlchemy, dépôts
│  │  ├─ queue/                  prise atomique, baux, heartbeat
│  │  ├─ storage/                chemins sûrs, écriture atomique, purge
│  │  ├─ api/v1/                 routeurs FastAPI, schémas
│  │  ├─ worker/                 superviseur, processus d'inférence, protocole
│  │  ├─ janitor/
│  │  └─ probes/                 sondes P0 (cuda, idol, gpu_load, ply)
│  └─ tests/                     unit/ · api/ · integration/ · gpu/ (marqueur « gpu »)
├─ frontend/                     create-next-app 16.4.4
├─ deploy/                       backend.Dockerfile (cibles api/worker), frontend.Dockerfile
├─ var/                          données persistantes (D4), ignoré par git
└─ docs/
```

## 4. File d'attente et persistance (ADR-0002, accepté — D1)

La table `generation` **est** la file : prise transactionnelle, baux, réservation atomique du
créneau GPU, reprise des jobs abandonnés.

### 4.1 Pourquoi pas Redis + RQ

| Exigence | Redis + RQ | Table + baux |
|---|---|---|
| Cohérence état métier / file | deux sources de vérité à réconcilier | **une seule** |
| Modèle conservé entre jobs | le worker RQ forke par job ; CUDA ne survit pas au fork | processus persistant par construction |
| Limite GPU indépendante | sémaphore à écrire en plus | `COUNT(RUNNING) < limite` dans la transaction de prise |
| Relance des seules erreurs transitoires | `Retry` relance tout | politique du domaine |
| Reprise après mort d'un worker | `FailedJobRegistry`, reprise à écrire | bail expiré → reprise |
| Services à opérer | +1 | 0 |

### 4.2 Mécanique

- **Prise atomique** : `BEGIN IMMEDIATE`, sélection du plus ancien `QUEUED` éligible
  (`not_before <= now`, adaptateur prêt sur ce worker), **à condition que** la distribution ne soit
  pas suspendue et que `COUNT(RUNNING) < GPU_MAX_CONCURRENT_JOBS` ; passage en `RUNNING` avec bail,
  création de la tentative. Le superviseur ne tente la prise **qu'après** le contrôle de VRAM libre (§5.3).
- **Heartbeat** : bail prolongé toutes les `JOB_LEASE_SECONDS/3` ; progression écrite au plus 1 fois/s.
- **Bail expiré** : le janitor clôt la tentative `ABANDONED` et applique la politique de relance.
- **Arrêt propre** (SIGTERM) : plus de prise ; le job en cours a `WORKER_SHUTDOWN_GRACE_SECONDS`,
  sinon il est interrompu ; retour en `QUEUED` sans consommer le budget (`INTERRUPTED`).
- SQLite : WAL, `busy_timeout`, `foreign_keys=ON`, fichier sur volume local.

Implémentation P2.5 :

- `persistence/database.py` construit `/data/db/hephaistos.sqlite3` depuis
  `HEPHAISTOS_DATA_DIR` et configure WAL, `foreign_keys=ON` et `busy_timeout=5000` sur chaque
  connexion. Aucune création de schéma implicite. `alembic upgrade head` utilise ce répertoire
  par défaut ; une URL fournie dans la configuration Alembic reste prioritaire.
- `queue/broker.py` expose `JobBroker` : `claim`, `heartbeat`, `report_progress`, `complete`,
  `fail`, `interrupt`, `recover_expired`, `retry`, `cancel`, `finish_cancel`.
  Chaque écriture prend le verrou SQLite par `BEGIN IMMEDIATE`, puis applique un
  `UPDATE ... WHERE status = ... AND deleting_at IS NULL`.
- La prise filtre les modèles disponibles, les délais et les suppressions ; elle respecte la
  suspension persistée (ou `DISPATCH_PAUSED` si la ligne système n'existe pas encore).
  Les baux expirés occupent toujours leur créneau jusqu'à leur reprise explicite.
- Le jeton `Lease` comprend le worker et l'ULID de tentative. Toute écriture issue du worker
  exige un bail encore valide et une tentative ouverte appartenant à la génération et au worker.
  Réutiliser le même nom de worker n'autorise jamais une ancienne tentative à écrire.
- `complete` valide les trois descriptions d'artefacts, prépare et flush les écritures SQL,
  puis appelle la promotion sous le verrou d'écriture avant le commit. Une erreur SQL détectable
  avant commit ne publie donc rien ; une erreur de promotion annule les écritures SQL.
  Les lecteurs WAL voient encore `RUNNING` jusqu'au commit.
- Le disque et SQLite ne partagent pas de transaction : un arrêt brutal après promotion et avant
  commit peut laisser un résultat non référencé. Sa réconciliation est sérialisée avec les
  promotions et contrôles de bail. P2.6 le fait lors du succès suivant : le broker
  refuse des artefacts déjà référencés, puis le superviseur remplace les résultats orphelins
  sous le même verrou et avec un bail valide. Le janitor P2.7 nettoie aussi les résultats non
  référencés de plus de 24 h, hors PENDING/RUNNING, sous verrou SQLite.
- `recover_expired` et `interrupt` sont des primitives transactionnelles ; leur déclenchement,
  l'effacement des sorties abandonnées et les métriques worker appartiennent à P2.6/P2.7.
  Le heartbeat valide le bail et renvoie l'indicateur d'annulation coopérative.

P2.6b : `system/dispatch.py` gère la ligne singleton et les mises à jour sérialisées
(`BEGIN IMMEDIATE`). Un GET d'une ligne existante reste une lecture WAL sans verrou d'écriture.
La première initialisation copie `DISPATCH_PAUSED` (`paused_by=config` si vrai) ; les démarrages
suivants préservent les décisions enregistrées. Les PUT manuels sont idempotents et ne modifient
aucun job ni bail. Les horodatages sont normalisés en UTC avant stockage et dans les réponses.

L'application factory `api/app.py:create_app` expose GET/PUT `/api/v1/system/dispatch` et
`/api/v1/openapi.json`, avec `current_principal`, validation stricte et erreurs RFC 9457.
L'API complète et les images Compose restent à intégrer en P2.8/P2.11.

### 4.3 Relances (D8)

- `JOB_MAX_RETRIES` relances **automatiques** au plus, **uniquement** pour les codes d'erreur
  explicitement classés transitoires (`data-model.md` §4).
- Délai : `JOB_RETRY_BACKOFF_SECONDS × 2^(n-1)`, plafonné à `JOB_RETRY_BACKOFF_MAX_SECONDS`.
- **Une erreur non classée (`INTERNAL`) n'est jamais relancée automatiquement** : la génération
  passe `FAILED` avec l'indicateur `needs_diagnosis`, sa trace complète est conservée, et seule une
  relance manuelle est possible. Une erreur récurrente se classe ensuite dans le code, après
  diagnostic.
- Un arrêt propre du worker ne consomme pas le budget.

### 4.4 Évolution

- **Workers déportés** : le superviseur ne parle qu'à `JobBroker` (`claim`, `heartbeat`,
  `report_progress`, `complete`, `fail`) et `ArtifactStore` ; implémentations locales au MVP, HTTP
  plus tard. Adaptateurs et domaine inchangés.
- **PostgreSQL** : `SELECT … FOR UPDATE SKIP LOCKED`, changement confiné à `queue/`.

## 5. GPU : partage et cycle de vie du modèle (ADR-0003, ADR-0011)

### 5.1 Principe

Le GPU peut être partagé avec d'autres charges de l'utilisateur, qu'Hephaistos ne connaît pas.
Il ne les arbitre pas :

- il **contrôle la VRAM libre avant de prendre un job**, jamais après ;
- il **ne cède jamais le GPU en cours de génération** : une génération démarrée va à son terme,
  sauf annulation explicite par l'utilisateur ;
- il **ne garde rien au repos** : le processus d'inférence se termine après inactivité.

Idol annonce 7,2 Go de pic. Sur une carte de 12 Go, une génération et une autre charge lourde
**alternent**, elles ne cohabitent pas. L'opérateur gère cette alternance à la main (§5.5) ou par
un plafond (§5.3).

### 5.2 Deux processus par worker

| Processus | Contient | Ne fait jamais |
|---|---|---|
| **Superviseur** | file, heartbeat, contrôle d'admission (NVML), suspension, cycle de vie de l'enfant | créer un contexte CUDA, importer torch |
| **Processus d'inférence** (`multiprocessing`, `spawn`) | torch, modèle chargé, adaptateur | parler à la base |

Pourquoi :

- **Un contexte CUDA ne se libère qu'à la sortie du processus.** `unload()` + `empty_cache()` rendent
  la mémoire des tenseurs, pas le contexte (plusieurs centaines de Mo, mesurés en P0). Pour rendre
  *toute* la VRAM, il faut terminer le processus qui la tient.
- **Libération garantie en temps borné** : l'annulation coopérative n'est vue qu'aux hooks ; pendant
  un long encodage ou décodage, ou si l'enfant ne répond plus, seul `SIGKILL` libère sans attendre.
- **Erreur CUDA** (ajustement 4) : le processus compromis meurt ; le superviseur, qui n'a jamais
  touché CUDA, reste sain et en crée un neuf au besoin. Aucun redémarrage de conteneur nécessaire.

Protocole (Pipe, messages typés) : superviseur → enfant `load`, `generate`, `cancel`, `shutdown` ;
enfant → superviseur `cuda_ready{context_overhead}`, `mem{reserved}`, `stage`, `step`, `result`,
`error{code, retryable}`. Prototype : `hephaistos.probes.idol`.

### 5.3 Contrôle à l'admission et plafond

| Règle | Condition |
|---|---|
| **Prendre un job** (créer l'enfant ou lui confier un job) | distribution non suspendue **et** VRAM libre lue par NVML (API v2) ≥ `GPU_MIN_FREE_VRAM_MB` |
| **Attendre** | sinon, le job reste `QUEUED` avec le motif `VRAM_INSUFFICIENT` ; au-delà de `GPU_WAIT_TIMEOUT_SECONDS` (si > 0), il échoue en `GPU_UNAVAILABLE`, non relancé automatiquement ; 0 = attendre indéfiniment |
| **Plafond** (optionnel) | `GPU_VRAM_LIMIT_MB` > 0 : l'enfant appelle `torch.cuda.set_per_process_memory_fraction(limite/totale)`. Au-delà, l'OOM se produit **dans** Hephaistos (`GPU_OOM`, erreur du job) et n'affecte pas les autres processus |
| **Pendant la génération** | aucune surveillance de l'usage des autres processus, aucune interruption |
| **Libérer à l'inactivité** | aucun job depuis `MODEL_IDLE_UNLOAD_SECONDS` → l'enfant est terminé |

- Un job qu'aucun état du GPU ne pourra jamais servir (besoin des paramètres supérieur au plafond,
  ou à la VRAM totale sans plafond) est **refusé à la soumission** (422 `INSUFFICIENT_VRAM_BUDGET`) —
  refus propre, rien n'est mis en file.
- Un job servable mais en attente porte un motif lisible : « VRAM insuffisante », « distribution
  suspendue », « aucun worker ».
- Défaut `GPU_MIN_FREE_VRAM_MB=8192` : pic d'Idol (≈ 7,3 Gio, contexte CUDA compris) plus une marge.

### 5.4 Limite résiduelle, acceptée

Le contrôle porte sur l'**admission** seulement. ⚠️ Une autre charge qui démarre **pendant** une
génération ne dispose que de la VRAM restante : sans plafond, elle peut subir un OOM. C'est à
l'opérateur de choisir : fixer `GPU_VRAM_LIMIT_MB` pour lui laisser une marge, ou arrêter le worker
avant cette charge (§5.5).

La préemption (céder le GPU quand l'usage des autres processus monte) a été essayée en P0 puis
**abandonnée** : l'usage étranger estimé par NVML est structurellement faux, et un projet générique
n'a pas à arbitrer pour des services qu'il ne connaît pas (ADR-0011).

Mécanismes étudiés et écartés :

| Mécanisme | Raison |
|---|---|
| Préemption sur estimation de l'usage étranger | estimation fausse (VRAM publiée en retard sur NVML, allocations hors allocateur torch) ; préemptions sans aucune charge mesurées en P0 |
| Verrou consultatif partagé avec les autres charges | exige de modifier des services tiers |
| MIG | non pris en charge par les GeForce |
| MPS avec limites de mémoire | démon MPS et reconfiguration des autres conteneurs |
| Mode `EXCLUSIVE_PROCESS` | un service résident empêcherait Hephaistos de tourner ; modification système |

### 5.5 Contrôle manuel : monter, démonter, suspendre (ajustement 3)

Trois niveaux, du plus léger au plus radical :

| Geste | Effet sur le GPU | Effet sur l'application |
|---|---|---|
| Ne rien faire | worker démarré mais inactif : **0 octet** après `MODEL_IDLE_UNLOAD_SECONDS` (processus d'inférence terminé) | tout fonctionne ; la génération suivante recharge le modèle |
| **Suspendre** la distribution (bouton, `PUT /api/v1/system/dispatch`, `DISPATCH_PAUSED`) | aucune nouvelle génération ; celle en cours va à son terme | soumissions acceptées, elles attendent (« distribution suspendue ») |
| **Démonter** le worker : `docker compose stop worker` | **0 octet, garanti** : plus aucun processus Hephaistos sur le GPU | interface, API et file restent en ligne ; la génération en cours a `WORKER_SHUTDOWN_GRACE_SECONDS` pour finir, sinon elle est interrompue (`INTERRUPTED`, budget intact) ; les générations attendent (« aucun worker ») ; `docker compose --profile gpu up -d worker` reprend la file |

- `worker` appartient au **profil Compose `gpu`** : `docker compose up -d` démarre l'application
  **sans** toucher au GPU ; `docker compose --profile gpu up -d` l'active.
- Avant une autre charge lourde sur le même GPU : **démonter le worker**. C'est le geste
  recommandé, car il ne dépend d'aucune estimation.
- Ligne `system_state` : `dispatch_paused`, `paused_reason`, `paused_at`, `paused_by`
  (`user` | `config` | `system`).

### 5.6 Erreurs CUDA (ajustement 4)

| Cas | Traitement |
|---|---|
| `torch.cuda.OutOfMemoryError` | dans l'enfant : `empty_cache()`, erreur `GPU_OOM` transitoire ; 3 consécutives → enfant terminé |
| Toute autre erreur CUDA (accès illégal, échec de lancement, ECC, Xid) | contexte **présumé compromis** : l'enfant sort (code 75) ; s'il ne sort pas, le superviseur le tue. `CUDA_ERROR`, transitoire |
| Mort de l'enfant (signal, code ≠ 0) | `WORKER_CRASH`, transitoire ; un enfant neuf sera créé au prochain job éligible |
| Contrôle après chaque job | l'enfant fait `torch.cuda.synchronize()` + une opération minime ; échec → sortie 75 |
| ≥ 3 morts d'enfant en 10 min | worker `DEGRADED` : plus de prise, distribution suspendue (`paused_by=system`), action manuelle requise |
| GPU absent au démarrage (eGPU) | état `NO_GPU`, nouvel essai toutes les 30 s, aucune boucle de crash |

Implémentation P2.6 : `python -m hephaistos.worker.supervisor` (schéma déjà migré).

- Le superviseur utilise `InferenceProcess` dans un thread de pilotage ; les calculs restent
  exclusivement dans l'enfant `spawn`. La boucle principale renouvelle les baux à `/3`, même
  pendant le chargement ou une étape silencieuse, et publie l'état du worker.
- Pas d'enfant avant une prise et un contrôle NVML positif. GPU absent : `NO_GPU`, nouvel essai
  après 30 s. Limite mémoire supérieure au total : erreur `GPU_VRAM_LIMIT_MB` avant la prise.
  Le contrôle de disponibilité filtre les adaptateurs ; le modèle/version et les paramètres
  figés sont vérifiés avant lancement.
- Les jobs successifs réutilisent l'enfant chargé. L'inactivité le termine (0 = déchargement
  automatique désactivé). Si la VRAM libre passe sous le seuil entre deux jobs, l'enfant au
  repos est aussi terminé avant une prochaine admission ; aucune interruption en cours de job.
- `GPU_WAIT_TIMEOUT_SECONDS` clôt seulement des jobs `QUEUED` éligibles pour les adaptateurs
  disponibles, hors suspension, sans créer de tentative. Le délai se mesure depuis `queued_at`.
- Les événements sont associés à l'ULID de tentative pour ignorer les messages tardifs d'un
  ancien enfant. Durées d'étape et pic alloué (si publié par l'adaptateur) vont dans `attempt` ;
  les artefacts reçoivent taille et SHA-256 calculés depuis les fichiers attendus.
- Une annulation est transmise au prochain heartbeat. Sans réponse après
  `WORKER_SHUTDOWN_GRACE_SECONDS`, l'enfant est tué et la tentative devient `CANCELLED`.
  Un bail expiré avec annulation demandée est également clôturé `CANCELLED` lors de la reprise.
- `SIGTERM`/`SIGINT` arrêtent les prises et laissent le job finir pendant le délai de grâce.
  À l'échéance, l'enfant est tué, la tentative passe `INTERRUPTED` et revient en file sans
  consommation de budget. Un bail perdu tue l'enfant et interdit toute publication.
- Le délai total `JOB_TIMEOUT_SECONDS` inclut le chargement. CUDA_ERROR, crash ou timeout
  remplacent l'enfant ; trois morts sur dix minutes placent le worker `DEGRADED` et suspendent
  la distribution (`paused_by=system`). Une reprise explicite via
  `PUT /api/v1/system/dispatch` remet aussi à zéro la fenêtre de morts des superviseurs
  dégradés lorsqu’ils observent la reprise ; aucun redémarrage n’est requis.
- Les erreurs classées du chargement traversent le protocole sans perdre leur code ; les
  erreurs inconnues conservent la trace et deviennent `INTERNAL`, avec diagnostic obligatoire.
- Validation CPU : GPU simulé, vrais enfants Fake, vrai SIGTERM et tests par mutation.
  Validation GPU et intégration Compose restent à démontrer avec accord explicite.

### 5.7 Modes d'exécution (ajustement 5)

| `IDOL_EXEC_MODE` | `optimize` (SDK) | `torch.compile` | Lot de guidage, attention cuDNN, TF32 |
|---|---|---|---|
| `full` | vrai | actif | oui |
| `no_compile` | vrai | **inopérant** (`TORCHDYNAMO_DISABLE=1` avant l'import de torch) | oui |
| `eager` | faux | non | non |

Pas de repli automatique : un mode qui échoue échoue visiblement, et le mode par défaut est fixé
d'après les mesures de G0.5.

## 6. Interface d'adaptateur (ADR-0004)

```python
class ModelAdapter(Protocol):
    descriptor: ModelDescriptor          # id, nom, version, capacités, formats, besoins GPU
    params_model: type[BaseModel]        # Pydantic ; son JSON Schema pilote le formulaire
    presets: Mapping[str, Preset]

    def check_installation(self, models_root: Path) -> Availability: ...
    def vram_required_bytes(self, params: BaseModel) -> int: ...      # dépend des paramètres
    def load(self, ctx: LoadContext) -> None: ...                     # dans le processus d'inférence
    def generate(self, request: GenerationInput, hooks: RunHooks) -> GenerationOutput: ...
    def unload(self) -> None: ...
    def classify_error(self, exc: BaseException) -> AdapterError: ...
```

| Élément | Contenu |
|---|---|
| `ModelDescriptor` | `id`, `name`, `version` (adaptateur + commit SDK + révision des poids), `capabilities`, `input_formats`, `output_kinds`, `gpu` (`min_compute_capability`, mesure de référence) |
| `RunHooks` | `stage(name)`, `step(i, n)`, `log(event, **fields)`, `check_cancel()` (lève `Cancelled`) |
| `AdapterError` | `code`, `message`, `retryable` |

**Registre** : adaptateurs déclarés en code (`idol`, `fake` si `ENABLE_FAKE_ADAPTER`). Chaque worker
appelle `check_installation()` au démarrage et publie la disponibilité dans son heartbeat ; l'API
lit ces lignes.

### 6.1 IdolAdapter — progression et annulation sans monkey-patching (ajustement 6)

Le SDK n'a pas de callback. La première proposition (remplacer `idol.sampler.tqdm`) dépendait d'un
symbole interne : **abandonnée**. La progression passe par l'**API publique de PyTorch**,
`nn.Module.register_forward_pre_hook`, sur les sous-modules du pipeline :

| Hook sur | Se déclenche | Donne |
|---|---|---|
| — (appel du pipeline) | — | début de `preprocess` |
| `pipeline.image_encoder.dinov3` | entrée de DINOv3 | fin de `preprocess`, début de `encode` |
| `pipeline.denoiser` | **une fois par pas** (deux si guidage sans lot, c.-à-d. `optimize=False` et `cfg>0`) | début de `sampling`, pas `i/N` |
| `pipeline.decoder`, `pipeline.decoder_gs` | entrée des décodeurs | début de `decode` |
| — (retour) | — | début de `export` |

Vérifié dans le code du SDK : `sample_flow` appelle `model(...)`, le décodeur octree `model(...)` à
chaque niveau, `decode` appelle `self.decoder_gs(...)` — tous passent par `__call__`, donc par les
hooks. **BiRefNet, lui, appelle `self.forward(t)` directement : un hook n'y verrait rien**, d'où le
choix de borner `preprocess` par l'entrée de DINOv3 plutôt que par BiRefNet.

Robustesse :

- **Annulation** : `check_cancel()` dans chaque hook lève `Cancelled` ; le SDK restaure ses réglages
  globaux dans un `finally` (`_sampling_settings`). Vérifié en P0 : l'état est rétabli et la
  génération suivante réussit.
- **Garde de cohérence** : à la fin, `appels_débruiteur == pas × appels_par_pas`. Sinon,
  l'adaptateur **cesse de publier des pas** (étapes seulement) et journalise l'écart — un compteur
  faux ne s'affiche jamais.
- **Neutralité** : en P0, sortie avec hooks comparée à sortie sans hooks à graine égale, rapportée à
  la variabilité de deux exécutions sans hooks.
- **Garde de forme** (test sans GPU) : les attributs `image_encoder.dinov3`, `denoiser`, `decoder`,
  `decoder_gs` existent et sont des `nn.Module`. Une mise à jour du SDK qui les renomme casse ce
  test, pas la production.
- **Filet** : même si un hook manquait, le superviseur peut terminer l'enfant par `SIGKILL` (§5.2).

**FakeAdapter** : mêmes étapes et messages, pas temporisés, PLY synthétique valide, erreurs
injectables (`GPU_OOM`, `NO_OBJECT`, `CUDA_ERROR`, `CRASH`, `HANG`).

## 7. Paramètres, préréglages et formulaire

- Paramètres = modèle Pydantic de l'adaptateur ; `GET /models/{id}/schema` publie son JSON Schema
  enrichi de `x-ui` (`widget`, `group`, `advanced`, `order`, `unit`).
- Le frontend contient **un** moteur de formulaire générique limité à un sous-ensemble déclaré
  (`integer`, `number`, `boolean`, `string` + `enum`, `minimum`, `maximum`, `multipleOf`, `default`,
  nullable). Un test backend refuse tout schéma qui en sortirait.
- Paramètres effectifs = `défauts ⊕ préréglage ⊕ saisie`, validés, enregistrés complets.

## 8. Stockage (`/opt/hephaistos3d/var/`, D4)

| Chemin hôte | Montage | Contenu | api | worker | janitor |
|---|---|---|---|---|---|
| `var/db` | `/data/db` | `hephaistos.sqlite3` (+ WAL) | rw | rw | rw |
| `var/sources` | `/data/sources` | `<gen_id>/original.<ext>`, `input.png` | rw | ro | rw |
| `var/results` | `/data/results` | `<gen_id>/splat.ply`, `preview.png`, `camera.json` | ro | rw | rw |
| `var/tmp` | `/data/tmp` | uploads en cours, sorties non promues | rw | rw | rw |
| `var/logs` | `/data/logs` | `<gen_id>/attempt-<n>.jsonl` | ro | rw | rw |
| `var/models` | `/models` | `idol/`, `huggingface/` | — | **ro** | — |
| `var/cache` | `/cache` | `torchinductor/`, `triton/` | — | rw | — |

- Écriture dans `tmp/` puis publication du répertoire complet ; sorties promues au seul succès.
  Si les montages permettent le renommage, un seul `rename` suffit. Sur `EXDEV` (montages
  distincts), copie dans un répertoire caché du volume de destination, puis renommage local.
  En cas d'échec de copie, le répertoire caché est nettoyé et aucune sortie n'est publiée.
  Après succès inter-montages, la copie de travail dans `tmp/` reste à nettoyer par le janitor.
  Un arrêt brutal peut aussi laisser un répertoire caché : à traiter comme un orphelin (> 24 h).
- P2.4 : `storage/local.py` construit les chemins et promeut exactement les fichiers attendus
  (`original.jpg/png/webp` + `input.png`, ou `splat.ply` + `preview.png` + `camera.json`).
  Chaque tentative possède son propre `tmp/<gen_id>/<attempt_id>/` ; les uploads utilisent
  `tmp/<gen_id>/upload/`. Une destination existante est refusée.
  Les liens symboliques dans les chemins de publication sont refusés ; l'effacement récursif
  ne suit pas les liens imbriqués. Les répertoires gérés restent réservés aux services.
- Le renommage donne une visibilité atomique, sans garantie de durabilité après panne électrique.
  Le contrôle du bail et la sérialisation de la promotion avec la mise à jour `COMPLETED`
  appartiennent à la file/superviseur (P2.5/P2.6), pas au stockage.
- Le backend reste local pour le MVP. Un backend objet compatible S3 pourra être ajouté ensuite ;
  sa publication devra utiliser des clés immuables puis une référence en base.
- Chemins construits depuis des ULID validés et des noms fixes, puis contrôlés `is_relative_to`.
- Suppression : `deleting_at` → fichiers → lignes ; reprise idempotente par le janitor ;
  `storage/deletion.py` valide et commit le marqueur par mise à jour conditionnelle, refuse
  `RUNNING`, puis efface les répertoires `sources`, `results`, `logs`, `tmp` de la génération.
  Une erreur disque conserve les lignes et le marqueur. Les lignes enfants sont supprimées dans
  la transaction finale. Les prises, relances et finalisations d'upload devront toutes exclure
  `deleting_at IS NOT NULL`. La suppression manuelle reste autorisée si `protected = true` ;
  rapprochement périodique des orphelins.
- Rétention : génération terminale, non protégée, plus vieille que `STORAGE_RETENTION_DAYS`
  (0 = désactivée). Jamais `QUEUED`, `RUNNING` ni protégée.

P2.9 : le parseur multipart borne les octets du fichier avant écriture dans le spool temporaire,
puis le service d'ingestion copie par blocs de 64 Kio, calcule le SHA-256, normalise et valide
les paramètres et le budget mémoire. Les rejets ne créent aucune ligne. Après création persistée
de PENDING, la finalisation prend BEGIN IMMEDIATE, vérifie PENDING et l'absence de deleting_at,
promeut les sources et passe QUEUED avant commit. Un crash SQL après publication laisse PENDING
et les sources pour réconciliation par le janitor. Un échec de publication marque FAILED /
INGEST_FAILED ; cette génération sans entrée publiée nécessite une nouvelle soumission,
le broker refuse une relance d'inférence. Aucun accès CUDA ou sonde GPU dans l'API.

### 8.1 Janitor (P2.7)

`python -m hephaistos.janitor` exécute les reprises toutes les
`min(30, JOB_LEASE_SECONDS / 3)` secondes ; `--once` effectue un cycle complet puis quitte.
Aucune création de schéma ni import CUDA. SIGTERM/SIGINT interrompent l'attente ; le cycle
transactionnel en cours se termine avant sortie. L'intégration Compose reste P2.11.

- Chaque tick reprend les baux expirés via le broker, réconcilie les PENDING de plus de 5 min
  et rejoue les suppressions marquées. Une erreur sur un élément est journalisée et ne bloque
  pas les autres. L'échec de la lecture initiale de la base interrompt le cycle.
- La reprise PENDING prend BEGIN IMMEDIATE et revérifie statut, âge et deleting_at. Les deux
  fichiers attendus doivent être réguliers, non vides et sans symlinks ; le SHA-256 de l'original
  doit correspondre à la ligne. Les sources déjà publiées sont réutilisées ; sinon les sources
  complètes du staging sont promues. La génération devient QUEUED, ou FAILED / INGEST_FAILED
  si les données sont incomplètes/corrompues ou la publication impossible.
- La maintenance complète s'exécute au démarrage puis toutes les
  `STORAGE_CLEANUP_INTERVAL_HOURS`. Elle applique la rétention aux seuls statuts terminaux,
  non protégés et dont finished_at dépasse l'âge configuré ; **0 désactive la rétention**.
  L'UPDATE qui pose deleting_at revérifie ces critères pour fermer les courses avec PATCH/retry.
- Les répertoires orphelins, les temporaires inactifs et les copies cachées de promotion sont
  supprimés après 24 h. La date de chaque descendant est contrôlée ; un fichier récent préserve
  l'ensemble. Les ULID et chemins sont vérifiés, les symlinks refusés. Les noms inconnus restent
  intacts. Le nettoyage prend BEGIN IMMEDIATE pour revérifier propriétaires et références :
  aucun fichier PENDING/RUNNING, aucune source/journal appartenant à une génération existante,
  aucun résultat référencé par un artefact n'est supprimé par cette collecte.
- Les workers sans heartbeat depuis plus de 7 jours sont purgés, sauf s'ils possèdent encore
  une génération RUNNING. Les tentatives historiques sont conservées.
- `janitor-status.json`, publié atomiquement dans le répertoire de données, contient at, ok,
  error_count et les compteurs du dernier cycle. Un cycle partiellement échoué publie ok=false ;
  une panne globale laisse le heartbeat précédent vieillir. La vérification Compose suivra en P2.11.

## 9. Visualisation et exports

### 9.1 Visualiseur (ADR-0006)

**Spark** (`@sparkjsdev/spark` 2.4.0, MIT, Three.js ≥ 0.180) : PLY, PLY compressé, SPZ, SPLAT,
KSPLAT, SOG. `OrbitControls`, réinitialisation, panneau d'informations. Chargement progressif d'un
PLY non vérifié ; repli : progression d'octets réelle.

### 9.2 Exports (ADR-0007)

| Nature | Exemple | Fidélité | MVP |
|---|---|---|---|
| Format natif | PLY 3DGS | exacte | ✅ |
| Conteneur splat | SPZ, SOG (`@playcanvas/splat-transform`) | même représentation, compression | étude P4 |
| Splats dans glTF | GLB + extension Khronos de splatting | représentation conservée, lecteurs à vérifier | étude P4 |
| Nuage de points | GLB/OBJ des centres | perd échelles, rotations, opacités | refusé comme export 3D |
| Maillage reconstruit | multi-vues + fusion TSDF | approximation | hors MVP |

## 10. API et mises à jour

REST `/api/v1`, Pydantic, OpenAPI généré, erreurs RFC 9457. Interrogation périodique (2 s actif,
15 s sinon) — ADR-0008. Détail : `api-contract.md`.

## 11. Sécurité

- **Exposition (D5)** : seul `frontend` publie un port, `127.0.0.1:3030`. L'ouverture au LAN est un
  changement explicite de `HEPHAISTOS_BIND_ADDR`. Docker contourne le pare-feu de l'hôte : l'adresse
  de liaison est la barrière.
- **Uploads** : taille bornée en flux, format vérifié par décodage, liste blanche JPEG/PNG/WebP,
  plafond de pixels, orientation EXIF appliquée, image d'inférence réencodée en PNG.
- **Paramètres** : Pydantic, bornes incluses, `extra=forbid`.
- **Conteneurs** : non-root (`1000:1000`), `read_only`, `tmpfs`, `cap_drop: [ALL]`,
  `no-new-privileges`, aucun `privileged`. Le worker n'a **aucun réseau sortant** utile (poids hors
  ligne) ; la sonde est en `network_mode: none`.
- **Secrets** : aucun dans les services ; le jeton HF ne sert qu'au geste d'installation, sur l'hôte.
- **Authentification future** : dépendance FastAPI `current_principal`, colonne `owner_id` dès la
  première migration.
- **Aucune modification des autres services de l'hôte** : Hephaistos lit seulement NVML ; il
  n'écrit ni dans leur configuration, ni dans leurs données.

## 12. Configuration

Validée au démarrage par `pydantic-settings` ; une valeur invalide arrête le service en nommant la
variable. Référence complète et commentée : `.env.example`.

| Variable | Défaut | Contrainte |
|---|---|---|
| `GPU_WORKERS` | 1 | ≥ 0 |
| `GPU_MAX_CONCURRENT_JOBS` | 1 | ≥ 1 ; avertissement si > `GPU_WORKERS` |
| `GPU_MIN_FREE_VRAM_MB` | 8192 | ≥ 0 |
| `GPU_VRAM_LIMIT_MB` | 0 | ≥ 0 (0 = aucun plafond) ; ≤ VRAM totale |
| `GPU_WAIT_TIMEOUT_SECONDS` | 0 | ≥ 0 (0 = indéfini) |
| `MODEL_IDLE_UNLOAD_SECONDS` | 900 | ≥ 0 (0 = jamais) |
| `DISPATCH_PAUSED` | false | booléen |
| `IDOL_EXEC_MODE` | full (à confirmer par G0.5) | `full` \| `no_compile` \| `eager` |
| `IDOL_DINOV3_DTYPE` | bf16 | `bf16` \| `fp32` |
| `JOB_MAX_RETRIES` | 3 | 0 → 10 |
| `JOB_RETRY_BACKOFF_SECONDS` | 30 | 1 → 3600 |
| `JOB_RETRY_BACKOFF_MAX_SECONDS` | 900 | ≥ backoff |
| `JOB_LEASE_SECONDS` | 60 | 15 → 600 |
| `JOB_TIMEOUT_SECONDS` | 1800 | > 0 |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | 90 | < `stop_grace_period` |
| `STORAGE_RETENTION_DAYS` | 30 | ≥ 0 |
| `STORAGE_CLEANUP_INTERVAL_HOURS` | 24 | 1 → 168 |
| `UPLOAD_MAX_BYTES` | 20 971 520 | 1 Mo → 100 Mo |
| `UPLOAD_MAX_PIXELS` | 40 000 000 | > 0 |
| `HEPHAISTOS_BIND_ADDR` / `HEPHAISTOS_PORT` | 127.0.0.1 / 3030 | |
| `ENABLE_FAKE_ADAPTER` | false | booléen |

## 13. Déploiement

- `compose.yaml` à la racine, projet `hephaistos3d`, réseau dédié ; aucun service extérieur référencé.
- `worker` : profil `gpu`, `deploy.replicas: ${GPU_WORKERS}`, réservation `nvidia` `count: 1`,
  `stop_grace_period` > `WORKER_SHUTDOWN_GRACE_SECONDS`.
- Healthchecks : `api` → `/api/v1/system/health` ; `worker` → fraîcheur du heartbeat (superviseur) ;
  `frontend` → requête locale ; `janitor` → fraîcheur de son dernier cycle.
- `restart: unless-stopped` ; `migrate` ponctuel dont dépendent les autres.
- Reproductibilité : `uv.lock`, `package-lock.json`, images de base épinglées par empreinte, commit
  Idol épinglé.
- Logs (P2.10) : `observability.py` configure stdlib et structlog en JSON sur stdout dans les
  points d'entrée worker, janitor et enfant d'inférence, ainsi que pendant le lifespan API.
  Champs communs : timestamp UTC, level, service, event, logger ; les lignes liées à une
  génération sont corrélées par generation_id et, pour l'inférence, attempt_id/worker_id.
- L'API émet `http_request` avec request_id, méthode, modèle de route, statut et durée, puis
  generation_id lorsque connu (soumission, génération ou artefact). Les requêtes ne journalisent
  ni corps ni query string. L'access logger Uvicorn est remplacé par ces événements corrélés.
  Le contexte de requête et la configuration du programme hôte sont restaurés après usage.
- Le superviseur écrit `logs/<gen_id>/attempt-<n>.jsonl` dès la prise, relaie stage/step/log des
  hooks puis journalise l'issue réellement persistée, y compris chargement échoué, interruption
  et annulation. Les traces sont conservées dans ce journal de diagnostic et en base.
  Chaque événement est ajouté avec O_APPEND et le fichier est fermé immédiatement ; symlinks
  refusés et O_NOFOLLOW sur le fichier. Le disque n'est pas fsync à chaque événement.
- Un échec de journalisation disque est signalé sur stdout et ne transforme pas une génération
  réussie en erreur. Les champs non sérialisables/non finis sont représentés en texte pour
  conserver une ligne JSON valide. Les champs d'un adaptateur sont imbriqués et ne remplacent
  pas la corrélation de la tentative.
- Les événements périmés sont rejetés par attempt_id ; un second drainage après la résolution
  du Future préserve les derniers messages. Le janitor conserve l'identifiant de la tentative
  avant reprise du bail pour éviter d'écrire dans le journal d'une tentative nouvellement prise.
  Les journaux suivent la suppression/rétention de leur génération ; aucune rotation séparée.
- Le format de diagnostic du CLI existant est conservé. La mise en forme concerne les logs
  applicatifs et ceux des bibliothèques qui utilisent logging, pas les écritures natives directes
  de dépendances sur les descripteurs stdout/stderr.
