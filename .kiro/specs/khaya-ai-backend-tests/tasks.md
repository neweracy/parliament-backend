# Implementation Plan: Khaya AI Backend Test Suite

## Overview

This plan builds a hermetic, automated test suite for the Khaya AI (GhanaNLP) ASR integration (`providers/khaya.js` and `routes/khaya.js`) using Node's built-in `node:test` + `node:assert/strict` runner and `supertest` for HTTP-level route testing. Work proceeds bottom-up: configure tooling, build shared test helpers and in-memory fixtures, then write the provider unit tests and route integration tests that depend on them, and finally wire the `test` script and confirm the full suite runs green.

Per the design's Testing Strategy, property-based testing is intentionally NOT used: the modules under test are thin adapters around a (mocked) external HTTP API and the acceptance criteria are concrete, example-based scenarios. Coverage is therefore achieved entirely through unit tests (provider) and integration tests (routes). There are no Correctness Properties, so no property-test tasks appear below.

All tests are hermetic by construction: no live Khaya AI API calls and no reads from the `test_audio` directory.

## Tasks

- [x] 1. Configure test tooling and dependencies
  - Add `supertest` as the single new `devDependency` in the root `package.json`, pinned to an exact version (no `^`/`~`), per project dependency conventions.
  - Add a `test` script to root `package.json` scripts that runs `node --test` (single non-watch run; exits zero on pass, non-zero on failure).
  - Install via `corepack pnpm install` so the lockfile is updated.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [ ] 2. Build in-memory fixtures (no filesystem, no network)
  - [x] 2.1 Create `test/fixtures/audio.js`
    - Export small in-memory `Buffer`s of fake audio bytes with their mimetypes (e.g. `{ buffer: Buffer.from([...]), mimetype: "audio/mpeg" }`).
    - No filesystem access; nothing read from `test_audio`.
    - _Requirements: 1.7_

  - [x] 2.2 Create `test/fixtures/responses.js`
    - Export representative Khaya payloads: a JSON string transcript body; an object body with `transcript`, `words`, `duration`; an object body with `text` and no `transcript`; a languages list payload; error bodies/status codes for 401, 429, and a generic 5xx.
    - _Requirements: 2.3, 2.4, 2.5, 2.6, 3.2, 3.3, 3.4, 3.6, 4.3_

- [ ] 3. Build shared test helpers
  - [x] 3.1 Create `test/helpers/fetchMock.js`
    - Implement `createFetchMock(opts)` returning `{ fn, calls }`, where `fn` replaces `global.fetch`, records each `{ url, options }` into `calls`, and returns a `Response`-like object exposing `ok`, `status`, async `json()` (returns `jsonBody`), and async `text()` (returns `textBody`).
    - Support both a raw JSON string body and an object body via `jsonBody`.
    - _Requirements: 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 4.1, 4.2, 4.3_

  - [x] 3.2 Create `test/helpers/providerEnv.js`
    - Implement `loadProviderWithEnv({ apiKey, asrVersion })` that clears `providers/khaya.js` from `require.cache`, sets the env vars, re-requires the provider (so the load-time `ASR_VERSION` constant is rebound), and returns `{ khaya, restore }`.
    - `restore()` resets the affected `process.env` keys to prior values and clears the cache to prevent cross-test leakage.
    - Treat `KHAYA_ASR_VERSION` as load-time-bound (requires reload) and `KHAYA_API_KEY` as call-time-bound.
    - _Requirements: 2.7, 2.8, 3.1, 3.5_

  - [x] 3.3 Create `test/helpers/jwt.js`
    - Implement `signValidToken(secret)` (`jwt.sign` with `expiresIn: "1h"`), `signExpiredToken(secret)` (`expiresIn: -1`), and `invalidToken()` (malformed token string), using the `jsonwebtoken` library already in the project.
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 3.4 Create `test/helpers/app.js`
    - Implement `buildApp({ secret })` that constructs an Express app with a production-faithful `requireSession` middleware (same `MISSING_TOKEN`/`INVALID_TOKEN` structured errors and `SESSION_SECRET` behavior as `server.js`), an in-memory `multer` instance, and mounts the real `createRouter(requireSession, upload)` at `/api/khaya`.
    - Do NOT import `server.js` (it binds a port and loads the Deepgram client); this helper is the single source of truth for the test auth middleware.
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 4. Checkpoint - Ensure helpers and fixtures load
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Write provider unit tests (`test/providers/khaya.test.js`)
  - [x] 5.1 Test successful transcription request construction and response normalization
    - Install a `createFetchMock` stub in `beforeEach`, restore `global.fetch` in `afterEach`.
    - Assert request URL targets `/asr/{version}/transcribe` with a URL-encoded `language` query param, the `Ocp-Apim-Subscription-Key` header equals `KHAYA_API_KEY`, and `Content-Type` is `audio/mpeg`.
    - Assert normalization branches: string body → `transcript`; object with `transcript`; object with `text` only; `words`/`duration` passthrough; `metadata.provider` = `khaya-ai`, `metadata.api_version` = configured version, `metadata.language` = requested language.
    - Using `loadProviderWithEnv`, assert a non-default `KHAYA_ASR_VERSION` appears in the request URL path.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 5.2 Test `transcribe` error branches
    - Assert missing `KHAYA_API_KEY` throws `statusCode` 500 / `code` `MISSING_API_KEY` / `type` `ConfigurationError`.
    - Drive the fetch mock to 401, 429, and a generic 5xx and assert the thrown errors carry the expected `statusCode`/`code`/`type` (`INVALID_API_KEY`/`AuthenticationError`, `QUOTA_EXCEEDED`/`RateLimitError`, `TRANSCRIPTION_FAILED`/`TranscriptionError` with `statusCode` equal to the response status).
    - Use `assert.rejects` with a predicate checking those fields.
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 5.3 Test `getLanguages` success and error paths
    - Assert success targets `/asr/{version}/languages` with the `Ocp-Apim-Subscription-Key` header and returns the response body verbatim.
    - Assert missing key throws `statusCode` 500 / `MISSING_API_KEY` / `ConfigurationError`.
    - Assert a non-ok response throws `LANGUAGES_FETCH_FAILED` / `ProviderError` with `statusCode` equal to the response status.
    - _Requirements: 3.5, 3.6, 4.1, 4.2, 4.3_

