# ADR-0001 — Structure du dépôt et scaffolding

- **Statut** : Accepté (2026-11-02)
- **Date** : 2026-11-02

## Contexte
Hephaistos 3D combine un backend Python (API, worker GPU, tâches de maintenance) et un frontend
Next.js. Les workers GPU devront pouvoir être déportés plus tard.

## Décision
- Monorepo : `backend/` (un paquet Python `hephaistos`), `frontend/`, `deploy/`, `docs/`.
- Un **seul** paquet Python, avec un extra `[idol]` qui apporte torch et le SDK. L'image `api`
  s'installe sans l'extra ; l'image `worker` avec. API, worker et janitor sont trois points
  d'entrée du même paquet.
- Initialisation par les outils officiels : `uv init --package`, `alembic init`,
  `create-next-app@16` (commandes exactes : `development-plan.md` §3).

## Conséquences
- Le code de domaine est partagé sans duplication entre API et worker.
- La séparation torch / sans torch est garantie par l'extra **et** par un test (`torch` absent de
  `sys.modules` après import du registre).

## Alternatives écartées
- Deux paquets Python (`hephaistos-core`, `hephaistos-worker`) : séparation plus stricte, mais
  double outillage pour un seul développeur. À reconsidérer si un second adaptateur arrive.
