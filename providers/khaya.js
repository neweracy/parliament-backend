/**
 * Khaya AI (GhanaNLP) ASR v3 Provider
 *
 * Handles transcription via Khaya AI's Automatic Speech Recognition API.
 * Supports African languages: Twi, Ewe, Ga, Dagbani, Frafra, and more.
 *
 * API Docs: https://translation.ghananlp.org
 * Base URL: https://translation-api.ghananlp.org
 * Auth: Ocp-Apim-Subscription-Key header
 */

const KHAYA_API_BASE = "https://translation-api.ghananlp.org";

/**
 * Returns the configured API key or null if not set.
 * @returns {string|null}
 */
function getApiKey() {
  return process.env.KHAYA_API_KEY || null;
}

/** ASR version to use (v1 = fast single-sentence, v3 = accurate long-form) */
const ASR_VERSION = process.env.KHAYA_ASR_VERSION || "v3";

/**
 * Transcribes audio using Khaya AI ASR.
 *
 * The Khaya API expects the raw audio bytes as the request body with a
 * Content-Type of audio/mpeg, and the language as a query parameter.
 *
 * @param {Buffer} buffer - Audio file buffer
 * @param {string} mimetype - Audio MIME type
 * @param {string} language - Language code (e.g., "tw", "ee", "gaa")
 * @returns {Promise<Object>} - Standardized transcription response
 */
async function transcribe(buffer, mimetype, language) {
  const apiKey = getApiKey();
  if (!apiKey) {
    throw Object.assign(
      new Error("KHAYA_API_KEY is not configured in environment variables"),
      { statusCode: 500, code: "MISSING_API_KEY", type: "ConfigurationError" }
    );
  }

  const url = `${KHAYA_API_BASE}/asr/${ASR_VERSION}/transcribe?language=${encodeURIComponent(language)}`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Ocp-Apim-Subscription-Key": apiKey,
      "Content-Type": "audio/mpeg",
    },
    body: buffer,
  });

  if (!response.ok) {
    const errorText = await response.text();
    const status = response.status;
    const error = new Error(
      status === 401
        ? "Invalid Khaya API key. Check Ocp-Apim-Subscription-Key."
        : status === 429
        ? "Monthly quota exceeded. Upgrade your Khaya AI plan."
        : `Khaya AI returned status ${status}: ${errorText}`
    );
    error.statusCode = status;
    error.code = status === 401 ? "INVALID_API_KEY" : status === 429 ? "QUOTA_EXCEEDED" : "TRANSCRIPTION_FAILED";
    error.type = status === 401 ? "AuthenticationError" : status === 429 ? "RateLimitError" : "TranscriptionError";
    throw error;
  }

  // Khaya returns the transcript as a JSON string (or object for v3)
  const data = await response.json();
  const transcript = typeof data === "string" ? data : data.transcript || data.text || "";

  return {
    transcript,
    words: (typeof data === "object" && data.words) || [],
    duration: (typeof data === "object" && data.duration) || undefined,
    metadata: {
      provider: "khaya-ai",
      api_version: ASR_VERSION,
      language,
    },
  };
}

/**
 * Fetches the list of supported languages from Khaya AI ASR v3.
 * @returns {Promise<Object>} - Languages response from API
 */
async function getLanguages() {
  const apiKey = getApiKey();
  if (!apiKey) {
    throw Object.assign(
      new Error("KHAYA_API_KEY is not configured"),
      { statusCode: 500, code: "MISSING_API_KEY", type: "ConfigurationError" }
    );
  }

  const response = await fetch(`${KHAYA_API_BASE}/asr/${ASR_VERSION}/languages`, {
    headers: { "Ocp-Apim-Subscription-Key": apiKey },
  });

  if (!response.ok) {
    const error = new Error(`Failed to fetch languages: ${response.status}`);
    error.statusCode = response.status;
    error.code = "LANGUAGES_FETCH_FAILED";
    error.type = "ProviderError";
    throw error;
  }

  return response.json();
}

module.exports = { transcribe, getLanguages, getApiKey };
