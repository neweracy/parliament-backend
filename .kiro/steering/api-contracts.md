---
inclusion: fileMatch
fileMatchPattern: "server.js,contracts/**"
---

# API Contracts & Documentation

The API contract (OpenAPI 3.1) lives in the contracts submodule:

#[[file:contracts/interfaces/transcription/openapi.yml]]

The same spec is served at runtime: Swagger UI at `/docs`, raw YAML at `/api/openapi.yml`.

## Endpoints Summary

| Endpoint | Method | Auth | Request | Response |
|----------|--------|------|---------|----------|
| `/api/session` | GET | None | — | `{ token: "<jwt>" }` (1h expiry) |
| `/api/metadata` | GET | None | — | `[meta]` table from `deepgram.toml` |
| `/api/transcription` | POST | Bearer JWT | multipart: `file` OR `url`, optional `model` | `Transcript` schema (post-processed) |
| `/api/transcription/hybrid` | POST | Bearer JWT | multipart: `file` | Hybrid transcript with `segments[]` carrying `corrected`/`language` |
| `/api/khaya/transcription` | POST | Bearer JWT | multipart: `file`, `language` (required) | Khaya transcript |
| `/api/khaya/languages` | GET | None | — | Khaya-supported languages |
| `/api/audio-proxy` | GET | None | `?url=` | Streams remote audio with CORS-safe headers |
| `/docs`, `/api/openapi.yml` | GET | None | — | Swagger UI / raw OpenAPI YAML |

## Response Schemas

### Transcript (200 OK)

`transcript` and `words[]` reflect Deepgram's output **after** post-processing. Which stages ran depends on `POSTPROCESS_MODE`.

```json
{
  "transcript": "string (required, post-processed)",
  "words": [
    {
      "word": "string",
      "start": 0.0,
      "end": 0.5,
      "confidence": 0.99,
      "locationCorrected": true,
      "bedrockCorrected": false,
      "entityKind": "location",
      "entityType": "supplementary"
    }
  ],
  "entities": [
    { "name": "Ningo-Prampram", "kind": "location", "type": "supplementary", "mentions": 8 }
  ],
  "duration": 25.9,
  "raw": { "transcript": "string (pre-correction)", "words": [] },
  "metadata": {
    "request_id": "uuid",
    "model_name": "nova-3",
    "model_uuid": "uuid",
    "_version": "v5-bedrock",
    "location_corrections": 19,
    "year_corrections": 3,
    "bedrock_corrections": 208
  }
}
```

- `locationCorrected`, `bedrockCorrected`, `entityKind`, `entityType` appear only on words that were corrected.
- `*_corrections` counters are omitted when zero. `entities` is omitted in `js` mode when nothing was recognized, and is `[]` in degraded mode.
- `_version` is `v5-bedrock` (`js` mode) or `v6-python` (`python`/`off` modes).
- In `python`/`off` modes a `metadata.postprocessing_status` of `applied`, `skipped`, or `disabled` is present; `python` mode success also passes through `llm_status`, `dataset_version`, `rule_latency_ms`, and `llm_latency_ms`. A failed service call still returns **200** with the raw transcript, not an error.

### Hybrid Transcript (200 OK)

`{ transcript, segments[], words[], duration, metadata }`. Segments carry `text`, `start`, `end`, `corrected`, plus `language` on corrected blocks and `confidence` on preserved ones. `metadata` carries `pipeline: "hybrid-confidence"`, a `correctionStats` object (`segmentsDetected`, `corrected`, `language`, `correctionSkipped`), and a `config` echo of the four `HYBRID_*` values.

### Error (4XX/5XX)

```json
{
  "error": {
    "type": "ValidationError | TranscriptionError | AuthenticationError | ConfigurationError | RateLimitError",
    "code": "MISSING_INPUT | MISSING_LANGUAGE | TRANSCRIPTION_FAILED | MISSING_TOKEN | INVALID_TOKEN | MISSING_API_KEY | INVALID_API_KEY | QUOTA_EXCEEDED",
    "message": "Human-readable message",
    "details": {}
  }
}
```

`details` is only populated by `formatErrorResponse` (the Deepgram path).

## Internal Service Contract

The Postprocessing Service is not public. The gateway calls `POST /v1/postprocess` with `{ transcript, words, options, correlationId }` and a `Bearer` token, and receives `{ transcript, words, entities, corrections, metadata }`. Errors use the same envelope with types `ValidationError` (422, `INVALID_REQUEST`), `ServiceUnavailable` (503, `DATASET_NOT_LOADED`), and `PostprocessingError` (500, `PIPELINE_FAILED`).

## Contract Conformance Tests

Run with the app running:
```bash
make test
# or directly:
bash contracts/tests/run-transcription-app.sh
```

Interfaces present in the submodule: `transcription`, `live-transcription`, `text-to-speech`, `live-text-to-speech`, `text-intelligence`, `voice-agent`, `flux`, plus `shared/` (session + deploy specs). Only `transcription` applies to this app — its suite is `contracts/interfaces/transcription/conformance/transcribe.spec.js`.

## Notes

- The OpenAPI spec uses `/stt/transcribe` as the canonical path; this app maps it to `/api/transcription`
- `model` defaults to `nova-3`
- File uploads use `multipart/form-data` with field name `file`; URL transcription passes `url` as a form field (not JSON)
- Khaya requires an explicit `language` code and does not auto-detect
