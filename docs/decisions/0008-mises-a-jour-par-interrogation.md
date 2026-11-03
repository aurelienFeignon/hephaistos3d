# ADR-0008 — Mises à jour du frontend par interrogation

- **Statut** : Accepté (2026-11-02)
- **Date** : 2026-11-02

## Contexte
Le frontend doit suivre statut et progression. Un utilisateur, quelques générations actives au plus.

## Décision
Interrogation périodique : 2 s avec une génération active, 15 s sinon, suspendue onglet masqué.

## Conséquences
- Aucun état de connexion à gérer, compatible sans réglage avec le proxy Next.
- Charge négligeable (une requête légère toutes les 2 s).

## Alternatives écartées
- SSE : latence plus faible, mais connexion longue à traverser le proxy, reprise à gérer, et pas de
  diffusion inter-processus sans Redis. À reconsidérer si plusieurs utilisateurs arrivent.
