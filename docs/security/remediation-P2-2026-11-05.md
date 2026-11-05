# Corrections de sécurité P2 avant P3 — 2026-11-05

Les cinq constats de l'[audit initial](audit-P2-2026-11-05.md) ont été traités dans le code.
P2 reste validée fonctionnellement ; P3 n'a pas commencé. Les vérifications ci-dessous utilisent
uniquement des données temporaires, sans déploiement permanent ni inférence GPU.

| Constat | Correction | Vérification |
|---|---|---|
| SEC-01 : frontière HTTP | Jeton obligatoire, refus de démarrage sans secret, Host exact et Origin filtrés avant lecture du corps ; docs et fichiers protégés ; santé publique uniquement | Tests HTTP sans jeton/jeton erroné, Host hostile, Origin hostile/null, cross-site et origine admise ; intégration Docker |
| SEC-02 : admission et ressources | Corps JSON 64 Kio et réception 30 s, concurrence Uvicorn 64 ; un upload, file active 100 ; réservations SQLite et budget disque conservateur ; mêmes contrôles pour les relances manuelles | Tests flux sans Content-Length, réception lente, quatre réservations concurrentes, expiration/libération des baux, quota futur, marge disque et relance ; intégration Docker |
| SEC-03 : lectures coûteuses | File paginée, chargement SQL par lot ; état agrégé SQL ; tentatives bornées ; lecture du suffixe des journaux limitée à 1 Mio | Pagination sans doublons et nombre constant de requêtes SQL ; journal avec préfixe volumineux non lu ; suite API |
| SEC-04 : minimatch de développement | Override limité à typescript-estree, minimatch 11.0.1 et verrou actualisé ; contrôles pip-audit API et npm audit complet ajoutés en CI | npm audit : zéro avis connu, lint/types/build frontend ; production déjà sans avis lors de l'audit initial |
| SEC-05 : métadonnées et sondes | PNG normalisé sans EXIF par défaut ; original uniquement via `?original=true`, en pièce jointe ; page/fichiers P0 indisponibles en production, erreurs sans chemin | Image EXIF synthétique, comparaison original/normalisé ; serveur Next en mode production : trois routes P0 à 404 même avec le flag de sondes |

## Preuves et reproduction

- Backend : **532 tests réussis**, 13 tests GPU exclus ; Ruff check et format, mypy strict verts.
  Les 25 cas de `tests/unit/test_security.py` incluent l'authentification réelle : leur client
  n'injecte pas de jeton par défaut. Les autres tests API utilisent un client authentifié explicite.
- Six mutations temporaires ont été détectées : retrait du contrôle d'authentification,
  d'Origin, de la borne JSON, des slots partagés, du budget disque futur et du défaut normalisé.
  Une mutation supplémentaire du contrôle d'admission des relances est également détectée.
  Les sources sont restaurées après chaque mutation et les tests sont rejoués.
- Frontend : ESLint, génération des types Next, TypeScript et build de production réussis ;
  `npm audit --audit-level=moderate` ne remonte aucun avis, développement compris.
- Docker CPU isolé : construction de l'image corrigée, migration sur base neuve, santé,
  authentification, Host/Origin, création multipart, EXIF, quota de file, borne JSON,
  suppression puis nouvelle admission. Aucun port publié, GPU, volume réel ou service extérieur.
  Les conteneurs et le réseau temporaires ont été arrêtés/supprimés à la fin.

Commandes backend (depuis `backend`, environnement synchronisé) :

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q
uv run pytest tests/unit/test_security.py -q
```

Commandes frontend (depuis `frontend`) :

```bash
npm ci
npm run lint
npx next typegen
npx tsc --noEmit
npm run build
npm audit --audit-level=moderate
```

L'avis corrigé est [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249).
Le verrou pinne la version corrigée pour la dépendance concernée, sans remplacement global
des autres versions de minimatch.

## Contrat d'exploitation et suite P3

L'API exige désormais `API_ACCESS_TOKEN`, généré localement : aucune valeur réutilisable n'est
fournie dans `.env.example`. Il faut renseigner ce secret avant le prochain démarrage et appliquer
la migration additive `20261105_ingest_reservations` sur la base choisie. Les bases réelles n'ont
pas été migrées pendant ces vérifications. Référence : [déploiement](../deployment.md).

Le [contrat API](../api-contract.md) décrit les changements avant le premier consommateur P3 :
file paginée, historique borné, source normalisée et accès explicite à l'original. La somme
`results_bytes` correspond aux artefacts SQL référencés, sans parcours disque à chaque polling.

P3 devra valider Host/Origin au niveau de son proxy avant d'ajouter le jeton API, conserver ce
jeton exclusivement côté serveur et définir l'accès utilisateur avant toute ouverture au LAN.
Les contrôles backend ne protègent pas un proxy qui authentifierait indistinctement les requêtes.
Cette intégration fera l'objet d'une nouvelle revue avant exposition.

Les quotas sont des réservations conservatrices à l'admission, pas des quotas matériels du disque.
Les modèles/cache sont exclus ; l'espace pris par d'autres processus doit rester surveillé.
Les limites de l'audit initial restent applicables : pas d'audit complet du SDK Idol, des binaires
CUDA, paquets système, pilote ou noyau, ni certification du filesystem contre un processus local
hostile. Ces corrections ferment les constats identifiés dans le périmètre HTTP examiné.
