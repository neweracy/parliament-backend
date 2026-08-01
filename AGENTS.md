# AGENTS.md — transcript-end (Backend Gateway)

Operational guide for AI coding agents working in the transcript-end backend submodule.

## Overview

Express 5 API gateway for the Ghana Parliament Hansard system. Handles JWT authentication, audio file uploads, ASR provider integration (Deepgram + Khaya AI), Hansard CRUD operations, and proxies RAG queries to the Python postprocessing service.

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Runtime | Node.js | >= 24.0.0 |
| Framework | Express | 5.x |
| Language | JavaScript (CommonJS) | ES2024 |
| Auth | JSON Web Tokens (jsonwebtoken) | 9.x |
| Database | PostgreSQL via `pg` pool | 8.x |
| File Upload | Multer | 2.x |
| ASR | Deepgram SDK, Khaya AI HTTP | 4.x |
| LLM | AWS Bedrock SDK (for JS pipeline mode) | 3.x |
| Audio | ffmpeg-static (audio format conversion) | 5.x |
| Testing | Node.js test runner + fast-check + supertest | — |
| Linting | ESLint 9 (flat config) | 9.x |
| Package Manager | pnpm | >= 10.0.0 |
| Dev Server | nodemon | 3.x |

## Project Structure

```
transcript-end/
├── server.js                    # Express app entry point, route mounting
├── lib/
│   ├── db.js                    # PostgreSQL connection pool (pg.Pool)
│   ├── transcription-pipeline.js  # Audio → transcript orchestration
│   ├── postprocess-client.js    # Python service HTTP client
│   ├── postprocess-mode.js      # Mode dispatcher (js/python/off)
│   ├── location-correction/     # Rule-based Ghana entity correction
│   └── hybrid/                  # Audio slicing for hybrid pipeline
├── routes/
│   ├── sittings.js              # CRUD: GET/POST/PATCH/DELETE sittings
│   ├── records.js               # CRUD: records within sittings
│   ├── audio.js                 # Upload/stream audio files
│   ├── transcription.js         # Start/poll transcription jobs
│   ├── transcript.js            # Get/update transcript with versioning
│   ├── search.js                # Proxy to RAG /rag/search
│   ├── ask.js                   # Proxy to RAG /rag/ask (chatbot Q&A)
│   ├── dashboard.js             # Aggregated statistics
│   ├── settings.js              # App settings CRUD
│   ├── khaya.js                 # Khaya AI routes
│   └── hybrid.js                # Hybrid confidence pipeline
├── providers/
│   └── khaya.js                 # Khaya AI HTTP adapter
├── services/
│   └── postprocess/             # Python FastAPI service (see its own AGENTS.md)
├── frontend/                    # Embedded frontend (see frontend/AGENTS.md)
├── test/
│   ├── routes/                  # Integration tests (supertest)
│   └── properties/              # Property-based tests (fast-check)
├── docs/                        # Architecture docs, research notes
├── deploy/
│   ├── Dockerfile               # Multi-stage production build
│   ├── Caddyfile                # Reverse proxy config
│   └── start.sh                 # Container entrypoint
├── uploads/                     # Local audio storage (gitignored)
├── Makefile                     # Automation commands
├── sample.env                   # Environment variable template
├── eslint.config.js             # ESLint flat config
└── package.json                 # Dependencies and scripts
```

## Commands

```bash
# Package management
pnpm install                # Install dependencies

# Development
pnpm run start-backend      # Start with nodemon (auto-reload, port 8081)
pnpm run start              # Start without auto-reload

# Testing & Linting
pnpm test                   # ESLint + node --test (all tests)
pnpm run lint               # ESLint only

# Individual test suites
node --test test/routes/*.test.js           # Integration tests only
node --test test/properties/*.property.test.js  # Property tests only

# Via Makefile
make init                   # Submodules + install all deps
make start-backend          # Backend on port 8081
make start-frontend         # Frontend on port 8080
make start-postprocess      # Python service on port 8082
make postprocess-setup      # DB + migrate + seed (one-time)
make test-unit              # Backend unit tests
make test-python            # Python service tests
make test-contracts         # Contract conformance (app must be running)
make lint                   # ESLint
make bench                  # Benchmark harness
make datasets-generate      # Regenerate dataset exports
make datasets-validate      # Validate dataset integrity
make clean                  # Remove node_modules and build artifacts
make status                 # Git + submodule status
```

## Architecture Patterns

