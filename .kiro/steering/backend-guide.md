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
5. **Helper Functions** — `validateTranscriptionInput`, `transcribeAudio`, `formatTranscriptionResponse`, `formatErrorResponse`
6. **Routes** — `/api/session`, `/api/transcription`, `/api/metadata`
7. **Server Start** — `app.listen()`

## Adding a New API Endpoint

1. Define the route after existing routes (before server start)
2. Use `requireSession` middleware if auth is needed
3. Return errors using `formatErrorResponse()` pattern
4. Add the endpoint to the startup log

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
