# Contrat d'API — v1

> Statut : validé, mis à jour le 2026-11-02 (suspension) et le 2026-11-03 (partage du GPU sans préemption, ADR-0011). La source de vérité sera l'OpenAPI généré par FastAPI à partir des schémas
> Pydantic ; ce document fixe l'intention et sert de référence de revue.

- Préfixe : `/api/v1`. Une rupture de contrat ouvre `/api/v2`, jamais une modification silencieuse.
- JSON en `snake_case` ; dates ISO 8601 UTC ; identifiants ULID.
- Erreurs : RFC 9457 `application/problem+json` — `type`, `title`, `status`, `detail`, plus `code`
  (stable, lisible par le frontend) et `errors` (détail de validation).
- Pagination par curseur : `?limit=` (1–100, défaut 50) et `?cursor=` ; réponse `{items, next_cursor}`.
- Toutes les routes passent par la dépendance `current_principal` (principal unique au MVP).
- OpenAPI : `/api/v1/openapi.json` ; types TypeScript générés par `openapi-typescript`.

## 1. Système

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/system/health` | `200 {"status":"ok"}` si la base répond. Sert au healthcheck. |
| GET | `/system/status` | état agrégé (ci-dessous) |
| GET | `/system/dispatch` | état de la distribution |
| PUT | `/system/dispatch` | `{"paused": bool, "reason": str?}` — suspend ou reprend ; la génération en cours va toujours à son terme |

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
             "has_alpha": false, "url": "/api/v1/generations/01JC…/source"},
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
| GET | `/generations/{id}/source` | image d'origine (`Content-Disposition: inline`) | 200, 404 |
| GET | `/generations/{id}/attempts` | historique des tentatives | 200 |
| GET | `/generations/{id}/logs?attempt=N&tail=500` | lignes JSON du journal d'une tentative | 200, 404 |
| GET | `/generations/{id}/artifacts` | liste des artefacts | 200 |

## 4. File

| Méthode | Route | Réponse |
|---|---|---|
| GET | `/queue` | générations `QUEUED` et `RUNNING`, ordre de prise, position, motif d'attente |

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

## 7. Codes d'erreur stables (`code`)

`MODEL_NOT_FOUND`, `UPLOAD_TOO_LARGE`, `UNSUPPORTED_MEDIA_TYPE`, `IMAGE_TOO_LARGE`,
`IMAGE_UNREADABLE`, `PARAMS_INVALID`, `INSUFFICIENT_VRAM_BUDGET`, `GENERATION_NOT_FOUND`, `INVALID_TRANSITION`,
`GENERATION_RUNNING`, `ARTIFACT_NOT_FOUND`, `ATTEMPT_NOT_FOUND`.
