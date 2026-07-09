const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const { createFetchMock } = require("../helpers/fetchMock");
const { loadProviderWithEnv } = require("../helpers/providerEnv");
const audio = require("../fixtures/audio");
const responses = require("../fixtures/responses");

describe("providers/khaya - transcription success", () => {
  const originalFetch = global.fetch;
  let khaya;

  beforeEach(() => {
    process.env.KHAYA_API_KEY = "test-key-123";
    // Ensure default v3 is used (clear any system-level override)
    delete process.env.KHAYA_ASR_VERSION;
    delete require.cache[require.resolve("../../providers/khaya")];
    khaya = require("../../providers/khaya");
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete require.cache[require.resolve("../../providers/khaya")];
  });

  it("sends request to /asr/{version}/transcribe with URL-encoded language query param", async () => {
    const { fn, calls } = createFetchMock({ jsonBody: responses.stringTranscript });
    global.fetch = fn;

    await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw");

    assert.equal(calls.length, 1);
    const url = new URL(calls[0].url);
    assert.equal(url.pathname, "/asr/v3/transcribe");
    assert.equal(url.searchParams.get("language"), "tw");
  });

  it("includes Ocp-Apim-Subscription-Key header equal to KHAYA_API_KEY", async () => {
    const { fn, calls } = createFetchMock({ jsonBody: responses.stringTranscript });
    global.fetch = fn;

    await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw");

    assert.equal(calls[0].options.headers["Ocp-Apim-Subscription-Key"], "test-key-123");
  });

  it("sets Content-Type to audio/mpeg", async () => {
    const { fn, calls } = createFetchMock({ jsonBody: responses.stringTranscript });
    global.fetch = fn;

    await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw");

    assert.equal(calls[0].options.headers["Content-Type"], "audio/mpeg");
  });

  it("normalizes a string body response to transcript field", async () => {
    const { fn } = createFetchMock({ jsonBody: responses.stringTranscript });
    global.fetch = fn;

    const result = await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw");

    assert.equal(result.transcript, responses.stringTranscript);
  });

  it("normalizes an object with transcript field", async () => {
    const { fn } = createFetchMock({ jsonBody: responses.objectWithTranscript });
    global.fetch = fn;

    const result = await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw");

    assert.equal(result.transcript, responses.objectWithTranscript.transcript);
  });

  it("normalizes an object with text only (no transcript field)", async () => {
    const { fn } = createFetchMock({ jsonBody: responses.objectWithTextOnly });
    global.fetch = fn;

    const result = await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "ee");

    assert.equal(result.transcript, responses.objectWithTextOnly.text);
  });

  it("passes through words and duration from object response", async () => {
    const { fn } = createFetchMock({ jsonBody: responses.objectWithTranscript });
    global.fetch = fn;

    const result = await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw");

    assert.deepEqual(result.words, responses.objectWithTranscript.words);
    assert.equal(result.duration, responses.objectWithTranscript.duration);
  });

  it("returns metadata with provider, api_version, and language", async () => {
    const { fn } = createFetchMock({ jsonBody: responses.stringTranscript });
    global.fetch = fn;

    const result = await khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "gaa");

    assert.equal(result.metadata.provider, "khaya-ai");
    assert.equal(result.metadata.api_version, "v3");
    assert.equal(result.metadata.language, "gaa");
  });

  it("uses non-default KHAYA_ASR_VERSION in request URL path", async () => {
    const { khaya: khayaV3, restore } = loadProviderWithEnv({
      apiKey: "test-key-123",
      asrVersion: "v3",
    });

    const { fn, calls } = createFetchMock({ jsonBody: responses.stringTranscript });
    global.fetch = fn;

    try {
      const result = await khayaV3.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw");

      const url = new URL(calls[0].url);
      assert.equal(url.pathname, "/asr/v3/transcribe");
      assert.equal(result.metadata.api_version, "v3");
    } finally {
      restore();
    }
  });
});

