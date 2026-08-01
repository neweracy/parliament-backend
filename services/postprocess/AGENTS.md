# AGENTS.md — Postprocessing Service (Python/FastAPI)

Operational guide for AI coding agents working in the Python postprocessing service.

## Overview

FastAPI async service responsible for transcript post-processing (entity correction, LLM refinement) and the RAG pipeline (ingestion, hybrid retrieval, grounded answering). Backed by PostgreSQL with pgvector for embedding storage and pg_trgm for fuzzy text matching.

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | FastAPI | 0.115 |
| Runtime | Python + Uvicorn | >= 3.12 |
| ORM | SQLAlchemy (async) | 2.0 |
| Migrations | Alembic | Latest |
| Database | PostgreSQL 16 + pgvector + pg_trgm | 16 |
| Fuzzy Match | RapidFuzz | Latest |
| LLM | Amazon Bedrock (Claude) via boto3 | Latest |
| Embeddings | Bedrock Titan Text Embeddings V2 | — |
| Logging | structlog (structured JSON) | Latest |
| Testing | pytest + hypothesis (property-based) | Latest |
| Linting | ruff (check + format) | Latest |
| Build | hatchling | Latest |
| Package Manager | pip (editable install) | Latest |

## Project Structure

```
services/postprocess/
├── app/
│   ├── main.py                  # FastAPI app, lifespan, exception handlers
│   ├── config.py                # Pydantic Settings (env vars)
│   ├── deps.py                  # Dependency injection (auth, session)
│   ├── middleware.py            # Request logging middleware
│   ├── api/
│   │   ├── routes_health.py     # GET /health, GET /ready
│   │   ├── routes_postprocess.py  # POST /v1/postprocess
│   │   └── routes_datasets.py  # Dataset management endpoints
│   ├── datasets/
│   │   ├── cache.py             # In-memory dataset cache with refresh
│   │   └── store.py             # SQLAlchemy engine + session factory
│   ├── history/
│   │   └── writer.py            # Async correction history writer
│   ├── llm/
│   │   └── bedrock.py           # Bedrock client wrapper
│   ├── obs/
│   │   ├── logging.py           # structlog configuration
│   │   └── metrics.py           # EMF metrics emission
│   └── rag/
│       ├── __init__.py
│       ├── ingestion.py         # Speaker-turn-aware chunking + embeddings
│       ├── retrieval.py         # Hybrid search (vector + full-text + RRF)
│       ├── answering.py         # Grounded Q&A with Claude + recommendations
│       └── router.py            # FastAPI router: /rag/search, /rag/ask, /rag/ingest
├── migrations/
│   ├── env.py                   # Alembic environment
│   └── versions/
│       ├── 001_initial_schema.py
│       ├── 002_production_hardening.py
│       └── 003_hansard_schema.py  # pgvector, transcript_chunk, etc.
├── datasets/                    # Exported entity datasets (JSON, CSV)
├── scripts/
│   ├── run_local.py             # Windows-safe local dev launcher
│   ├── generate_dataset_exports.py  # Regenerate dataset artifacts
│   ├── validate_datasets.py    # Validate dataset integrity
│   └── migrate_js_datasets.py  # Seed DB from JS source of truth
├── tests/
│   ├── unit/                    # Unit tests
│   └── rag/                     # RAG property tests (hypothesis)
├── deploy/
│   └── ecs-task-definition.json # AWS ECS deployment config
├── docker-compose.yml           # Local dev: Postgres + service + gateway
├── Dockerfile                   # Production container build
├── pyproject.toml               # Dependencies, build config, tool settings
├── alembic.ini                  # Alembic configuration
└── sample.env                   # Environment template
```

## Commands

