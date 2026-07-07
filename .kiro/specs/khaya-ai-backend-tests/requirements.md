# Requirements Document

## Introduction

This feature establishes an automated test suite for the Khaya AI (GhanaNLP) ASR integration in the Node.js/Express backend. The integration consists of a provider module (`providers/khaya.js`) that calls the Khaya AI ASR API and a route module (`routes/khaya.js`) that exposes `POST /api/khaya/transcription` and `GET /api/khaya/languages`.

The project currently has no test framework configured. This spec introduces a lightweight testing setup (aligned with the project's "minimal dependencies, vanilla JS, CommonJS, corepack pnpm" conventions) and defines the coverage needed to validate provider behavior, route behavior, JWT authentication, input validation, provider-error mapping, and response-shape normalization.

All tests rely on mocking (`global.fetch`, the provider module, and JWT/session helpers) and in-memory fixtures. Tests MUST NOT make live Khaya AI API calls and MUST NOT read audio files from the `test_audio` directory, which is explicitly excluded from scope.

## Glossary

- **Test_Suite**: The complete set of automated tests introduced by this feature for the Khaya AI backend integration.
- **Test_Runner**: The testing tool that discovers and executes test files and reports pass/fail results.
- **Provider_Test**: A unit test that exercises `providers/khaya.js` in isolation with a mocked `global.fetch`.
- **Route_Test**: An integration test that exercises the Express router in `routes/khaya.js` through HTTP requests using a mocked provider or mocked `fetch`.
- **Khaya_Provider**: The module `providers/khaya.js`, exposing `transcribe(buffer, mimetype, language)`, `getLanguages()`, and `getApiKey()`.
- **Khaya_Router**: The Express router created by `routes/khaya.js`, mounted at `/api/khaya`.
- **Fetch_Mock**: A test double that replaces `global.fetch` to simulate Khaya AI API responses without network calls.
- **Provider_Mock**: A test double that replaces the `Khaya_Provider` methods when testing the `Khaya_Router` in isolation.
- **Session_Token**: A JWT issued by the backend's `/api/session` endpoint and validated by the `requireSession` middleware.
- **Structured_Error**: An error response of the form `{ error: { type, code, message } }`.
- **Transcription_Result**: The normalized object returned by `Khaya_Provider.transcribe`, of the form `{ transcript, words, duration, metadata: { provider, api_version, language } }`.
- **KHAYA_API_KEY**: The environment variable holding the Khaya AI subscription key.
- **KHAYA_ASR_VERSION**: The environment variable selecting the ASR version, defaulting to `v3`.

## Requirements

### Requirement 1: Test Tooling and Execution

**User Story:** As a backend developer, I want a test framework configured with a runnable command, so that I can execute the Khaya AI test suite locally and in CI.

#### Acceptance Criteria

1. THE Test_Suite SHALL use a test framework compatible with Node.js CommonJS modules and the pinned Node.js version (>=24).
2. THE Test_Suite SHALL be executable through a `test` script defined in the root `package.json`.
3. WHEN the `test` script is invoked, THE Test_Runner SHALL execute all Khaya AI test files in a single non-watch run and exit with a zero status code when all tests pass.
4. IF one or more tests fail, THEN THE Test_Runner SHALL exit with a non-zero status code.
5. WHERE new test dependencies are required, THE Test_Suite SHALL declare each dependency with an exact pinned version in the `devDependencies` of the root `package.json`.
6. THE Test_Suite SHALL execute without making network requests to the Khaya AI API.
7. THE Test_Suite SHALL execute without reading files from the `test_audio` directory.

### Requirement 2: Provider Transcription Success

**User Story:** As a backend developer, I want unit tests for successful transcription, so that I can confirm the provider builds requests correctly and normalizes responses.

#### Acceptance Criteria

1. WHEN `Khaya_Provider.transcribe` is called with a valid buffer and language and the Fetch_Mock returns a successful response, THE Provider_Test SHALL assert that the request URL targets the `/asr/{version}/transcribe` path with the language supplied as a URL-encoded query parameter.
2. WHEN `Khaya_Provider.transcribe` is called, THE Provider_Test SHALL assert that the request includes the `Ocp-Apim-Subscription-Key` header set to the value of KHAYA_API_KEY and a `Content-Type` header of `audio/mpeg`.
3. WHEN the Fetch_Mock returns a JSON string response body, THE Provider_Test SHALL assert that the returned Transcription_Result `transcript` field equals that string.
4. WHEN the Fetch_Mock returns a JSON object containing a `transcript` field, THE Provider_Test SHALL assert that the returned Transcription_Result `transcript` field equals the object's `transcript` value.
5. WHEN the Fetch_Mock returns a JSON object containing a `text` field and no `transcript` field, THE Provider_Test SHALL assert that the returned Transcription_Result `transcript` field equals the object's `text` value.
6. WHEN the Fetch_Mock returns a response with `words` and `duration` fields, THE Provider_Test SHALL assert that the returned Transcription_Result includes those `words` and `duration` values.
7. WHEN transcription succeeds, THE Provider_Test SHALL assert that the returned Transcription_Result `metadata` object contains `provider` equal to `khaya-ai`, `api_version` equal to the configured KHAYA_ASR_VERSION, and `language` equal to the requested language.
8. WHERE KHAYA_ASR_VERSION is set to a value other than the default, THE Provider_Test SHALL assert that the request URL path contains the configured version value.

### Requirement 3: Provider Error Handling

**User Story:** As a backend developer, I want unit tests for provider error paths, so that I can confirm each failure produces the correct structured error.

#### Acceptance Criteria

1. IF KHAYA_API_KEY is not configured WHEN `Khaya_Provider.transcribe` is called, THEN THE Provider_Test SHALL assert that the thrown error carries `statusCode` 500, `code` `MISSING_API_KEY`, and `type` `ConfigurationError`.
2. IF the Fetch_Mock returns a response with status 401, THEN THE Provider_Test SHALL assert that `Khaya_Provider.transcribe` throws an error with `statusCode` 401, `code` `INVALID_API_KEY`, and `type` `AuthenticationError`.
3. IF the Fetch_Mock returns a response with status 429, THEN THE Provider_Test SHALL assert that `Khaya_Provider.transcribe` throws an error with `statusCode` 429, `code` `QUOTA_EXCEEDED`, and `type` `RateLimitError`.
4. IF the Fetch_Mock returns a response with a server error status other than 401 or 429, THEN THE Provider_Test SHALL assert that `Khaya_Provider.transcribe` throws an error with `code` `TRANSCRIPTION_FAILED` and `type` `TranscriptionError` and a `statusCode` equal to the response status.
5. IF KHAYA_API_KEY is not configured WHEN `Khaya_Provider.getLanguages` is called, THEN THE Provider_Test SHALL assert that the thrown error carries `statusCode` 500, `code` `MISSING_API_KEY`, and `type` `ConfigurationError`.
6. IF the Fetch_Mock returns a non-successful response WHEN `Khaya_Provider.getLanguages` is called, THEN THE Provider_Test SHALL assert that the thrown error carries `code` `LANGUAGES_FETCH_FAILED` and `type` `ProviderError` with a `statusCode` equal to the response status.

### Requirement 4: Provider Languages Retrieval

**User Story:** As a backend developer, I want unit tests for the languages retrieval, so that I can confirm the provider requests and returns supported languages correctly.

#### Acceptance Criteria

1. WHEN `Khaya_Provider.getLanguages` is called with KHAYA_API_KEY configured and the Fetch_Mock returns a successful response, THE Provider_Test SHALL assert that the request URL targets the `/asr/{version}/languages` path.
2. WHEN `Khaya_Provider.getLanguages` is called, THE Provider_Test SHALL assert that the request includes the `Ocp-Apim-Subscription-Key` header set to the value of KHAYA_API_KEY.
3. WHEN the Fetch_Mock returns a successful languages response body, THE Provider_Test SHALL assert that `Khaya_Provider.getLanguages` returns that response body.

### Requirement 5: Route Authentication

**User Story:** As a backend developer, I want tests for authentication on the transcription route, so that I can confirm protected access is enforced and public access is allowed where intended.

#### Acceptance Criteria

1. WHEN a request is sent to `POST /api/khaya/transcription` without an Authorization header, THE Route_Test SHALL assert that the response status is 401 and the response body is a Structured_Error with `code` `MISSING_TOKEN`.
2. IF a request to `POST /api/khaya/transcription` includes an invalid or expired Session_Token, THEN THE Route_Test SHALL assert that the response status is 401 and the response body is a Structured_Error with `code` `INVALID_TOKEN`.
3. WHEN a request is sent to `POST /api/khaya/transcription` with a valid Session_Token, THE Route_Test SHALL assert that the request passes authentication and reaches input validation or the Provider_Mock.
4. WHEN a request is sent to `GET /api/khaya/languages` without an Authorization header, THE Route_Test SHALL assert that the request is processed without an authentication failure.

### Requirement 6: Route Input Validation

**User Story:** As a backend developer, I want tests for input validation on the transcription route, so that I can confirm missing inputs are rejected with clear errors.

#### Acceptance Criteria

1. IF an authenticated request to `POST /api/khaya/transcription` omits the audio file, THEN THE Route_Test SHALL assert that the response status is 400 and the response body is a Structured_Error with `type` `ValidationError` and `code` `MISSING_INPUT`.
2. IF an authenticated request to `POST /api/khaya/transcription` includes a file but omits the language field, THEN THE Route_Test SHALL assert that the response status is 400 and the response body is a Structured_Error with `type` `ValidationError` and `code` `MISSING_LANGUAGE`.
3. WHEN an authenticated request to `POST /api/khaya/transcription` includes both a file and a language, THE Route_Test SHALL assert that the Provider_Mock `transcribe` method is invoked with the uploaded file buffer, the file mimetype, and the supplied language.

### Requirement 7: Route Success Response

**User Story:** As a backend developer, I want tests for the successful transcription route response, so that I can confirm the provider result is returned unchanged to the client.

#### Acceptance Criteria

1. WHEN an authenticated request to `POST /api/khaya/transcription` provides a valid file and language and the Provider_Mock returns a Transcription_Result, THE Route_Test SHALL assert that the response status is 200 and the response body equals the Transcription_Result.
2. WHEN `GET /api/khaya/languages` is requested and the Provider_Mock returns a languages payload, THE Route_Test SHALL assert that the response status is 200 and the response body equals the languages payload.

### Requirement 8: Route Error Mapping

**User Story:** As a backend developer, I want tests for route error mapping, so that I can confirm provider errors are translated into the correct HTTP status and Structured_Error.

#### Acceptance Criteria

1. IF the Provider_Mock `transcribe` throws an error with `statusCode` 401, `code` `INVALID_API_KEY`, and `type` `AuthenticationError`, THEN THE Route_Test SHALL assert that `POST /api/khaya/transcription` responds with status 401 and a Structured_Error carrying the same `type`, `code`, and `message`.
2. IF the Provider_Mock `transcribe` throws an error with `statusCode` 429, `code` `QUOTA_EXCEEDED`, and `type` `RateLimitError`, THEN THE Route_Test SHALL assert that `POST /api/khaya/transcription` responds with status 429 and a Structured_Error carrying the same `type`, `code`, and `message`.
3. IF the Provider_Mock `transcribe` throws an error with `statusCode` 500, `code` `MISSING_API_KEY`, and `type` `ConfigurationError`, THEN THE Route_Test SHALL assert that `POST /api/khaya/transcription` responds with status 500 and a Structured_Error carrying the same `type`, `code`, and `message`.
4. IF the Provider_Mock `transcribe` throws an error without a `statusCode`, THEN THE Route_Test SHALL assert that `POST /api/khaya/transcription` responds with status 500 and a Structured_Error with `code` `TRANSCRIPTION_FAILED` and `type` `TranscriptionError`.
5. IF the Provider_Mock `getLanguages` throws an error with a `statusCode`, THEN THE Route_Test SHALL assert that `GET /api/khaya/languages` responds with that status and a Structured_Error carrying the error's `type`, `code`, and `message`.
6. IF the Provider_Mock `getLanguages` throws an error without a `statusCode`, THEN THE Route_Test SHALL assert that `GET /api/khaya/languages` responds with status 500 and a Structured_Error with `code` `LANGUAGES_FETCH_FAILED` and `type` `ProviderError`.
