# Ghana Parliament Hansard System

A full-stack parliamentary transcription and research platform for the Ghana Parliament. Processes audio recordings of parliamentary sittings into structured, searchable, citable transcripts with an AI-powered research assistant.

Built with a three-tier architecture: a React SPA frontend, an Express 5 API gateway handling transcription and authentication, and a Python/FastAPI postprocessing service providing entity correction, RAG-based search, and conversational Q&A over the parliamentary record.

## Tech Stack

### Frontend (Hansard/)

| Category | Technology |
|----------|-----------|
| Framework | React 18, TypeScript |
| Build Tool | Vite 6 |
| Styling | Tailwind CSS 4, Emotion (MUI theming) |
| UI Components | Radix UI (20+ primitives), MUI Material 7, shadcn/ui patterns (CVA + clsx + tailwind-merge) |
| Audio | WaveSurfer.js (waveform playback) |
| Charts | Recharts |
| Forms | React Hook Form |
| Animation | Motion (Framer Motion) |
| Markdown | react-markdown + remark-gfm |
| Drag & Drop | react-dnd |
| Testing | Vitest 4, Testing Library (React + DOM + user-event), fast-check (property-based) |

### Backend Gateway (Express)

| Category | Technology |
|----------|-----------|
| Runtime | Node.js 24 |
| Framework | Express 5 |
| ASR Providers | Deepgram SDK 4, Khaya AI (GhanaNLP) |
| Audio Processing | ffmpeg-static |
| Authentication | jsonwebtoken, jwks-rsa (Cognito RS256 validation), bcrypt |
| AWS SDKs | @aws-sdk/client-bedrock-runtime, @aws-sdk/client-cognito-identity-provider |
| Database | pg (PostgreSQL client) |
| File Uploads | Multer 2 |
| API Docs | swagger-ui-express |
| Config | dotenv, TOML |
| Testing | Node.js built-in test runner, fast-check (property-based), supertest, ESLint 9 |

### Postprocessing Service (Python/FastAPI)

| Category | Technology |
|----------|-----------|
| Framework | FastAPI 0.115, Uvicorn (ASGI) |
| Validation | Pydantic 2, pydantic-settings |
| Database | SQLAlchemy 2 (async), psycopg 3, Alembic (migrations) |
| Vector Store | pgvector (cosine similarity, HNSW indexing) |
| Full-Text Search | PostgreSQL tsvector + GIN index |
| **LLM/AI** | **LangChain 1.3**, **LangChain-AWS 1.6** (ChatBedrock, BedrockEmbeddings) |
| Agent Framework | LangChain Agents (`create_agent` with tool-based retrieval) |
| Text Splitting | langchain-text-splitters (RecursiveCharacterTextSplitter) |
| Embeddings | Amazon Titan Text Embeddings V2 (1024-dim vectors) |
| LLM | Amazon Bedrock — Claude (via ChatBedrock async invocation) |
| Retrieval | Hybrid search: LangChain BaseRetriever subclasses + Reciprocal Rank Fusion |
| Entity Matching | RapidFuzz (fuzzy string matching), pybktree (BK-tree for edit distance) |
| Observability | structlog (structured logging), aws-embedded-metrics (CloudWatch EMF) |
| AWS | boto3 (credential probing, Bedrock calls) |
| Auth | bcrypt (password hashing) |
| Linting | Ruff (check + format, line-length=100, Python 3.12 target) |
| Testing | pytest 8, pytest-asyncio, hypothesis (property-based), httpx |

### Infrastructure & Deployment

| Category | Technology |
|----------|-----------|
| Containerization | Docker (multi-stage: Go + Node 24 builder stages) |
| Reverse Proxy | Caddy 2 (custom build with caddy-ratelimit module via xcaddy) |
| Rate Limiting | Caddy rate-limit zones (session: 5 req/min, API: 120 req/min) |
| Hosting | Fly.io |
| Database | PostgreSQL 16 with pgvector + pg_trgm extensions |
| Package Manager | pnpm 10 (via corepack) |
| Orchestration | concurrently (multi-service dev startup) |
| Migrations | Alembic (offline + online modes, manual SQL) |

