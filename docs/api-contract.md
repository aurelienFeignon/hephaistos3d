# Contrat d'API — v1

> Statut : validé, mis à jour le 2026-11-02 (suspension) et le 2026-11-03 (partage du GPU sans préemption, ADR-0011). La source de vérité sera l'OpenAPI généré par FastAPI à partir des schémas
> Pydantic ; ce document fixe l'intention et sert de référence de revue.

- Préfixe : `/api/v1`. Une rupture de contrat ouvre `/api/v2`, jamais une modification silencieuse.
- JSON en `snake_case` ; dates ISO 8601 UTC ; identifiants ULID.
- Erreurs : RFC 9457 `application/problem+json` — `type`, `title`, `status`, `detail`, plus `code`
  (stable, lisible par le frontend) et `errors` (détail de validation).
- Pagination par curseur : `?limit=` (1–100, défaut 50) et `?cursor=` ; réponse `{items, next_cursor}`.
- Toutes les routes passent par `current_principal` (principal unique au MVP), avec un jeton
  `Authorization: Bearer <API_ACCESS_TOKEN>` obligatoire, sauf GET `/system/health`.
  Docs et OpenAPI sont également protégés. Host est filtré ; les mutations vérifient Origin.
- Les corps non multipart sont bornés à 64 Kio et leur réception à 30 s par défaut. Rejets
  problem+json : 400 `HOST_NOT_ALLOWED`/`INVALID_CONTENT_LENGTH`, 401 `AUTHENTICATION_REQUIRED`,
  403 `ORIGIN_NOT_ALLOWED`, 408 `REQUEST_TIMEOUT`, 413 `REQUEST_TOO_LARGE`/`UPLOAD_TOO_LARGE`.
  Les réponses portent `Cache-Control: private, no-store` et `X-Content-Type-Options: nosniff`.
- Changements de durcissement du 2026-11-05, avant tout consommateur P3 : `/queue` est paginée,
  les tentatives sont bornées et `/source` sert le PNG normalisé par défaut. Les clients doivent
  suivre les curseurs et utiliser `?original=true` pour télécharger l'original.
- OpenAPI : `/api/v1/openapi.json` ; types TypeScript générés par `openapi-typescript`.

