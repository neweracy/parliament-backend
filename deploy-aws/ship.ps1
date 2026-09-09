# =============================================================================
# ship.ps1 — ship pre-built ARM images + compose files + .env to the EC2 box
# =============================================================================
# Path B deployment: no ECR, no server-side git. Saves the local arm64 images,
# scps them + the compose/Caddy files + a .env (materialised from SSM) to the
# instance, loads the images, and brings the stack up.
#
# Usage: pwsh ./ship.ps1
# =============================================================================
$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = "prod"
$env:AWS_REGION  = "us-east-1"

$IP   = "3.237.29.243"
$KEY  = "$PSScriptRoot\parliament-key.pem"
$USER = "ec2-user"
$REMOTE = "/opt/parliament"
$prefix = "/parliament/prod"

function SSM([string]$name) {
  (aws ssm get-parameter --name "$prefix/$name" --with-decryption --region us-east-1 --query "Parameter.Value" --output text).Trim()
}

# --- 1. Materialise .env from SSM ---
Write-Host "==> Building .env from SSM"
$names = @(
  'SERVICE_TOKEN','SESSION_SECRET','POSTGRES_USER','POSTGRES_PASSWORD','POSTGRES_DB',
  'SITE_ADDRESS','AWS_REGION','BEDROCK_MODEL_ID','RAG_MODEL_ID','AUTH_MODE',
  'COGNITO_USER_POOL_ID','COGNITO_APP_CLIENT_ID','COGNITO_REGION','COGNITO_DOMAIN',
  'DEEPGRAM_API_KEY','KHAYA_API_KEY'
)
$lines = @()
foreach ($n in $names) {
  $v = ""
  try { $v = SSM $n } catch { $v = "" }
  if ($v -and $v -ne "None") { $lines += "$n=$v" }
}
# Frontend build args are already baked into the image; not needed at runtime.
$envPath = "$PSScriptRoot\.env.ship"
$lines -join "`n" | Set-Content -NoNewline -Path $envPath -Encoding ascii
Write-Host "    wrote $($lines.Count) vars to .env.ship"

# --- 2. Save images to tarballs ---
Write-Host "==> Saving arm64 images (this takes a minute)"
docker save parliament-gateway:arm64     -o "$PSScriptRoot\_gateway-arm64.tar"
docker save parliament-postprocess:arm64 -o "$PSScriptRoot\_postprocess-arm64.tar"

# --- 3. Copy artifacts to the box ---
Write-Host "==> Copying files to ${IP}:${REMOTE}"
$scp = "scp -i `"$KEY`" -o StrictHostKeyChecking=no"
Invoke-Expression "$scp `"$PSScriptRoot\docker-compose.prod.yml`" ${USER}@${IP}:${REMOTE}/docker-compose.prod.yml"
Invoke-Expression "$scp `"$PSScriptRoot\docker-compose.ship.yml`" ${USER}@${IP}:${REMOTE}/docker-compose.ship.yml"
Invoke-Expression "$scp `"$PSScriptRoot\Caddyfile.prod`" ${USER}@${IP}:${REMOTE}/Caddyfile.prod"
Invoke-Expression "$scp `"$envPath`" ${USER}@${IP}:${REMOTE}/.env"
Invoke-Expression "$scp `"$PSScriptRoot\_gateway-arm64.tar`" ${USER}@${IP}:${REMOTE}/gateway.tar"
Invoke-Expression "$scp `"$PSScriptRoot\_postprocess-arm64.tar`" ${USER}@${IP}:${REMOTE}/postprocess.tar"

# --- 4. Load images + bring up the stack on the box ---
Write-Host "==> Loading images and starting stack on the box"
$remoteCmd = @"
set -e
cd $REMOTE
docker load -i gateway.tar
docker load -i postprocess.tar
# Pre-built images only; the ship override disables build on the box.
docker compose -f docker-compose.prod.yml -f docker-compose.ship.yml up -d
rm -f gateway.tar postprocess.tar
docker compose -f docker-compose.prod.yml -f docker-compose.ship.yml ps
"@
ssh -i "$KEY" -o StrictHostKeyChecking=no "${USER}@${IP}" $remoteCmd

Write-Host "==> Shipped. Check: https://3-237-29-243.sslip.io/health"
