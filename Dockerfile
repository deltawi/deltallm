FROM node:20-alpine AS frontend

WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY ui/ ./
RUN npm run build

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

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

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health/liveliness" || exit 1

CMD ["sh", "-c", "attempt=0; until prisma db push --schema=./prisma/schema.prisma --accept-data-loss; do attempt=$((attempt + 1)); if [ \"$attempt\" -ge 30 ]; then echo 'Database never became reachable for Prisma schema push' >&2; exit 1; fi; echo \"Waiting for database before Prisma schema push... (${attempt}/30)\"; sleep 2; done; exec uvicorn src.main:app --host ${HOST} --port ${PORT}"]
