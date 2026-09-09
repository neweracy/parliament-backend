# CI/CD Pipeline

GitHub Actions, no external registry (zero extra AWS cost). Images are built in
CI and shipped to the EC2 host over SSH, deployed with a health-gated
auto-rollback.

## Workflows

| File | Trigger | Does |
|------|---------|------|
| `.github/workflows/ci.yml` | every push / PR | lint + tests (backend, postprocess) + Docker build check |
| `.github/workflows/cd.yml` | tag `v*` (or manual) | build arm64 images → ship to EC2 → deploy → health-gate → rollback on failure |

## How to release

```bash
git tag v1.0.0
git push origin v1.0.0
```

CD builds the images, ships them to the box, tags them `:current`, runs
`docker compose up`, waits for `/health`, and **auto-rolls back** to the prior
`:current` if the health check fails. Old releases are pruned (keeps last 3).

Manual run: Actions → "CD (build + deploy to EC2)" → Run workflow.

## Required GitHub configuration

### Secrets (Settings → Secrets and variables → Actions → Secrets)

| Secret | Value |
|--------|-------|
| `EC2_SSH_KEY` | Contents of `deploy-aws/parliament-key.pem` (the private key) |
| `EC2_HOST` | `3.237.29.243` (or the domain once set) |
| `EC2_USER` | `ec2-user` |
| `HANSARD_PAT` | (optional) PAT with read access to `bigdataghana/Hansard` if it is private. Omit if public. |

### Variables (Settings → Secrets and variables → Actions → Variables)

| Variable | Value |
|----------|-------|
| `PUBLIC_HOST` | `3-237-29-243.sslip.io` (or the domain) |
| `HANSARD_REF` | `feat/qa-assistant-recommendations` |
| `COGNITO_DOMAIN` | `parliament-hansard-bdg.auth.us-east-1.amazoncognito.com` |
| `COGNITO_CLIENT_ID` | `jhnvd1i76l4dkafni5secfo4m` |
| `COGNITO_REDIRECT_URI` | `https://3-237-29-243.sslip.io/callback` |
| `COGNITO_LOGOUT_URI` | `https://3-237-29-243.sslip.io` |

> The EC2 instance keeps using its **IAM role** for Bedrock — no AWS keys in CI.
> Runtime app secrets (Deepgram, Cognito ids, DB) stay in the on-host `.env`
> (from SSM) and are not touched by the pipeline.

### GitHub Environment

Create an environment named **`production`** (Settings → Environments). Add
required reviewers there if you want a manual approval gate before deploy.

## Rollback

Automatic on failed health check. Manual rollback to a prior tag:

```bash
ssh ec2-user@<host>
cd /opt/parliament
docker tag parliament-gateway:vX.Y.Z parliament-gateway:current
docker tag parliament-postprocess:vX.Y.Z parliament-postprocess:current
docker compose -f docker-compose.prod.yml -f docker-compose.ship.yml up -d
```

(Released image tags are retained on the box; `docker images | grep parliament`.)

## Notes

- ARM builds run under QEMU emulation on GitHub-hosted runners — slower
  (~15-20 min) but free. ARM-native runners would be faster but cost money.
- The manual `deploy-aws/*.ps1` scripts (ship, push-chunks) are the pre-CI/CD
  laptop workflow, kept for reference. CI/CD does not use them.
- DB migrations run automatically via the postprocess container entrypoint.
