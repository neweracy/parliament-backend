# AWS Deployment — Single-Host (cost-optimized)

Runs the **full** Ghana Parliament Hansard System (frontend + gateway + Python
postprocess + PostgreSQL/pgvector + Redis) on **one EC2 instance** with Docker
Compose. No ECS, no App Runner, no ALB, no RDS — the cheapest way to run the
whole system with RAG intact.

## Topology

```
Internet ──443──▶ Caddy (TLS, Let's Encrypt)
                    │
                    ▼
              gateway:8080  (bundled Caddy + Express + built SPA)
                    │
                    ├──▶ postprocess:8082  (Python FastAPI: correction + RAG)
                    │         │
                    │         └──▶ postgres:5432 (pgvector)  ──▶ EBS volume
                    ├──▶ postgres:5432 (Hansard CRUD)
                    └──▶ redis:6379 (cache)

Bedrock ◀── instance IAM role (no AWS keys on the box)
Deepgram / Khaya ◀── API keys from SSM Parameter Store
```

## Cost (approx., us-east-1)

| Item | Monthly |
|------|---------|
| EC2 `t4g.small` (2 vCPU Graviton, 2 GB) on-demand | ~$12 |
| 30 GB gp3 EBS root | ~$2.40 |
| SSM Parameter Store (standard tier) | $0 |
| S3 DB backups (a few GB) | <$1 |
| Data transfer (low traffic) | ~$1–3 |
| **Fixed infra total** | **~$16/month** |

Plus per-use **Deepgram** and **Bedrock** (unchanged from today). Drop to
`t4g.micro` (~$6/mo) for a light pilot, or buy a 1-year Compute Savings Plan to
cut the EC2 line ~30–40%.

> If 2 GB RAM proves tight (Postgres + Python + Node + Caddy), step up to
> `t4g.medium` (4 GB, ~$24/mo). Start small; resize is a stop/change/start.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.prod.yml` | The full stack for the host |
| `Caddyfile.prod` | Edge TLS + proxy to the gateway |
| `sample.env.prod` | Documents every env var the compose expects |
| `put-secrets.sh` | Seed secrets into SSM Parameter Store (run once, from laptop) |
| `env-from-ssm.sh` | On the host: materialise `.env` from SSM |
| `iam-instance-policy.json` | Instance role: Bedrock invoke + SSM read |
| `provision.sh` | Create IAM role, SG, and launch the EC2 instance |
| `user-data.sh` | First-boot bootstrap baked into the instance |
| `backup-db.sh` | Nightly `pg_dump` → S3 (cron) |

## One-time deploy

Run from a machine with the AWS CLI configured. These scripts default to the
**`prod`** named profile (account 487054651077) — override with `AWS_PROFILE=...`
if needed. Region is pinned to **us-east-1** for Bedrock regardless of the
profile's own default region.

```bash
# 1. Seed secrets into SSM (prompts for each value; nothing hits shell history)
cd deploy-aws
AWS_PROFILE=prod ./put-secrets.sh      # stores under /parliament/prod

# 2. Provision IAM + security group + the instance
AWS_PROFILE=prod \
REPO_URL=https://github.com/YOUR_ORG/Parliament-Project.git \
KEY_NAME=your-ec2-keypair \
./provision.sh
# prints the public IP when done
```

> The on-instance scripts (`env-from-ssm.sh`, `backup-db.sh`) authenticate via
> the EC2 instance IAM role, not a named profile — never set `AWS_PROFILE` there.

First boot installs Docker, clones the repo **with submodules**, pulls secrets
from SSM, builds the images, runs DB migrations (via the postprocess
entrypoint), and starts everything. Allow a few minutes.

```bash
# 3. Verify
curl http://<PUBLIC_IP>/health          # gateway health
# then point your domain's A record at <PUBLIC_IP> and set SITE_ADDRESS
# to that domain in SSM; Caddy fetches a Let's Encrypt cert automatically.
```

## Updating a running deployment

```bash
ssh ec2-user@<host>
cd /opt/parliament/transcript-end/deploy-aws     # or wherever the repo landed
git pull --recurse-submodules
./env-from-ssm.sh                                 # refresh secrets if rotated
docker compose -f docker-compose.prod.yml up -d --build
```

## Backups

Postgres runs on the box, so back it up. Create an S3 bucket, grant the instance
role `s3:PutObject` on it, then cron `backup-db.sh`:

```bash
crontab -e
# 0 3 * * * BACKUP_BUCKET=your-backup-bucket /opt/parliament/.../deploy-aws/backup-db.sh >> /var/log/pgbackup.log 2>&1
```

Restore: `gunzip -c dump.sql.gz | docker exec -i <postgres_cid> psql -U postprocess -d postprocess`.

**Never** run `docker compose down -v` — it deletes the `pgdata` volume.

## ⚠️ Bedrock region caveat

The default model id `us.anthropic.claude-haiku-4-5-...` is a **US** cross-region
inference profile and only resolves in US regions. Your CLI is currently on
`eu-central-1`. Choose one:

- Deploy in a **US region** (`us-east-1`), keep the default model id, OR
- Deploy in `eu-central-1` and set `BEDROCK_MODEL_ID` to an **`eu.`** inference
  profile (e.g. an `eu.anthropic...` id) available in that region, and confirm
  the model is enabled in the Bedrock console for that region.

Set the choice via `put-secrets.sh` (`BEDROCK_MODEL_ID`) and `AWS_REGION`.

## Security notes

- Secrets live only in SSM (SecureString) and the on-host `.env` (mode 600).
- Bedrock uses the **instance role** — no AWS access keys anywhere on the box.
- SSH is locked to your IP at provision time; 80/443 are open to the world.
- `.env` and any filled secrets are git-ignored — only templates are committed.
```
