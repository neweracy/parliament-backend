'use strict';

/**
 * @typedef {import('./segment-grouper').SegmentBundle} SegmentBundle
 */

/**
 * @typedef {Object} BundleBatch
 * @property {SegmentBundle[]} bundles   The contiguous bundles in this batch (original order).
 * @property {Array<{start: number, end: number}>} ranges  The padded ranges for slicing/concat.
 */

/**
 * Groups an ordered list of Segment_Bundles into at most `maxBatches`
 * contiguous batches, balanced by total audio duration. Each batch is sent to
 * the Correction_Engine once per candidate language, so the number of batches
 * caps the number of calls per model per transcription.
 *
 * Batching preserves bundle order (batches never interleave in time), which
 * keeps corrected text positioned near where it occurs in the audio. Within a
 * batch, the combined correction transcript is later split back across that
 * batch's bundles by duration, so drift stays local to a short time window
 * instead of spanning the whole file.
 *
 * @param {SegmentBundle[]} bundles   Ordered by ascending start.
 * @param {number} maxBatches         Max batches (>= 1). Equals max calls per model.
 * @returns {BundleBatch[]}
 */
function batchBundles(bundles, maxBatches) {
  if (!Array.isArray(bundles) || bundles.length === 0) {
    return [];
  }

  const cap = Math.max(1, Math.floor(maxBatches));

  // Fewer bundles than the cap: one bundle per batch (exact alignment).
  if (bundles.length <= cap) {
    return bundles.map((b) => ({
      bundles: [b],
      ranges: [{ start: b.paddedStart, end: b.paddedEnd }],
    }));
  }

  // Balance batches by cumulative audio duration so each Correction_Engine
  // request handles roughly the same amount of audio.
  const durations = bundles.map((b) => b.paddedEnd - b.paddedStart);
  const totalDuration = durations.reduce((sum, d) => sum + d, 0);
  const targetPerBatch = totalDuration / cap;

  const batches = [];
  let current = [];
  let currentDuration = 0;

  for (let i = 0; i < bundles.length; i++) {
    current.push(bundles[i]);
    currentDuration += durations[i];

    const batchesRemaining = cap - batches.length;
    const bundlesRemaining = bundles.length - (i + 1);

    // Close the current batch when it has met its duration target, but only if
    // there are still enough remaining bundles to fill every remaining batch.
    const reachedTarget = currentDuration >= targetPerBatch;
    const mustReserveForRemaining = bundlesRemaining <= batchesRemaining - 1;

    if ((reachedTarget || mustReserveForRemaining) && batches.length < cap - 1) {
      batches.push(current);
      current = [];
      currentDuration = 0;
    }
  }

  if (current.length > 0) {
    batches.push(current);
  }

  return batches.map((group) => ({
    bundles: group,
    ranges: group.map((b) => ({ start: b.paddedStart, end: b.paddedEnd })),
  }));
}

module.exports = { batchBundles };