### AI/ML Pipeline

| Component | Technology |
|-----------|-----------|
| Speech-to-Text | Deepgram (English), Khaya AI (Ghanaian languages: Twi, Ewe, Dagbani, etc.) |
| Entity Correction | Rule-based datasets (Ghana locations, MPs, presidents, parties) + Bedrock LLM refinement |
| RAG Ingestion | LangChain RecursiveCharacterTextSplitter → Titan V2 embeddings → pgvector storage |
| RAG Retrieval | Hybrid: FulltextRetriever (tsvector) + VectorRetriever (pgvector cosine) → RRF fusion |
| Conversational Agent | LangChain `create_agent` with `search_hansard` tool (model decides when to search) |
| Grounded Answering | LangChain ChatBedrock with citation parsing and validation |
| Recommendations | Deterministic derivation from retrieved chunks (no extra model call) + LLM-backed search suggestions |

## Architecture

```
┌──────────────────┐     ┌───────────────────────┐     ┌─────────────────────────┐
│  Hansard (React) │────▶│  transcript-end       │────▶│  Postprocess Service    │
│  SPA Frontend    │ API │  Express Gateway      │ HTTP│  Python/FastAPI          │
│  Port 5173       │     │  Port 8081            │     │  Port 8082              │
└──────────────────┘     └───────────┬───────────┘     └────────────┬────────────┘
                                     │                              │
                              ┌──────┴──────┐              ┌───────┴────────┐
                              │ Deepgram    │              │ PostgreSQL 16  │
                              │ Khaya AI    │              │ (pgvector)     │
                              │ (ASR)       │              │ Port 5432      │
                              └─────────────┘              └───────┬────────┘
                                                                   │
                                                           ┌───────┴────────┐
                                                           │ Amazon Bedrock │
                                                           │ (Claude + Titan│
                                                           │  Embeddings)   │
                                                           └────────────────┘
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/transcription` | Deepgram transcription + post-processing |
| `POST` | `/api/transcription/hybrid` | Deepgram + Khaya AI hybrid confidence correction |
| `POST` | `/api/khaya/transcription` | Khaya AI transcription (African languages) |
| `GET` | `/api/khaya/languages` | List Khaya-supported languages |
| `GET` | `/api/session` | Issue a JWT session token |
| `GET` | `/api/metadata` | App metadata from `deepgram.toml` |
| `GET` | `/api/audio-proxy` | CORS-friendly remote audio proxy |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI (OpenAPI spec) |
| `GET` | `/api/settings` | Get app settings including export config |
| `PATCH` | `/api/settings` | Update settings fields |
| `GET` | `/api/settings/export` | Get export configuration |
| `PATCH` | `/api/settings/export` | Update export configuration |
| `GET` | `/api/dictionary` | Paginated dictionary list |
| `POST` | `/api/dictionary` | Add dictionary term |
| `DELETE` | `/api/dictionary/:term` | Remove dictionary term |
| `POST` | `/api/dictionary/import` | Bulk CSV import |
| `GET` | `/api/users` | List users (with search) |
| `POST` | `/api/users/invite` | Invite user via Cognito |
| `PATCH` | `/api/users/:userId/role` | Change user role |
| `PATCH` | `/api/users/:userId/status` | Activate/deactivate user |
| `POST` | `/api/ask` | Conversational Q&A (proxied to RAG agent) |
| `POST` | `/api/search` | Hybrid transcript search (proxied to RAG retriever) |
| `POST` | `/api/search/recommendations` | Search-query suggestions |
| `POST` | `/rag/search` | Direct hybrid retrieval over indexed chunks |
| `POST` | `/rag/ask` | Grounded Q&A with citations via LangChain agent |
| `POST` | `/rag/recommendations` | Search recommendations (LLM + deterministic) |
| `POST` | `/rag/ingest` | Trigger async transcript ingestion |

