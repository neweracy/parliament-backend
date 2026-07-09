'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { batchBundles } = require('../../lib/hybrid/batcher');

/** Builds a minimal SegmentBundle with the given padded window. */
function bundle(paddedStart, paddedEnd, idx) {
  return {
    originalStart: paddedStart,
    originalEnd: paddedEnd,
    paddedStart,
    paddedEnd,
    wordIndexRange: [idx, idx],
    words: [],
  };
}

describe('lib/hybrid/batcher - batchBundles', () => {
  it('returns an empty array for no bundles', () => {
    assert.deepEqual(batchBundles([], 3), []);
  });

  it('produces one bundle per batch when count <= max (exact alignment)', () => {
    const bundles = [bundle(0, 1, 0), bundle(2, 3, 1), bundle(4, 5, 2)];
    const batches = batchBundles(bundles, 5);
    assert.equal(batches.length, 3);
    for (const b of batches) {
      assert.equal(b.bundles.length, 1);
      assert.equal(b.ranges.length, 1);
    }
  });

  it('caps the number of batches at maxCallsPerModel', () => {
    const bundles = Array.from({ length: 20 }, (_, i) => bundle(i, i + 0.5, i));
    const batches = batchBundles(bundles, 3);
    assert.ok(batches.length <= 3, `expected <= 3 batches, got ${batches.length}`);
    assert.equal(batches.length, 3);
  });

  it('preserves order and covers every bundle exactly once', () => {
    const bundles = Array.from({ length: 11 }, (_, i) => bundle(i, i + 0.5, i));
    const batches = batchBundles(bundles, 3);

    const flat = batches.flatMap((b) => b.bundles);
    assert.equal(flat.length, bundles.length);
    // Order preserved (indices ascending across the flattened batches).
    for (let i = 0; i < flat.length; i++) {
      assert.equal(flat[i].wordIndexRange[0], i);
    }
  });

  it('exposes ranges matching each bundle padded window', () => {
    const bundles = [bundle(0, 1, 0), bundle(2, 3.5, 1)];
    const batches = batchBundles(bundles, 1);
    assert.equal(batches.length, 1);
    assert.deepEqual(batches[0].ranges, [
      { start: 0, end: 1 },
      { start: 2, end: 3.5 },
    ]);
  });

  it('balances batches by duration', () => {
    // 6 bundles, 3 batches: durations should split roughly evenly.
    const bundles = [
      bundle(0, 1, 0),
      bundle(1, 2, 1),
      bundle(2, 3, 2),
      bundle(3, 4, 3),
      bundle(4, 5, 4),
      bundle(5, 6, 5),
    ];
    const batches = batchBundles(bundles, 3);
    assert.equal(batches.length, 3);
    for (const b of batches) {
      assert.equal(b.bundles.length, 2);
    }
  });
});
