---
inclusion: always
---

# Project Overview — Node Transcription

A Deepgram Speech-to-Text app with a Node.js/Express gateway and a React + Vite (TypeScript) frontend. It also integrates Khaya AI (GhanaNLP) for African-language transcription and a Ghana-focused entity correction post-processing pipeline that exists in two implementations: the legacy in-process JavaScript pipeline and a Python FastAPI microservice.

## Architecture

- Gateway/backend: Express 5 on port 8081 (`server.js`, CommonJS, Node >= 24)
- Frontend: React 19 + Vite 7 + TypeScript on port 8080 (`frontend/`)
- Postprocessing Service: Python 3.12 FastAPI on port 8082 (`services/postprocess/`), PostgreSQL-backed datasets
- API: REST — see `api-contracts.md` for the full endpoint table
- Auth: JWT session tokens via `/api/session`, enforced by `requireSession`
- Deepgram SDK: `@deepgram/sdk` v4.11.3 — Pre-recorded Speech-to-Text (`/v1/listen`)
- Khaya AI: GhanaNLP ASR (Twi, Ewe, Ga, Dagbani, and more), used standalone and as the correction engine in the hybrid pipeline (`lib/hybrid/`)

## Post-Processing Modes

`POSTPROCESS_MODE` selects the pipeline in `server.js`:

| Mode | Behaviour |
|------|-----------|
| `js` (default) | In-process: `lib/location-correction/` rule-based correction → year correction → optional Bedrock LLM pass |
| `python` | Calls the Postprocessing Service via `lib/postprocess-client.js`; degrades to raw transcript on failure |
| `off` | Returns the raw Deepgram transcript unmodified |

Unknown values warn and fall back to `js`.

## Key Conventions

- Package manager: `corepack pnpm` (pinned to `pnpm@10.0.0`)
- Backend: CommonJS. Frontend: TypeScript + TSX, ES modules.
- Frontend uses React Router v7, Framer Motion, Tailwind v4 + daisyUI, over an MD3 token layer
- Submodules: `frontend/` and `contracts/` (conformance tests)
- Environment: `.env` with `DEEPGRAM_API_KEY` (required, process exits without it). `KHAYA_API_KEY`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, and the `POSTPROCESS_*` group are optional. See `sample.env`.
- Commits: conventional commits format (`feat(scope): message`)

## File Layout

| Path | Role |
|------|------|
| `server.js` | Gateway — Deepgram route, session/metadata/audio-proxy routes, mode dispatch, Swagger UI |
| `routes/khaya.js`, `routes/hybrid.js` | Routers mounted at `/api/khaya` and `/api/transcription/hybrid` |
| `providers/khaya.js` | Khaya AI ASR provider (`transcribe`, `getLanguages`, `getApiKey`) |
| `lib/hybrid/` | Deepgram + Khaya hybrid confidence-correction pipeline |
| `lib/location-correction/index.js` | Rule-based Ghana entity correction engine |
| `lib/location-correction/year-correction.js` | Spoken year/date normalization |
| `lib/location-correction/*-dataset.js` | Persons, ministers, MPs, parties datasets |
| `lib/location-correction/bedrock-postprocess.js` | Amazon Bedrock (Claude) LLM post-processing |
| `lib/postprocess-client.js` | HTTP client for the Python service (timeout, one retry, circuit breaker) |
| `lib/postprocess-mode.js` | Degraded-mode response builder + success merge |
| `services/postprocess/` | Python FastAPI Postprocessing Service |
| `frontend/src/{main,App,router}.tsx` | Root render, app shell, route table |
| `frontend/vite.config.ts` | Vite + Tailwind + React plugins, `/api` proxy to backend |
| `test/` | Node test runner suites (`node --test`) |
| `deepgram.toml` | App metadata and lifecycle commands |
| `Makefile` | Build/run/test targets |
| `deploy/` | Caddy + Node production container |

## Running Locally

```bash
make start                  # backend (nodemon) + frontend in parallel
# Or separately:
node server.js                                                 # :8081
cd frontend && corepack pnpm run dev -- --port 8080 --no-open   # :8080
```

The Python service is optional and runs separately — see `backend-guide.md`.

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/session` | GET | None | Issue JWT session token |
| `/api/metadata` | GET | None | App metadata from deepgram.toml |
| `/api/transcription` | POST | JWT | Transcribe via Deepgram + post-processing |
| `/api/transcription/hybrid` | POST | JWT | Deepgram + Khaya AI hybrid correction |
| `/api/khaya/transcription` | POST | JWT | Transcribe via Khaya AI |
| `/api/khaya/languages` | GET | None | List Khaya-supported languages |
| `/health` | GET | None | Health check — status, uptime, postprocess mode, version |
