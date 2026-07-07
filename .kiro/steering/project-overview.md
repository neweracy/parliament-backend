---
inclusion: always
---

# Project Overview — Node Transcription

This is a Deepgram Speech-to-Text demo app with a Node.js/Express backend and a React + Vite frontend.

## Architecture

- Backend: Express on port 8081 (`server.js`)
- Frontend: React + Vite on port 8080 (`frontend/`)
- API: REST — `POST /api/transcription`, `GET /api/session`, `GET /api/metadata`
- Auth: JWT session tokens via `/api/session`
- Deepgram SDK: `@deepgram/sdk` v4.11.3 — Pre-recorded Speech-to-Text (`/v1/listen`)

## Key Conventions

- Package manager: `corepack pnpm` (pinned to v10.0.0)
- No TypeScript — plain JavaScript with JSX (CommonJS backend, ES modules + React frontend)
- Frontend uses React Router v6, Framer Motion, MD3 token layer
- Contracts submodule at `contracts/` for conformance tests
- Environment: `.env` file with `DEEPGRAM_API_KEY` (required)
- Commits: conventional commits format (`feat(scope): message`)

## File Layout

| Path | Role |
|------|------|
| `server.js` | Express backend — all API routes |
| `frontend/src/main.jsx` | React root render |
| `frontend/src/App.jsx` | App shell — NavBar, Router Outlet, Footer |
| `frontend/src/router.jsx` | Route definitions with React.lazy pages |
| `frontend/vite.config.js` | Vite + React plugin, proxy to backend |
| `deepgram.toml` | App metadata and lifecycle commands |
| `Makefile` | Build/run targets |
| `deploy/` | Docker + Caddy production config |

## Running Locally

```bash
make start
# Or separately:
node server.js              # Backend on :8081
cd frontend && pnpm dev -- --port 8080 --no-open  # Frontend on :8080
```
