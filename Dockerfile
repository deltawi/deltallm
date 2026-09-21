# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
ARG NODE_IMAGE=node:22.16.0-bookworm-slim@sha256:048ed02c5fd52e86fda6fbd2f6a76cf0d4492fd6c6fee9e2c463ed5108da0e34
ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1

FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS frontend
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
ENV NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false
RUN --mount=type=cache,target=/root/.npm npm ci --prefer-offline
COPY ui/ ./
RUN npm run build

FROM ${NODE_IMAGE} AS node-runtime
FROM ${PYTHON_IMAGE} AS base
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl libatomic1 libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 deltallm \
    && useradd --uid 10001 --gid 10001 --create-home deltallm \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version
ENV PATH=/opt/venv/bin:$PATH \
    PRISMA_BINARY_CACHE_DIR=/opt/prisma/binaries \
    PRISMA_NODEENV_CACHE_DIR=/opt/prisma/nodeenv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DELTALLM_CONFIG_PATH=/app/config/config.yaml \
    HOST=0.0.0.0 \
    PORT=4000

FROM base AS builder
ARG INSTALL_PRESIDIO=false
RUN pip install --no-cache-dir --no-deps uv==0.9.28
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    case "$INSTALL_PRESIDIO" in \
      false) uv sync --frozen --no-dev --no-install-project ;; \
      true) uv sync --frozen --no-dev --no-install-project --extra guardrails-presidio ;; \
      *) exit 2 ;; \
    esac
COPY prisma ./prisma
RUN prisma generate --schema=./prisma/schema.prisma && prisma py fetch
COPY src ./src

FROM base
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/prisma /opt/prisma
COPY --from=builder /app /app
COPY --from=frontend /app/ui/dist ./ui/dist
COPY config.example.yaml ./config.example.yaml
USER 10001:10001
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health/liveliness" || exit 1
CMD ["python", "-m", "src.server"]
