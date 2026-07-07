---
inclusion: fileMatch
fileMatchPattern: "deploy/**,fly.toml,Dockerfile,.dockerignore"
---

# Deployment Guide

## Production Stack

- Caddy reverse proxy (static files + API proxy)
- Node.js backend (Express)
- Single container on Fly.io

## Container Layout (`deploy/Dockerfile`)

Multi-stage build:
1. Stage 1: Build frontend with Vite (`pnpm build`)
2. Stage 2: Production image with Caddy + Node.js
   - Caddy serves static frontend files
   - Caddy proxies `/api/*` to Node.js backend on :8081

## Caddy Configuration (`deploy/Caddyfile`)

- Serves frontend static files from `/srv/frontend`
- Reverse proxies `/api/*` to `localhost:8081`
- Rate limiting configured for API endpoints
- Automatic HTTPS in production (Fly.io handles TLS)

## Fly.io (`fly.toml`)

- App runs as a single machine
- Internal port exposed via Fly proxy
- Health check on `/api/metadata`

## Environment Variables in Production

| Variable | Required | Notes |
|----------|----------|-------|
| `DEEPGRAM_API_KEY` | Yes | Set via `fly secrets set` |
| `SESSION_SECRET` | Yes | Set a strong random value in prod |
| `PORT` | No | Defaults to 8081 |
| `HOST` | No | Defaults to 0.0.0.0 |

## Deploy Commands

```bash
# Deploy to Fly.io
fly deploy

# Set secrets
fly secrets set DEEPGRAM_API_KEY=xxx SESSION_SECRET=xxx
```
