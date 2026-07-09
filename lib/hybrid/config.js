'use strict';

/**
 * @typedef {Object} HybridConfig
 * @property {number} threshold      Confidence_Threshold in [0,1]. Default 0.85.
 * @property {number} gapTolerance   Gap_Tolerance in seconds (>= 0). Default 0.5.
 * @property {number} padding        Padding in seconds (>= 0). Default 0.25.
 * @property {number} maxCallsPerModel  Max Correction_Engine calls per language per transcription (>= 1). Default 3.
 */

const DEFAULTS = {
  threshold: 0.85,
  gapTolerance: 0.5,
  padding: 0.25,
  maxCallsPerModel: 3,
};

/**
 * Parses an integer from an env string and validates it against a range.
 * Returns the default if the value is missing, non-numeric, non-finite, or out of range.
 *
 * @param {string|undefined} raw
 * @param {string} name
 * @param {number} defaultVal
 * @param {(v: number) => boolean} inRange
 * @param {(msg: string) => void} warn
 * @returns {number}
 */
function parseEnvInt(raw, name, defaultVal, inRange, warn) {
  if (raw === undefined || raw === '') {
    return defaultVal;
  }
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || !inRange(parsed)) {
    warn(`Invalid ${name}="${raw}", using default ${defaultVal}`);
    return defaultVal;
  }
  return parsed;
}

/**
 * Parses a float from an env string and validates it against a range.
 * Returns the default if the value is missing, non-numeric, non-finite, or out of range.
 *
 * @param {string|undefined} raw
 * @param {string} name
 * @param {number} defaultVal
 * @param {(v: number) => boolean} inRange
 * @param {(msg: string) => void} warn
 * @returns {number}
 */
function parseEnvFloat(raw, name, defaultVal, inRange, warn) {
  if (raw === undefined || raw === '') {
    return defaultVal;
  }
  const parsed = parseFloat(raw);
  if (!Number.isFinite(parsed) || !inRange(parsed)) {
    warn(`Invalid ${name}="${raw}", using default ${defaultVal}`);
    return defaultVal;
  }
  return parsed;
}

/**
 * Reads and validates hybrid pipeline configuration from an env-like object.
 * Non-numeric or out-of-range values fall back to defaults and emit a warning
 * via the injected logger (defaults to console.warn).
 *
 * @param {Object} [env=process.env]
 * @param {(msg: string) => void} [warn=console.warn]
 * @returns {HybridConfig}
 */
function loadHybridConfig(env = process.env, warn = console.warn) {
  const threshold = parseEnvFloat(
    env.HYBRID_CONFIDENCE_THRESHOLD,
    'HYBRID_CONFIDENCE_THRESHOLD',
    DEFAULTS.threshold,
    (v) => v >= 0 && v <= 1,
    warn
  );

  const gapTolerance = parseEnvFloat(
    env.HYBRID_GAP_TOLERANCE,
    'HYBRID_GAP_TOLERANCE',
    DEFAULTS.gapTolerance,
    (v) => v >= 0,
    warn
  );

  const padding = parseEnvFloat(
    env.HYBRID_PADDING,
    'HYBRID_PADDING',
    DEFAULTS.padding,
    (v) => v >= 0,
    warn
  );

  const maxCallsPerModel = parseEnvInt(
    env.HYBRID_MAX_CALLS_PER_MODEL,
    'HYBRID_MAX_CALLS_PER_MODEL',
    DEFAULTS.maxCallsPerModel,
    (v) => v >= 1,
    warn
  );

  return { threshold, gapTolerance, padding, maxCallsPerModel };
}

module.exports = { loadHybridConfig };
