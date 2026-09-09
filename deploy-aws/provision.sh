#!/usr/bin/env bash
# =============================================================================
# provision.sh — create the AWS resources for a single-host deployment
# =============================================================================
# Creates (idempotently):
#   - IAM role + instance profile (Bedrock invoke + SSM read)
#   - Security group (22 from your IP, 80/443 from anywhere)
#   - A t4g.small (Graviton, arm64) EC2 instance running Amazon Linux 2023
#     with an encrypted 30 GB gp3 root volume, launched with user-data.sh
#
# Prereqs: aws CLI configured with admin-ish creds, put-secrets.sh already run.
#
# Usage:
#   REPO_URL=https://github.com/you/Parliament-Project.git \
#   KEY_NAME=my-ec2-key \
#   ./provision.sh
# =============================================================================
set -euo pipefail

# Use the named CLI profile for every AWS call. Exported so the AWS CLI picks
# it up globally without threading --profile through each command.
export AWS_PROFILE="${AWS_PROFILE:-prod}"

# Pinned to us-east-1: the app's Bedrock model IDs are US inference profiles
# and are confirmed ACTIVE + invokable there. Override AWS_REGION only if you
# also switch BEDROCK_MODEL_ID to a profile available in that region.
REGION="${AWS_REGION:-us-east-1}"
NAME="${NAME:-parliament}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.small}"
VOLUME_SIZE="${VOLUME_SIZE:-30}"
SSM_PREFIX="${SSM_PREFIX:-/parliament/prod}"
KEY_NAME="${KEY_NAME:-}"
# Ship mode: no server-side git clone. Images and compose files are shipped over
# SSH after boot. Set USERDATA to override the bootstrap script.
USERDATA="${USERDATA:-user-data-shipmode.sh}"

ROLE_NAME="${NAME}-ec2-role"
PROFILE_NAME="${NAME}-ec2-profile"
POLICY_NAME="${NAME}-ec2-policy"
SG_NAME="${NAME}-sg"
HERE="$(cd "$(dirname "$0")" && pwd)"

# --- Mandatory resource tags (sourced from tags.env) ---
# shellcheck disable=SC1091
set -a; . "${HERE}/tags.env"; set +a

# Tag formats differ per AWS API:
#   TAGSPEC_* : Key=..,Value=.. list for --tag-specifications (EC2)
#   IAM_TAGS  : Key=..,Value=.. list for `aws iam ... --tags`
TAG_KVS="Key=Environment,Value=${TAG_ENVIRONMENT} \
Key=Project,Value=${TAG_PROJECT} \
Key=Owner,Value=${TAG_OWNER} \
Key=Client,Value=${TAG_CLIENT} \
Key=CostCenter,Value=${TAG_COSTCENTER} \
Key=ManagedBy,Value=${TAG_MANAGEDBY}"

echo "==> Profile: ${AWS_PROFILE}  Region: ${REGION}  Instance: ${INSTANCE_TYPE}"
echo "==> Tags: Environment=${TAG_ENVIRONMENT} Project=${TAG_PROJECT} Owner=${TAG_OWNER} Client=${TAG_CLIENT} CostCenter=${TAG_COSTCENTER} ManagedBy=${TAG_MANAGEDBY}"

# --- IAM role + instance profile ---
if ! aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "==> Creating IAM role ${ROLE_NAME}"
  aws iam create-role --role-name "${ROLE_NAME}" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]
    }' \
    --tags ${TAG_KVS} >/dev/null
fi

echo "==> Attaching inline policy ${POLICY_NAME}"
# Read the policy inline (avoids file:// path issues when the repo path has spaces)
POLICY_DOC="$(cat "${HERE}/iam-instance-policy.json")"
aws iam put-role-policy --role-name "${ROLE_NAME}" \
  --policy-name "${POLICY_NAME}" \
  --policy-document "${POLICY_DOC}"

if ! aws iam get-instance-profile --instance-profile-name "${PROFILE_NAME}" >/dev/null 2>&1; then
  echo "==> Creating instance profile ${PROFILE_NAME}"
  aws iam create-instance-profile --instance-profile-name "${PROFILE_NAME}" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "${PROFILE_NAME}" --role-name "${ROLE_NAME}"
  echo "    waiting for instance profile to propagate..."
  sleep 15
fi

# --- Default VPC + security group ---
VPC_ID="$(aws ec2 describe-vpcs --region "${REGION}" \
  --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)"
echo "==> Default VPC: ${VPC_ID}"

SG_ID="$(aws ec2 describe-security-groups --region "${REGION}" \
  --filters Name=group-name,Values="${SG_NAME}" Name=vpc-id,Values="${VPC_ID}" \
  --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")"