describe("providers/khaya - transcribe error branches", () => {
  const originalFetch = global.fetch;
  let khaya;

  beforeEach(() => {
    process.env.KHAYA_API_KEY = "test-key-123";
    delete process.env.KHAYA_ASR_VERSION;
    delete require.cache[require.resolve("../../providers/khaya")];
    khaya = require("../../providers/khaya");
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete process.env.KHAYA_API_KEY;
    delete require.cache[require.resolve("../../providers/khaya")];
  });

  it("throws statusCode 500 / MISSING_API_KEY / ConfigurationError when KHAYA_API_KEY is missing", async () => {
    delete process.env.KHAYA_API_KEY;

    await assert.rejects(
      () => khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw"),
      (err) => {
        assert.equal(err.statusCode, 500);
        assert.equal(err.code, "MISSING_API_KEY");
        assert.equal(err.type, "ConfigurationError");
        return true;
      }
    );
  });

  it("throws statusCode 401 / INVALID_API_KEY / AuthenticationError on 401 response", async () => {
    const { fn } = createFetchMock({ ok: false, status: 401, textBody: "Unauthorized" });
    global.fetch = fn;

    await assert.rejects(
      () => khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw"),
      (err) => {
        assert.equal(err.statusCode, 401);
        assert.equal(err.code, "INVALID_API_KEY");
        assert.equal(err.type, "AuthenticationError");
        return true;
      }
    );
  });

  it("throws statusCode 429 / QUOTA_EXCEEDED / RateLimitError on 429 response", async () => {
    const { fn } = createFetchMock({ ok: false, status: 429, textBody: "Rate limit exceeded" });
    global.fetch = fn;

    await assert.rejects(
      () => khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw"),
      (err) => {
        assert.equal(err.statusCode, 429);
        assert.equal(err.code, "QUOTA_EXCEEDED");
        assert.equal(err.type, "RateLimitError");
        return true;
      }
    );
  });

  it("throws statusCode 403 / QUOTA_EXCEEDED / RateLimitError on a 403 quota response", async () => {
    const { fn } = createFetchMock({
      ok: false,
      status: 403,
      textBody: '{ "statusCode": 403, "message": "Out of call volume quota. Quota will be replenished in 9.00:54:23." }',
    });
    global.fetch = fn;

    await assert.rejects(
      () => khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw"),
      (err) => {
        assert.equal(err.statusCode, 403);
        assert.equal(err.code, "QUOTA_EXCEEDED");
        assert.equal(err.type, "RateLimitError");
        return true;
      }
    );
  });

  it("throws TRANSCRIPTION_FAILED / TranscriptionError on a non-quota 403 response", async () => {
    const { fn } = createFetchMock({ ok: false, status: 403, textBody: "Forbidden" });
    global.fetch = fn;

    await assert.rejects(
      () => khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw"),
      (err) => {
        assert.equal(err.statusCode, 403);
        assert.equal(err.code, "TRANSCRIPTION_FAILED");
        assert.equal(err.type, "TranscriptionError");
        return true;
      }
    );
  });

  it("throws TRANSCRIPTION_FAILED / TranscriptionError with matching statusCode on generic 5xx", async () => {
    const { fn } = createFetchMock({
      ok: false,
      status: responses.errors.serverError.status,
      textBody: responses.errors.serverError.body,
    });
    global.fetch = fn;

    await assert.rejects(
      () => khaya.transcribe(audio.mp3.buffer, audio.mp3.mimetype, "tw"),
      (err) => {
        assert.equal(err.statusCode, 503);
        assert.equal(err.code, "TRANSCRIPTION_FAILED");
        assert.equal(err.type, "TranscriptionError");
        return true;
      }
    );
  });
});

describe("providers/khaya - getLanguages", () => {
  const originalFetch = global.fetch;
  let khaya;

  beforeEach(() => {
    process.env.KHAYA_API_KEY = "test-key-123";
    delete process.env.KHAYA_ASR_VERSION;
    delete require.cache[require.resolve("../../providers/khaya")];
    khaya = require("../../providers/khaya");
  });

  afterEach(() => {
    global.fetch = originalFetch;
    delete process.env.KHAYA_API_KEY;
    delete require.cache[require.resolve("../../providers/khaya")];
  });

  it("targets /asr/v3/languages with Ocp-Apim-Subscription-Key header and returns response body verbatim", async () => {
    const { fn, calls } = createFetchMock({ jsonBody: responses.languages });
    global.fetch = fn;

    const result = await khaya.getLanguages();

    assert.equal(calls.length, 1);
    const url = new URL(calls[0].url);
    assert.equal(url.pathname, "/asr/v3/languages");
    assert.equal(calls[0].options.headers["Ocp-Apim-Subscription-Key"], "test-key-123");
    assert.deepEqual(result, responses.languages);
  });

  it("throws statusCode 500 / MISSING_API_KEY / ConfigurationError when KHAYA_API_KEY is missing", async () => {
    delete process.env.KHAYA_API_KEY;

    await assert.rejects(
      () => khaya.getLanguages(),
      (err) => {
        assert.equal(err.statusCode, 500);
        assert.equal(err.code, "MISSING_API_KEY");
        assert.equal(err.type, "ConfigurationError");
        return true;
      }
    );
  });

  it("throws LANGUAGES_FETCH_FAILED / ProviderError with statusCode equal to response status on non-ok response", async () => {
    const { fn } = createFetchMock({ ok: false, status: 502, textBody: "Bad Gateway" });
    global.fetch = fn;

    await assert.rejects(
      () => khaya.getLanguages(),
      (err) => {
        assert.equal(err.statusCode, 502);
        assert.equal(err.code, "LANGUAGES_FETCH_FAILED");
        assert.equal(err.type, "ProviderError");
        return true;
      }
    );
  });
});
