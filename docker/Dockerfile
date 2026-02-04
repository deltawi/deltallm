# DeltaLLM Docker Image - Optimized for faster rebuilds
# syntax=docker/dockerfile:1

FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first (for better caching)
COPY pyproject.toml README.md ./

# Install Python dependencies with cache mount for faster rebuilds
# Cache pip downloads and wheels between builds
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install ".[providers]"

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r deltallm && useradd -r -g deltallm deltallm

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code (this layer changes frequently, keep it last)
COPY deltallm/ ./deltallm/
COPY config/ ./config/
COPY data/ ./data/

# Copy alembic for migrations
COPY alembic.ini ./

# Create directories for logs and data
RUN mkdir -p /app/logs /app/data && \
    chown -R deltallm:deltallm /app

# Switch to non-root user
USER deltallm

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Default command - run the server
CMD ["python", "-m", "uvicorn", "deltallm.proxy.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
