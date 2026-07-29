---
inclusion: fileMatch
fileMatchPattern: "server.js"
---

# Backend Development Guide

## server.js Structure

Sections, in order:

1. **Postprocess mode** — `POSTPROCESS_MODE` resolution (`js` | `python` | `off`, unknown → warn + `js`)
2. **Configuration** — `DEFAULT_MODEL` (`nova-3`), `CONFIG` (`PORT` default 8081, `HOST` default 0.0.0.0)
3. **Session auth** — `SESSION_SECRET` (random if unset), `JWT_EXPIRY` 1h, `requireSession` middleware
4. **API key loading** — `loadApiKey()`: `DEEPGRAM_API_KEY` → `config.json` → `process.exit(1)`
5. **Setup** — Deepgram client, Multer memory storage, Express, `cors()`
6. **API docs** — Swagger UI at `/docs` serving `contracts/interfaces/transcription/openapi.yml` (skipped if missing)
7. **Helpers** — `validateTranscriptionInput`, `transcribeAudio`, `transcribePrimaryForHybrid`, `extractDeepgramResult`, `legacyPostprocess`, `formatTranscriptionResponse`, `formatErrorResponse`
8. **Routes** — `/api/session`, `/api/transcription`, `/api/metadata`, mounted `/api/khaya` and `/api/transcription/hybrid` routers, `/api/audio-proxy`
9. **Server start** — `app.listen()` with a startup banner listing every route

`formatTranscriptionResponse` and `legacyPostprocess` are `async`. **Always `await` at the call site** — a missing `await` hands an unresolved Promise to `res.json()`, producing an empty body with no error logged.

## Adding a New API Endpoint

1. For a route group, add a factory in `routes/` that takes `(requireSession, upload, deps)` and returns an `express.Router`; mount it in `server.js`. Single routes can go inline before `app.listen`.
2. Use `requireSession` middleware if auth is needed
3. Return errors using the `formatErrorResponse()` shape
4. Add the endpoint to the startup banner

## Mode Dispatch

`formatTranscriptionResponse` calls `extractDeepgramResult` (throws if Deepgram returned no alternatives), then branches:

- `off` → `degradedResponse(...)` from `lib/postprocess-mode.js`, `postprocessing_status: "disabled"`
- `js` → `legacyPostprocess(...)` (below)
- `python` → `postprocess(...)` from `lib/postprocess-client.js`. On failure: `logDegraded` + `degradedResponse` with status `skipped` (still HTTP 200). On success: `mergeSuccess` — service supplies transcript/words/entities/counters, the gateway keeps `model_uuid`/`request_id`/`model_name`, `raw`, and `duration`.

`_version` is `v5-bedrock` in `js` mode and `v6-python` in `python`/`off` modes.

## Legacy JS Pipeline (`POSTPROCESS_MODE=js`)

1. **Rule-based entity correction** — `correctLocations(text)` from `lib/location-correction/index.js` (thin facade re-exporting from `dataset-builder.js`, `normalize.js`, `indexes.js`, `matchers.js`), applied to the transcript and to the word list via `correctWordsWalk(words, deps)` from `lib/location-correction/word-walk.js` (title-aware person lookup, then 3-word, 2-word, single-word joins against a stopword list). Handles fused words (`ningoprampram` → `Ningo-Prampram`), split words (`pram pram` → `Prampram`), spelling (`Kumase` → `Kumasi`), phonetic matches, initials, and party abbreviation ↔ full-name normalization. Never expands short person-name aliases at the word level (would break word timing); only party abbreviations normalize, since they're always short.
2. **Year/date correction** — `correctYears(words)` / `correctYearsInText(text)` from `lib/location-correction/year-correction.js` (facade re-exporting from `years/numbers.js`, `years/parsers.js`, `years/patterns.js`). Adds `metadata.year_corrections`.
3. **Bedrock LLM (optional)** — `postProcessWithBedrock(transcript, words)` from `lib/location-correction/bedrock-postprocess.js` (facade over `bedrock/client.js`, `bedrock/prompt.js`, `bedrock/align.js`). Skipped when `isBedrockConfigured()` is false. Splits into 300-word chunks, 3 chunks in parallel per wave via `InvokeModelCommand`, with the datasets injected into the system prompt. Falls back to LCS diff alignment when Claude's token count differs. Marks words `bedrockCorrected: true`. Failures are caught and logged — never fail the request.

Add new Ghana entities once in the relevant `lib/location-correction/*-dataset.js`; both the rule engine and the Bedrock prompt read from the same source.

### Response Shape Additions

Corrected `words[]` entries carry `locationCorrected` (rule-based), `bedrockCorrected` (LLM), `entityKind` (`"location" | "person" | "party"`), and `entityType` (e.g. `"region"`, `"city"`, `"mp"`). The response also carries an `entities: [{ name, kind, type, mentions }]` summary, `metadata.{location_corrections, year_corrections, bedrock_corrections}` (omitted when zero), and `raw: { transcript, words }` holding the pre-correction output.

