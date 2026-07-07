/**
 * Helper to reload `providers/khaya.js` with specific environment variables.
 *
 * `KHAYA_ASR_VERSION` is captured once at module load time, so testing a
 * non-default version requires clearing require.cache and re-requiring the
 * module. `KHAYA_API_KEY` is read on each call via `getApiKey()`, so it only
 * needs to be set on `process.env` before the call — no reload required.
 *
 * This helper handles both: it saves prior env values, clears the module from
 * cache, sets the requested vars, re-requires the provider, and returns a
 * `restore()` function that undoes everything.
 *
 * @module test/helpers/providerEnv
 */

"use strict";

const PROVIDER_PATH = require.resolve("../../providers/khaya");

/**
 * Clears the provider from require.cache, sets the specified env vars,
 * re-requires the module, and returns the fresh module plus a restore function.
 *
 * @param {{ apiKey?: string|undefined, asrVersion?: string|undefined }} env
 * @returns {{ khaya: object, restore: Function }}
 */
function loadProviderWithEnv({ apiKey, asrVersion } = {}) {
  // Save prior env values (may be undefined if not set)
  const priorApiKey = process.env.KHAYA_API_KEY;
  const priorAsrVersion = process.env.KHAYA_ASR_VERSION;

  // Clear the cached module so re-require picks up new env
  delete require.cache[PROVIDER_PATH];

  // Set or delete env vars based on what was passed
  if (apiKey !== undefined) {
    process.env.KHAYA_API_KEY = apiKey;
  } else {
    delete process.env.KHAYA_API_KEY;
  }

  if (asrVersion !== undefined) {
    process.env.KHAYA_ASR_VERSION = asrVersion;
  } else {
    delete process.env.KHAYA_ASR_VERSION;
  }

  // Re-require the provider — ASR_VERSION is now rebound to the new env value
  const khaya = require(PROVIDER_PATH);

  /**
   * Restores process.env keys to their prior values and clears the module
   * from require.cache to prevent cross-test leakage.
   */
  function restore() {
    // Restore KHAYA_API_KEY
    if (priorApiKey !== undefined) {
      process.env.KHAYA_API_KEY = priorApiKey;
    } else {
      delete process.env.KHAYA_API_KEY;
    }

    // Restore KHAYA_ASR_VERSION
    if (priorAsrVersion !== undefined) {
      process.env.KHAYA_ASR_VERSION = priorAsrVersion;
    } else {
      delete process.env.KHAYA_ASR_VERSION;
    }

    // Clear the cache so the next require gets a clean module
    delete require.cache[PROVIDER_PATH];
  }

  return { khaya, restore };
}

module.exports = { loadProviderWithEnv };
