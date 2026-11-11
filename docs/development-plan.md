# Plan de développement — Hephaistos 3D

> Statut : validé le 2026-11-02 avec ajustements. **P0 : ✅ validé le 2026-11-03 (`docs/gates/P0.md`). P1 : ✅ validé le 2026-11-03 (`docs/gates/P1.md`). P2 : non commencée.** Une phase n'est déclarée terminée que
> lorsque chacun de ses critères d'acceptation est démontré, preuve à l'appui (sortie de commande,
> mesure, capture), et consignée dans `docs/gates/Pn.md`.

## 1. Règles transverses

- Branches courtes, aucun commit ni push sans demande explicite.
- Aucun téléchargement de poids, aucun déploiement, aucune modification de l'hôte sans accord.
- Outils de scaffolding officiels (§3) ; la commande exacte est notée dans le commit qui l'emploie.
- Les fichiers générés par un outil ne sont modifiés que lorsque c'est nécessaire, et la
  modification est signalée.

## 2. Décisions

| # | Question | Décision (2026-11-02) |
|---|---|---|
| D1 | File d'attente | ✅ SQLite, file transactionnelle, baux, réservation atomique, reprise des abandons |
| D2 | Partage du GPU | ✅ révisée le 2026-11-03 : **pas de préemption ; contrôle à l'admission** (VRAM libre ≥ `GPU_MIN_FREE_VRAM_MB`, plafond optionnel `GPU_VRAM_LIMIT_MB`) — ADR-0011. Une génération démarrée va à son terme, sauf annulation |
| D3 | Accès et poids | ✅ préparer les demandes ; ⛔ ne rien télécharger avant accès confirmés **et** espace vérifié |
| D4 | Emplacement | ✅ `/opt/hephaistos3d/`, données dans `var/` |
| D5 | Exposition | ✅ port 3030, `127.0.0.1` d'abord, LAN plus tard |
| D6 | Dépôt | ✅ `git init` local ; ⛔ aucun commit ni push sans demande |
| D7 | Licences | ✅ usage personnel non commercial |
| D8 | Relances | ✅ erreurs transitoires identifiées seulement ; l'inconnu est diagnostiqué avant toute relance |
| D9 | Coordination explicite avec les autres charges (verrou partagé) | ❌ **retirée le 2026-11-03** : sans objet, Hephaistos ne cède pas le GPU ; pas de préemption, contrôle à l'admission (D2) |
| D10 | Fenêtre des tests GPU | ✅ aucune autre charge lourde sur le GPU pendant les tests, hormis la charge simulée de G0.10 |
| D11 | Comportement du worker démarré mais inactif | ✅ **option B** : processus d'inférence terminé après inactivité (0 octet au repos) |
| D12 | Démarrage du worker | ✅ profil Compose **`gpu`** : `docker compose up` ne touche pas au GPU ; `docker compose stop worker` avant une autre charge lourde |

## 3. Scaffolding — commandes réellement exécutées le 2026-11-02

```bash
cd /opt/hephaistos3d
git init -b main

uv init backend --package --python 3.12 --name hephaistos
cd backend
uv add fastapi "uvicorn[standard]" pydantic pydantic-settings sqlalchemy alembic \
       python-multipart pillow python-ulid structlog nvidia-ml-py plyfile numpy
uv add --dev ruff mypy pytest pytest-asyncio httpx
uv run alembic init alembic
# index PyTorch cu132 déclaré dans [tool.uv.index] / [tool.uv.sources] de pyproject.toml, puis :
uv add --no-sync --optional idol "torch>=2.14.1,<2.15" "torchvision>=0.29.1,<0.30" \
       "idol @ git+https://github.com/speridlabs/idol-sdk@4702a7f00850e34b6e83a2170cc123c3e9570744"
cd ..

npx create-next-app@16.4.4 frontend --typescript --tailwind --eslint --app --src-dir \
    --use-npm --import-alias "@/*" --disable-git --no-agent-feedback --yes
cd frontend
npm install three@0.187.0 @sparkjsdev/spark@2.4.0 zod
npm install -D @types/node@^24 vitest @vitejs/plugin-react jsdom @testing-library/react \
    @testing-library/user-event @types/three openapi-typescript @playwright/test
```