- [x] 6. Checkpoint - Ensure provider tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Write route integration tests (`test/routes/khaya.test.js`)
  - [x] 7.1 Test authentication on the routes
    - Build the app with `buildApp({ secret })` and set `process.env.SESSION_SECRET`.
    - Assert `POST /api/khaya/transcription` without an Authorization header → 401 with structured error `code` `MISSING_TOKEN`.
    - Assert an invalid/expired token → 401 with structured error `code` `INVALID_TOKEN`.
    - Assert a valid token passes auth and reaches validation/provider.
    - Assert `GET /api/khaya/languages` is processed without an auth failure when no Authorization header is present.
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 7.2 Test input validation on `POST /api/khaya/transcription`
    - Using `mock.method` on the cached provider and `supertest` multipart uploads: assert missing file → 400 `ValidationError` / `MISSING_INPUT`; file present but missing language → 400 `ValidationError` / `MISSING_LANGUAGE`.
    - Assert that when both file and language are present, provider `transcribe` is invoked with the uploaded buffer, mimetype, and supplied language.
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 7.3 Test successful route responses
    - Mock `transcribe` to return a `Transcription_Result` and assert `POST /api/khaya/transcription` → 200 with a body equal to that result.
    - Mock `getLanguages` to return a languages payload and assert `GET /api/khaya/languages` → 200 with a body equal to that payload.
    - _Requirements: 7.1, 7.2_

  - [x] 7.4 Test route error mapping
    - Mock `transcribe` to throw errors with `statusCode` 401/429/500 and assert the response uses the same status and a structured error carrying the same `type`, `code`, `message`.
    - Assert a thrown error without `statusCode` maps to 500 with `TRANSCRIPTION_FAILED` / `TranscriptionError`.
    - Mock `getLanguages` to throw with a `statusCode` (mapped to that status + structured error) and without a `statusCode` (mapped to 500 with `LANGUAGES_FETCH_FAILED` / `ProviderError`).
    - Call `mock.restoreAll()` after route tests to restore provider methods.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [x] 8. Final checkpoint - Run the full suite green
  - Run `pnpm test` (`node --test`) and confirm all provider and route tests pass with a zero exit code, and that no network calls are made and no `test_audio` files are read.
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 1.3, 1.4, 1.6, 1.7_

## Notes

- Tasks marked with `*` are optional and can be skipped; this plan has none because the suite itself is the deliverable and every test task is core to the feature.
- Property-based testing is intentionally omitted per the design's Testing Strategy; all coverage is via `node:test` unit tests (provider) and `supertest` integration tests (routes).
- Helpers and fixtures (tasks 2–3) are built before the tests that depend on them (tasks 5, 7).
- Each task references specific requirements for traceability; checkpoints ensure incremental validation.
