#!/usr/bin/env bash
# =============================================================================
# backup-db.sh — nightly logical backup of the containerised PostgreSQL
# =============================================================================
# Runs pg_dump inside the postgres container and uploads the compressed dump to
# S3 with a date-stamped key. Since Postgres runs on the instance (no RDS), this
# is the durability layer. Schedule via cron on the host:
#
#   0 3 * * *  /opt/parliament/.../deploy-aws/backup-db.sh >> /var/log/pgbackup.log 2>&1
#
# Requires: instance role permission s3:PutObject on the backup bucket, and the
# .env present (for POSTGRES_USER / POSTGRES_DB).
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "${HERE}"

# shellcheck disable=SC1091
set -a; [ -f ./.env ] && . ./.env; set +a

BUCKET="${BACKUP_BUCKET:?set BACKUP_BUCKET to the S3 bucket name}"
REGION="${AWS_REGION:-us-east-1}"
DB_USER="${POSTGRES_USER:-postprocess}"
DB_NAME="${POSTGRES_DB:-postprocess}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEY="pg/${DB_NAME}/${STAMP}.sql.gz"

CID="$(docker compose -f docker-compose.prod.yml ps -q postgres)"
if [ -z "${CID}" ]; then
  echo "ERROR: postgres container not running" >&2
  exit 1
fi

echo "==> Dumping ${DB_NAME} and uploading to s3://${BUCKET}/${KEY}"
docker exec "${CID}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" \
  | gzip -9 \
  | aws s3 cp - "s3://${BUCKET}/${KEY}" --region "${REGION}"

echo "==> Backup complete: s3://${BUCKET}/${KEY}"
