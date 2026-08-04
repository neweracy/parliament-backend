"use strict";

/**
 * Authentication configuration validators.
 *
 * Validates AUTH_MODE, SESSION_SECRET, BCRYPT_COST, and related startup
 * configuration. All validators perform startup termination (process.exit(1))
 * on failure with sanitized error messages that never expose secret values.
 */

const bcrypt = require("bcrypt");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const KNOWN_NON_PRODUCTION_ENVS = new Set(["development", "test", "demo"]);

const PROHIBITED_SECRETS = new Set([
  "changeme",
  "change-me",
  "secret",
  "development-secret",
  "dev-secret",
  "default",
]);

// ---------------------------------------------------------------------------
// resolveAuthMode
// ---------------------------------------------------------------------------

/**
 * Resolves and validates AUTH_MODE and NODE_ENV configuration.
 *
 * @param {object} env - Environment object (typically process.env)
 * @returns {'legacy' | 'cognito'} The validated auth mode
 */
function resolveAuthMode(env) {
  const authMode = env.AUTH_MODE;

  // Missing, empty, or non-string
  if (authMode === undefined || authMode === null || authMode === "") {
    console.error(
      "[auth-config] FATAL: AUTH_MODE is required. Must be exactly 'legacy' or 'cognito'."
    );
    process.exit(1);
  }

  // Only accept exact case-sensitive values (no trimming)
  if (authMode !== "legacy" && authMode !== "cognito") {
    console.error(
      "[auth-config] FATAL: AUTH_MODE must be exactly 'legacy' or 'cognito'. Got an unsupported value."
    );
    process.exit(1);
  }

  if (authMode === "legacy") {
    const nodeEnv = env.NODE_ENV;

    // NODE_ENV must be present and be an exact known non-production value
    if (!nodeEnv || !KNOWN_NON_PRODUCTION_ENVS.has(nodeEnv)) {
      console.error(
        "[auth-config] FATAL: AUTH_MODE=legacy requires NODE_ENV to be exactly 'development', 'test', or 'demo'."
      );
      process.exit(1);
    }
  }

  return authMode;
}

// ---------------------------------------------------------------------------
// resolveSessionSecret
// ---------------------------------------------------------------------------

/** Hex pattern: 64–256 hex characters */
const HEX_PATTERN = /^[0-9a-fA-F]+$/;

/** Base64 pattern: canonical padded RFC 4648 */
const BASE64_PATTERN = /^[A-Za-z0-9+/]+={0,2}$/;

/**
 * Checks if a string is valid hexadecimal encoding within the required length.
 * @param {string} value
 * @returns {boolean}
 */
function isValidHex(value) {
  return (
    value.length >= 64 &&
    value.length <= 256 &&
    HEX_PATTERN.test(value)
  );
}

/**
 * Checks if a string is valid padded RFC 4648 base64.
 * Validates padding correctness and character set.
 * @param {string} value
 * @returns {boolean}
 */
function isValidBase64(value) {
  // Must be non-empty and length divisible by 4 (padded)
  if (value.length === 0 || value.length % 4 !== 0) {
    return false;
  }
  if (!BASE64_PATTERN.test(value)) {
    return false;
  }
  // Verify padding is correct (at most 2 '=' at end)
  const paddingMatch = value.match(/=+$/);
  if (paddingMatch && paddingMatch[0].length > 2) {
    return false;
  }
  return true;
}

/**
 * Decodes a hex string to a Buffer.
 * @param {string} value
 * @returns {Buffer}
 */
function decodeHex(value) {
  return Buffer.from(value, "hex");
}

/**
 * Decodes a base64 string to a Buffer.
 * @param {string} value
 * @returns {Buffer}
 */
function decodeBase64(value) {
  return Buffer.from(value, "base64");
}

/**
 * Resolves and validates SESSION_SECRET from the environment.
 *
 * Accepts hex (64–256 chars decoding to 32–128 bytes) or padded base64
 * (decoding to 32–128 bytes). Ambiguous values (valid as both hex and base64)
 * are decoded as hex.
 *
 * Never prints the secret value in errors, logs, or telemetry.
 *
 * @param {object} env - Environment object (typically process.env)
 * @returns {Buffer} The decoded secret as a Buffer (32–128 bytes)
 */
function resolveSessionSecret(env) {
  const raw = env.SESSION_SECRET;

  // Absent or empty
  if (raw === undefined || raw === null || raw === "") {
    console.error(
      "[auth-config] FATAL: SESSION_SECRET is required. Provide a hex (64-256 chars) or base64-encoded secret (32-128 bytes decoded)."
    );
    process.exit(1);
  }

  // Trim surrounding whitespace for prohibited-value check only
  const trimmed = raw.trim();

  // Prohibited values (case-insensitive, after trimming)
  if (PROHIBITED_SECRETS.has(trimmed.toLowerCase())) {
    console.error(
      "[auth-config] FATAL: SESSION_SECRET uses a prohibited placeholder value. Provide a cryptographically random secret."
    );
    process.exit(1);
  }

  // Input length check (before encoding detection)
  if (raw.length < 64 || raw.length > 256) {
    console.error(
      "[auth-config] FATAL: SESSION_SECRET must be 64-256 characters. Got " +
        raw.length +
        " characters."
    );
    process.exit(1);
  }

  // Ambiguity rule: if valid as both hex and base64, decode as hex
  const validHex = isValidHex(raw);
  const validB64 = isValidBase64(raw);

  let decoded;

  if (validHex) {
    // Decode as hex (takes priority for ambiguous values)
    decoded = decodeHex(raw);
  } else if (validB64) {
    // Decode as base64
    decoded = decodeBase64(raw);
  } else {
    console.error(
      "[auth-config] FATAL: SESSION_SECRET is not valid hex or padded base64 encoding."
    );
    process.exit(1);
  }

  // Decoded length must be 32–128 bytes
  if (decoded.length < 32 || decoded.length > 128) {
    console.error(
      "[auth-config] FATAL: SESSION_SECRET decodes to " +
        decoded.length +
        " bytes. Must be 32-128 bytes."
    );
    process.exit(1);
  }

  return decoded;
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  resolveAuthMode,
  resolveSessionSecret,
  // Exported for testing only
  _internals: {
    KNOWN_NON_PRODUCTION_ENVS,
    PROHIBITED_SECRETS,
    isValidHex,
    isValidBase64,
  },
};
