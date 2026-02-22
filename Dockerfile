FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
COPY prisma ./prisma
RUN prisma generate --schema=./prisma/schema.prisma

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /root/.local
COPY --from=builder /app /app
COPY config.example.yaml ./config.example.yaml

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV DELTALLM_CONFIG_PATH=/app/config/config.yaml
ENV HOST=0.0.0.0
ENV PORT=4000

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health/liveliness" || exit 1

CMD ["sh", "-c", "uvicorn src.main:app --host ${HOST} --port ${PORT}"]