All transcription endpoints require a valid JWT (obtain via `/api/session`).
Settings, dictionary, and user management endpoints require appropriate RBAC permissions (see below).

## Authentication & RBAC

The backend supports two authentication modes, controlled by the `AUTH_MODE` environment variable:

| Mode | Behavior |
|------|----------|
| `legacy` (default) | Existing `/api/session` JWT flow. All permissions granted — fully backward-compatible. |
| `cognito` | Validates AWS Cognito JWTs (RS256 via jwks-rsa). Extracts role from `cognito:groups`. Enforces RBAC. |

### Middleware

- `middleware/cognito-auth.js` — Cognito JWT validation
- `middleware/require-permission.js` — checks the user's role has the required permission before allowing access
- `lib/rbac-config.js` — role-permission registry with 60s cache (backed by `rbac_config` table)

### Roles & Permissions

Five roles, highest to lowest precedence:

| Role | Key Permissions |
|------|----------------|
| Admin | manage_users, system_config, + all below |
| Chief Editor | manage_users, create_sitting, assign_editor, certify_record |
| Supervisor | review_record, approve_certification, export_hansard |
| Editor | edit_record, upload_audio, rename_speakers, submit_for_review |
| Viewer | view_records, search_hansard, export_published |

### Database (Migration 004)

- `users` — local user cache (id, email, name, role, status, department, last_active)
- `rbac_config` — updatable role-permission mappings (seeded with the 5 default roles)
- `export_config` JSONB column on `app_settings` — PDF/DOCX export configuration

### Seeding Test Users

```bash
cd services/postprocess
python scripts/seed_users.py          # Seed one user per role
python scripts/seed_users.py --remove # Remove seeded users
```

## Quick Start

### Prerequisites

- Node.js ≥ 24
- pnpm ≥ 10 (via corepack)
- Git (with SSH access for submodules)

### Makefile (Recommended)

```bash
make init                     # Clone submodules + install deps
cp sample.env .env            # Add your DEEPGRAM_API_KEY
make start                    # Backend :8081 + Frontend :8080
```

### Manual Setup

```bash
git clone --recurse-submodules https://github.com/deepgram-starters/node-transcription.git
cd node-transcription
corepack pnpm install
cd frontend && corepack pnpm install && cd ..
cp sample.env .env            # Add your DEEPGRAM_API_KEY
```

Start in separate terminals:

```bash
# Terminal 1 — Backend (port 8081)
node server.js

# Terminal 2 — Frontend (port 8080)
cd frontend && corepack pnpm run dev -- --port 8080 --no-open
```

