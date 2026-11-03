# ADR-0002 — File d'attente portée par la base de données

- **Statut** : Accepté (2026-11-02, D1)
- **Date** : 2026-11-02

## Contexte
La pile cible mentionne Redis + RQ « ou solution équivalente, après justification ». Exigences :
persistance, limite GPU indépendante du nombre de workers, relance seulement sur erreur
transitoire, reprise des jobs abandonnés, cohérence entre état métier et file, modèle conservé en
VRAM entre deux jobs.

## Décision
La table `generation` **est** la file. Prise atomique en transaction (`BEGIN IMMEDIATE` sous SQLite),
bail avec heartbeat, reprise des baux expirés par le janitor, relances différées par `not_before`.
Pas de Redis au MVP.

## Conséquences
- Une seule source de vérité : aucune réconciliation entre un job RQ et une ligne métier.
- Limite GPU appliquée dans la même transaction que la prise.
- Interrogation toutes les secondes : latence de prise ≤ 1 s, sans importance pour des jobs de
  30 s et plus.
- ~200 lignes de code de file à écrire et tester (tests sans infrastructure).
- Passage à PostgreSQL : `FOR UPDATE SKIP LOCKED`, changement confiné à `queue/`.

## Alternatives écartées
- **Redis + RQ** : le worker par défaut forke un processus par job, or un contexte CUDA ne survit
  pas au fork — le modèle serait rechargé à chaque job. `SimpleWorker` évite le fork mais il reste
  à écrire le sémaphore GPU, le tri des erreurs relançables, la reprise des jobs abandonnés et la
  réconciliation avec la base. RQ n'apporterait alors que le transport.
- **Celery** : même problème de double état, plus lourd encore.
