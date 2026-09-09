# Seeds SSM Parameter Store with all non-external secrets (generated) + Cognito
# config. Deepgram/Khaya are added separately when the dev provides them.
# Usage: pwsh ./seed-ssm.ps1
$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = "prod"
$env:AWS_REGION = "us-east-1"
$prefix = "/parliament/prod"

function New-Secret([int]$bytes) {
  $b = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  ($b | ForEach-Object { $_.ToString('x2') }) -join ''
}

$tags = "Key=Environment,Value=production Key=Project,Value=parliament Key=Owner,Value=app-team Key=Client,Value=bdg-internal Key=CostCenter,Value=bdg-parliament Key=ManagedBy,Value=manual"

function Put-Param([string]$name, [string]$value) {
  $full = "$prefix/$name"
  aws ssm put-parameter --name $full --type SecureString --value $value --overwrite --region us-east-1 | Out-Null
  # Tag it (put-parameter --overwrite drops tags, so tag separately)
  $tagArgs = $tags -split ' '
  aws ssm add-tags-to-resource --resource-type Parameter --resource-id $full --tags $tagArgs --region us-east-1 2>$null | Out-Null
  "  stored $full"
}

Write-Host "==> Generating and storing secrets under $prefix"
Put-Param "SERVICE_TOKEN"           (New-Secret 32)
Put-Param "SESSION_SECRET"          (New-Secret 48)
Put-Param "POSTGRES_USER"           "postprocess"
Put-Param "POSTGRES_PASSWORD"       (New-Secret 24)
Put-Param "POSTGRES_DB"             "postprocess"
Put-Param "SITE_ADDRESS"            "3-237-29-243.sslip.io"
Put-Param "AWS_REGION"              "us-east-1"
Put-Param "BEDROCK_MODEL_ID"        "us.anthropic.claude-haiku-4-5-20251001-v1:0"
Put-Param "AUTH_MODE"               "cognito"
Put-Param "COGNITO_USER_POOL_ID"    "us-east-1_3UH8Mk0sH"
Put-Param "COGNITO_APP_CLIENT_ID"   "jhnvd1i76l4dkafni5secfo4m"
Put-Param "COGNITO_REGION"          "us-east-1"
Put-Param "COGNITO_DOMAIN"          "parliament-hansard-bdg.auth.us-east-1.amazoncognito.com"

Write-Host "==> Done. Deepgram/Khaya added separately once provided."