### Route Module Pattern

Every route file exports a factory function:

```javascript
module.exports = function routeName(requireSession, db) {
  const router = express.Router();

  router.get("/api/endpoint", requireSession, async (req, res) => {
    // implementation
  });

  return router;
};
```

The factory receives:
- `requireSession` — JWT auth middleware
- `db` — PostgreSQL pool client (`db.query(text, params)`)

### Authentication

- `GET /api/session` issues a JWT (no auth required)
- All other `/api/*` routes pass through `requireSession` middleware
- Token in `Authorization: Bearer <token>` header
- Token expires in 1 hour
- `SESSION_SECRET` env var (auto-generated if absent in dev)

### Postprocessing Modes

Controlled by `POSTPROCESS_MODE` env var:

| Mode | Behavior |
|------|----------|
| `js` | In-process correction (location, year, Bedrock LLM) |
| `python` | Proxy to Python service at `POSTPROCESS_URL` |
| `off` | Return raw transcript without corrections |

### RAG Proxy Pattern

Routes like `search.js` and `ask.js` proxy requests to the Python service:
1. Validate input parameters
2. Map camelCase → snake_case for Python
3. Forward to `POSTPROCESS_URL/rag/*` with `POSTPROCESS_TOKEN` Bearer header
4. Map snake_case → camelCase in response
5. Handle timeouts (30s) and connection errors gracefully

### Database Access

- Pool module: `lib/db.js` (exports `query(text, params)`)
- Connection via `DATABASE_URL` env var
- **Parameterized queries only** — use `$1, $2` placeholders, never string interpolation
- Used directly in route handlers for Hansard CRUD (sittings, records, transcripts)
- Python service has its own pool for RAG/correction tables

### Error Response Envelope

All errors follow a consistent shape:

```json
{
  "error": {
    "type": "validation_error",
    "code": "INVALID_SITTING_ID",
    "message": "Sitting ID must be a positive integer"
  }
}
```

## Testing

### Running Tests

```bash
pnpm test                        # Full suite (lint + tests)
node --test test/routes/*.test.js  # Integration tests only
node --test test/properties/*.property.test.js  # Property tests only
```

### Test Conventions

- **Integration tests**: `test/routes/*.test.js` — test route handlers with supertest
- **Property tests**: `test/properties/*.property.test.js` — fast-check generators for invariants
- **Naming**: `*.test.js` for integration, `*.property.test.js` for properties
- **No real services in tests**: mock Deepgram, Khaya, and Bedrock calls
- **No real database in unit tests**: mock `db.query` responses

### Writing New Tests

For a new route `routes/myroute.js`:
1. Create `test/routes/myroute.test.js` with supertest
2. Create `test/properties/myroute.property.test.js` for invariants (if applicable)
3. Test happy path, validation errors, auth failures, and edge cases

## Environment Variables

Key variables (see `sample.env` for full list):

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `DEEPGRAM_API_KEY` | Yes | — | Deepgram ASR API key |
| `POSTPROCESS_MODE` | No | `js` | `js`/`python`/`off` |
| `POSTPROCESS_URL` | No | `http://localhost:8082` | Python service URL |
| `POSTPROCESS_TOKEN` | No | — | Shared auth token for Python service |
| `DATABASE_URL` | No | — | PostgreSQL connection (for Hansard CRUD) |
| `PORT` | No | `8081` | Server port |
| `SESSION_SECRET` | No | auto-generated | JWT signing key |
| `KHAYA_API_KEY` | No | — | Khaya AI API key (Ghanaian languages) |

## Security

### Secrets Management

- Never hardcode AWS credentials in docker-compose or config files
- Never commit `.env` files — only `sample.env` (with placeholder values)
- `DEEPGRAM_API_KEY` and `POSTPROCESS_TOKEN` are the most sensitive values
- `SESSION_SECRET` should be a strong random string in production

### File Upload Security

- `uploads/` is gitignored — never commit audio files
- MIME type validation: `audio/mpeg`, `audio/wav`, `audio/ogg`, `audio/webm`, `audio/mp4`
- 500MB max file size for audio uploads
- Files stored locally in dev, cloud storage in production

### API Security

- 30s timeout on RAG proxy calls (prevents resource exhaustion)
- Rate limiting handled by Caddy in production
- CORS configured for frontend origin only
- SQL injection prevention: parameterized queries only (`$1`, `$2`)

## Deployment

### Docker Build

