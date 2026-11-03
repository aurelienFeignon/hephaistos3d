# ADR-0009 — Exposition réseau minimale

- **Statut** : Accepté (2026-11-02, D5 : localhost d'abord)
- **Date** : 2026-11-02

## Contexte
Application sans authentification, destinée au LAN. L'hôte peut déjà publier d'autres services
(proxy inverse sur 80/443, applications sur 3000…). Docker contourne le pare-feu de l'hôte pour les ports publiés.

## Décision
- Un seul port publié, celui du `frontend`, qui proxifie `/api/v1` vers `api` sur le réseau interne.
- Liaison à `HEPHAISTOS_BIND_ADDR`, **`127.0.0.1` par défaut** ; l'IP LAN est un choix explicite.
- Port par défaut 3030.
- Aucun routage Traefik, aucun tunnel.

## Conséquences
- L'API n'est jamais joignable directement depuis le LAN ; pas de CORS.
- L'ajout futur d'une authentification se fait en un point (dépendance FastAPI + garde Next).

## Alternatives écartées
- Publier aussi l'API : surface doublée, CORS à gérer.