Écarts au plan initial, et pourquoi :

- **torch 2.14.1 + torchvision 0.29.2, CUDA 13.2** au lieu de torch 2.15 : aucun torchvision n'est
  encore publié pour 2.15 (vérifié sur les index cu132 et cu134).
- `--no-sync` pour l'extra `idol` : le verrou est résolu, mais torch (plusieurs Go) ne s'installe que
  dans l'image `worker`, jamais dans le venv de développement.
- `@types/node@^24` : le scaffold déclarait `^20`, incompatible avec vitest 5 et décalé du runtime
  Node 24.
- `npx playwright install` **différé** à P3 (téléchargement de navigateurs inutile avant).
- Fichiers écrits à la main, aucun outil ne les produisant : `.gitignore` racine, `.env.example`,
  `compose.yaml`, `deploy/backend.Dockerfile`, configuration ruff/mypy/pytest dans `pyproject.toml`,
  sondes `hephaistos.probes.*`, `hephaistos.gpu.nvml`.

---

## P0 — Fondation et faisabilité

**Objectif** : prouver qu'une inférence Idol réelle tourne sur une RTX 5070 12 Go (sm_120), dans un
conteneur, avec un jeu de dépendances figé, **en rendant toute sa VRAM quand elle a fini**.

**Prérequis restants** : accès DINOv3 et FLUX.2-dev accordés ; espace disque vérifié ; votre accord
pour télécharger les poids puis pour lancer les tests GPU, sans autre charge lourde sur la carte (D10).

| Tâche | Contenu | État au 2026-11-02 |
|---|---|---|
| P0.1 | Scaffolding, `.gitignore`, `.env.example`, `var/` | ✅ fait (non commité) |
| P0.2 | `deploy/backend.Dockerfile` cibles `api` / `worker` | ✅ écrit ; construction : voir `gates/P0.md` |
| P0.3 | `compose.yaml` : `worker-probe`, profil `probe`, sans réseau | ✅ écrit, `config` valide, rien démarré |
| P0.4 | Procédure des poids (`install-models.md`) | ✅ écrite ; ⏳ accès non accordés |
| P0.5 | Sonde CUDA (`hephaistos.probes.cuda`) | ✅ écrite, ⛔ non exécutée |
| P0.6 | Sonde Idol (`hephaistos.probes.idol`) : enfant `spawn`, hooks, modes, annulation, plafond, équivalence | ✅ écrite, ⛔ non exécutée |
| P0.7 | Charge simulée pour G0.10 (`hephaistos.probes.gpu_load`) | ✅ écrite, ⛔ non exécutée |
| P0.8 | Vérificateur PLY (`hephaistos.probes.ply`) | ✅ écrit, **8 tests verts** |
| P0.9 | Mesures et mise à jour de `idol-feasibility.md` | ⏳ après les tests GPU |

Procédure pas à pas et cases de preuve : `docs/gates/P0.md`.

### Gate P0 — critères d'acceptation

