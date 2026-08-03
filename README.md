# Node Transcription

A Node.js transcription backend powered by [Deepgram](https://deepgram.com) (Speech-to-Text) and [Khaya AI](https://translation.ghananlp.org) (Ghanaian languages), with a Ghana-focused entity correction post-processing pipeline. Includes rule-based datasets for locations, presidents/ministers, MPs, and political parties, plus an optional Amazon Bedrock (Claude) LLM pass for accuracy beyond the static datasets.

The frontend is a React + TypeScript SPA (Vite, Tailwind CSS, daisyUI) with audio waveform playback and real-time transcript display.

## Architecture

```
┌─────────────┐        ┌─────────────────┐        ┌──────────────────┐
│   Frontend  │──API──▶│  Express Gateway │──ASR──▶│  Deepgram / Khaya│
│  (React/TS) │        │   (server.js)   │        │   AI providers   │
└─────────────┘        └────────┬────────┘        └──────────────────┘
                                │
                   ┌────────────┼────────────┐
                   ▼            ▼            ▼
            Rule-based     Year/Date     Bedrock LLM
            Location       Correction    Post-processing
            Correction                   (optional)
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
├── server.js                 # Express API gateway
├── lib/
│   ├── location-correction/  # Rule-based Ghana entity correction
│   ├── hybrid/               # Audio slicing for hybrid pipeline
│   ├── postprocess-client.js # Python service HTTP client
│   └── postprocess-mode.js   # Mode dispatcher utilities
├── routes/
│   ├── khaya.js              # Khaya AI route handlers
│   └── hybrid.js             # Hybrid confidence pipeline routes
├── providers/
│   └── khaya.js              # Khaya AI provider adapter
├── services/
│   └── postprocess/          # Python postprocessing service
├── frontend/                 # React/TS SPA (git submodule)
├── contracts/                # API contracts & conformance tests (git submodule)
├── deploy/
│   ├── Dockerfile            # Multi-stage production build
│   ├── Caddyfile             # Caddy reverse proxy config
│   └── start.sh             # Container entrypoint
├── test/                     # Backend test suites
├── bench/                    # Benchmark harness
└── Makefile                  # Project automation
```

## Testing

```bash
make lint              # ESLint
make test-unit         # Backend unit tests (eslint + node --test)
make test-contracts    # Contract conformance tests (requires running app)
make test-python       # Python postprocessing service tests
make bench             # Benchmark harness against baseline
```

The project uses [fast-check](https://github.com/dubzzz/fast-check) for property-based testing and Node.js built-in test runner for unit tests.

## Deployment

Production deployment uses a multi-stage Docker build deployed to [Fly.io](https://fly.io):

- Caddy reverse proxy (with rate limiting) serves the frontend and proxies API calls
- Node.js backend runs behind Caddy on port 8081
- Frontend is built at image time and served as static assets

```bash
fly deploy
```

## Submodules

| Submodule | Repository | Description |
|-----------|-----------|-------------|
| `frontend` | [transcription-html](https://github.com/deepgram-starters/transcription-html) | React/TypeScript frontend |
| `contracts` | [starter-contracts](https://github.com/deepgram/starter-contracts) | API contracts & conformance tests |

```bash
make update            # Pull latest submodule commits
make eject-frontend    # Convert frontend submodule to regular directory
```

## Make Targets

| Target | Description |
|--------|-------------|
| `make init` | Initialize submodules + install all dependencies |
| `make start` | Start backend + frontend in parallel |
| `make start-backend` | Start backend only (port 8081) |
| `make start-frontend` | Start frontend only (port 8080) |
| `make update` | Update submodules to latest |
| `make clean` | Remove node_modules and build artifacts |
| `make status` | Show git and submodule status |
| `make eject-frontend` | Eject frontend submodule to regular directory |

## License

MIT — See [LICENSE](./LICENSE)
