# Modèle de données et cycle de vie des générations

> Statut : validé (D1, D8), mis à jour le 2026-11-02. SQLite au MVP via SQLAlchemy 2 + Alembic ; aucun type propre à SQLite,
> pour que le passage à PostgreSQL reste une migration ordinaire.

Conventions : identifiants **ULID** (texte, 26 caractères, triables par date) ; horodatages UTC
(`DateTime(timezone=True)`) ; JSON dans des colonnes `JSON` ; énumérations stockées en texte avec
contrainte `CHECK`.

## 1. Tables

### `generation` — l'unité métier, et le job

| Colonne | Type | Notes |
|---|---|---|
| `id` | ULID, PK | |
| `owner_id` | texte, nullable | réservé à l'authentification future |
| `model_id` | texte | `idol` |
| `model_version` | texte | version de l'adaptateur + commit SDK + révision des poids, figée à la création |
| `status` | enum | §2 |
| `preset` | texte, nullable | `fast` \| `balanced` \| `quality` \| null (avancé) |
| `params` | JSON | **paramètres effectifs complets**, après fusion défauts ⊕ préréglage ⊕ saisie |
| `seed` | entier | toujours renseigné (tiré par le serveur si « aléatoire ») |
| `source_filename` | texte | nom d'origine **affiché seulement**, jamais utilisé dans un chemin |
| `source_mime`, `source_width`, `source_height`, `source_sha256`, `source_has_alpha` | | |
| `created_at`, `queued_at`, `started_at`, `finished_at` | horodatages | `started_at` = début de la **première** tentative |
| `progress_stage` | texte, nullable | `preprocess` \| `encode` \| `sampling` \| `decode` \| `export` |
| `progress_current`, `progress_total` | entiers, nullables | renseignés seulement pendant `sampling` |
| `progress_updated_at` | horodatage | |
| `attempt_count` | entier | tentatives commencées depuis la dernière (re)mise en file manuelle |
| `max_retries` | entier | copie de `JOB_MAX_RETRIES` à la mise en file |
| `not_before` | horodatage, nullable | relance différée (backoff) |
| `lease_owner` | texte, nullable | id du worker détenteur |
| `lease_expires_at` | horodatage, nullable | |
| `cancel_requested_at` | horodatage, nullable | annulation coopérative d'un `RUNNING` |
| `error_code`, `error_message` | texte, nullables | dernière erreur |
| `needs_diagnosis` | booléen, défaut faux | vrai si l'échec vient d'une erreur **non classée** (D8) : jamais relancée automatiquement |
| `protected` | booléen, défaut faux | exclut de la rétention automatique |
| `deleting_at` | horodatage, nullable | suppression en cours (masquée des listes) |
| `retry_of` | ULID, nullable | non utilisé au MVP (la relance réutilise la ligne) ; réservé à « dupliquer » |

Index : `(status, not_before, created_at)` pour la prise ; `(finished_at)` pour la rétention ;
`(lease_expires_at)` pour la reprise.

Durée affichée : `finished_at − started_at` (temps mur, relances comprises) ; la durée de chaque
tentative est dans `attempt`.

### `attempt` — historique des exécutions

