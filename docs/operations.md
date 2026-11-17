# Exploitation — installation, mise à jour, sauvegarde, dépannage (P5.4)

Guide pour l'administrateur d'une installation sur un serveur personnel. Il complète
[deployment.md](deployment.md) : celui-ci détaille la configuration Compose et ses durcissements,
et ce guide rassemble les gestes d'exploitation. Toutes les commandes se lancent depuis la racine
du dépôt.

> État au 2026-11-17 : l'installation, la **mise à jour** (avec une migration) et la
> **sauvegarde/restauration** ont été rejouées de bout en bout sur une installation jetable avec GPU
> ([preuves](validation/P5.4-exploitation.md)).

## 1. Installation

### Prérequis

- Linux, Docker avec Compose v2.
- Pour générer : GPU NVIDIA avec au moins 8 Gio de VRAM libre et NVIDIA Container Toolkit. Seule
  l'architecture Blackwell `sm_120` est testée. Sans GPU, l'interface, l'API et le janitor
  fonctionnent, mais aucune génération ne démarre.
- Disque : environ 7 Go de poids, environ 12 Go pour l'image `worker`, plus les résultats (budget
  de 20 Gio par défaut). Prévoir **au moins 50 Go libres** avant toute construction d'image.

### Étapes

1. **Poids** : suivre [install-models.md](install-models.md). Ils arrivent dans `var/models/`.
2. **Répertoires et identité** :

   ```bash
   test -e .env || cp .env.example .env
   mkdir -p var/{db,sources,results,logs,tmp,cache,models}
   id -u; id -g      # à reporter dans HEPHAISTOS_UID et HEPHAISTOS_GID du .env
   ```

   Compose ne crée pas les montages (`create_host_path: false`). Ces répertoires doivent appartenir
   à l'UID/GID configuré.
3. **Secret** : dans `.env`, renseigner `API_ACCESS_TOKEN` (32 à 512 caractères URL-safe) :

   ```bash
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

   L'API refuse de démarrer sans ce secret. Ne jamais commiter `.env`.
4. **Construction et démarrage** :

   ```bash
   docker compose config --quiet
   docker compose --profile gpu build
   docker compose --profile gpu up -d
   docker compose --profile gpu ps -a
   ```

   `migrate` applique le schéma puis quitte. `api`, `janitor`, `frontend` et `worker` démarrent
   ensuite. Sans `--profile gpu`, le worker ne démarre pas.
5. **Accès** : le Studio écoute sur `http://127.0.0.1:3030` (`HEPHAISTOS_BIND_ADDR`,
   `HEPHAISTOS_PORT`). Depuis un autre poste, passer par une redirection de port SSH
   (`ssh -L 3030:127.0.0.1:3030 <hôte>`) et ouvrir `http://localhost:3030`.

   ⛔ N'ouvrez pas le port sur le réseau local : le Studio n'a pas encore de contrôle d'accès pour
   ses utilisateurs.

   Si vous changez de port ou d'adresse, mettez à jour `FRONTEND_ALLOWED_ORIGINS` et
   `API_ALLOWED_ORIGINS` en conséquence. Sinon les modifications répondent 403.

### Contrôle

```bash
docker compose exec api python -m hephaistos.healthcheck api
docker compose exec janitor python -m hephaistos.healthcheck janitor
docker compose --profile gpu exec worker python -m hephaistos.healthcheck worker
```

Chaque commande rend 0 si le service est sain. La barre d'état du Studio montre ensuite le worker,
la VRAM libre et la file.

## 2. Lire l'état du système

L'API n'est pas publiée sur l'hôte. Pour lire `/system/status` sans passer par le navigateur :

```bash
docker compose exec api python -c '
import json, os, urllib.request as u
r = u.Request("http://127.0.0.1:8000/api/v1/system/status",
              headers={"Authorization": "Bearer " + os.environ["API_ACCESS_TOKEN"]})
print(json.dumps(json.load(u.urlopen(r)), indent=2))'
```

Champs utiles au diagnostic (contrat : [api-contract.md](api-contract.md) §1) :

