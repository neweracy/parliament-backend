"use strict";

/**
 * Representative Khaya AI API response fixtures for testing.
 * Covers success payloads (string, object variants) and error responses.
 */

/** A plain string transcript — simulates Khaya v1 response where json() returns a string */
const stringTranscript = "Hello world this is a test transcription";

/** Object body with transcript, words array, and duration — simulates Khaya v3 response */
const objectWithTranscript = {
  transcript: "Ɛte sɛn? Me din de Kofi.",
  words: [
    { word: "Ɛte", start: 0.0, end: 0.3 },
    { word: "sɛn?", start: 0.3, end: 0.7 },
    { word: "Me", start: 0.8, end: 1.0 },
    { word: "din", start: 1.0, end: 1.3 },
    { word: "de", start: 1.3, end: 1.5 },
    { word: "Kofi.", start: 1.5, end: 1.9 },
  ],
  duration: 5.2,
};

/** Object body with `text` field but no `transcript` — alternate response shape */
const objectWithTextOnly = {
  text: "Wo ho te sɛn?",
};

/** Sample languages list as returned by GET /asr/v3/languages */
const languages = [
  { code: "tw", name: "Twi" },
  { code: "ee", name: "Ewe" },
  { code: "gaa", name: "Ga" },
  { code: "dag", name: "Dagbani" },
  { code: "gur", name: "Frafra" },
];

/** Error response bodies and status codes for provider error-path testing */
const errors = {
  unauthorized: {
    status: 401,
    body: "Invalid subscription key or wrong API endpoint.",
  },
  rateLimited: {
    status: 429,
    body: "Rate limit is exceeded. Try again later.",
  },
  serverError: {
    status: 503,
    body: "Service temporarily unavailable",
  },
};

module.exports = {
  stringTranscript,
  objectWithTranscript,
  objectWithTextOnly,
  languages,
  errors,
};
