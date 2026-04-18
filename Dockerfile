FROM node:20-alpine AS frontend

WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY ui/ ./
RUN npm run build

FROM python:3.11-slim AS builder

WORKDIR /app
ARG INSTALL_PRESIDIO=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

RUN if [ "$INSTALL_PRESIDIO" = "true" ]; then \
      pip install --no-cache-dir --user presidio-analyzer presidio-anonymizer; \
    fi

# `pip install --user` drops console scripts (like `prisma`) into /root/.local/bin,
# which is not on PATH by default during the build stage.
ENV PATH=/root/.local/bin:$PATH

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

COPY --from=builder /root/.local /root/.local
COPY --from=builder /root/.cache/prisma-python /root/.cache/prisma-python
COPY --from=builder /app /app
COPY --from=frontend /app/ui/dist ./ui/dist
COPY config.example.yaml ./config.example.yaml

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV DELTALLM_CONFIG_PATH=/app/config/config.yaml
ENV HOST=0.0.0.0
ENV PORT=4000
ENV DELTALLM_PRISMA_STARTUP_MODE=deploy
ENV DELTALLM_PRISMA_SCHEMA_PATH=./prisma/schema.prisma
ENV DELTALLM_PRISMA_MAX_ATTEMPTS=30
ENV DELTALLM_PRISMA_SLEEP_SECONDS=2

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health/liveliness" || exit 1

CMD ["sh", "-c", "python -m src.prisma_bootstrap --mode \"${DELTALLM_PRISMA_STARTUP_MODE}\" --schema \"${DELTALLM_PRISMA_SCHEMA_PATH}\" --max-attempts \"${DELTALLM_PRISMA_MAX_ATTEMPTS}\" --sleep-seconds \"${DELTALLM_PRISMA_SLEEP_SECONDS}\" && exec uvicorn src.main:app --host ${HOST} --port ${PORT}"]
