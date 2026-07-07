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
| `/api/transcription` | POST | Bearer JWT | multipart: `file` OR `url`, optional `model` | `Transcript` schema |

## Response Schemas

### Transcript (200 OK)

```json
{
  "transcript": "string (required)",
  "words": [{ "word": "string", "start": 0.0, "end": 0.5, "confidence": 0.99 }],
  "duration": 25.9,
  "metadata": { "request_id": "uuid", "model_name": "nova-3", "model_uuid": "uuid" }
}
```

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