if [ "${SG_ID}" = "None" ] || [ -z "${SG_ID}" ]; then
  echo "==> Creating security group ${SG_NAME}"
  SG_ID="$(aws ec2 create-security-group --region "${REGION}" \
    --group-name "${SG_NAME}" --description "Parliament Hansard single-host" \
    --vpc-id "${VPC_ID}" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=${SG_NAME}},{Key=Environment,Value=${TAG_ENVIRONMENT}},{Key=Project,Value=${TAG_PROJECT}},{Key=Owner,Value=${TAG_OWNER}},{Key=Client,Value=${TAG_CLIENT}},{Key=CostCenter,Value=${TAG_COSTCENTER}},{Key=ManagedBy,Value=${TAG_MANAGEDBY}}]" \
    --query GroupId --output text)"
  MYIP="$(curl -s https://checkip.amazonaws.com || echo 0.0.0.0)"
  aws ec2 authorize-security-group-ingress --region "${REGION}" --group-id "${SG_ID}" \
    --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${MYIP}/32,Description=ssh-admin}]" >/dev/null
  aws ec2 authorize-security-group-ingress --region "${REGION}" --group-id "${SG_ID}" \
    --ip-permissions "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]" >/dev/null
  aws ec2 authorize-security-group-ingress --region "${REGION}" --group-id "${SG_ID}" \
    --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]" >/dev/null
fi
echo "==> Security group: ${SG_ID}"

# --- Latest Amazon Linux 2023 arm64 AMI ---
# describe-images is more reliable than the public SSM parameter under assumed
# roles (which may lack ssm:GetParameter on the public AMI path). Allow an
# explicit override via AMI_ID.
if [ -z "${AMI_ID:-}" ]; then
  AMI_ID="$(aws ec2 describe-images --region "${REGION}" --owners amazon \
    --filters "Name=name,Values=al2023-ami-2023.*-arm64" "Name=state,Values=available" \
    --query "reverse(sort_by(Images,&CreationDate))[0].ImageId" --output text)"
fi
if [ -z "${AMI_ID}" ] || [ "${AMI_ID}" = "None" ]; then
  echo "ERROR: could not resolve an AL2023 arm64 AMI" >&2; exit 1
fi
echo "==> AMI: ${AMI_ID}"

# --- User-data bootstrap (ship mode: Docker only, no clone) ---
# Base64-encode inline to avoid file:// path issues when the repo path has spaces.
USERDATA_B64="$(base64 -w0 "${HERE}/${USERDATA}" 2>/dev/null || base64 "${HERE}/${USERDATA}" | tr -d '\n')"

# --- Launch ---
KEY_ARG=()
[ -n "${KEY_NAME}" ] && KEY_ARG=(--key-name "${KEY_NAME}")

echo "==> Launching ${INSTANCE_TYPE} instance"
INSTANCE_ID="$(aws ec2 run-instances --region "${REGION}" \
  --image-id "${AMI_ID}" \
  --instance-type "${INSTANCE_TYPE}" \
  "${KEY_ARG[@]}" \
  --security-group-ids "${SG_ID}" \
  --iam-instance-profile "Name=${PROFILE_NAME}" \
  --block-device-mappings "DeviceName=/dev/xvda,Ebs={VolumeSize=${VOLUME_SIZE},VolumeType=gp3,Encrypted=true}" \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --user-data "${USERDATA_B64}" \
  --tag-specifications \
    "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}},{Key=Environment,Value=${TAG_ENVIRONMENT}},{Key=Project,Value=${TAG_PROJECT}},{Key=Owner,Value=${TAG_OWNER}},{Key=Client,Value=${TAG_CLIENT}},{Key=CostCenter,Value=${TAG_COSTCENTER}},{Key=ManagedBy,Value=${TAG_MANAGEDBY}}]" \
    "ResourceType=volume,Tags=[{Key=Name,Value=${NAME}-root},{Key=Environment,Value=${TAG_ENVIRONMENT}},{Key=Project,Value=${TAG_PROJECT}},{Key=Owner,Value=${TAG_OWNER}},{Key=Client,Value=${TAG_CLIENT}},{Key=CostCenter,Value=${TAG_COSTCENTER}},{Key=ManagedBy,Value=${TAG_MANAGEDBY}}]" \
  --query "Instances[0].InstanceId" --output text)"

echo "==> Instance launched: ${INSTANCE_ID}"
echo "    waiting for it to enter running state..."
aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"

PUBLIC_IP="$(aws ec2 describe-instances --region "${REGION}" --instance-ids "${INSTANCE_ID}" \
  --query "Reservations[0].Instances[0].PublicIpAddress" --output text)"

cat <<EOF

============================================================
 Provisioned.
   Instance : ${INSTANCE_ID}
   Public IP: ${PUBLIC_IP}
   Region   : ${REGION}

 Next:
   1. Point your domain's A record at ${PUBLIC_IP}, then set
      SITE_ADDRESS to that domain in SSM (put-secrets.sh) for TLS.
   2. First boot builds images and runs migrations — allow a few minutes.
   3. Check health once up:
        curl http://${PUBLIC_IP}/health
============================================================
EOF