```bash
# Virtual environment setup (first time)
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"   # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # Unix

# Run locally (handles Windows event loop policy)
python scripts/run_local.py
python scripts/run_local.py --reload   # With auto-reload

# Migrations
python -m alembic upgrade head         # Apply all migrations
python -m alembic revision --autogenerate -m "description"  # New migration

# Tests
python -m pytest -v                    # All tests
python -m pytest tests/rag/ -v         # RAG tests only
python -m pytest tests/unit/ -v        # Unit tests only

# Linting & Formatting
ruff check .                           # Lint check
ruff format .                          # Auto-format
ruff check --fix .                     # Auto-fix lint errors

# Dataset management
python scripts/generate_dataset_exports.py   # Regenerate exports
python scripts/validate_datasets.py          # Validate integrity
python scripts/migrate_js_datasets.py        # Seed from JS datasets

# Docker (local dev with Postgres)
docker compose up -d postgres          # Start Postgres only
docker compose up                      # Start all services
docker compose down                    # Stop all
```

## Architecture Patterns

### Service Initialization (Lifespan)

`app/main.py` uses FastAPI's lifespan context manager:
1. Load settings from environment via `app/config.py`
2. Create single shared SQLAlchemy engine (one pool for all workers)
3. Start DatasetCache (background refresh task)
4. Initialize BedrockClient (if `LLM_ENABLED=true`)
5. Start CorrectionHistoryWriter (bounded async queue)
6. On shutdown: stop workers, dispose engine, cleanup resources

### RAG Pipeline

```
Ingest:  transcript → speaker-turn chunking → Titan embeddings → PostgreSQL (pgvector)
Search:  query → embed → vector similarity + ts_rank → RRF fusion → top-K results
Ask:     query → retrieve chunks → build prompt with history → Claude → parse citations + recommendations
```

Key files:
- `rag/ingestion.py` — `TranscriptIngestionWorker` (background queue, speaker-turn chunking)
- `rag/retrieval.py` — `HybridRetriever` (vector + full-text + Reciprocal Rank Fusion)
- `rag/answering.py` — `GroundedAnswerer` (Claude with citation parsing + recommendations)
- `rag/router.py` — FastAPI endpoints + Pydantic request/response models

### Authentication

All endpoints require `Authorization: Bearer <SERVICE_TOKEN>` header.
Dependency: `app/deps.py:verify_service_token`

The `SERVICE_TOKEN` must match the gateway's `POSTPROCESS_TOKEN` env var.

### Configuration

- All config via environment variables, parsed by `app/config.py:Settings` (pydantic-settings)
- Never read from `.env` in production — only `scripts/run_local.py` loads dotenv for dev
- Add new env vars as fields on the `Settings` class with sensible defaults
- Validation happens at startup — fail fast on missing required config

### Database

- Single async engine shared across dataset cache, history writer, RAG
- Async sessions via `make_session_factory(engine)`
- Pool sizing: `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` connections per worker process
- With multi-worker deployment: total connections = workers × (pool_size + max_overflow)
- Statement timeout prevents runaway queries from exhausting the pool
- `DB_SSLMODE=require` enforced in production

### Error Response Envelope

All errors follow a consistent shape:

```json
{
  "error": {
    "type": "retrieval_error",
    "code": "EMBEDDING_FAILED",
    "message": "Failed to generate embedding for query"
  }
}
```

## Testing

### Running Tests

```bash
python -m pytest -v                    # All tests
python -m pytest tests/unit/ -v        # Unit tests
python -m pytest tests/rag/ -v         # RAG property tests
python -m pytest -k "test_chunking"    # Specific test by name
python -m pytest --tb=short            # Shorter tracebacks
```

### Test Conventions

- Use `@pytest.mark.asyncio` for async tests
- Hypothesis strategies for generating transcripts, chunks, queries
- **Mock Bedrock calls** — never call real AWS in tests
- **Mock database** — use in-memory fixtures or mock session
- Property-based tests verify invariants:
  - Chunk size bounds (100–200 words)
  - Speaker boundary integrity
  - RRF fusion correctness (rank monotonicity)
  - Entity pre-filter completeness
  - Citation validity (references exist in source chunks)

