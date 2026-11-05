# Hephaistos 3D

**English** · [Français](README.fr.md)

A self-hosted web application that turns **a 2D image into a 3D object** (Gaussian Splatting),
using Sperid Labs' [Idol](https://github.com/speridlabs/idol-sdk) model on your own NVIDIA GPU.

> **Status: in development.** Gates P0 and P1 are validated; the backend (P2) is implemented
> and its CPU/GPU Compose integration is validated. Gate P2 awaits review. There is no usable interface yet. Plan and acceptance criteria:
> [`docs/development-plan.md`](docs/development-plan.md) (in French, like the rest of `docs/`).

## What it does

- Upload an image (JPEG, PNG, WebP), pick a preset, start a generation.
- Follow real progress (stage and sampling step, never an invented percentage), cancel at any time.
- View the result in the browser ([Spark](https://github.com/sparkjsdev/spark) on Three.js) and
  download the PLY.
- A SQLite-backed job queue, and a GPU worker that releases all of its VRAM when idle.

Measured on an RTX 5070 12 GB: ~43 s per generation (262,144 Gaussians), peak ≈ 7.3 GiB of VRAM.

## Requirements

- An NVIDIA GPU with ≥ 8 GB of free VRAM (only the Blackwell `sm_120` architecture is tested), a
  recent driver, and Docker with the NVIDIA Container Toolkit.
- A Hugging Face account that has accepted the model licenses (see below).

## Weights and licenses

Hephaistos' code is released under the [Apache-2.0](LICENSE) license. **The model weights are not
distributed** here: you install them yourself, under their own licenses.

| Component | License |
|---|---|
| Idol (code and weights) | Apache-2.0 |
| DINOv3 (Meta) | DINOv3 License, access granted after manual review |
| FLUX.2-dev VAE (Black Forest Labs) | **FLUX Non-Commercial License** |

The FLUX.2-dev VAE license forbids any commercial use of the generations that depend on it.

Offline weight installation: [`docs/install-models.md`](docs/install-models.md).

Backend Compose preparation and validation: [`docs/deployment.md`](docs/deployment.md) (French).
No host port is published until the frontend is added in P3; the GPU worker requires an explicit profile.

## Development

```bash
# Backend (Python 3.12, uv)
cd backend && uv sync && uv run pytest && uv run ruff check && uv run mypy

# Frontend (Next.js 16)
cd frontend && npm ci && npm run lint && npx next typegen && npx tsc --noEmit

# Gate P0 GPU probes (Compose profile "probe", no network)
docker compose --profile probe build worker-probe
docker compose --profile probe run --rm worker-probe python -m hephaistos.probes.cuda
```

Continuous integration runs the same checks on every push, without a GPU. Tests marked `gpu` need
an NVIDIA card and the weights, and are run by hand.

## Documentation

The design documents are written in French.

- [Specifications](docs/specifications.md) · [Architecture](docs/architecture.md) ·
  [Data model](docs/data-model.md) · [API contract](docs/api-contract.md)
- [Idol feasibility audit](docs/idol-feasibility.md) · [Gate P0 and evidence](docs/gates/P0.md)
- [Architecture decision records](docs/decisions/README.md)