## 1. Système

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/system/health` | `200 {"status":"ok"}` si la base répond. Sert au healthcheck. |
| GET | `/system/status` | état agrégé (ci-dessous) |
| GET | `/system/dispatch` | état de la distribution |
| PUT | `/system/dispatch` | `{"paused": bool, "reason": str?}` — suspend ou reprend ; la génération en cours va toujours à son terme |

`GET` et `PUT /system/dispatch` renvoient le même objet (200) :

```json
{"paused": true, "paused_by": "user", "reason": "maintenance", "since": "2026-01-01T12:00:00Z"}
```

- `paused` est un booléen strict ; `reason` est facultatif ou null, limité à 1 000 caractères,
  et débarrassé des espaces en début/fin. Une raison vide devient null. Les champs inconnus
  sont refusés (422 `VALIDATION_ERROR`, `application/problem+json`).
- Une pause manuelle enregistre `paused_by=user`. À la reprise, `paused_by`, `reason` et `since`
  redeviennent null ; une raison fournie avec `paused=false` est ignorée. Répéter la même
  commande ne change pas sa date.
- L'état est persisté. `DISPATCH_PAUSED` initialise la ligne seulement si elle n'existe pas :
  une reprise manuelle survit donc à un redémarrage, même si cette variable reste vraie.
- Une suspension ne modifie ni les baux ni les jobs en cours. Une reprise explicite permet aussi
  aux superviseurs `DEGRADED` de remettre à zéro leur fenêtre de morts et de reprendre les prises.
  Une nouvelle panne peut de nouveau suspendre la distribution.

Implémenté en P2.6b puis P2.8 : factory `hephaistos.api.app:create_app`, routes système,
modèles, consultation et actions sur les générations, file, sources, tentatives, lecture des
journaux et artefacts. Toutes utilisent `current_principal` (principal unique local), des
réponses typées OpenAPI et des erreurs problem+json. Aucune migration automatique au démarrage.
`POST /generations` est implémenté en P2.9 (upload borné) ; les exports restent réservés à P4.

Précisions de lecture (P2.8) :

- Les workers dont le heartbeat date d'au moins `JOB_LEASE_SECONDS`, ainsi que les workers
  `STOPPING`, sont exclus de l'état actif. `NO_GPU` et `DEGRADED` ne rendent pas un modèle disponible.
  La disponibilité vient des heartbeats, sans contrôle local des poids ni import de CUDA.
  Sans mesure récente, la VRAM libre est `null`. Les métadonnées GPU non publiées sont `null`.
- La liste des modèles et les listes d'artefacts/tentatives sont des tableaux ; les presets sont
  un objet indexé par identifiant contenant `{label, values}`. `/queue` retourne `{items, next_cursor}` avec
  la représentation Generation et `position` (1 pour la première génération en attente, null
  pour RUNNING), triée par `created_at`, puis `id`, croissants.
- La pagination des générations utilise `(created_at, id)` décroissant. Le curseur opaque reste
  utilisable après suppression de sa génération d'ancrage. Les filtres s'appliquent à chaque page.
- Priorité des motifs d'attente : suspension, délai de relance, absence de worker disponible,
  VRAM libre insuffisante. Un job prêt à être pris peut avoir un motif null.
- Les générations marquées pour suppression sont invisibles, y compris leurs artefacts.
  Les représentations Generation/Attempt n'exposent ni chemin interne ni trace d'exception.
  Le journal de diagnostic `/logs`, protégé par le même principal, peut contenir la trace complète.
- `/logs` exige `attempt` ≥ 1 ; `tail` vaut 500 par défaut (1–10 000). La réponse est un tableau
  d'événements JSON, extraits du dernier Mio au maximum (`LOGS_TAIL_MAX_BYTES`). Les lignes
  tronquées ou invalides sont ignorées ; la réponse peut donc contenir moins de `tail` événements.
  L'écriture est implémentée en P2.10.
- Les fichiers sont servis à partir de noms fixes reconstruits par le serveur ; les liens
  symboliques sont refusés. `ETag` est le SHA-256 entre guillemets. Le téléchargement supporte Range.

```json
{
  "version": "0.1.0",
  "database": {"ok": true},
  "queue": {"queued": 2, "running": 1, "gpu_slots": 1},
  "dispatch": {"paused": false, "paused_by": null, "reason": null, "since": null},
  "gpu": {"free_vram_bytes": 11500000000, "min_free_vram_bytes": 8589934592, "vram_limit_bytes": null,
          "waiting_reason": null},
  "workers": [{
    "id": "hote:17:2026-11-02T18:00:00Z", "state": "BUSY", "waiting_reason": null, "last_heartbeat_at": "…",
    "gpu": {"name": "NVIDIA GeForce RTX 5070", "compute_capability": "12.0",
            "vram_total_bytes": 12821987328, "vram_free_bytes": 4120000000},
    "adapters": [{"id": "idol", "available": true}]
  }],
  "storage": {"results_bytes": 1830000000, "free_bytes": 367000000000}
}
```

`storage.results_bytes` est la somme SQL des tailles des artefacts référencés ; les fichiers
orphelins ne sont pas inclus. `free_bytes` est le minimum libre sur les volumes persistants.

## 2. Modèles

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/models` | liste des adaptateurs déclarés, avec disponibilité agrégée des workers |
| GET | `/models/{model_id}` | descripteur complet |
| GET | `/models/{model_id}/schema` | JSON Schema des paramètres (+ `x-ui`) |
| GET | `/models/{model_id}/presets` | préréglages et leurs valeurs |

