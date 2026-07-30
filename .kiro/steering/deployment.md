---
inclusion: fileMatch
fileMatchPattern: "deploy/**,fly.toml,Dockerfile,.dockerignore"
---

# Deployment Guide

## Production Stack

- Caddy reverse proxy (static files + API proxy + rate limiting)
- Node.js gateway (Express)
- Single container on Fly.io (`deepgram-node-transcription`, primary region `iad`)
- The Python Postprocessing Service deploys separately (ECS Fargate) and is only reached when `POSTPROCESS_MODE=python`

## Container Layout (`deploy/Dockerfile`)

Three stages:
1. `caddy-builder` — xcaddy build of Caddy with `caddy-ratelimit`
2. `frontend-builder` — `pnpm install --frozen-lockfile` + `pnpm build`, then injects a `<base href>` Caddy template directive into `dist/index.html` for subpath hosting
3. Runtime (`node:24-slim`) — production backend deps, backend source, built frontend at `/app/frontend/dist`, Caddy binary, `deploy/start.sh`

`start.sh` runs `$BACKEND_CMD` (`node server.js`) in the background and Caddy in the foreground. Container env: `PORT=8081`, `HOST=0.0.0.0`, exposes `8080`.

## Caddy Configuration (`deploy/Caddyfile`)

Listens on `:8080`:
- `/` and `/index.html` served through the `templates` handler from `/app/frontend/dist`
- `/api/session` — rate limited to 5 req/min per IP, proxied to `localhost:{$BACKEND_PORT:8081}`
- `/api/*` — rate limited to 120 req/min per IP, proxied to the backend
- `/health` — proxied to the backend
- everything else — static file server from `/app/frontend/dist`

Note: `/health` is proxied but the backend does not implement it. Health checks should target `/api/metadata`, or a `/health` route should be added to `server.js`.

## Fly.io (`fly.toml`)

- `[build] dockerfile = "deploy/Dockerfile"`
- `internal_port = 8080`, `force_https = true`
- `auto_stop_machines = 'stop'`, `auto_start_machines = true`, `min_machines_running = 0`
- Single shared-CPU VM, 256 MB. No `[[http_service.checks]]` block is defined.

## Environment Variables in Production

| Variable | Required | Notes |
|----------|----------|-------|
| `DEEPGRAM_API_KEY` | Yes | Process exits without it. Set via `fly secrets set` |
| `SESSION_SECRET` | Yes in prod | Random per-process if unset, which invalidates tokens on restart and across machines |
| `KHAYA_API_KEY` | No | Enables Khaya AI transcription + the hybrid pipeline |
| `KHAYA_ASR_VERSION` | No | Defaults to `v3` |
| `HYBRID_*` | No | Threshold / gap tolerance / padding / max calls per model |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | No | Enables Bedrock LLM post-processing in `js` mode; failures are non-fatal |
| `AWS_REGION` / `BEDROCK_MODEL_ID` | No | Default `us-east-1` and Claude Haiku 4.5 |
| `POSTPROCESS_MODE` | No | `js` (default) / `python` / `off` |
| `POSTPROCESS_URL` / `POSTPROCESS_TOKEN` | For `python` | Service base URL and shared Bearer token |
| `POSTPROCESS_TIMEOUT_MS` / `POSTPROCESS_BREAKER_THRESHOLD` / `POSTPROCESS_BREAKER_COOLDOWN_MS` | No | Defaults 20000 / 5 / 30000 |
| `PORT` | No | Defaults to 8081 |
| `HOST` | No | Defaults to 0.0.0.0 |
| `BACKEND_PORT` | No | Read by the Caddyfile; defaults to 8081 |

## Deploy Commands

```bash
fly deploy
fly secrets set DEEPGRAM_API_KEY=... SESSION_SECRET=...
```

## Postprocessing Service Deployment

- `services/postprocess/Dockerfile` — multi-stage `python:3.12-slim`, non-root `appuser`, `EXPOSE 8082`, `HEALTHCHECK` on `/health`, `STOPSIGNAL SIGTERM`, Uvicorn with `--workers ${UVICORN_WORKERS:-2} --timeout-graceful-shutdown ${DRAIN_TIMEOUT_SECONDS:-15}`
- `services/postprocess/deploy/ecs-task-definition.json` — Fargate task (512 CPU / 1024 MB, `awsvpc`), `SERVICE_TOKEN` and `DATABASE_URL` injected from Secrets Manager, logs to `/ecs/postprocess-service`
- `services/postprocess/deploy/iam-policy.json` — task-role policy for `bedrock:InvokeModel`
- `services/postprocess/docker-compose.yml` — local topology: PostgreSQL 16 (with `pg_trgm` init script) → postprocess → gateway, all on an internal bridge network. `docker compose down -v` drops the volume.