| # | Critère vérifiable |
|---|---|
| G0.1 | Dans `worker-probe` : `torch.cuda.is_available()`, capacité `(12, 0)`, `sm_120` dans `get_arch_list()` |
| G0.2 | 3 images de référence → PLY valides par `probes.ply` : 17 propriétés `float32`, sommets = gaussiennes demandées, 0 NaN/inf, positions dans `[-0.6, 0.6]³` |
| G0.3 | Exécution en `network_mode: none` avec `HF_HUB_OFFLINE=1` : succès |
| G0.4 | Mesurés pour 262 144 et 1 048 576 : chargement, 1re génération (compilation comprise), médiane de 3 à chaud, pic VRAM (`max_memory_allocated` et NVML), surcoût de contexte CUDA |
| G0.5 | `full` fonctionne sur sm_120, **ou** son échec est documenté et `no_compile` puis `eager` sont évalués selon G0.2 ; mode par défaut fixé (ADR-0012) |
| G0.6 | Plusieurs générations successives dans le **même** processus d'inférence, sans rechargement |
| G0.7 | Le PLY `nb-armchair` s'affiche dans une page Spark minimale (capture consignée) |
| G0.8 | `uv.lock` et image `worker` reconstruits depuis zéro avec les mêmes versions |
| **G0.9** | **Libération et annulation** — (a) VRAM rendue à la fin du processus d'inférence : usage du périphérique revenu au niveau de départ ± 100 Mo ; (b) annulation au pas 10 : arrêt avant le pas 12, réglages globaux du SDK restaurés, génération suivante réussie ; (c) neutralité des hooks : écart avec/sans hooks ≤ écart entre deux exécutions sans hooks |
| **G0.10** | **Contrôle à l'admission et plafond** — avec une charge simulée (`gpu_load`, conteneur séparé) qui ramène la VRAM libre sous `GPU_MIN_FREE_VRAM_MB` : la sonde sort en code 3, **aucun processus d'inférence n'est créé** ; avec un `GPU_VRAM_LIMIT_MB` inférieur au besoin : l'OOM se produit **dans** Hephaistos (erreur `GPU_OOM` rapportée), jamais chez la charge simulée |

**Si le Gate échoue** : blocage documenté (commande, trace, versions) et présenté avant toute suite.

**Tests** : `probes.ply` en tests unitaires (faits). Les sondes GPU sont des **tests manuels
identifiés**, joués seulement sur votre accord.

**Risques** : approbation DINOv3 tardive (bloquant) ; transformers 5 / huggingface_hub 2 à l'exécution
(compatibilité statique vérifiée, pas l'exécution) ; `torch.compile` absent sur sm_120 (repli
`no_compile`) ; GPU occupé par une autre charge pendant la fenêtre de mesure (la sonde se retire
d'elle-même, code 3).

---

## P1 — Moteur d'inférence

**Objectif** : interface d'adaptateur, registre, `FakeAdapter`, `IdolAdapter` instrumenté.

**Prérequis** : Gate P0 validé.

| Tâche | Contenu |
|---|---|
| P1.1 | `adapters/base.py` : `ModelAdapter`, `ModelDescriptor`, `RunHooks`, `GenerationInput/Output`, `AdapterError` — sans torch |
| P1.2 | `adapters/registry.py` : adaptateurs déclarés, `check_installation` |
| P1.3 | `FakeAdapter` : étapes, pas temporisés, PLY synthétique valide, erreurs injectables, annulation |
| P1.4 | Normalisation d'image commune (EXIF, conversion, PNG, détection d'alpha) |
| P1.5 | `IdolAdapter.check_installation` (fichiers + cache HF, hors ligne) |
| P1.6 | Processus d'inférence : protocole Pipe, `load`/`generate`/`cancel`/`shutdown`, contrôle après job, sortie 75 sur erreur CUDA |
| P1.7 | `IdolAdapter.generate` instrumenté par hooks de module (ADR-0005), garde de cohérence, annulation |
| P1.8 | `classify_error` : OOM, CUDA, `no object found`, paramètres |
| P1.9 | Modèle de paramètres Pydantic + `x-ui` ; contrôle du sous-ensemble JSON Schema |
| P1.10 | Calibrage des préréglages Rapide/Qualité, ADR de valeurs |
| P1.11 | CLI `hephaistos generate --model idol --image … --preset …` (outil de diagnostic) |

**Critères d'acceptation**

