# AGENTS.md — Postprocessing Service (Python/FastAPI)

Operational guide for AI coding agents working in the Python postprocessing service.

## Overview

FastAPI async service responsible for transcript post-processing (entity correction, LLM refinement) and the RAG pipeline (ingestion, hybrid retrieval, grounded answering). Backed by PostgreSQL with pgvector for embedding storage and pg_trgm for fuzzy text matching.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI 0.115 |
| Runtime | Python >= 3.12, Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| Migrations | Alembic |
| Database | PostgreSQL 16 + pgvector + pg_trgm |
| Fuzzy Match | RapidFuzz |
| LLM | Amazon Bedrock (Claude) via boto3 |
| Embeddings | Bedrock Titan Text Embeddings V2 |
| Logging | structlog (structured JSON) |
| Testing | pytest + hypothesis (property-based) |
| Linting | ruff |
| Package Manager | pip (via hatchling build system) |

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
│   ├── generate_dataset_exports.py
│   ├── validate_datasets.py
│   └── migrate_js_datasets.py   # Seed DB from JS source of truth
├── tests/
│   ├── unit/                    # Unit tests
│   └── rag/                     # RAG property tests (hypothesis)
├── deploy/
│   └── ecs-task-definition.json # AWS ECS deployment config
├── docker-compose.yml           # Local dev: Postgres + service + gateway
├── Dockerfile
├── pyproject.toml               # Dependencies, build config, tool settings
├── sample.env                   # Environment template
└── alembic.ini                  # Alembic configuration
```

## Commands

```bash
# Activate venv first
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate

# Run locally (handles Windows event loop)
python scripts/run_local.py
python scripts/run_local.py --reload   # With auto-reload

# Migrations
python -m alembic upgrade head         # Apply all migrations
python -m alembic revision --autogenerate -m "description"  # New migration

# Tests
python -m pytest -v                    # All tests
python -m pytest tests/rag/ -v         # RAG tests only
python -m pytest tests/unit/ -v        # Unit tests only

# Linting
ruff check .
ruff format .

# Dataset management
python scripts/generate_dataset_exports.py
python scripts/validate_datasets.py
```

## Architecture Patterns

### Service Initialization (Lifespan)

`app/main.py` uses FastAPI's lifespan context manager:
1. Load settings from environment
2. Create single shared SQLAlchemy engine (one pool for all)
3. Start DatasetCache (background refresh task)
4. Initialize BedrockClient (if LLM enabled)
5. Start CorrectionHistoryWriter (bounded async queue)
6. On shutdown: stop workers, dispose engine

### RAG Pipeline

```
Ingest:  transcript → speaker-turn chunking → Titan embeddings → PostgreSQL (pgvector)
Search:  query → embed → vector similarity + ts_rank → RRF fusion → top-K results
Ask:     query → retrieve chunks → build prompt with history → Claude → parse citations + recommendations
```

Key files:
- `rag/ingestion.py` — `TranscriptIngestionWorker` (background queue)
- `rag/retrieval.py` — `HybridRetriever` (vector + full-text + RRF)
- `rag/answering.py` — `GroundedAnswerer` (Claude with citation parsing)
- `rag/router.py` — FastAPI endpoints + Pydantic models

### Authentication

All endpoints require `Authorization: Bearer <SERVICE_TOKEN>` header.
Dependency: `app/deps.py:verify_service_token`

### Configuration

All config via environment variables, parsed by `app/config.py:Settings` (pydantic-settings).
Never read from `.env` in production — only `scripts/run_local.py` loads dotenv for dev.

### Database

- Single engine shared across dataset cache, history writer, RAG
- Async sessions via `make_session_factory(engine)`
- Pool sizing: `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` connections per worker process
- With multi-worker: total = workers × (pool_size + max_overflow)

## Testing

### pytest + hypothesis

```bash
python -m pytest -v
```

- **Unit tests**: `tests/unit/` — test individual functions
- **RAG tests**: `tests/rag/` — property-based tests for:
  - Chunk size bounds (100–200 words)
  - Speaker boundary integrity
  - RRF fusion correctness
  - Entity pre-filter completeness
  - Citation validity

### Key testing patterns

- Use `@pytest.mark.asyncio` for async tests
- Hypothesis strategies for generating transcripts, chunks, queries
- Mock Bedrock calls — never call real AWS in tests

## Environment Variables

Critical variables (see `sample.env` for all):

| Variable | Required | Purpose |
|----------|----------|---------|
| `SERVICE_TOKEN` | Yes | Auth token (must match gateway's `POSTPROCESS_TOKEN`) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `LLM_ENABLED` | No | Enable Bedrock LLM (default: false) |
| `AWS_REGION` | No | AWS region for Bedrock (default: us-east-1) |
| `BEDROCK_MODEL_ID` | No | Claude model ID |
| `UVICORN_WORKERS` | No | Worker processes (default: 2) |
| `DB_POOL_SIZE` | No | Pool connections per worker (default: 5) |
| `DB_MAX_OVERFLOW` | No | Burst connections (default: 5) |
| `LOG_LEVEL` | No | Logging level (default: info) |

## Security

- Never hardcode AWS credentials — use env vars or IAM roles
- `SERVICE_TOKEN` authenticates all requests — equivalent to an API key
- `DB_SSLMODE=require` in production (plaintext credentials otherwise)
- Statement timeout prevents runaway queries from exhausting the pool
- History queue is bounded — drops records on overflow rather than blocking

## Agent Instructions

1. **async everywhere**: all route handlers and DB operations must be async
2. **Pydantic models**: define request/response schemas in the router file
3. **structlog**: use `structlog.get_logger("module_name")` for logging
4. **Error envelope**: return `{ "error": { "type", "code", "message" } }` on failure
5. **snake_case**: all Python identifiers, API fields, and database columns
6. **Type hints**: all function signatures must have type annotations
7. **No comments in code** (per run_script rules) — docstrings are fine
8. **Settings via config.py**: add new env vars to the `Settings` class with defaults
9. **Migrations**: new tables/columns require an Alembic migration file
10. **Tests**: add hypothesis property tests for any new RAG logic
11. **Windows compatibility**: the service must work on Windows with WindowsSelectorEventLoopPolicy
12. **ruff**: code must pass `ruff check .` (line-length=100, target py312)
