# AGENTS.md — transcript-end (Backend Gateway)

Operational guide for AI coding agents working in the transcript-end backend submodule.

## Overview

Express 5 API gateway for the Ghana Parliament Hansard system. Handles JWT authentication, audio file uploads, ASR provider integration (Deepgram + Khaya AI), Hansard CRUD operations, and proxies RAG queries to the Python postprocessing service.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Node.js >= 24 |
| Framework | Express 5 |
| Language | JavaScript (CommonJS) |
| Auth | JSON Web Tokens (jsonwebtoken) |
| Database | PostgreSQL via `pg` pool |
| File Upload | Multer |
| ASR | Deepgram SDK, Khaya AI HTTP |
| LLM | AWS Bedrock SDK (for JS pipeline mode) |
| Testing | Node.js test runner + fast-check |
| Linting | ESLint 9 (flat config) |
| Package Manager | pnpm 10 |
| Dev Server | nodemon |

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
├── test/
│   ├── routes/                  # Integration tests
│   └── properties/              # Property-based tests (fast-check)
├── docs/                        # Architecture docs, research notes
├── deploy/
│   ├── Dockerfile               # Multi-stage production build
│   ├── Caddyfile                # Reverse proxy config
│   └── start.sh                 # Container entrypoint
├── uploads/                     # Local audio storage (gitignored)
├── Makefile                     # Automation commands
├── sample.env                   # Environment variable template
└── package.json
```

## Commands

```bash
pnpm install                # Install dependencies
pnpm run start-backend      # Start with nodemon (auto-reload)
pnpm run start              # Start without auto-reload
pnpm test                   # ESLint + node --test
pnpm run lint               # ESLint only

# Via Makefile (preferred)
make init                   # Submodules + install
make start-backend          # Port 8081
make start-postprocess      # Port 8082
make postprocess-setup      # DB + migrate + seed (one-time)
make test-unit              # Backend tests
make test-python            # Python service tests
make datasets-generate      # Regenerate dataset exports
make datasets-validate      # Validate dataset integrity
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

### Postprocessing Modes

Controlled by `POSTPROCESS_MODE` env var:

| Mode | Behavior |
|------|----------|
| `js` | In-process correction (location, year, Bedrock LLM) |
| `python` | Proxy to Python service at `POSTPROCESS_URL` |
| `off` | Return raw transcript |

### RAG Proxy Pattern

Routes like `search.js` and `ask.js` proxy requests to the Python service:
1. Validate input
2. Map camelCase → snake_case
3. Forward to `POSTPROCESS_URL/rag/*` with `POSTPROCESS_TOKEN` Bearer header
4. Map snake_case → camelCase in response
5. Handle timeouts (30s) and connection errors

### Database Access

- Pool module: `lib/db.js` (exports `query(text, params)`)
- Connection via `DATABASE_URL` env var
- Used directly in route handlers for Hansard CRUD (sittings, records, transcripts)
- Python service has its own pool for RAG/correction tables

## Testing

### Node.js Tests

```bash
pnpm test                        # ESLint + all tests
node --test test/routes/*.test.js  # Integration tests only
node --test test/properties/*.property.test.js  # Property tests only
```

- **Integration tests**: `test/routes/*.test.js` — test route handlers with supertest
- **Property tests**: `test/properties/*.property.test.js` — fast-check generators for invariants
- **Naming**: `*.test.js` for integration, `*.property.test.js` for properties

### Python Tests

```bash
cd services/postprocess
.venv/Scripts/python.exe -m pytest -v
```

See `services/postprocess/AGENTS.md` for details.

## Environment Variables

Key variables (see `sample.env` for full list):

| Variable | Required | Purpose |
|----------|----------|---------|
| `DEEPGRAM_API_KEY` | Yes | Deepgram ASR API key |
| `POSTPROCESS_MODE` | No | `js`/`python`/`off` (default: `js`) |
| `POSTPROCESS_URL` | No | Python service URL (default: `http://localhost:8082`) |
| `POSTPROCESS_TOKEN` | No | Shared auth token for Python service |
| `DATABASE_URL` | No | PostgreSQL connection (for Hansard CRUD) |
| `PORT` | No | Server port (default: `8081`) |
| `SESSION_SECRET` | No | JWT signing key (auto-generated if absent) |

## Security

- Never hardcode AWS credentials in docker-compose or config files
- `uploads/` is gitignored — never commit audio files
- MIME type validation on uploads: `audio/mpeg`, `audio/wav`, `audio/ogg`, `audio/webm`, `audio/mp4`
- 500MB max file size for audio uploads
- 30s timeout on RAG proxy calls

## Agent Instructions

1. **Route factories**: always export `function(requireSession, db)` returning `express.Router()`
2. **Register routes in server.js**: import and mount with `app.use(routeFactory(requireSession, db))`
3. **camelCase in JS, snake_case to Python**: always map when proxying to/from postprocess service
4. **Validate input early**: return 400 with `{ error: { type, code, message } }` envelope
5. **Error envelope**: all errors use `{ error: { type: string, code: string, message: string } }`
6. **CommonJS**: this is not an ES module — use `require()` and `module.exports`
7. **No TypeScript**: this codebase is plain JavaScript
8. **Test new routes**: add both `test/routes/*.test.js` and `test/properties/*.property.test.js`
9. **Makefile**: update `transcript-end/Makefile` if adding new automation targets