| # | Critère |
|---|---|
| A1.1 | `import hephaistos.adapters.registry` ne charge pas `torch` (vérifié par test sur `sys.modules`) |
| A1.2 | La CLI produit sur GPU, pour les 3 images de référence, des artefacts qui passent `check_ply.py` |
| A1.3 | Les événements de progression couvrent les 5 étapes et `steps` événements de pas, dans l'ordre |
| A1.4 | Une annulation demandée au pas 10 arrête la génération avant le pas 12, sans artefact promu |
| A1.5 | Une image unie (aucun objet) donne `NO_OBJECT_DETECTED`, `retryable=false` |
| A1.6 | Après libération, le processus d'inférence n'existe plus et NVML ne lui attribue plus aucune mémoire |
| A1.7 | Le test de forme échoue si l'un des quatre sous-modules accrochés disparaît ou cesse d'être un `nn.Module` (vérifié par mutation) |
| A1.8 | Préréglages mesurés : Rapide ≤ 60 % du temps d'Équilibré ; pic de Qualité ≤ VRAM totale − 1,5 Go |

**Tests** : unitaires sur le contrat et le registre ; `FakeAdapter` testé comme un adaptateur réel
(suite de conformité partagée, jouée sur les deux) ; tests `@pytest.mark.gpu` pour Idol, exclus par
défaut.

**Risques** : instrumentation fragile (atténué par A1.7 et l'épinglage) ; annulation impossible
pendant l'encodage ou le décodage (non découpables : l'annulation n'y est vue qu'à l'étape
suivante — documenté).

---

## P2 — Backend et orchestration

**Objectif** : API, base, file, workers, relances, stockage, janitor.

**Prérequis** : P1 validé.

| Tâche | Contenu |
|---|---|
| P2.1 | `config.py` : variables, bornes, messages d'erreur |
| P2.2 | Modèles SQLAlchemy + première migration Alembic (tables de `data-model.md`) |
| P2.3 | Domaine : machine à états, transitions conditionnelles, politique de relance |
| P2.4 | Stockage : chemins sûrs, écriture atomique, promotion, suppression en trois temps |
| P2.5 | File : prise atomique avec limite GPU, bail, heartbeat |
| P2.6 | Superviseur : boucle, contrôle à l'admission (VRAM libre, plafond), suspension, cycle de vie du processus d'inférence, signaux, état `DEGRADED` |
| P2.6b | `PUT /system/dispatch`, ligne `system_state`, `DISPATCH_PAUSED` |
| P2.7 | Janitor : baux expirés, `PENDING` orphelins, rétention, suppressions en échec, orphelins disque, `tmp/` |
| P2.8 | API v1 complète (`api-contract.md`), problem+json, OpenAPI |
| P2.9 | Upload : lecture en flux bornée, validation Pillow, plafond de pixels |
| P2.10 | Journaux structurés + journal par tentative |
| P2.11 | Compose : `migrate`, `api`, `worker` (replicas, **profil `gpu`**), `janitor` ; healthchecks ; durcissement |

**Critères d'acceptation**