```bash
# Build from transcript-end/deploy/Dockerfile
docker build -f deploy/Dockerfile -t transcript-end .
```

The Dockerfile is a multi-stage build:
1. Install Node.js dependencies
2. Copy application code
3. Caddy reverse proxy as entrypoint

### Production Configuration

- **Caddy** handles TLS termination, rate limiting, and static asset serving
- **start.sh** is the container entrypoint (starts Caddy + Node.js)
- Environment variables injected at runtime (never baked into image)
- Health check: `GET /api/health` (no auth required)

### Files in deploy/

| File | Purpose | Modify with caution |
|------|---------|-------------------|
| `Dockerfile` | Multi-stage production build | Yes — affects prod |
| `Caddyfile` | Reverse proxy, TLS, rate limits | Yes — affects prod traffic |
| `start.sh` | Container entrypoint script | Yes — affects startup |

## Agent Workflow Instructions

### Before Making Changes

1. Read `server.js` to understand route mounting and middleware order
2. Check `lib/` for existing utilities before creating new ones
3. Identify if the change touches the Python service boundary (case mapping)
4. Review `sample.env` if adding new configuration

### Creating a New Route

1. Create `routes/<name>.js` exporting `function(requireSession, db)`
2. Import and mount in `server.js`: `app.use(routeFactory(requireSession, db))`
3. Add integration test: `test/routes/<name>.test.js`
4. Add property test if the route has complex invariants
5. Update `sample.env` if new env vars are needed
6. Update the Makefile if new automation targets are needed

### Safe Refactoring

- Changes to `lib/db.js` affect every route — test thoroughly
- Changes to `server.js` affect middleware ordering — verify auth still works
- When modifying the postprocess proxy, verify both camelCase→snake_case and back
- When updating Multer config, verify upload size limits and MIME types still enforced

### Areas Requiring Extra Caution

| Area | Risk | Mitigation |
|------|------|-----------|
| `lib/db.js` | Shared pool, all routes depend on it | Test connection handling, error recovery |
| `server.js` | Route ordering, middleware chain | Verify auth middleware applied correctly |
| `routes/audio.js` | File uploads, memory, disk usage | Check MIME validation, size limits |
| `lib/transcription-pipeline.js` | Core business logic, ASR orchestration | Test with mock providers |
| `lib/postprocess-client.js` | Python service boundary | Verify timeout, error handling |
| `deploy/*` | Production infrastructure | Never modify without deployment plan |

### Files That Should Not Be Modified Without Good Reason

- `deploy/Caddyfile` — production reverse proxy (affects live traffic)
- `deploy/Dockerfile` — production build (test locally first)
- `pnpm-lock.yaml` — only via `pnpm install` or `pnpm add`
- `eslint.config.js` — team-shared lint rules
- Dataset files in `services/postprocess/datasets/` — generated, not hand-edited

## Git & Commit Guidelines

### Commit Format

```
<type>(<scope>): <short description>
```

Scopes for this submodule: `routes`, `lib`, `auth`, `upload`, `rag`, `khaya`, `config`, `test`, `deploy`, `deps`

Examples:
- `feat(routes): add PATCH /api/sittings/:id endpoint`
- `fix(rag): handle timeout on Python service unavailable`
- `test(properties): add fast-check generators for search params`
- `chore(deps): update @deepgram/sdk to 4.11.3`

### What Not to Commit

- `.env` files (only `sample.env`)
- `uploads/` directory
- `node_modules/`
- `*.log` files
- Temporary test audio files

## Agent Instructions (Quick Reference)

1. **Route factories**: always export `function(requireSession, db)` returning `express.Router()`
2. **Register routes in server.js**: import and mount with `app.use(routeFactory(requireSession, db))`
3. **camelCase in JS, snake_case to Python**: always map when proxying to/from postprocess service
4. **Validate input early**: return 400 with `{ error: { type, code, message } }` envelope
5. **CommonJS**: this is not an ES module — use `require()` and `module.exports`
6. **No TypeScript**: this codebase is plain JavaScript
7. **Parameterized queries**: always use `$1, $2` — never interpolate SQL
8. **Test new routes**: add both integration (`test/routes/`) and property tests (`test/properties/`)
9. **Makefile**: update `Makefile` if adding new automation targets
10. **Pin versions**: use exact versions in package.json (no `^`/`~` for primary deps)
11. **Error handling**: wrap async route handlers, catch and return proper error envelope
12. **No global state**: route modules receive deps via factory args, not globals
