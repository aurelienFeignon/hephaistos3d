# syntax=docker/dockerfile:1
# Images du backend. Contexte de construction : racine du dépôt.
#   cible « api »    : FastAPI, janitor, migrations — sans torch
#   cible « worker » : + extra [idol] (torch CUDA 13.2, SDK Idol épinglé) + compilateur C pour Triton

ARG PYTHON_IMAGE=python:3.12-slim-trixie@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.3@sha256:90bbb3c16635e9627f49eec6539f956d70746c409209041800a0280b93152823

FROM ${UV_IMAGE} AS uv

# --------------------------------------------------------------------------- construction des venv
FROM ${PYTHON_IMAGE} AS builder-base
COPY --from=uv /uv /usr/local/bin/uv
# git : uv l'emploie pour la dépendance « idol @ git+… »
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./

FROM builder-base AS builder-api
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM builder-base AS builder-worker
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project --extra idol
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --extra idol

# --------------------------------------------------------------------------- exécution
FROM ${PYTHON_IMAGE} AS runtime-base
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
WORKDIR /app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic

FROM runtime-base AS api
COPY --from=builder-api /opt/venv /opt/venv
USER 1000:1000
CMD ["python", "-c", "print('hephaistos api : point d’entrée ajouté en P2')"]

FROM runtime-base AS worker
# Triton compile un lanceur C à la volée (torch.compile) : gcc et libc6-dev sont requis à l'exécution.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY --from=builder-worker /opt/venv /opt/venv
ENV HF_HOME=/models/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor \
    TRITON_CACHE_DIR=/cache/triton \
    ATTN_BACKEND=sdpa
USER 1000:1000
CMD ["python", "-m", "hephaistos.probes.cuda"]