```json
{
  "id": "idol", "name": "Idol", "version": "adapter 0.1.0 · sdk 4702a7f · weights <rev>",
  "available": true, "unavailable_reason": null,
  "capabilities": ["alpha_input", "seeded", "step_progress", "cancellable"],
  "input_formats": ["image/jpeg", "image/png", "image/webp"],
  "output_kinds": ["SPLAT_PLY", "PREVIEW_PNG", "CAMERA_JSON"],
  "gpu": {"vram_required_bytes": 7700000000, "min_compute_capability": "8.0"},
  "default_preset": "balanced"
}
```

## 3. Générations

### `POST /generations` — `multipart/form-data`

| Champ | Type | Obligatoire |
|---|---|---|
| `image` | fichier | oui |
| `model_id` | texte | oui |
| `preset` | texte | non (défaut du modèle) |
| `params` | texte JSON | non — surcharges, validées contre le schéma |
| `seed` | entier ou `"random"` | non (défaut `"random"`) |

Validation serveur, dans l'ordre : modèle connu → taille lue en flux ≤ `UPLOAD_MAX_BYTES` (sinon
**413**) → décodage Pillow, format dans la liste blanche (sinon **415**) → pixels ≤
`UPLOAD_MAX_PIXELS` (sinon **422**) → paramètres effectifs valides (sinon **422** avec `errors`) →
besoin VRAM des paramètres ≤ `GPU_VRAM_LIMIT_MB` si un plafond est fixé, sinon ≤ VRAM totale (sinon
**422** `INSUFFICIENT_VRAM_BUDGET` : la génération ne pourrait jamais tourner, elle n'est pas mise en
file).

Une génération acceptée alors que la VRAM libre est sous `GPU_MIN_FREE_VRAM_MB` ou que la
distribution est suspendue attend en `QUEUED` avec son `waiting_reason` (`VRAM_INSUFFICIENT`,
`DISPATCH_PAUSED`, `NO_WORKER`, `RETRY_BACKOFF`).

Réponse **201** + `Location`, corps `Generation`. Un modèle indisponible n'empêche pas la création :
la génération attend en `QUEUED` avec le motif « aucun worker pour ce modèle ».

Admission de sécurité (avant lecture du multipart) : réservation partagée SQLite, par défaut un
upload simultané et 100 générations actives/réservées. Les relances manuelles vérifient aussi
la capacité sous le même verrou que leur transition. Saturation : 429 `UPLOAD_CAPACITY_EXCEEDED`
ou `QUEUE_CAPACITY_EXCEEDED`. Budget disque insuffisant : 507 `STORAGE_QUOTA_EXCEEDED` ou
`STORAGE_SPACE_INSUFFICIENT`. Réception trop lente : 408, avec libération de la réservation.
Les détails du budget et de ses limites figurent dans [deployment.md](deployment.md).

Précisions d'implémentation P2.9 :

- Le flux multipart est borné avant stockage du fichier par le parseur, sans dépendre de
  `Content-Length`. Le fichier accepte exactement `UPLOAD_MAX_BYTES` ; le premier dépassement
  interrompt la lecture (413). Le budget du corps entier comprend 256 Kio supplémentaires pour
  l'enveloppe multipart. Les champs texte sont limités à 16 Kio chacun, les doublons et champs
  inconnus refusés (422). La borne de transport peut interrompre le flux avant la validation du
  modèle si ce champ arrive après le fichier. Le principal est vérifié avant consommation du corps.
- `seed` vaut `random` ou un entier entre 0 et 2 147 483 647 inclus. La valeur aléatoire tirée
  par le serveur et tous les paramètres effectifs sont persistés. Le preset par défaut est
  `balanced` si présent, sinon le premier preset déclaré. Les erreurs de paramètres incluent `errors`.
- Le format décodé, et non le nom ni le MIME déclaré, détermine le fichier original conservé.
  `input.png` applique EXIF et retire les métadonnées ; les dimensions exposées sont celles
  de cette image normalisée. Le nom fourni reste une métadonnée d'affichage, réduite au nom
  final sans chemin ni caractères de contrôle. Le SHA-256 porte sur l'original exact reçu.