Open [http://localhost:8080](http://localhost:8080) in your browser.

## Post-Processing Modes

Controlled by the `POSTPROCESS_MODE` environment variable:

| Mode | Behavior |
|------|----------|
| `js` (default) | In-process JavaScript correction pipeline (location, year, Bedrock LLM) |
| `python` | Calls the external Python Postprocessing Service at `POSTPROCESS_URL` |
| `off` | Returns raw transcript with no corrections |

### Running the Python Postprocessing Service

Only needed when `POSTPROCESS_MODE=python`. If the service isn't reachable the
gateway logs `postprocess.degraded` with `reason: connection` and falls back to
returning the raw transcript, so transcription keeps working either way.

The service is a FastAPI app backed by PostgreSQL (with `pg_trgm` for fuzzy
matching). Requires Docker and Python ≥ 3.12.

```bash
cd services/postprocess
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # macOS / Linux

cp sample.env .env      # set SERVICE_TOKEN to match the gateway's POSTPROCESS_TOKEN
```

One-time provisioning (database, schema, and entity datasets):

```bash
make postprocess-setup
```

Then start it on port 8082:

```bash
make start-postprocess
```

Verify it came up — `/health` returns 200 only after the dataset cache has loaded:

```bash
curl http://localhost:8082/health
# {"record_count":653,"loaded_at":"...","llm_configured":false,"uptime_seconds":1.2}
```

The gateway's `POSTPROCESS_TOKEN` must match the service's `SERVICE_TOKEN`, or
every call fails with 401. A successful round-trip shows
`metadata.postprocessing_status: "applied"` in the transcription response;
`"skipped"` means the gateway degraded and used the raw transcript.

Setup steps individually, if you need them:

| Target | Description |
|--------|-------------|
| `make postprocess-db` | Start PostgreSQL and wait until healthy |
| `make postprocess-migrate` | Apply Alembic schema migrations |
| `make postprocess-seed` | Seed Ghana entity datasets (idempotent) |

#### Health vs readiness

Two separate probes, because they answer different questions:

| Endpoint | Checks | Use for |
|----------|--------|---------|
| `GET /health` | Cached snapshot only, no database round-trip | Container liveness / restart decisions |
| `GET /ready` | Snapshot **and** live `SELECT 1` against the database | Load balancer target group, deploy gates |

The split matters. The service intentionally keeps serving corrections from its
in-memory snapshot when the database is unreachable, so pointing liveness at a
database check would restart a service that is still working. Conversely, if the
only probe ignores the database, an outage stays invisible. `/ready` returns 503
with `checks.database: "unreachable"` so orchestrators and alarms can see it.

#### Production database settings

Defaults are safe for local Docker. For managed PostgreSQL (RDS, Cloud SQL,
Azure) review these:

| Variable | Default | Why it matters |
|----------|---------|----------------|
| `DB_SSLMODE` | unset | Set to `require` (or `verify-full`). Without it, credentials and transcript text cross the network in plaintext. Ignored if `DATABASE_URL` already has `sslmode`. |
| `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` | 5 + 5 | Peak connections per instance. Multiply by instance count and keep under the server's `max_connections`. |
| `DB_POOL_RECYCLE_SECONDS` | 1800 | Must be lower than any upstream idle timeout (RDS Proxy, PgBouncer, NAT), or connections get closed while the pool still thinks they're live. |
| `DB_STATEMENT_TIMEOUT_MS` | 15000 | Server-side cap so a runaway query can't hold a pool slot indefinitely. |
| `HISTORY_RETENTION_DAYS` | 90 | `correction_history` gains a row per correction. A background sweeper prunes older rows; `0` disables it and growth becomes unbounded. |

Two operational notes:

- `pg_trgm` requires elevated privileges to create. The migration treats an
  existing extension as success and, on a privilege error, fails with an
  instruction to have an administrator run `CREATE EXTENSION pg_trgm;`. Managed
  providers often pre-install it.
- Run migrations as a separate one-off task in production (`RUN_MIGRATIONS=false`
  on the service). Multiple tasks racing `alembic upgrade head` during a rolling
  deploy or scale-out is not safe.

Alarm on `postprocess.history_dropped` — a non-zero value means audit records are
being discarded because the writer can't keep up with or reach the database.

#### Dataset exports

The JS modules under `lib/location-correction/` are the single source of truth for
Ghana entities. Two derived artifacts exist:

| Artifact | Produced by | Consumed by |
|----------|-------------|-------------|
| `datasets_export_raw.json` | `node scripts/export_js_datasets.js` | `migrate_js_datasets.py` (seeds the database) |
| `datasets/*.json` + `datasets/*.csv` | `generate_dataset_exports.py` | `record_baseline.py`, human review |

Regenerate and validate the `datasets/` artifacts with:

```bash
make datasets-generate
make datasets-validate
```

The validator checks encoding, duplicate aliases, natural-key integrity against
the database's `UNIQUE (canonical, entity_kind, source)` constraint, and CSV/JSON
parity. It exits non-zero on any error, so it is safe to wire into CI.

Two things it deliberately reports as warnings rather than errors, because both
are legitimate here:

- The same `canonical` under different sources. Atta Mills was Vice President and
  later President; Tema appears in both the city and supplementary lists. The
  natural key keeps them distinct, so these must not be "deduplicated".
- An alias identical to its canonical. Redundant but harmless — exact-match
  relies on the canonical being present in the index.

Generation is deterministic: running it twice produces byte-identical files, so a
noisy diff means the source data actually changed.

Two platform notes, both handled by `scripts/run_local.py`:

- On Windows, psycopg cannot use asyncio's default `ProactorEventLoop`. Without
  the selector policy the dataset cache fails to load and `/health` stays at 503.
- The venv's console scripts (`alembic.exe`, `uvicorn.exe`) hardcode an absolute
  path when created, so they break if the repo is moved. Invoke modules via
  `python -m alembic` / `python -m uvicorn` instead, or recreate the venv.

`LLM_ENABLED=false` in local config keeps the service free of AWS credentials.
Set it to `true` and supply AWS credentials to enable the Bedrock refinement stage.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPGRAM_API_KEY` | Yes | Deepgram API key ([get one free](https://console.deepgram.com)) |
| `KHAYA_API_KEY` | No | Khaya AI (GhanaNLP) ASR key — enables African language transcription |
| `KHAYA_ASR_VERSION` | No | `v1` (fast) or `v3` (accurate, default) |
| `AWS_ACCESS_KEY_ID` | No | AWS credentials for Bedrock LLM post-processing |
| `AWS_SECRET_ACCESS_KEY` | No | AWS credentials for Bedrock LLM post-processing |
| `AWS_REGION` | No | AWS region (default: `us-east-1`) |
| `BEDROCK_MODEL_ID` | No | Claude model ID for post-processing |
| `POSTPROCESS_MODE` | No | `js` / `python` / `off` (default: `js`) |
| `POSTPROCESS_URL` | No | Python service URL (default: `http://localhost:8082`) |
| `PORT` | No | Backend port (default: `8081`) |
| `SESSION_SECRET` | No | JWT signing secret (auto-generated if unset) |
| `AUTH_MODE` | No | `legacy` (default) or `cognito` — controls authentication strategy |
| `COGNITO_USER_POOL_ID` | If AUTH_MODE=cognito | AWS Cognito User Pool ID |
| `COGNITO_REGION` | If AUTH_MODE=cognito | AWS region for Cognito |
| `COGNITO_APP_CLIENT_ID` | If AUTH_MODE=cognito | Cognito App Client ID |

### Hybrid Pipeline Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HYBRID_CONFIDENCE_THRESHOLD` | `0.85` | Confidence below which words are sent for correction |
| `HYBRID_GAP_TOLERANCE` | `0.5` | Max gap (s) to group adjacent low-confidence words |
| `HYBRID_PADDING` | `0.25` | Audio padding (s) around correction segments |
| `HYBRID_MAX_CALLS_PER_MODEL` | `3` | Max Khaya calls per language model per transcription |

See `sample.env` for the full list with defaults.

## Project Structure

```
├── server.js                 # Express 5 API gateway
├── lib/
│   ├── location-correction/  # Rule-based Ghana entity correction
│   ├── hybrid/               # Audio slicing for hybrid pipeline
│   ├── rbac-config.js        # Role-permission registry (60s cache)
│   ├── postprocess-client.js # Python service HTTP client
│   └── postprocess-mode.js   # Mode dispatcher utilities
├── routes/
│   ├── account.js            # User account routes
│   ├── ask.js                # Q&A proxy to RAG agent
│   ├── audio.js              # Audio upload & proxy
│   ├── auth.js               # Session/JWT routes
│   ├── dashboard.js          # Dashboard analytics
│   ├── dictionary.js         # Custom dictionary CRUD
│   ├── hybrid.js             # Hybrid confidence pipeline
│   ├── khaya.js              # Khaya AI provider routes
│   ├── records.js            # Hansard record CRUD
│   ├── search.js             # Search proxy to RAG retriever
│   ├── settings.js           # App settings & export config
│   ├── sittings.js           # Sitting CRUD with pagination
│   ├── transcript.js         # Transcript management
│   ├── transcription.js      # Deepgram transcription + postprocess
│   └── users.js              # User management (Cognito)
├── middleware/
│   ├── cognito-auth.js       # AWS Cognito JWT validation
│   └── require-permission.js # RBAC permission guard
├── providers/
│   └── khaya.js              # Khaya AI provider adapter
├── services/
│   └── postprocess/          # Python FastAPI service
│       ├── app/
│       │   ├── main.py       # FastAPI app with lifespan
│       │   ├── config.py     # Pydantic settings
│       │   ├── pipeline.py   # Entity correction pipeline
│       │   ├── rag/
│       │   │   ├── agent.py          # LangChain conversational agent
│       │   │   ├── answerer.py       # Grounded answering chain (ChatBedrock)
│       │   │   ├── clients.py        # LangChain client factory (ChatBedrock, BedrockEmbeddings)
│       │   │   ├── ingestion.py      # Chunking + embedding worker
│       │   │   ├── retriever.py      # FulltextRetriever + VectorRetriever + RRF
│       │   │   ├── recommendations.py # Deterministic suggestion derivation
│       │   │   ├── search_recommendations.py # LLM-backed search suggestions
│       │   │   ├── parsing.py        # Citation & recommendation parsing
│       │   │   └── router.py         # FastAPI RAG endpoints
│       │   ├── correction/   # Entity correction logic
│       │   ├── datasets/     # Ghana entity datasets (JSON/CSV)
│       │   ├── history/      # Correction history writer
│       │   ├── llm/          # Bedrock LLM correction pass
│       │   ├── models/       # SQLAlchemy models
│       │   ├── obs/          # Observability (CloudWatch EMF)
│       │   └── years/        # Year/date correction
│       ├── migrations/       # Alembic schema migrations
│       ├── tests/            # pytest + hypothesis test suite
│       └── pyproject.toml    # Python project config (hatchling)
├── deploy/
│   ├── Dockerfile            # Multi-stage build (Go + Node 24)
│   ├── Caddyfile             # Caddy reverse proxy + rate limits
│   └── start.sh             # Container entrypoint
├── test/                     # Backend test suites (Node.js)
├── bench/                    # Benchmark harness
└── Makefile                  # Project automation
```

## Testing

```bash
# Backend (Node.js)
pnpm test              # ESLint + node --test
pnpm run lint          # ESLint only

# Python Postprocessing
cd services/postprocess
.venv/Scripts/python.exe -m pytest -v          # Windows
# source .venv/bin/activate && pytest -v       # macOS / Linux

# Frontend (from parent project)
cd ../Hansard && pnpm run test
```

| Layer | Framework | Style |
|-------|-----------|-------|
| Frontend | Vitest 4 + Testing Library | Unit + component tests, co-located `*.test.ts` |
| Backend | Node.js built-in test runner | Property-based (fast-check), integration (supertest) |
| Python | pytest + hypothesis | Property-based, async (pytest-asyncio), HTTP (httpx) |

## Deployment

Production deployment uses a multi-stage Docker build deployed to [Fly.io](https://fly.io):

1. **Stage 1** — Custom Caddy binary built from Go with `xcaddy` + `caddy-ratelimit` module
2. **Stage 2** — Frontend built with pnpm (Vite static output)
3. **Stage 3** — Node 24 runtime with backend deps, Caddy binary, and built frontend

Rate limiting zones:
- `/api/session` — 5 requests/min per IP
- `/api/*` — 120 requests/min per IP

```bash
fly deploy
```

## Submodules

This repository is part of a parent project (`Parliament-Project`) that manages it as a Git submodule alongside the frontend:

| Submodule | Repository | Branch | Role |
|-----------|-----------|--------|------|
| `Hansard/` | bigdataghana/Hansard | `feat/backend-integration` | React TypeScript frontend |
| `transcript-end/` | neweracy/node-transcription | `python-integration` | Express + Python backend (this repo) |

## License

MIT
