# Hephaistos 3D

Application web auto-hébergée qui transforme **une image 2D en objet 3D** (Gaussian Splatting),
avec le modèle [Idol](https://github.com/speridlabs/idol-sdk) de Sperid Labs, sur votre propre GPU
NVIDIA.

> **État : en développement.** Le Gate P0 (faisabilité sur GPU réel) est validé ; le moteur
> d'inférence (P1) est en cours. Il n'y a pas encore d'interface utilisable. Plan et critères :
> [`docs/development-plan.md`](docs/development-plan.md).

## Ce que fait le projet

- Importer une image (JPEG, PNG, WebP), choisir un préréglage, lancer une génération.
- Suivre la progression réelle (étape et pas d'échantillonnage, sans pourcentage inventé), annuler.
- Visualiser le résultat dans le navigateur ([Spark](https://github.com/sparkjsdev/spark) sur
  Three.js) et télécharger le PLY.
- Une file d'attente en SQLite, un worker GPU qui rend toute sa VRAM après inactivité.

Mesuré sur une RTX 5070 12 Go : ~43 s par génération (262 144 gaussiennes), pic ≈ 7,3 Gio de VRAM.

## Prérequis

- GPU NVIDIA avec ≥ 8 Go de VRAM libre (seule l'architecture Blackwell `sm_120` est testée),
  pilote récent, Docker avec le NVIDIA Container Toolkit.
- Un compte Hugging Face ayant accepté les licences des poids (voir ci-dessous).

## Poids et licences

Le code d'Hephaistos est sous licence [Apache-2.0](LICENSE). **Les poids ne sont pas distribués**
ici : vous les installez vous-même, sous leurs propres licences.

| Composant | Licence |
|---|---|
| Idol (code et poids) | Apache-2.0 |
| DINOv3 (Meta) | DINOv3 License, accès sur validation manuelle |
| VAE de FLUX.2-dev (Black Forest Labs) | **FLUX Non-Commercial License** |

La licence du VAE FLUX.2-dev interdit tout usage commercial des générations qui en dépendent.

Installation hors ligne des poids : [`docs/install-models.md`](docs/install-models.md).

## Développement

```bash
# Backend (Python 3.12, uv)
cd backend && uv sync && uv run pytest && uv run ruff check && uv run mypy

# Frontend (Next.js 16)
cd frontend && npm ci && npm run lint && npx tsc --noEmit

# Sondes GPU du Gate P0 (profil Compose « probe », sans réseau)
docker compose --profile probe build worker-probe
docker compose --profile probe run --rm worker-probe python -m hephaistos.probes.cuda
```

## Documentation

- [Spécifications](docs/specifications.md) · [Architecture](docs/architecture.md) ·
  [Modèle de données](docs/data-model.md) · [Contrat d'API](docs/api-contract.md)
- [Audit de faisabilité d'Idol](docs/idol-feasibility.md) · [Gate P0 et preuves](docs/gates/P0.md)
- [Décisions d'architecture (ADR)](docs/decisions/README.md)
