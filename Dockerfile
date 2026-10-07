# syntax=docker/dockerfile:1.7

# The UI output is static, so keep this stage on the native builder platform.
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend

WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json* ./
ENV NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FETCH_RETRIES=5 \
    NPM_CONFIG_FETCH_RETRY_FACTOR=2 \
    NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=20000 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=120000 \
    NPM_CONFIG_FETCH_TIMEOUT=600000 \
    NPM_CONFIG_FUND=false
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci --prefer-offline; else npm install --prefer-offline; fi

COPY ui/ ./
RUN npm run build

FROM python:3.11-slim AS builder

WORKDIR /app
ARG INSTALL_PRESIDIO=false
ENV PATH=/opt/python/bin:$PATH \
    PYTHONPATH=/opt/python/lib/python3.11/site-packages \
    PRISMA_BINARY_CACHE_DIR=/opt/prisma \
    PRISMA_NODEENV_CACHE_DIR=/opt/prisma-nodeenv

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/opt/python -r requirements.txt

RUN if [ "$INSTALL_PRESIDIO" = "true" ]; then \
      pip install --no-cache-dir --prefix=/opt/python presidio-analyzer presidio-anonymizer; \
    fi

COPY pyproject.toml ./
COPY src ./src
COPY prisma ./prisma
RUN prisma generate --schema=./prisma/schema.prisma
RUN prisma py fetch

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/python /opt/python
COPY --from=builder /opt/prisma /opt/prisma
COPY --from=builder /opt/prisma-nodeenv /opt/prisma-nodeenv
COPY --from=builder /app /app
COPY --from=frontend /app/ui/dist ./ui/dist
COPY config.example.yaml ./config.example.yaml

ENV PATH=/opt/python/bin:/opt/prisma-nodeenv/bin:$PATH
ENV PYTHONPATH=/opt/python/lib/python3.11/site-packages
ENV PRISMA_BINARY_CACHE_DIR=/opt/prisma
ENV PRISMA_NODEENV_CACHE_DIR=/opt/prisma-nodeenv
ENV PYTHONUNBUFFERED=1
ENV DELTALLM_CONFIG_PATH=/app/config/config.yaml
ENV HOST=0.0.0.0
ENV PORT=4000

RUN groupadd --gid 10001 deltallm \
    && useradd --uid 10001 --gid deltallm --create-home deltallm \
    && mkdir -p /app/config \
    && chown -R deltallm:deltallm /app /opt/prisma /opt/prisma-nodeenv
USER 10001:10001

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health/liveliness" || exit 1

CMD ["sh", "-c", "python -m src.prisma_bootstrap --schema ./prisma/schema.prisma --max-attempts 30 --sleep-seconds 2 && exec uvicorn src.main:app --host ${HOST} --port ${PORT}"]