### Adding New Tests

For new RAG logic:
1. Add hypothesis strategies in `tests/rag/` for property-based testing
2. Test invariants, not specific outputs (LLM outputs are non-deterministic)
3. For correction logic, test both rule-based and LLM-enhanced paths

For new endpoints:
1. Add unit tests in `tests/unit/`
2. Test request validation (Pydantic model rejection)
3. Test auth (missing/invalid token)
4. Test happy path with mocked dependencies

## Environment Variables

Critical variables (see `sample.env` for all):

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `SERVICE_TOKEN` | Yes | — | Auth token (must match gateway's `POSTPROCESS_TOKEN`) |
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `LLM_ENABLED` | No | `false` | Enable Bedrock LLM for corrections + RAG |
| `AWS_REGION` | No | `us-east-1` | AWS region for Bedrock |
| `AWS_ACCESS_KEY_ID` | No | — | AWS credentials (or use IAM roles) |
| `AWS_SECRET_ACCESS_KEY` | No | — | AWS credentials (or use IAM roles) |
| `BEDROCK_MODEL_ID` | No | — | Claude model ID for answering |
| `UVICORN_WORKERS` | No | `2` | Worker processes |
| `DB_POOL_SIZE` | No | `5` | Pool connections per worker |
| `DB_MAX_OVERFLOW` | No | `5` | Burst connections per worker |
| `DB_SSLMODE` | No | `prefer` | PostgreSQL SSL mode |
| `LOG_LEVEL` | No | `info` | Logging level |

## Security

### Secrets Management

- Never hardcode AWS credentials — use env vars or IAM roles in production
- `SERVICE_TOKEN` authenticates all requests — treat as an API key
- `DATABASE_URL` contains credentials — never log or expose
- AWS credentials should use IAM roles in production (not access keys)

### Database Security

- `DB_SSLMODE=require` in production (plaintext credentials otherwise exposed)
- Statement timeout prevents runaway queries from exhausting the pool
- Connection pool limits prevent resource exhaustion
- Parameterized queries via SQLAlchemy — never raw string interpolation

### Application Security

- History queue is bounded — drops records on overflow rather than blocking (DoS prevention)
- Input validation via Pydantic models (rejects malformed requests at the boundary)
- Rate limiting handled by upstream Caddy, not the Python service
- No filesystem access from request handlers (all data flows through DB)

## Deployment

### Production Architecture

- Runs as a Docker container on AWS ECS (see `deploy/ecs-task-definition.json`)
- Multiple Uvicorn workers behind an internal load balancer
- PostgreSQL managed instance with pgvector extension
- AWS Bedrock accessed via IAM roles (no static credentials)

### Docker Build

```bash
docker build -t postprocess-service .
```

### Database Migrations in Production

Run as a **separate one-off task** before deploying new code:

```bash
python -m alembic upgrade head
```

**Critical rules:**
- Never run migrations automatically during service startup
- Never modify existing migration files (they're immutable once applied)
- Always test migrations against a copy of production data first
- Include rollback steps in migration docstrings for critical changes

### Deployment Checklist

1. `python -m pytest -v` passes locally
2. `ruff check .` passes (no lint errors)
3. New migrations tested against production-like data
4. `SERVICE_TOKEN` matches gateway's `POSTPROCESS_TOKEN`
5. `DATABASE_URL` points to correct production database
6. AWS IAM role has Bedrock permissions (if `LLM_ENABLED=true`)
7. Pool sizing appropriate for worker count × expected load

## Agent Workflow Instructions

### Before Making Changes

1. Read `app/main.py` lifespan to understand service initialization order
2. Check `app/config.py` for existing settings before adding new env vars
3. Read `rag/router.py` for the Pydantic models (request/response schemas)
4. Check if existing utilities in `app/deps.py` cover your needs

### Creating a New Endpoint

1. Create/update router in `app/api/routes_*.py`
2. Define Pydantic request/response models in the router file
3. Add dependency injection via `app/deps.py` if new shared resources needed
4. Register router in `app/main.py` (lifespan or router includes)
5. Add tests in `tests/unit/` or `tests/rag/`
6. Create Alembic migration if new tables/columns needed
7. Update `sample.env` if new env vars required

### Creating a New Migration

```bash
python -m alembic revision --autogenerate -m "add_new_table"
```

Then review the generated file in `migrations/versions/`:
- Verify up/down operations are correct
- Add explicit `op.create_index()` for columns used in WHERE clauses
- Test with `alembic upgrade head` then `alembic downgrade -1`

### Safe Refactoring

- Changes to `app/main.py` lifespan affect all initialization — verify startup order
- Changes to `rag/retrieval.py` affect search quality — test with representative queries
- Changes to `rag/ingestion.py` affect stored data — may require re-ingestion
- Changes to `config.py` Settings — verify defaults don't break existing deployments
- Changes to `deps.py` — affect auth for all endpoints

### Areas Requiring Extra Caution

| Area | Risk | Mitigation |
|------|------|-----------|
| `app/main.py` lifespan | All service init happens here | Test startup/shutdown ordering |
| `rag/retrieval.py` | Search quality, user-facing | Test with known-good queries, verify RRF weights |
| `rag/ingestion.py` | Stored embeddings, re-ingestion expensive | Test chunking invariants |
| `rag/answering.py` | LLM costs, citation accuracy | Mock in tests, verify prompt structure |
| `migrations/versions/*` | Production schema | Never modify applied migrations |
| `config.py` Settings | Breaking config changes | Always provide defaults |
| `docker-compose.yml` | Local dev environment | Test `docker compose up` works |

### Files That Should Not Be Modified Without Good Reason

- `migrations/versions/*.py` — existing migrations are immutable in production
- `alembic.ini` — Alembic configuration (rarely needs changes)
- `docker-compose.yml` — local dev environment (shared across team)
- `deploy/ecs-task-definition.json` — production deployment config
- `datasets/*.json` / `datasets/*.csv` — generated files, not hand-edited

## Git & Commit Guidelines

### Commit Format

```
<type>(<scope>): <short description>
```

Scopes for this service: `rag`, `ingestion`, `retrieval`, `answering`, `correction`, `config`, `migration`, `test`, `deploy`, `deps`

Examples:
- `feat(rag): add entity pre-filter to hybrid search`
- `fix(retrieval): correct RRF fusion weight normalization`
- `test(ingestion): add hypothesis tests for speaker boundary detection`
- `chore(migration): add index on transcript_chunk.sitting_id`

### What Not to Commit

- `.env` files (only `sample.env`)
- `.venv/` directory
- `__pycache__/` directories
- `*.pyc` files
- Generated dataset files (commit via scripts only)
- AWS credentials or tokens

## Agent Instructions (Quick Reference)

1. **async everywhere**: all route handlers and DB operations must be async
2. **Pydantic models**: define request/response schemas in the router file
3. **structlog**: use `structlog.get_logger("module_name")` for logging
4. **Error envelope**: return `{ "error": { "type", "code", "message" } }` on failure
5. **snake_case**: all Python identifiers, API fields, and database columns
6. **Type hints**: all function signatures must have type annotations
7. **Docstrings over comments**: use docstrings for modules, classes, and functions
8. **Settings via config.py**: add new env vars to the `Settings` class with defaults
9. **Migrations**: new tables/columns require an Alembic migration file
10. **Tests**: add hypothesis property tests for any new RAG logic
11. **Windows compatibility**: service must work on Windows with `WindowsSelectorEventLoopPolicy`
12. **ruff compliance**: code must pass `ruff check .` (line-length=100, target py312)
13. **No raw SQL strings**: use SQLAlchemy query builder or text() with bound params
14. **Bounded resources**: queues and pools must have limits (never unbounded growth)
