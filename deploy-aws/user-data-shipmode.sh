#!/usr/bin/env bash
# =============================================================================
# user-data-shipmode.sh — EC2 first-boot bootstrap for "ship pre-built images"
# =============================================================================
# Installs Docker + the compose plugin only. No git clone: the deploy artifacts
# (images, compose files, .env) are shipped over SSH afterward. Amazon Linux
# 2023, arm64.
# =============================================================================
set -euxo pipefail

dnf update -y
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user || true

# Docker Compose v2 plugin
DOCKER_CFG=/usr/local/lib/docker/cli-plugins
mkdir -p "${DOCKER_CFG}"
ARCH="$(uname -m)"  # aarch64 on Graviton
curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" \
  -o "${DOCKER_CFG}/docker-compose"
chmod +x "${DOCKER_CFG}/docker-compose"

# Landing directory for shipped artifacts
mkdir -p /opt/parliament
chown ec2-user:ec2-user /opt/parliament

echo "Bootstrap complete (ship mode). Docker + compose installed."
