#!/usr/bin/env bash
# =============================================================================
# user-data.sh — EC2 first-boot bootstrap (Amazon Linux 2023, arm64)
# =============================================================================
# Installs Docker + compose plugin + git, clones the repo with submodules,
# pulls secrets from SSM, and brings up the full stack. Runs once at launch.
#
# Replace REPO_URL / REPO_REF below, or bake them in when calling provision.sh.
# =============================================================================
set -euxo pipefail

REPO_URL="${REPO_URL:-https://github.com/YOUR_ORG/Parliament-Project.git}"
REPO_REF="${REPO_REF:-main}"
APP_DIR="/opt/parliament"
SSM_PREFIX="${SSM_PREFIX:-/parliament/prod}"

# --- System packages ---
dnf update -y
dnf install -y docker git
systemctl enable --now docker

# Docker Compose v2 plugin
DOCKER_CFG=/usr/local/lib/docker/cli-plugins
mkdir -p "${DOCKER_CFG}"
ARCH="$(uname -m)"  # aarch64 on Graviton
curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" \
  -o "${DOCKER_CFG}/docker-compose"
chmod +x "${DOCKER_CFG}/docker-compose"

# --- Clone the umbrella repo with submodules (frontend + backend) ---
git clone --recurse-submodules --branch "${REPO_REF}" "${REPO_URL}" "${APP_DIR}"
cd "${APP_DIR}"

# The compose file lives in the backend submodule under deploy-aws/.
# Adjust COMPOSE_DIR if your layout differs.
COMPOSE_DIR="${APP_DIR}/transcript-end/deploy-aws"
if [ ! -d "${COMPOSE_DIR}" ]; then
  # Fallback: standalone backend checkout
  COMPOSE_DIR="${APP_DIR}/deploy-aws"
fi
cd "${COMPOSE_DIR}"

# --- Materialise secrets from SSM and boot the stack ---
export AWS_REGION="$(curl -s http://169.254.169.254/latest/meta-data/placement/region || echo us-east-1)"
chmod +x ./env-from-ssm.sh
SSM_PREFIX="${SSM_PREFIX}" ./env-from-ssm.sh "${SSM_PREFIX}"

docker compose -f docker-compose.prod.yml up -d --build

echo "Bootstrap complete. Stack starting under ${COMPOSE_DIR}."