| Colonne | Type | Notes |
|---|---|---|
| `id` | ULID, PK | |
| `generation_id` | FK → `generation` (`ON DELETE CASCADE`) | |
| `number` | entier | 1, 2, … sur toute la vie de la génération (jamais remis à zéro) |
| `trigger` | enum | `INITIAL` \| `AUTO_RETRY` \| `MANUAL_RETRY` \| `RECOVERY` \| `RESUME` (après arrêt propre du worker) |
| `worker_id` | texte | |
| `started_at`, `finished_at` | horodatages | |
| `outcome` | enum, nullable | `SUCCEEDED` \| `FAILED` \| `ABANDONED` \| `INTERRUPTED` \| `CANCELLED` |
| `error_code`, `error_message`, `retryable` | | |
| `stage_durations_ms` | JSON | durée réelle de chaque étape |
| `peak_vram_bytes` | entier, nullable | `torch.cuda.max_memory_allocated` |
| `free_vram_at_start_bytes` | entier, nullable | VRAM libre lue par NVML à la prise (contrôle à l'admission) |
| `error_trace` | texte, nullable | trace complète, conservée pour diagnostic |
| `log_path` | texte | relatif à `/data/logs` |

### `artifact` — fichiers produits

| Colonne | Type | Notes |
|---|---|---|
| `id` | ULID, PK | |
| `generation_id` | FK (`CASCADE`) | |
| `kind` | enum | `SPLAT_PLY` \| `PREVIEW_PNG` \| `CAMERA_JSON` \| (P4) `EXPORT_*` |
| `relative_path` | texte | relatif à `/data/results`, construit par le serveur |
| `mime`, `size_bytes`, `sha256` | | |
| `metadata` | JSON | ex. `{"gaussians": 262144}` |
| `created_at` | | |

### `worker` — état des processus d'inférence

| Colonne | Type | Notes |
|---|---|---|
| `id` | texte, PK | `hostname:pid:démarrage` |
| `state` | enum | `NO_GPU` \| `IDLE` (pas de processus d'inférence) \| `LOADING` \| `READY` \| `BUSY` \| `WAITING_GPU` \| `DEGRADED` \| `STOPPING` |
| `waiting_reason` | texte, nullable | `VRAM_INSUFFICIENT` \| `DISPATCH_PAUSED` |
| `gpu_free_bytes` | entier | dernière VRAM libre lue par NVML |
| `inference_pid`, `inference_started_at` | | processus d'inférence courant |
| `child_deaths_window` | JSON | horodatages des morts récentes (seuil `DEGRADED`) |
| `adapters` | JSON | `[{id, version, available, reason}]` |
| `gpu` | JSON | nom, capacité, VRAM totale/libre, pilote, CUDA |
| `current_generation_id` | ULID, nullable | |
| `started_at`, `last_heartbeat_at` | | worker considéré mort après 3 × `JOB_LEASE_SECONDS` sans heartbeat |

Les lignes de workers morts depuis plus de 7 jours sont purgées par le `janitor`.

### `system_state` — une seule ligne

| Colonne | Type | Notes |
|---|---|---|
| `id` | entier, PK = 1 | contrainte `CHECK (id = 1)` |
| `dispatch_paused` | booléen | aucune nouvelle prise tant que vrai |
| `paused_reason` | texte, nullable | |
| `paused_by` | enum, nullable | `user` \| `config` (`DISPATCH_PAUSED`) \| `system` (worker `DEGRADED`) |
| `paused_at` | horodatage, nullable | |
| `updated_at` | horodatage | |

## 2. Statuts

| Statut | Sens |
|---|---|
| `PENDING` | ligne créée, upload en cours de finalisation (déplacement des fichiers, normalisation). État court. |
| `QUEUED` | prête ; attend un worker, un créneau GPU, de la VRAM ou son `not_before`. |
| `RUNNING` | détenue par un worker sous bail valide. |
| `COMPLETED` | tentative réussie, artefacts promus. **Terminal.** |
| `FAILED` | erreur déterministe, ou budget de relances épuisé. Terminal, relançable à la main. |
| `CANCELLED` | annulée par l'utilisateur. Terminal, relançable à la main. |

Le motif d'attente d'un `QUEUED` (« en attente de VRAM », « aucun worker pour ce modèle »,
« relance dans 45 s ») est **dérivé** à la lecture (workers + `not_before`), pas stocké.

## 3. Transitions

```
            ┌────────────── échec d'ingestion ──────────────────────────┐
            │                                                            ▼
 [créée] → PENDING ──► QUEUED ──claim──► RUNNING ──succès──────────► COMPLETED
            │            ▲  │              │  │
            │            │  │              │  ├─ erreur déterministe ─────► FAILED ◄─┐
            │            │  │              │  ├─ transitoire, budget épuisé ─► FAILED │
            │            │  │              │  ├─ transitoire, budget restant ─┐       │
            │            │  │              │  │                               │       │
            │            │  │              │  ├─ bail expiré (janitor) ───────┤       │
            │            │  │              │  ├─ arrêt du worker (SIGTERM) ───┤       │
            │            │  │              │  │                               ▼       │
            │            └──┼──────────────┼──┼──────────────────────────── QUEUED    │
            │               │              │  └─ annulation coopérative ─► CANCELLED │
            │               └─ annulation ─┼─────────────────────────────► CANCELLED │
            └── annulation ────────────────┴─────────────────────────────► CANCELLED │
                                                                                     │
 FAILED / CANCELLED ── relance manuelle ──► QUEUED (budget remis à 0) ───────────────┘
```

| De → vers | Déclencheur | Effets |
|---|---|---|
| ∅ → `PENDING` | `POST /generations` | ligne + fichiers en `tmp/` |
| `PENDING` → `QUEUED` | fichiers promus dans `sources/` | `queued_at`, `attempt_count=0` |
| `PENDING` → `FAILED` | promotion impossible | `INGEST_FAILED` |
| `PENDING` → `QUEUED`/`FAILED` | `janitor` : `PENDING` depuis > 5 min | `QUEUED` si les fichiers sont complets, `FAILED` sinon |
| `QUEUED` → `RUNNING` | prise atomique (§architecture 4.2) | bail, `attempt` créée, `attempt_count+1`, `started_at` si null |
| `RUNNING` → `RUNNING` | heartbeat | prolonge le bail, met à jour la progression |
| `RUNNING` → `COMPLETED` | succès | artefacts promus **puis** statut, dans cet ordre ; `finished_at` |
| `RUNNING` → `QUEUED` | erreur transitoire et `attempt_count ≤ max_retries` | `not_before = now + backoff(attempt_count)` ; bail effacé |
| `RUNNING` → `FAILED` | erreur déterministe, ou transitoire hors budget | `error_*`, `finished_at` |
| `RUNNING` → `QUEUED` | bail expiré, constaté par le `janitor` | tentative `ABANDONED`, compte dans le budget ; `FAILED` si épuisé |
| `RUNNING` → `QUEUED` | arrêt propre du worker | tentative `INTERRUPTED`, **ne compte pas** dans le budget |
| `QUEUED` → `FAILED` | attente GPU > `GPU_WAIT_TIMEOUT_SECONDS` (si > 0) | `GPU_UNAVAILABLE`, pas de relance automatique |
| `RUNNING` → `CANCELLED` | `cancel_requested_at` vu par le crochet de pas | tentative `CANCELLED`, sorties temporaires supprimées |
| `PENDING`/`QUEUED` → `CANCELLED` | `POST /cancel` | immédiat |
| `FAILED`/`CANCELLED` → `QUEUED` | `POST /retry` | `attempt_count=0`, erreurs effacées, nouvelle tentative `MANUAL_RETRY` à la prise |

Toute autre transition est refusée par le domaine (exception `InvalidTransition`, HTTP 409 côté
API). Chaque transition est une **mise à jour conditionnelle** (`UPDATE … WHERE id=? AND
status=?`) : si aucune ligne ne change, quelqu'un d'autre a gagné la course et l'appelant le sait.

Un worker qui termine une tentative dont il n'est plus le détenteur du bail (bail expiré puis
repris) voit sa mise à jour refusée et **jette ses sorties** : un résultat ne peut pas être promu
deux fois.

## 4. Classification des erreurs

| Code | Origine | `retryable` |
|---|---|---|
| `INPUT_INVALID` | image illisible après coup | non |
| `NO_OBJECT_DETECTED` | Idol `ValueError('no object found…')` | non |
| `PARAMS_INVALID` | paramètres refusés par l'adaptateur | non |
| `MODEL_UNAVAILABLE` | poids absents au chargement | non |
| `INGEST_FAILED` | fichiers source incomplets | non |
| `GPU_OOM` | `torch.cuda.OutOfMemoryError` | **oui** |
| `CUDA_ERROR` | autre erreur CUDA (le worker sort ensuite en code 75) | **oui** |
| `WORKER_LOST` | bail expiré | **oui** |
| `TIMEOUT` | `JOB_TIMEOUT_SECONDS` dépassé | **oui** |
| `WORKER_CRASH` | mort du processus d'inférence (signal, code ≠ 0) | **oui** |
| `GPU_UNAVAILABLE` | attente GPU au-delà de `GPU_WAIT_TIMEOUT_SECONDS` | non |
| `COMPILE_ERROR` | échec de `torch.compile` en mode `full` | non — défaut de configuration, à corriger par `IDOL_EXEC_MODE` |
| `INTERNAL` | exception **non classée** | **non** (D8) : `needs_diagnosis = vrai`, trace conservée, relance manuelle seulement |

`INSUFFICIENT_VRAM_BUDGET` n'apparaît jamais ici : il est refusé dès la soumission. Hephaistos ne
cède jamais le GPU en cours de génération (ADR-0011) : seule une annulation explicite arrête une
génération démarrée.

Une génération n'est **jamais** relancée automatiquement pour un code non relançable.

## 5. Règles de suppression

| Opération | Condition | Procédure |
|---|---|---|
| Suppression manuelle | statut ≠ `RUNNING` (annuler d'abord) ; autorisée même si `protected` (geste explicite) | `deleting_at` → fichiers → lignes |
| Rétention automatique | statut terminal, `protected = faux`, `finished_at < now − STORAGE_RETENTION_DAYS` | idem |
| Reprise | `deleting_at` non nul | le `janitor` rejoue la procédure (idempotente) |
| Orphelins | répertoire sans ligne, plus vieux que 24 h | journalisé puis supprimé |
| `tmp/` | fichier plus vieux que 24 h et non référencé | supprimé |
