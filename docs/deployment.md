# Backend Compose — P2.11

**État : build et intégration Docker CPU/GPU validés le 2026-11-05 sur volumes temporaires ; gate P2 en attente de revue.**
Preuves et périmètre : [CPU](validation/P2.11-cpu.md) et [GPU](validation/P2.11-gpu.md).
P0/P1 sont validées, le backend P2 est implémenté ; aucun gate P2 n'est encore validé.
Le frontend sera ajouté en P3. Aucun port hôte n'est publié à cette étape (ADR-0009) :
l'API écoute sur 8000 uniquement dans le réseau interne. Le port 3030 reste réservé au frontend.

## Préparer les répertoires

Depuis la racine du dépôt :

```bash
test -e .env || cp .env.example .env
mkdir -p var/{db,sources,results,logs,tmp,cache,models}
id -u
id -g
```

Reporter ces UID/GID dans `HEPHAISTOS_UID` et `HEPHAISTOS_GID` dans `.env`. Tous les services
utilisent cette identité non root. Les répertoires persistants doivent déjà exister et lui être
accessibles en écriture ; Compose refuse de créer automatiquement des bind mounts appartenant
à root. Pour des données existantes, vérifier leurs propriétaires avant tout changement.

Les données SQL, sources, résultats, journaux et temporaires sont montés séparément sous
`/data`. La racine `/data` est un tmpfs de 1 Mio appartenant à l'UID/GID configuré : seuls les
points de montage et le heartbeat local du janitor y résident. Les modèles et le cache ne sont
montés que dans le worker, respectivement en lecture seule et en écriture. Le worker ne peut
pas modifier les sources. L'API possède l'écriture sur les résultats/journaux pour supprimer
une génération via la procédure en trois étapes.

`.dockerignore` utilise une liste d'inclusion des seules sources backend, migrations, tests,
manifestes verrouillés et Dockerfile. Les poids, données, `.env`, notes locales, historique git,
venv et node_modules n'entrent pas dans le contexte de build.

## Vérifier et lancer le backend sans GPU

Commandes d'exploitation. Leur équivalent a été validé dans un projet isolé, avec les bind mounts
redirigés vers des volumes temporaires et un tag d'image dédié, sans utiliser les données réelles :

```bash
docker compose config --quiet
docker compose build api
docker compose up -d
docker compose ps -a
docker compose exec api python -m hephaistos.healthcheck api
docker compose exec janitor python -m hephaistos.healthcheck janitor
```

Sans profil, seuls `migrate`, `api` et `janitor` sont sélectionnés. `migrate` exécute
`alembic upgrade head` puis quitte ; les autres services attendent sa sortie réussie.
Une migration échouée empêche leur démarrage. L'API ne crée pas le schéma implicitement.
Les migrations affectent le répertoire de données choisi : sauvegarder une base existante avant
une mise à niveau. Les tests de cette session utilisaient exclusivement des bases temporaires.

L'API utilise un réseau interne dédié. Migration, janitor et worker ont `network_mode: none` :
ils partagent la file SQLite par les volumes, sans accès réseau. Aucune dépendance à un service
extérieur ou au socket Docker. Les systèmes de fichiers racine sont en lecture seule, les
capabilities supprimées et l'acquisition de privilèges interdite ; `/tmp` est un tmpfs de 2 Gio.
Les services tournent avec un init, une limite de processus et des logs Docker bornés.
API : mémoire limitée à 2 Gio ; migration et janitor : 512 Mio chacun. Le worker conserve une
limite de processus plus large pour les bibliothèques de calcul, sans limite RAM imposée ici.

Les services longs ont `restart: unless-stopped`, le migrateur `restart: "no"`. L'état Docker
`unhealthy` signale une panne ; il ne redémarre pas automatiquement un processus encore vivant.

## Activer le worker GPU

Préparer les poids hors ligne selon [install-models.md](install-models.md), puis, pour la
validation GPU autorisée :

```bash
docker compose --profile gpu build worker
docker compose --profile gpu up -d worker
docker compose exec worker python -m hephaistos.healthcheck worker
# Arrêt propre du superviseur et de son enfant d'inférence :
docker compose --profile gpu stop worker
```

`GPU_WORKERS` règle les réplicas (défaut 1), `GPU_MAX_CONCURRENT_JOBS` la capacité globale SQLite.
Le worker dispose d'une réservation NVIDIA explicite. Les profils historiques `probe` et
`gpu-test` restent séparés et ne démarrent jamais par défaut. Une variable `COMPOSE_PROFILES`
explicitement définie peut naturellement activer ces profils : l'examiner avant un lancement.

`WORKER_STOP_GRACE_SECONDS` (120 par défaut) doit dépasser strictement
`WORKER_SHUTDOWN_GRACE_SECONDS` (90). Les deux valeurs sont validées au démarrage ; modifier la
seconde impose d'ajuster la première. Docker laisse ainsi au superviseur le temps d'interrompre
sa tentative et de terminer son enfant avant SIGKILL.

## Santé et diagnostic

`python -m hephaistos.healthcheck <api|worker|janitor>` retourne 0 si sain, 1 sinon.
Aucune sonde ne charge torch, n'alloue de VRAM ni ne crée une base/un schéma.

| Service | Critère |
|---|---|
| API | GET local `/api/v1/system/health`, HTTP 200, corps `{"status":"ok"}`, timeout 2 s |
| Worker | Heartbeat récent d'un worker du **même hostname de conteneur**, hors DEGRADED/STOPPING ; lecture SQLite `mode=ro` |
| Janitor | `janitor-status.json` valide, `ok=true`, date récente |

La fraîcheur maximale est `JOB_LEASE_SECONDS`. NO_GPU reste un état de superviseur vivant ;
l'absence du GPU ou des poids est visible dans `/system/status`, et non corrigée par un restart
automatique. Le heartbeat du janitor est local au conteneur et réinitialisé au redémarrage.
Une maintenance longue peut dépasser sa fenêtre de fraîcheur et doit être examinée dans les logs.

```bash
docker compose logs --tail=100 api janitor
docker compose --profile gpu logs --tail=100 worker
```

Les logs applicatifs sont des JSON corrélés ; les journaux par tentative restent dans
`var/logs/<generation>/attempt-<n>.jsonl`. Les healthchecks utilisent Python présent dans l'image,
sans ajouter curl ni des outils GPU.

## Revue restant à effectuer

- Revue des critères A2.1–A2.16 et des précisions consignées dans les [preuves GPU](validation/P2.11-gpu.md).
- Prendre connaissance de la distinction entre simulation immédiate par cutoff et configuration 0
  (purge désactivée), toutes deux testées : [dossier du gate P2](gates/P2.md).
- Partie UI d'A2.15 réservée à P3, interface non encore implémentée.
- Approbation du gate P2 par l'utilisateur avant de commencer P3 ; aucun déploiement permanent.
