# =============================================================================
# Postprocessing Service — Multi-stage Docker build
# =============================================================================
# Runs Uvicorn with configurable workers, exposes port 8082 by default.
# Graceful shutdown via SIGTERM allows in-flight requests to drain.

# --- Stage 1: Dependencies ---
FROM python:3.12-slim AS deps

WORKDIR /app

# Install system dependencies needed for psycopg binary
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir . && \
    pip cache purge

# --- Stage 2: Application ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install only runtime system deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application code, migrations, and the seed tooling
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./alembic.ini
COPY scripts/ ./scripts/
COPY datasets_export_raw.json ./datasets_export_raw.json
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Configuration defaults
ENV HOST=0.0.0.0 \
    PORT=8082 \
    UVICORN_WORKERS=2 \
    DRAIN_TIMEOUT_SECONDS=15

EXPOSE 8082

# Health check — ECS and Docker compose use this
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:${PORT:-8082}/health || exit 1

# Graceful shutdown: Uvicorn handles SIGTERM by completing in-flight requests
# within the timeout-graceful-shutdown period (mapped to DRAIN_TIMEOUT_SECONDS)
STOPSIGNAL SIGTERM

# Entrypoint applies migrations and seeds the datasets (idempotent) when
# RUN_MIGRATIONS is not "false", then exec's the CMD. This makes a cold
# `docker compose up` come up with a populated Dataset_Store.
ENTRYPOINT ["./docker-entrypoint.sh"]

CMD ["sh", "-c", "uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8082} --workers ${UVICORN_WORKERS:-2} --timeout-graceful-shutdown ${DRAIN_TIMEOUT_SECONDS:-15}"]