| # | Critère |
|---|---|
| A2.1 | 5 générations soumises d'affilée avec Idol se terminent toutes `COMPLETED`, **jamais plus de `GPU_MAX_CONCURRENT_JOBS` en `RUNNING`** (vérifié par requête périodique) |
| A2.2 | `docker kill` du worker pendant une génération : elle repasse `QUEUED` en moins de 3 × `JOB_LEASE_SECONDS`, puis aboutit ; tentative `ABANDONED` enregistrée |
| A2.3 | `docker compose stop worker` pendant une génération : tentative `INTERRUPTED`, budget non consommé |
| A2.4 | OOM injecté (FakeAdapter) : 3 relances espacées de 30, 60, 120 s (± 2 s), puis `FAILED` |
| A2.5 | `NO_OBJECT_DETECTED` : 1 seule tentative, `FAILED` immédiatement |
| A2.6 | Rétention à 0 jour simulée : génération non protégée supprimée (fichiers + lignes), protégée intacte, `RUNNING` intacte |
| A2.7 | Suppression dont l'effacement de fichier échoue (permission retirée) : reprise par le janitor au cycle suivant, état final cohérent |
| A2.8 | Upload de 21 Mo → 413 ; PDF renommé `.png` → 415 ; image 10 000 × 10 000 → 422 |
| A2.9 | `GPU_MAX_CONCURRENT_JOBS=0`, `JOB_MAX_RETRIES=-1` ou `GPU_VRAM_LIMIT_MB=-1` : le service refuse de démarrer en nommant la variable |
| A2.11 | Suspension : aucune prise pendant la suspension ; le job en cours va à son terme ; reprise dans l'ordre de la file |
| A2.12 | VRAM libre simulée sous `GPU_MIN_FREE_VRAM_MB` (FakeAdapter + NVML factice) : le job reste `QUEUED` avec le motif `VRAM_INSUFFICIENT`, sans processus d'inférence, puis démarre quand la VRAM revient ; avec `GPU_WAIT_TIMEOUT_SECONDS` > 0, `FAILED` en `GPU_UNAVAILABLE`, sans relance automatique |
| A2.13 | Exception non classée : `FAILED`, `needs_diagnosis`, **aucune** relance automatique (D8) |
| A2.15 | `docker compose stop worker` hors génération : NVML ne montre plus aucun processus Hephaistos ; API répond (UI à vérifier en P3, report validé le 2026-11-05) ; une génération soumise attend avec le motif `NO_WORKER` puis aboutit après `docker compose --profile gpu up -d worker` |
| A2.16 | `docker compose up -d` sans profil : aucun conteneur Hephaistos ne réserve le GPU |
| A2.14 | Erreur CUDA injectée : processus d'inférence remplacé, superviseur intact, 3 morts en 10 min → `DEGRADED` et distribution suspendue |
| A2.10 | Aucun chemin de téléchargement ne sort de `/data/results` (tests de traversée : `..`, encodages, liens symboliques) |

**Tests** : unitaires domaine (table des transitions exhaustive, transitions interdites), file
(concurrence : N prises simultanées, une seule gagne), politique de relance, stockage
(traversée, atomicité), API (httpx + FakeAdapter), janitor ; intégration worker + FakeAdapter en
processus réel (signaux, kill).

**Risques** : contention SQLite (atténuée par WAL et des écritures de progression bornées à 1/s) ;
horloges différentes entre conteneurs (même hôte : non) ; promotion double (empêchée par la mise à
jour conditionnelle sur le bail).

---

## P3 — Interface Studio

**Objectif** : soumettre et suivre une génération depuis le navigateur.

**Prérequis** : P2 validé ; D5 tranché.

**✅ Gate validé le 2026-11-10.** P3.1 à P3.9 implémentés et vérifiés, en Docker CPU isolé pour P3.9. Preuves [P3.1](validation/P3.1-client-proxy.md) et
[P3.2](validation/P3.2-studio-layout.md), [P3.3](validation/P3.3-image-upload.md),
[P3.4](validation/P3.4-generation-form.md), [P3.5](validation/P3.5-queue-progress.md),
[P3.6](validation/P3.6-library-detail.md), [P3.7](validation/P3.7-generation-actions.md) et
[P3.8](validation/P3.8-system-status.md), [P3.9](validation/P3.9-frontend-compose.md),
[A3.1](validation/A3.1-playwright.md) et [A3.2](validation/A3.2-idol-manuel.md) ; [gate P3 validé](gates/P3.md).

