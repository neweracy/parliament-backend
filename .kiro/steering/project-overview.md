---
inclusion: always
---

# Project Overview — Node Transcription

This is a Deepgram Speech-to-Text demo app with a Node.js/Express backend and a React + Vite (TypeScript) frontend. It also integrates Khaya AI (GhanaNLP) for African-language transcription and a Ghana-focused entity correction post-processing pipeline (rule-based datasets + optional Amazon Bedrock LLM pass).

## Architecture

- Backend: Express on port 8081 (`server.js`, CommonJS)
- Frontend: React + Vite + TypeScript on port 8080 (`frontend/`)
- API: REST — `POST /api/transcription`, `POST /api/transcription/hybrid`, `POST /api/khaya/transcription`, `GET /api/khaya/languages`, `GET /api/session`, `GET /api/metadata`
- Auth: JWT session tokens via `/api/session`
- Deepgram SDK: `@deepgram/sdk` v4.11.3 — Pre-recorded Speech-to-Text (`/v1/listen`)
- Khaya AI: GhanaNLP ASR (Twi, Ewe, Ga, Dagbani, and more), used standalone and as the correction engine in the hybrid pipeline (`lib/hybrid/`)
- Post-processing: `lib/location-correction/` — rule-based Ghana location/person/party correction, plus optional Bedrock LLM refinement (`lib/location-correction/bedrock-postprocess.js`)

## Key Conventions

- Package manager: `corepack pnpm` (pinned to v10.0.0)
- Backend: CommonJS. Frontend: TypeScript + JSX/TSX, ES modules.
- Frontend uses React Router v6, Framer Motion, MD3 token layer
- Contracts submodule at `contracts/` for conformance tests
- Environment: `.env` file with `DEEPGRAM_API_KEY` (required); `KHAYA_API_KEY` and `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are optional (enable Khaya and Bedrock respectively)
- Commits: conventional commits format (`feat(scope): message`)

## File Layout

| Path | Role |
|------|------|
| `server.js` | Express backend — all API routes, post-processing wiring |
| `providers/khaya.js` | Khaya AI ASR provider |
| `lib/hybrid/` | Deepgram + Khaya hybrid confidence-correction pipeline |
| `lib/location-correction/index.js` | Rule-based Ghana entity correction engine |
| `lib/location-correction/*-dataset.js` | Persons, MPs, parties datasets consumed by both correction stages |
| `lib/location-correction/bedrock-postprocess.js` | Amazon Bedrock (Claude) LLM post-processing |
| `frontend/src/main.tsx` | React root render |
| `frontend/src/App.tsx` | App shell — NavBar, Router Outlet, Footer |
| `frontend/src/router.tsx` | Route definitions with React.lazy pages |
| `frontend/vite.config.ts` | Vite + React plugin, proxy to backend |
| `deepgram.toml` | App metadata and lifecycle commands |
| `Makefile` | Build/run targets |
| `deploy/` | Docker + Caddy production config |

## Running Locally

```bash
make start
# Or separately:
node server.js              # Backend on :8081
cd frontend && corepack pnpm run dev -- --port 8080 --no-open  # Frontend on :8080
```