## Postprocessing Service (`POSTPROCESS_MODE=python`)

FastAPI app at `services/postprocess/`, port 8082, Bearer-token auth (`SERVICE_TOKEN` on the service must match `POSTPROCESS_TOKEN` on the gateway).

| Route | Auth | Purpose |
|-------|------|---------|
| `POST /v1/postprocess` | Bearer | Run the pipeline; 503 until the dataset cache completes its first load |
| `GET /health` | None | 200 with record count/version/uptime, 503 before first dataset load |
| `POST /v1/datasets/reload` | Bearer | Force a Dataset_Cache reload |

Pipeline order in `app/pipeline.py`: Correction_Engine (`app/correction/engine.py`) → Year_Corrector (`app/years/`) → LLM_Refiner gate. **The LLM_Refiner is fully wired** — when `LLM_ENABLED` is true, a `BedrockClient` is constructed, and `refine_chunks` from `app/llm/refiner.py` is invoked as stage 3. The pipeline reports `llm_status` as `applied` (all chunks OK), `degraded` (partial or all failed), `skipped` (disabled by request or setting), or `unconfigured` (no credentials / empty model ID).

Run it:

```bash
cd services/postprocess
uvicorn app.main:app --host 0.0.0.0 --port 8082 --workers ${UVICORN_WORKERS:-2}
docker compose up          # service + PostgreSQL + gateway
pip install -e ".[dev]" && pytest   # tests/{unit,property,integration,load}
```

Gateway-side env vars: `POSTPROCESS_MODE`, `POSTPROCESS_URL` (default `http://localhost:8082`), `POSTPROCESS_TOKEN`, `POSTPROCESS_TIMEOUT_MS` (20000), `POSTPROCESS_BREAKER_THRESHOLD` (5), `POSTPROCESS_BREAKER_COOLDOWN_MS` (30000). The client retries once on connection errors and 502/503/504 — not on timeouts.

## Hybrid Pipeline (`lib/hybrid/`)

`routes/hybrid.js` loads config per request via `loadHybridConfig()` and calls `runHybridPipeline(input, deps, config)`. Deps are injected from `server.js`: `transcribePrimary` (punctuated Deepgram call), `khayaTranscribe`, `sliceAndConcatAudio`, `khayaConfigured`. Throws `ConfigurationError`/`MISSING_API_KEY` when `KHAYA_API_KEY` is unset. Env vars: `HYBRID_CONFIDENCE_THRESHOLD` (0.85), `HYBRID_GAP_TOLERANCE` (0.5), `HYBRID_PADDING` (0.25), `HYBRID_MAX_CALLS_PER_MODEL` (3) — invalid values warn and fall back to the default.

## Deepgram SDK Usage

```javascript
// URL transcription
const result = await deepgram.listen.prerecorded.transcribeUrl(
  { url: audioUrl },
  { model: "nova-3" }
);

// File transcription
const result = await deepgram.listen.prerecorded.transcribeFile(buffer, {
  model: "nova-3",
  mimetype: "audio/wav"
});
```

Add features via the options object in `transcribeAudio()`: `diarize`, `punctuate`, `smart_format`, `language`, `paragraphs`.

## Error Response Format

```json
{
  "error": {
    "type": "ValidationError | TranscriptionError | AuthenticationError | ConfigurationError | RateLimitError",
    "code": "MISSING_INPUT | MISSING_LANGUAGE | TRANSCRIPTION_FAILED | MISSING_TOKEN | INVALID_TOKEN | MISSING_API_KEY | INVALID_API_KEY | QUOTA_EXCEEDED",
    "message": "Human-readable error message"
  }
}
```

## Testing

```bash
corepack pnpm test    # node --test 'test/**/*.test.js'
make test             # contract conformance (app must be running)
```

`test/` covers `hybrid/`, `location-correction/`, `postprocess-client/`, `providers/`, and `routes/`, with `fast-check` property tests alongside unit tests and helpers in `test/helpers/`.

## Authoritative Implementation and Data Flow

**Correction algorithms:** `services/postprocess/app/correction/engine.py` is the authoritative implementation for correction-algorithm changes. The JS_Correction_Engine (`lib/location-correction/`) follows Python's algorithm decisions. When a fix or enhancement is applied to the Python engine, the same change must be ported to JS or recorded as an accepted divergence in `test/parity/accepted-divergences.json`.

**Entity data:** flows the other direction. The primary Dataset_Source lives in `lib/location-correction/*-dataset.js` plus `SUPPLEMENTARY_LOCATIONS` exported from `lib/location-correction/index.js`. Data is generated into the Python service via `services/postprocess/scripts/export_js_datasets.js`. Adding a new entity means editing the JS dataset file; the Python service receives it through the generation pipeline.

These are independent axes — an algorithm fix does not touch dataset files, and adding an entity does not touch algorithm code.