| Champ | Lecture |
|---|---|
| `dispatch.paused`, `paused_by` | distribution suspendue, et par qui (`user`, `config`, `system`) |
| `workers[].state` | `IDLE`, `LOADING`, `READY`, `BUSY`, `WAITING_GPU`, `NO_GPU`, `DEGRADED` |
| `workers[].waiting_reason` | `VRAM_INSUFFICIENT` ou `DISPATCH_PAUSED` |
| `workers[].model` | modèle chargé (`LOADING`, `RESIDENT`) ; `null` une fois déchargé |
| `workers[].gpu` | VRAM libre, utilisée et utilisation, pour **tout** le périphérique |
| `gpu.last_error` | dernière tentative en `CUDA_ERROR` ou `GPU_OOM` (n'expire pas : lire `at`) |
| `storage` | octets des résultats, espace libre |

Un worker absent de la liste n'a pas émis de heartbeat depuis `JOB_LEASE_SECONDS` : il est arrêté,
bloqué ou n'a jamais démarré.

## 3. Mise à jour

1. **Lire ce qui change** : `git fetch` puis `git log HEAD..origin/main`. Repérer les nouvelles
   migrations (`backend/alembic/versions/`) et les nouvelles variables de `.env.example`.
2. **Sauvegarder** (§4). Une migration ne se défait pas en production : le retour arrière passe par
   la restauration.
3. **Mettre à jour et reconstruire** :

   ```bash
   git pull --ff-only
   docker compose --profile gpu build
   ```

4. **Redémarrer** :

   ```bash
   docker compose --profile gpu up -d
   ```

   Compose recrée les services dont l'image a changé et relance `migrate` avant eux. Si une
   génération est en cours, le worker reçoit SIGTERM. Il laisse
   `WORKER_SHUTDOWN_GRACE_SECONDS` (90 s) à la tentative, puis l'interrompt. Cette tentative est
   reprise au redémarrage **sans consommer de relance**. Pour éviter d'en interrompre une,
   suspendez d'abord la distribution depuis le Studio, puis attendez que la file soit vide.
5. **Contrôler** : `docker compose --profile gpu ps -a`, puis les commandes de santé du §1.
   `migrate` doit être `Exited (0)`.

Si `migrate` échoue, les autres services ne démarrent pas : c'est voulu. Lire
`docker compose logs migrate`, corriger, ou restaurer la sauvegarde.

## 4. Sauvegarde et restauration de `var/`

### Quoi sauvegarder

| Répertoire | Contenu | Sauvegarde |
|---|---|---|
| `var/db/` | base SQLite (`hephaistos.sqlite3`, `-wal`, `-shm`) | **obligatoire** |
| `var/sources/` | images d'entrée | **obligatoire** |
| `var/results/` | PLY, aperçus, caméras | **obligatoire** |
| `var/logs/` | journaux par tentative | recommandée |
| `var/tmp/` | travail en cours | inutile |
| `var/cache/` | caches de compilation (Triton, Inductor) | inutile, se reconstruit |
| `var/models/` | poids (environ 7 Go) | à part : retéléchargeables ([install-models.md](install-models.md)) |
| `.env` | configuration et secret | **obligatoire**, à garder hors du dépôt et protégée |

La base et les fichiers doivent dater **du même instant**. La base référence les artefacts par
identifiant, et le janitor supprime après 24 h les résultats qu'aucune ligne ne référence. Copier la
base et les fichiers à des moments différents fait donc perdre des données.

### Sauvegarde (à froid)

La méthode de référence arrête la pile : aucune écriture n'est alors en cours, et le WAL de SQLite
est cohérent avec les fichiers.

```bash
docker compose --profile gpu stop          # le worker attend la fin de sa grâce
docker compose --profile gpu ps -a         # tout doit être « Exited »
stamp=$(date -u +%Y%m%dT%H%M%SZ)
tar -C var -czf "/chemin/des/sauvegardes/hephaistos-$stamp.tar.gz" db sources results logs
cp .env "/chemin/des/sauvegardes/hephaistos-$stamp.env" && chmod 600 "/chemin/des/sauvegardes/hephaistos-$stamp.env"
docker compose --profile gpu up -d
```

Après un arrêt propre, SQLite a reporté son journal dans la base : `var/db/` ne contient alors
souvent que `hephaistos.sqlite3`, et c'est normal. S'ils sont présents, gardez les fichiers `-wal` et
`-shm` avec la base. Ne copiez jamais `hephaistos.sqlite3` seul pendant que la pile tourne.

### Restauration

```bash
docker compose --profile gpu down          # conserve var/, retire les conteneurs
mv var/db var/db.avant-restauration         # garder l'état courant tant que la restauration n'est pas vérifiée
mv var/sources var/sources.avant-restauration
mv var/results var/results.avant-restauration
mv var/logs var/logs.avant-restauration
mv var/tmp var/tmp.avant-restauration       # sinon il garde des répertoires de générations postérieures à la sauvegarde
mkdir -p var/{db,sources,results,logs,tmp}
tar -C var -xzf /chemin/des/sauvegardes/hephaistos-<horodatage>.tar.gz
chown -R "$(id -u):$(id -g)" var/db var/sources var/results var/logs   # = HEPHAISTOS_UID/GID
docker compose --profile gpu up -d
```

- Une sauvegarde plus ancienne que le code reçoit au démarrage les migrations qui lui manquent.
  L'inverse n'est pas possible : avec un code plus ancien que la base, `migrate` échoue. Revenez
  alors au commit de la sauvegarde.
- Les générations qui tournaient au moment de la sauvegarde sont reprises par le janitor (bail
  expiré) ou relancées selon leur budget.
- Supprimez les répertoires `*.avant-restauration` seulement après avoir contrôlé la bibliothèque
  dans le Studio.

## 5. Dépannage

| Symptôme | Cause probable | Geste |
|---|---|---|
| `migrate` en échec, les autres services ne démarrent pas | migration refusée, droits sur `var/db`, base plus récente que le code | `docker compose logs migrate` ; vérifier les propriétaires ; revenir au bon commit ou restaurer |
| `api` redémarre en boucle | `API_ACCESS_TOKEN` vide ou invalide, réglage hors bornes | `docker compose logs api` : le message nomme la variable |
| Le Studio s'affiche, mais toute action répond 403 | origine non autorisée (autre port ou autre adresse que 3030) | utiliser `localhost:3030`, ou ajuster `FRONTEND_ALLOWED_ORIGINS` et `API_ALLOWED_ORIGINS` |
| « Aucun worker » dans le Studio | worker non démarré (profil `gpu` oublié), arrêté ou bloqué | `docker compose --profile gpu ps -a worker` puis ses logs |
| Worker `NO_GPU` | GPU invisible dans le conteneur (pilote, NVIDIA Container Toolkit, eGPU déconnecté) | `nvidia-smi` sur l'hôte ; le worker réessaie toutes les 30 s, sans redémarrage |
| Worker `WAITING_GPU`, motif `VRAM_INSUFFICIENT` | une autre application occupe le GPU partagé | attendre ou libérer la VRAM ; le seuil est `GPU_MIN_FREE_VRAM_MB` |
| Worker `DEGRADED`, distribution suspendue par `system` | trois morts de l'enfant d'inférence en dix minutes | lire `gpu.last_error` et les journaux de tentative, corriger, puis **reprendre la distribution** depuis le Studio (aucun redémarrage nécessaire) |
| Générations en `CUDA_ERROR` ou `GPU_OOM` | contexte CUDA compromis, ou mémoire insuffisante pour ce préréglage | ces erreurs se relancent automatiquement ; si elles se répètent, baisser le préréglage ou libérer la VRAM |
| Soumission refusée en 429 | file pleine (`QUEUE_MAX_ACTIVE`) ou upload déjà en cours | attendre ; ajuster le réglage si besoin |
| Soumission refusée en 507 | budget disque (`STORAGE_MAX_BYTES`) ou marge libre (`STORAGE_MIN_FREE_BYTES`). Chaque génération active réserve environ 640 Mo : avec les réglages par défaut, la file plafonne vers **33 générations actives**, bien avant `QUEUE_MAX_ACTIVE` | attendre que la file se vide, ou relever `STORAGE_MAX_BYTES` ; supprimer des générations ; vérifier `df -h` ; la rétention (`STORAGE_RETENTION_DAYS`) purge les générations non protégées |
| Service `unhealthy` | le healthcheck échoue, mais Docker ne relance pas un processus vivant | lire ses logs ; `docker compose restart <service>` si nécessaire |
| « WebGL est indisponible dans ce navigateur » | accélération matérielle désactivée ou pilote bloqué | activer l'accélération matérielle du navigateur ; Chrome et Firefox sont vérifiés (A4.1) |

### Journaux

```bash
docker compose logs --tail=200 api janitor frontend
docker compose --profile gpu logs --tail=200 worker
ls var/logs/<génération>/        # attempt-<n>.jsonl : chargement, étapes, issue, trace complète
```

Les journaux des services sont en JSON. Docker les borne à 3 × 10 Mo par service. Le Studio
affiche aussi le journal d'une tentative (bouton « Journal » du détail).