- Le budget VRAM utilise le plafond configuré et, si disponible, le plus grand total publié
  dans les heartbeats persistés (y compris un worker arrêté). Le budget connu le plus contraignant
  s'applique. Sans plafond ni total connu, la soumission est acceptée en attente : l'API ne sonde
  pas le GPU. La VRAM libre n'est jamais utilisée pour refuser définitivement une soumission.
- Après validation, la ligne `PENDING` est persistée, puis promotion et transition `QUEUED`
  sont sérialisées sous `BEGIN IMMEDIATE`, avec contrôle du statut et de `deleting_at` avant
  toute promotion. Une annulation/suppression concurrente provoque 409 sans remise en file.
- Si la promotion échoue, la ressource créée est retournée en 201 avec `status=FAILED`,
  `INGEST_FAILED`, `needs_diagnosis=true` et `queued_at=null`. Les détails disque ne sont pas
  exposés. Cette erreur ne peut pas être relancée via `/retry` (409) car l'entrée d'inférence
  n'a pas été publiée : il faut soumettre à nouveau l'image. Une erreur disque avant création
  de la ligne retourne 503 `INGEST_FAILED`, sans ressource créée.
- Les rejets nettoient leurs temporaires ; un arrêt ou échec SQL après création de `PENDING`
  conserve les fichiers nécessaires à la reprise du janitor (P2.7).


### Ressource `Generation`

```json
{
  "id": "01JC…", "model_id": "idol", "model_version": "…",
  "status": "RUNNING",
  "waiting_reason": null,
  "preset": "balanced",
  "params": {"mode": "synthetic", "steps": 50, "cfg_strength": 3.0, "rescale_t": 3.0,
             "num_gaussians": 262144, "crop_margin": null},
  "seed": 1834201,
  "source": {"filename": "chaise.jpg", "mime": "image/jpeg", "width": 3024, "height": 4032,
             "has_alpha": false, "url": "/api/v1/generations/01JC…/source",
             "normalized_mime": "image/png", "original_url": "/api/v1/generations/01JC…/source?original=true"},
  "progress": {"stage": "sampling", "current": 23, "total": 50, "updated_at": "…"},
  "created_at": "…", "queued_at": "…", "started_at": "…", "finished_at": null,
  "elapsed_ms": 18450,
  "attempt_count": 1, "max_retries": 3, "next_attempt_at": null,
  "error": null,
  "protected": false,
  "artifacts": []
}
```

En cas d'échec, `error` vaut `{"code", "message", "retryable", "needs_diagnosis"}`.

### Routes

| Méthode | Route | Effet | Codes |
|---|---|---|---|
| GET | `/generations` | liste ; filtres `status` (multiple), `protected`, `model_id` ; tri `created_at` desc | 200 |
| GET | `/generations/{id}` | détail | 200, 404 |
| PATCH | `/generations/{id}` | `{"protected": bool}` — seul champ modifiable | 200, 404 |
| POST | `/generations/{id}/cancel` | `PENDING`/`QUEUED` → `CANCELLED` ; `RUNNING` → demande coopérative (**202**) | 200, 202, 409 |
| POST | `/generations/{id}/retry` | `FAILED`/`CANCELLED` → `QUEUED`, budget remis à zéro | 200, 409 |
| DELETE | `/generations/{id}` | suppression (refusée en `RUNNING`) | 204, 409 |
| GET | `/generations/{id}/source` | PNG normalisé sans métadonnées, inline ; `?original=true` : original en pièce jointe | 200, 404 |
| GET | `/generations/{id}/attempts` | historique borné : `limit=50` (1–100), `after=0` | 200 |
| GET | `/generations/{id}/logs?attempt=N&tail=500` | lignes JSON du journal d'une tentative | 200, 404 |
| GET | `/generations/{id}/artifacts` | liste des artefacts | 200 |

Le champ `source.mime` et son SHA-256 décrivent l'original conservé ; `source.url` et
`normalized_mime` décrivent la version normalisée affichable. L'original peut contenir des
métadonnées sensibles et n'est téléchargé que sur demande explicite. Pour les tentatives,
l'en-tête `X-Next-Attempt` indique la valeur de `after` de la page suivante lorsqu'elle existe.

