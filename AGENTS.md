# node-transcription

Node.js transcription app with two AI engines: Deepgram (Speech-to-Text) and Khaya AI (Ghanaian languages), plus a Ghana-focused entity correction post-processing pipeline (rule-based + Amazon Bedrock LLM).

## Architecture

- **Backend:** Node.js / Express on port 8081 (`server.js`, CommonJS)
- **Frontend:** React + Vite + TypeScript on port 8080 (`frontend/`, ES modules + JSX/TSX)
- **API type:** REST
- **Deepgram API:** Pre-recorded Speech-to-Text (`/v1/listen`) via `@deepgram/sdk`
- **Khaya AI:** GhanaNLP ASR v3 for African languages (Twi, Ewe, Ga, and more)
- **Hybrid pipeline:** Deepgram primary + Khaya AI correction for low-confidence segments (`lib/hybrid/`)
- **Post-processing:** Rule-based Ghana entity correction (`lib/location-correction/`) + optional Amazon Bedrock LLM pass for accuracy beyond the static datasets
- **Auth:** JWT session tokens via `/api/session`, enforced by `requireSession` middleware

## Key Files

| File | Purpose |
|------|---------|
| `server.js` | Main backend — Deepgram routes, session auth, metadata, post-processing wiring |
| `providers/khaya.js` | Khaya AI ASR provider (transcribe + getLanguages) |
| `lib/hybrid/` | Hybrid confidence pipeline — Deepgram + Khaya AI correction for low-confidence words |
| `lib/location-correction/index.js` | Rule-based correction engine — fused/split/hyphenated/spelling fixes for Ghana locations, persons, MPs, and parties |
| `lib/location-correction/persons-dataset.js` | Presidents, VPs, Speakers, ministers dataset |
| `lib/location-correction/mps-dataset.js` | Members of Parliament dataset (current + previous parliament) |
| `lib/location-correction/parties-dataset.js` | Registered + historical Ghana political parties dataset |
| `lib/location-correction/bedrock-postprocess.js` | Amazon Bedrock (Claude) LLM post-processing pass, dataset-aware, parallel-batched |
| `deepgram.toml` | Metadata, lifecycle commands, tags |
| `Makefile` | Standardized build/run targets |
| `sample.env` | Environment variable template |
| `frontend/src/main.tsx` | React root render |
| `frontend/src/App.tsx` | App shell — NavBar, Outlet, Footer, ThemeProvider |
| `frontend/src/router.tsx` | Route definitions with React.lazy pages |
| `frontend/src/pages/` | Landing, Transcribe, Projects, History, About |
| `frontend/src/services/` | Pure-JS/TS service layer (transcription, history-repo, project-repo, export) |
| `frontend/src/components/features/TranscriptViewer.tsx` | Renders transcript with entity correction highlighting (location/person/party/AI icons) |
| `frontend/src/components/features/ResultsSidebar.tsx` | Entities panel (Locations, People, Political Parties) + correction counts |
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
  - Key deps: `@deepgram/sdk`, `@aws-sdk/client-bedrock-runtime` (Bedrock post-processing), `ghana-locations` (base location dataset), `express`, `jsonwebtoken`, `multer`.
- **Frontend:** `frontend/package.json` — Vite dev server, React, React Router, Framer Motion, daisyUI.
- **Submodules:** `frontend/` and `contracts/` (conformance tests).

Install: `corepack pnpm install` — Frontend: `cd frontend && corepack pnpm install`

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/session` | GET | None | Issue JWT session token |
| `/api/metadata` | GET | None | App metadata (useCase, framework, language) |
| `/api/transcription` | POST | JWT | Transcribe audio file/URL via Deepgram, then run rule-based + Bedrock post-processing |
| `/api/transcription/hybrid` | POST | JWT | Deepgram primary + Khaya AI correction pipeline for low-confidence segments |
| `/api/khaya/transcription` | POST | JWT | Transcribe audio via Khaya AI (requires `language` code) |
| `/api/khaya/languages` | GET | None | List Khaya-supported languages |

## Post-Processing Pipeline

Every `/api/transcription` response passes through two correction stages before returning:

1. **Rule-based correction** (`lib/location-correction/index.js`) — instant, deterministic. Fixes fused/split/hyphenated/misspelled Ghana locations, presidents/ministers/MPs, and political parties (full name ↔ abbreviation). Adds `locationCorrected`, `entityKind` (`location`/`person`/`party`), `entityType` to corrected words, and an `entities` summary array on the response.
2. **Bedrock LLM post-processing** (`lib/location-correction/bedrock-postprocess.js`, optional) — splits the transcript into ~300-word chunks, runs up to 3 chunks in parallel per wave via Claude on Amazon Bedrock, using the same datasets injected into the system prompt as reference context. Adds `bedrockCorrected: true` to words it fixes. Skipped entirely if `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are not set; failures are non-fatal (falls back to rule-based result).

When adding new Ghana entities (locations, officials, parties), add them to the relevant dataset file in `lib/location-correction/` — both correction stages read from the same datasets.

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
| `HYBRID_CONFIDENCE_THRESHOLD` | No | `0.85` | Confidence below which a Deepgram word is sent to Khaya for correction in the hybrid pipeline |
| `HYBRID_GAP_TOLERANCE` | No | `0.5` | Max time gap (s) to group adjacent low-confidence words into one hybrid correction segment |
| `HYBRID_PADDING` | No | `0.25` | Extra audio (s) padded around each hybrid correction segment |
| `HYBRID_MAX_CALLS_PER_MODEL` | No | `3` | Max Khaya calls per language model per transcription in the hybrid pipeline |
| `AWS_ACCESS_KEY_ID` | For Bedrock | — | AWS access key for Bedrock LLM post-processing |
| `AWS_SECRET_ACCESS_KEY` | For Bedrock | — | AWS secret key for Bedrock LLM post-processing |
| `AWS_REGION` | For Bedrock | `us-east-1` | AWS region for Bedrock (cross-region inference profile IDs are region-prefixed, e.g. `us.anthropic...`) |
| `BEDROCK_MODEL_ID` | No | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model ID used for post-processing |
| `PORT` | No | `8081` | Backend server port |
| `HOST` | No | `0.0.0.0` | Backend bind address |
| `SESSION_SECRET` | No | — | JWT signing secret (production) |

Bedrock is entirely optional — if the AWS keys are unset, the backend silently skips that stage and returns the rule-based result.

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
