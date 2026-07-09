# node-transcription

Node.js transcription app with two AI engines: Deepgram (Speech-to-Text) and Khaya AI (Ghanaian languages).

## Architecture

- **Backend:** Node.js / Express on port 8081 (`server.js`, CommonJS)
- **Frontend:** React + Vite + TypeScript on port 8080 (`frontend/`, ES modules + JSX/TSX)
- **API type:** REST
- **Deepgram API:** Pre-recorded Speech-to-Text (`/v1/listen`) via `@deepgram/sdk`
- **Khaya AI:** GhanaNLP ASR v3 for African languages (Twi, Ewe, Ga, and more)
- **Auth:** JWT session tokens via `/api/session`, enforced by `requireSession` middleware

## Key Files

| File | Purpose |
|------|---------|
| `server.js` | Main backend — Deepgram routes, session auth, metadata, helpers |
| `providers/khaya.js` | Khaya AI ASR provider (transcribe + getLanguages) |
| `routes/khaya.js` | Express router for Khaya endpoints, mounted at `/api/khaya` |
| `deepgram.toml` | Metadata, lifecycle commands, tags |
| `Makefile` | Standardized build/run targets |
| `sample.env` | Environment variable template |
| `frontend/src/main.tsx` | React root render |
| `frontend/src/App.tsx` | App shell — NavBar, Outlet, Footer, ThemeProvider |
| `frontend/src/router.tsx` | Route definitions with React.lazy pages |
| `frontend/src/pages/` | Landing, Transcribe, Projects, History, About |
| `frontend/src/services/` | Pure-JS/TS service layer (transcription, history-repo, project-repo, export) |
| `frontend/vite.config.ts` | Vite + React plugin, `/api` proxy to backend |
| `deploy/Dockerfile` | Production container (Caddy + backend) |
| `deploy/Caddyfile` | Reverse proxy, rate limiting, static serving |

See `frontend/AGENTS.md` for frontend-specific conventions.

## Quick Start

```bash
# Initialize (clone submodules + install deps)
make init

# Set up environment
test -f .env || cp sample.env .env  # then set DEEPGRAM_API_KEY (and KHAYA_API_KEY)

# Start both servers
make start
# Backend: http://localhost:8081
# Frontend: http://localhost:8080
```

## Start / Stop

**Start (recommended):** `make start`

**Start separately:**
```bash
# Terminal 1 — Backend
node server.js

# Terminal 2 — Frontend
cd frontend && corepack pnpm run dev -- --port 8080 --no-open
```

**Stop all:** `lsof -ti:8080,8081 | xargs kill -9 2>/dev/null`

**Clean rebuild:**
```bash
rm -rf node_modules frontend/node_modules frontend/.vite
make init
```

## Dependencies

- **Backend:** root `package.json` — managed with `corepack pnpm` (pinned to v10.0.0). Pin exact versions (no `^`/`~`).
- **Frontend:** `frontend/package.json` — Vite dev server, React, React Router, Framer Motion, daisyUI.
- **Submodules:** `frontend/` and `contracts/` (conformance tests).

Install: `corepack pnpm install` — Frontend: `cd frontend && corepack pnpm install`

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/session` | GET | None | Issue JWT session token |
| `/api/metadata` | GET | None | App metadata (useCase, framework, language) |
| `/api/transcription` | POST | JWT | Transcribe audio file/URL via Deepgram pre-recorded API |
| `/api/khaya/transcription` | POST | JWT | Transcribe audio via Khaya AI (requires `language` code) |
| `/api/khaya/languages` | GET | None | List Khaya-supported languages |

## Backend Conventions

- CommonJS (`require`/`module.exports`), Express 5.x, `cors`, Multer memory storage.
- All protected routes use the `requireSession` JWT middleware.
- Error responses follow a consistent shape: `{ error: { type, code, message } }`.
- Add new endpoints after existing routes, before `app.listen`, and log them in the startup banner.

### Khaya AI Notes

- Khaya requires an explicit `language` code per request and does **not** auto-detect language.
- Supported codes include Twi (`tw`), Ewe (`ee`), Ga (`gaa`), Dagbani (`dag`).
- Configured via `KHAYA_API_KEY`; endpoints return 500 if unset.

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `DEEPGRAM_API_KEY` | Yes | — | Deepgram API key |
| `KHAYA_API_KEY` | For Khaya | — | Khaya AI (GhanaNLP) subscription key |
| `KHAYA_ASR_VERSION` | No | `v3` | Khaya ASR version |
| `PORT` | No | `8081` | Backend server port |
| `HOST` | No | `0.0.0.0` | Backend bind address |
| `SESSION_SECRET` | No | — | JWT signing secret (production) |

## Specs

Feature specs live in `.kiro/specs/`. Each has `requirements.md`, `design.md`, and `tasks.md`.
Consult the relevant spec before implementing a feature it covers (e.g. `hybrid-confidence-transcription`).

## Knowledge Graph (graphify)

A local knowledge graph lives in `graphify-out/`. For codebase, architecture, or dependency
questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or
`graphify explain "<concept>"` over grepping. Rebuild after code changes with
`graphify update . --no-cluster` (local AST, no API key needed).

## Conventional Commits

All commits must follow conventional commits format. Never include `Co-Authored-By` lines.

```
feat(node-transcription): add diarization support
fix(node-transcription): resolve session handling
refactor(node-transcription): simplify session endpoint
chore(deps): update frontend submodule
```

Scope is typically `node-transcription` or `deps`.

## Testing

```bash
# Backend unit tests
corepack pnpm test

# Conformance tests (requires app running)
make test

# Manual endpoint check
curl -sf http://localhost:8081/api/metadata | python3 -m json.tool
curl -sf http://localhost:8081/api/session | python3 -m json.tool
```
