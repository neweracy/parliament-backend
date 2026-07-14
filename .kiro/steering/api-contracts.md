---
inclusion: fileMatch
fileMatchPattern: "server.js,contracts/**"
---

# API Contracts & Documentation

The API contract (OpenAPI 3.1) lives in the contracts submodule:

#[[file:contracts/interfaces/transcription/openapi.yml]]

## Endpoints Summary

| Endpoint | Method | Auth | Request | Response |
|----------|--------|------|---------|----------|
| `/api/session` | GET | None | — | `{ token: "<jwt>" }` |
| `/api/metadata` | GET | None | — | App metadata from `deepgram.toml` `[meta]` |
| `/api/transcription` | POST | Bearer JWT | multipart: `file` OR `url`, optional `model` | `Transcript` schema (post-processed) |
| `/api/transcription/hybrid` | POST | Bearer JWT | multipart: `file`, optional `model` | Hybrid `Transcript` with `segments[]` carrying `corrected`/`language` |

## Response Schemas

### Transcript (200 OK)

The `transcript` and `words[]` fields reflect Deepgram's raw output **after** the post-processing pipeline (rule-based correction, then optional Bedrock LLM pass) has run. Corrected words are annotated in place.

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
  "metadata": {
    "request_id": "uuid",
    "model_name": "nova-3",
    "model_uuid": "uuid",
    "location_corrections": 19,
    "bedrock_corrections": 208
  }
}
```

`locationCorrected`, `bedrockCorrected`, `entityKind`, `entityType` are only present on words that were actually corrected. `entities` and the `*_corrections` metadata counts are only present when at least one correction was applied.

### Error (4XX/5XX)

```json
{
  "error": {
    "type": "ValidationError | TranscriptionError | AuthenticationError",
    "code": "MISSING_INPUT | TRANSCRIPTION_FAILED | MISSING_TOKEN | INVALID_TOKEN",
    "message": "Human-readable message",
    "details": {}
  }
}
```

## Contract Conformance Tests

Run with the app running:
```bash
make test
# or directly:
bash contracts/tests/run-transcription-app.sh
```

## Notes

- The OpenAPI spec uses `/stt/transcribe` as the canonical path but this starter maps it to `/api/transcription`
- The `model` field defaults to `nova-3` if not provided
- File uploads use `multipart/form-data` with the field name `file`
- URL transcription passes `url` as a form field (not JSON body)
