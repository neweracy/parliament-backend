#!/usr/bin/env bash
# =============================================================================
# put-secrets.sh — seed SSM Parameter Store with the app secrets
# =============================================================================
# Run once (and whenever a secret rotates) from a trusted machine with admin
# AWS creds. Prompts for each value so nothing is echoed into shell history.
# Parameters are stored as SecureString under /parliament/prod (SSM standard
# tier is free). The EC2 instance role reads them at boot via env-from-ssm.sh.
#
# Usage:  ./put-secrets.sh [SSM_PREFIX]
# =============================================================================
set -euo pipefail

SSM_PREFIX="${1:-/parliament/prod}"
export AWS_PROFILE="${AWS_PROFILE:-prod}"
REGION="${AWS_REGION:-us-east-1}"

put() {
  local key="$1" prompt="$2"
  local value
  read -rsp "${prompt}: " value; echo
  if [ -z "${value}" ]; then
    echo "  (skipped ${key} — empty)"; return
  fi
  aws ssm put-parameter \
    --name "${SSM_PREFIX}/${key}" \
    --type SecureString \
    --value "${value}" \
    --overwrite \
    --region "${REGION}" >/dev/null
  echo "  stored ${SSM_PREFIX}/${key}"
}

echo "==> Seeding secrets under ${SSM_PREFIX} in ${REGION} (profile ${AWS_PROFILE})"
put SERVICE_TOKEN     "SERVICE_TOKEN (gateway<->postprocess shared token)"
put DEEPGRAM_API_KEY  "DEEPGRAM_API_KEY"
put KHAYA_API_KEY     "KHAYA_API_KEY"
put SESSION_SECRET    "SESSION_SECRET (JWT signing)"
put POSTGRES_USER     "POSTGRES_USER"
put POSTGRES_PASSWORD "POSTGRES_PASSWORD"
put POSTGRES_DB       "POSTGRES_DB"
put SITE_ADDRESS      "SITE_ADDRESS (domain or :80)"
put BEDROCK_MODEL_ID  "BEDROCK_MODEL_ID"
put AUTH_MODE         "AUTH_MODE (cognito for production)"
put COGNITO_USER_POOL_ID "COGNITO_USER_POOL_ID"
put COGNITO_APP_CLIENT_ID "COGNITO_APP_CLIENT_ID"
put COGNITO_REGION    "COGNITO_REGION (e.g. us-east-1)"
put COGNITO_DOMAIN    "COGNITO_DOMAIN (hosted UI domain)"
put VITE_COGNITO_DOMAIN "VITE_COGNITO_DOMAIN (same hosted UI domain)"
put VITE_COGNITO_CLIENT_ID "VITE_COGNITO_CLIENT_ID (same app client id)"
put VITE_COGNITO_REDIRECT_URI "VITE_COGNITO_REDIRECT_URI (http://<ip-or-domain>/callback)"
put VITE_COGNITO_LOGOUT_URI "VITE_COGNITO_LOGOUT_URI (http://<ip-or-domain>)"

echo "==> Done. Verify with: aws ssm get-parameters-by-path --path ${SSM_PREFIX} --recursive --region ${REGION}"
