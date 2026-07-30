#!/usr/bin/env sh
# =============================================================================
# Postprocessing Service container entrypoint
# =============================================================================
# Applies the Alembic schema and seeds the Dataset_Store (both idempotent)
# before starting the server, so a cold `docker compose up` comes up populated.
#
# Set RUN_MIGRATIONS=false to skip the schema+seed step (e.g. when a separate
# job owns migrations). DATABASE_URL must be set either way.
set -e

if [ "${RUN_MIGRATIONS:-true}" != "false" ]; then
  echo "[entrypoint] applying schema (alembic upgrade head)..."
  alembic upgrade head

  echo "[entrypoint] seeding datasets (idempotent)..."
  python scripts/migrate_js_datasets.py
else
  echo "[entrypoint] RUN_MIGRATIONS=false — skipping schema and seed"
fi

echo "[entrypoint] starting service..."
exec "$@"
