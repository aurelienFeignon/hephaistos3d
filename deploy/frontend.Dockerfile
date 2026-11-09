# syntax=docker/dockerfile:1
# Image du frontend (sortie standalone de Next). Contexte de construction : racine du dépôt.
# Aucun secret à la construction : le jeton n'est lu par le serveur qu'à l'exécution.

ARG NODE_IMAGE=node:24-trixie-slim@sha256:0820eaa85e07d0490446dd01d14707e17e6108a36988cf374899cd057eb3812d

FROM ${NODE_IMAGE} AS builder
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/next.config.ts frontend/tsconfig.json ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOME=/tmp
WORKDIR /app
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
USER 1000:1000
EXPOSE 3030
CMD ["node", "server.js"]