| Tâche | Contenu |
|---|---|
| P3.1 | Types générés depuis l'OpenAPI ; client HTTP typé ; erreurs problem+json |
| P3.2 | Mise en page studio (barre, bibliothèque, centre, panneau droit, file) |
| P3.3 | Upload + aperçu (orientation, dimensions, alpha) + validation zod |
| P3.4 | Sélecteur de modèle, préréglages, moteur de formulaire JSON Schema (sous-ensemble) |
| P3.5 | File et progression par interrogation (2 s / 15 s, pause onglet masqué) |
| P3.6 | Bibliothèque paginée, filtres, détail (paramètres, tentatives, journaux, erreurs) |
| P3.7 | Actions : annuler, relancer, protéger, supprimer (confirmation) |
| P3.8 | État système dans la barre supérieure |
| P3.9 | Proxy `/api/v1` dans Next ; Dockerfile `standalone` ; service `frontend` |

**Critères d'acceptation**

| # | Critère |
|---|---|
| A3.1 | Parcours E2E (Playwright, FakeAdapter) : importer → choisir Équilibré → générer → voir `sampling i/N` progresser → `COMPLETED` |
| A3.2 | Même parcours manuel sur l'hôte cible avec Idol, depuis un autre poste du LAN |
| A3.3 | Le formulaire avancé ne contient aucun nom de paramètre Idol codé en dur (test : rendu correct d'un schéma factice arbitraire) |
| A3.4 | Valeur hors bornes refusée côté client ; la même requête forcée est refusée par l'API (422) |
| A3.5 | `tsc --noEmit` strict, ESLint et tests unitaires verts |
| A3.6 | Aucune requête d'interrogation quand l'onglet est masqué (vérifié en E2E) |

**Tests** : Vitest + Testing Library (moteur de formulaire, hooks d'interrogation, composants) ;
Playwright sur la pile complète avec `ENABLE_FAKE_ADAPTER=true`.

**Risques** : dérive des types (régénérés en CI et comparés) ; proxy Next sur gros fichiers (mesurer
un PLY de 71 Mo en P4).

---

## P4 — Visualisation et exports

**Objectif** : visualiser et télécharger ; rétention et protection dans l'UI ; étude chiffrée des
exports.

**Prérequis** : P3 validé.

**Avancement au 2026-11-10** : P4.1 et P4.2 implémentés et vérifiés sans GPU, A4.2 et A4.3 démontrés en
parcours automatisés ([preuves](validation/P4.1-viewer.md)). A4.1 reste à vérifier sur le poste cible.
P4.4 implémenté et vérifié sans GPU le 2026-11-11, A4.4 démontré ([preuves](validation/P4.4-downloads.md)).
P4.3 implémenté et vérifié sans GPU le 2026-11-11 ([preuves](validation/P4.3-information.md)).
P4.8 implémenté et vérifié sans GPU le 2026-11-11 ([preuves](validation/P4.8-retention.md)).
P4.5 validé le 2026-11-11 : changement de repère confirmé sur trois objets réels ([preuves](validation/P4.5-camera.md)).
P4.6 mesurée le 2026-11-11 : SPZ et SOG divisent la taille par 5 à 6 sans écart visible ([rapport](exports-study.md)).

| Tâche | Contenu |
|---|---|
| P4.1 | Composant visualiseur Spark (chargement client uniquement), `OrbitControls`, réinitialisation |
| P4.2 | Progression de téléchargement réelle, gestion d'erreur (fichier absent, PLY invalide, WebGL indisponible) |
| P4.3 | Panneau d'informations (gaussiennes, taille, paramètres, caméra prédite) |
| P4.4 | Téléchargements (PLY, aperçu, caméra) avec noms construits par le serveur |
| P4.5 | Option : vue initiale depuis `camera.json` après validation du changement de repère |
| P4.6 | **Étude** SPZ / SOG via `splat-transform` : taille, temps, écart visuel |
| P4.7 | **Étude** GLB avec splats (support des lecteurs) et reconstruction de maillage (méthode, coût, qualité) — rapport `docs/exports-study.md` |
| P4.8 | Affichage de la rétention (date de suppression prévue) et de la protection |

**Critères d'acceptation**

| # | Critère |
|---|---|
| A4.1 | Un PLY de 262 144 et un de 1 048 576 gaussiennes s'affichent dans Chrome et Firefox sur le poste cible |
| A4.2 | Rotation, zoom, déplacement, réinitialisation fonctionnent (E2E sur le canvas : la matrice de caméra change puis revient à l'état initial) |
| A4.3 | Fichier supprimé côté serveur → message d'erreur explicite, pas d'écran blanc |
| A4.4 | Le PLY téléchargé est identique octet pour octet au fichier stocké (sha256 = `ETag`) |
| A4.5 | Le rapport d'étude chiffre chaque option d'export et classe GLB/OBJ en « livrable », « expérimental » ou « écarté », avec sa raison |
| A4.6 | Aucun export n'est présenté comme reconstruction fidèle sans mesure qui le démontre |

**Risques** : performances WebGL sur 1 M de gaussiennes (mesurer, proposer SPZ si besoin) ; support
glTF des splats immature.

---

## P5 — Fiabilisation

**Objectif** : application stable et exploitable sur le serveur personnel.

| Tâche | Contenu |
|---|---|
| P5.1 | Diagnostics GPU dans `/system/status` (VRAM, état modèle, dernière erreur CUDA) |
| P5.2 | Optimisation mémoire : mesure du pic par préréglage, ajustement de `vram_required_bytes` |
| P5.3 | Couverture de tests et CI complète (lint, format, types, tests, build des images) |
| P5.4 | Documentation d'exploitation : installation, mise à jour, sauvegarde/restauration de `var/`, dépannage |
| P5.5 | Vérification de reproductibilité : reconstruction des images depuis zéro |
| P5.6 | Essai d'endurance |

**Critères d'acceptation**

| # | Critère |
|---|---|
| A5.1 | Endurance : 50 générations Idol en file (préréglages mélangés), 0 génération bloquée, 0 incohérence au rapprochement |
| A5.2 | Redémarrage complet de l'hôte pendant une génération : reprise sans intervention |
| A5.3 | Observation sur au moins 7 jours d'usage normal : aucune génération démarrée avec une VRAM libre sous `GPU_MIN_FREE_VRAM_MB`, et 0 octet de VRAM tenu par Hephaistos après chaque `MODEL_IDLE_UNLOAD_SECONDS` d'inactivité (journaux + NVML) |
| A5.4 | `docker compose build --no-cache` sur une machine propre produit les mêmes versions (comparaison des listes `uv pip freeze` / `npm ls`) |
| A5.5 | Couverture backend hors adaptateur Idol ≥ 85 % des lignes ; domaine et file ≥ 95 % |
| A5.6 | CI verte sur la branche principale ; tests GPU joués et consignés pour la version livrée |

**Risques** : instabilité eGPU indépendante de l'application (documenter, ne pas masquer) ;
saturation disque (`/` à 80 % aujourd'hui) — alerte quand l'espace libre passe sous un seuil.

---

## 4. Dépendances entre phases

```
P0 ──► P1 ──► P2 ──► P3 ──► P4 ──► P5
              │             ▲
              └── FakeAdapter permet de démarrer P3 (maquettes, formulaire) en parallèle de la fin de P2
```

## 5. CI (cible)

| Job | Contenu | GPU |
|---|---|---|
| backend-lint | `ruff check`, `ruff format --check`, `mypy --strict src/` | non |
| backend-test | `pytest -m "not gpu"` | non |
| frontend | `eslint`, `tsc --noEmit`, `vitest run`, `next build` | non |
| contract | régénère les types depuis l'OpenAPI et échoue s'ils diffèrent | non |
| e2e | pile Compose avec `ENABLE_FAKE_ADAPTER=true`, `GPU_WORKERS=1` sur worker factice, Playwright | non |
| images | build des 3 images | non |
| gpu | `pytest -m gpu` — **manuel, sur une machine équipée d'un GPU NVIDIA**, résultat consigné | oui |
