#!/usr/bin/env bash
# =============================================================================
# env-from-ssm.sh — materialise .env from AWS SSM Parameter Store
# =============================================================================
# Reads the application secrets from SSM (SecureString, free tier) and writes
# them to ./.env next to docker-compose.prod.yml. Run on the EC2 host at boot
# or before `docker compose up`. Bedrock uses the instance IAM role, so no AWS
# access keys are written here.
#
# Usage:  ./env-from-ssm.sh [SSM_PREFIX]
#   SSM_PREFIX defaults to /parliament/prod
#
# Requires: aws CLI + the instance role permission ssm:GetParametersByPath.
# =============================================================================
set -euo pipefail

# Runs ON the EC2 instance and authenticates via the instance IAM role — do NOT
# set AWS_PROFILE here. Named profiles are only for the provisioning laptop.
SSM_PREFIX="${1:-/parliament/prod}"
REGION="${AWS_REGION:-us-east-1}"
OUT="$(dirname "$0")/.env"

echo "==> Fetching parameters from ${SSM_PREFIX} in ${REGION}"

# Pull every parameter under the prefix, decrypted, as name/value pairs.
mapfile -t PAIRS < <(
  aws ssm get-parameters-by-path \
    --path "${SSM_PREFIX}" \
    --with-decryption \
    --recursive \
    --region "${REGION}" \
    --query "Parameters[].[Name,Value]" \
    --output text
)

if [ "${#PAIRS[@]}" -eq 0 ]; then
  echo "ERROR: no parameters found under ${SSM_PREFIX}" >&2
  exit 1
fi

: > "${OUT}"
chmod 600 "${OUT}"

for line in "${PAIRS[@]}"; do
  # Each line is "<full/name>\t<value>"; the env key is the last path segment.
  name="${line%%$'\t'*}"
  value="${line#*$'\t'}"
  key="${name##*/}"
  printf '%s=%s\n' "${key}" "${value}" >> "${OUT}"
done

# Non-secret runtime config (safe to keep in the clear).
{
  echo "AWS_REGION=${REGION}"
} >> "${OUT}"

echo "==> Wrote ${OUT} with ${#PAIRS[@]} parameters (mode 600)"
