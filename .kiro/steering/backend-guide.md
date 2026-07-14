---
inclusion: fileMatch
fileMatchPattern: "server.js"
---

# Backend Development Guide

## server.js Structure

The backend is a single-file Express server organized in sections:

1. **Configuration** — `DEFAULT_MODEL`, `CONFIG` object
2. **Session Auth** — JWT signing/verification, `requireSession` middleware
3. **API Key Loading** — `loadApiKey()` with env/config.json fallback
4. **Setup** — Express init, CORS, Multer, Deepgram client
5. **Helper Functions** — `validateTranscriptionInput`, `transcribeAudio`, `formatTranscriptionResponse` (async), `formatErrorResponse`
6. **Routes** — `/api/session`, `/api/transcription`, `/api/transcription/hybrid`, `/api/metadata`
7. **Server Start** — `app.listen()`

`formatTranscriptionResponse` is `async` because it awaits the post-processing pipeline (rule-based correction, then optional Bedrock). **Always `await` it at the call site** — a missing `await` silently returns an unresolved Promise to `res.json()`, producing an empty response body with no error logged.

## Adding a New API Endpoint

1. Define the route after existing routes (before server start)
2. Use `requireSession` middleware if auth is needed
3. Return errors using `formatErrorResponse()` pattern
4. Add the endpoint to the startup log

## Post-Processing Pipeline (`lib/location-correction/`)

`formatTranscriptionResponse` runs two correction stages on the Deepgram result before responding:

1. **Rule-based** — `correctLocations(text)` from `lib/location-correction/index.js`. Deterministic, instant, dataset-driven (regions/cities, supplementary constituencies, presidents/ministers, MPs, parties). Handles fused words (`ningoprampram` → `Ningo-Prampram`), split words (`pram pram` → `Prampram`), spelling (`Kumase` → `Kumasi`), phonetic matches, initials (`K. Ofori-Atta` → `Ken Ofori-Atta`), and party abbreviation ↔ full-name normalization. Never expands short person-name aliases into full names at the word level (would break word timing) — only party abbreviations expand/normalize since they're always short.
2. **Bedrock (optional)** — `postProcessWithBedrock(transcript, words)` from `lib/location-correction/bedrock-postprocess.js`. Skipped if AWS credentials are unset (`isBedrockConfigured()`). Splits the corrected transcript into ~300-word chunks, sends up to 3 chunks in parallel per wave to Claude via `InvokeModelCommand`, with the full dataset reference (regions, cities, officials, MPs, parties) injected into the system prompt so the model can ground corrections in real Ghanaian entities. Marks corrected words `bedrockCorrected: true`. Bedrock failures are caught and logged — they never fail the request; the rule-based result is returned as-is.

When adding a new dataset entry (location, official, MP, party), add it once in the relevant `lib/location-correction/*-dataset.js` file — both stages read from the same source, so Bedrock's prompt and the rule-based engine stay in sync automatically.

### Response Shape Additions

Corrected `words[]` entries carry: `locationCorrected` (bool, rule-based), `bedrockCorrected` (bool, LLM), `entityKind` (`"location" | "person" | "party"`), `entityType` (finer category, e.g. `"region"`, `"city"`, `"mp"`).

The response also includes a deduplicated `entities: [{ name, kind, type, mentions }]` array summarizing every Ghana location/person/party mentioned in the transcript, and `metadata.location_corrections` / `metadata.bedrock_corrections` counts.

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

## Adding Deepgram Features

Add parameters to the options object in `transcribeAudio()`:
- `diarize: true` — Speaker identification
- `punctuate: true` — Add punctuation
- `smart_format: true` — Format numbers/dates
- `language: "es"` — Non-English transcription
- `paragraphs: true` — Add paragraph breaks

## Error Response Format

All errors must follow this structure:
```json
{
  "error": {
    "type": "ValidationError | TranscriptionError | AuthenticationError",
    "code": "MISSING_INPUT | TRANSCRIPTION_FAILED | MISSING_TOKEN | INVALID_TOKEN",
    "message": "Human-readable error message"
  }
}
```