## 4. File

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/queue` | générations `QUEUED` et `RUNNING`, `?limit=50&cursor=…`, `{items, next_cursor}` |

La file est triée par `(created_at, id)` croissant ; `position` reste globale entre les pages.
Les lectures de listes chargent workers et artefacts par lot, sans requête par génération.

Les « jobs » de la demande initiale correspondent ici aux **tentatives** (`/generations/{id}/attempts`)
et à la **file** (`/queue`) : une génération porte son job, il n'existe pas de ressource job séparée
qui pourrait diverger d'elle.

## 5. Artefacts et exports

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/artifacts/{artifact_id}` | métadonnées |
| GET | `/artifacts/{artifact_id}/download` | fichier ; `Content-Disposition: attachment; filename="hephaistos-<gen>-<kind>.<ext>"` ; support `Range` ; `ETag` = sha256 |
| GET | `/generations/{id}/exports` | (P4) formats disponibles et leur statut `experimental` |
| POST | `/generations/{id}/exports` | (P4) `{"format": "spz"}` → création asynchrone d'un artefact `EXPORT_*` |

Le téléchargement se fait **par identifiant d'artefact** ; aucun paramètre de requête ne désigne
un chemin. Le nom proposé au navigateur est construit par le serveur.

## 6. Mise à jour côté client

Interrogation périodique de `GET /generations?status=QUEUED&status=RUNNING` et `GET /queue` :
toutes les **2 s** tant qu'il existe une génération active, **15 s** sinon ; suspendue quand
l'onglet est masqué. SSE (`/events`) est réservé à une version ultérieure si la charge le justifie
(ADR-0008).

### Journaux de tentative (P2.10)

Chaque ligne JSONL contient `timestamp` (UTC), `level`, `service`, `event`, `generation_id`,
`attempt_id`, `attempt` (numéro), `worker_id`, `model_id` et `fields` (données de l'événement).
Les événements comprennent `attempt_started`, `model_loading`, `model_loaded`, `stage`, `step`,
`adapter_log`, `attempt_finished`, `attempt_released`, `lease_lost` et `attempt_recovered`.
Les champs d'un adaptateur restent sous `fields` et ne peuvent remplacer les identifiants.

- `attempt_finished.fields.outcome` reflète la tentative persistée (`SUCCEEDED`, `FAILED`,
  `CANCELLED`, `INTERRUPTED`, etc.), avec code/message, caractère relançable, trace éventuelle
  et métriques. Un succès d'inférence annulé avant publication est donc journalisé CANCELLED.
- Le janitor écrit `attempt_recovered` dans le journal de la tentative abandonnée/annulée,
  même si un autre worker a déjà pris la génération pour une nouvelle tentative.
- Les fichiers sont ouverts en ajout puis fermés à chaque événement. Les journaux précédents
  sont conservés après relance. Les événements d'une ancienne tentative sont ignorés, et les
  derniers événements sont drainés avant journalisation de l'issue.
- La journalisation sur disque est best effort : un échec d'écriture/symlink est signalé dans
  les logs du service sans changer le statut de la génération. La suppression reste effectuée
  avec la génération ; aucun mécanisme de rotation supplémentaire n'est introduit en P2.10.

## 7. Codes d'erreur stables (`code`)

`MODEL_NOT_FOUND`, `UPLOAD_TOO_LARGE`, `UNSUPPORTED_MEDIA_TYPE`, `IMAGE_TOO_LARGE`,
`IMAGE_UNREADABLE`, `PARAMS_INVALID`, `INSUFFICIENT_VRAM_BUDGET`, `GENERATION_NOT_FOUND`, `INVALID_TRANSITION`,
`GENERATION_RUNNING`, `ARTIFACT_NOT_FOUND`, `ATTEMPT_NOT_FOUND`, `INGEST_FAILED`, `VALIDATION_ERROR`.
