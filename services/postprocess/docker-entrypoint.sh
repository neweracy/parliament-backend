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
  # Migrations are NON-FATAL: a transient failure (DB not ready yet, or the DB
  # being ahead of this image after a rollback) must not crash-loop the whole
  # service and take the site down. Retry a few times, then start anyway and
  # let /health + logs surface the problem. MIGRATIONS_REQUIRED=true restores
  # strict behaviour (exit on failure) if ever needed.
  echo "[entrypoint] applying schema (alembic upgrade head)..."
  migrated=""
  i=1
  while [ "$i" -le 5 ]; do
    if alembic upgrade head; then migrated="ok"; break; fi
    echo "[entrypoint] alembic attempt $i failed; retrying in 5s..."
    i=$((i + 1)); sleep 5
  done

  if [ "$migrated" = "ok" ]; then
    echo "[entrypoint] seeding datasets (idempotent)..."
    python scripts/migrate_js_datasets.py || echo "[entrypoint] WARN: dataset seed failed (non-fatal)"
  else
    echo "[entrypoint] WARN: migrations did not apply after retries."
    if [ "${MIGRATIONS_REQUIRED:-false}" = "true" ]; then
      echo "[entrypoint] MIGRATIONS_REQUIRED=true — refusing to start."; exit 1
    fi
    echo "[entrypoint] starting anyway (DB may already be at head or ahead)."
  fi
else
  echo "[entrypoint] RUN_MIGRATIONS=false — skipping schema and seed"
fi

echo "[entrypoint] starting service..."
exec "$@"
