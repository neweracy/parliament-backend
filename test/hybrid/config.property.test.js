'use strict';

// Feature: hybrid-confidence-transcription, Property 2:
// Threshold validation clamps to a valid configuration

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { loadHybridConfig } = require('../../lib/hybrid/config');

/**
 * Validates: Requirements 3.2, 3.3, 10.1, 10.2, 10.3
 */
describe('Property 2: Threshold validation clamps to a valid configuration', () => {
  it('loadHybridConfig always returns threshold ∈ [0,1], gapTolerance >= 0, padding >= 0', () => {
    fc.assert(
      fc.property(
        fc.record({
          HYBRID_CONFIDENCE_THRESHOLD: fc.oneof(
            fc.constant(undefined),
            fc.string(),
            fc.double().map(String),
            fc.integer({ min: -100, max: 100 }).map(String),
            fc.constant('NaN'),
            fc.constant('Infinity'),
            fc.constant('-Infinity'),
            fc.constant('')
          ),
          HYBRID_GAP_TOLERANCE: fc.oneof(
            fc.constant(undefined),
            fc.string(),
            fc.double().map(String),
            fc.integer({ min: -100, max: 100 }).map(String),
            fc.constant('NaN'),
            fc.constant('Infinity'),
            fc.constant('-Infinity'),
            fc.constant('')
          ),
          HYBRID_PADDING: fc.oneof(
            fc.constant(undefined),
            fc.string(),
            fc.double().map(String),
            fc.integer({ min: -100, max: 100 }).map(String),
            fc.constant('NaN'),
            fc.constant('Infinity'),
            fc.constant('-Infinity'),
            fc.constant('')
          ),
        }),
        (env) => {
          const warnings = [];
          const warn = (msg) => warnings.push(msg);
          const config = loadHybridConfig(env, warn);

          // threshold must be in [0, 1]
          assert.ok(config.threshold >= 0 && config.threshold <= 1,
            `threshold ${config.threshold} not in [0,1]`);

          // gapTolerance must be >= 0
          assert.ok(config.gapTolerance >= 0,
            `gapTolerance ${config.gapTolerance} is negative`);

          // padding must be >= 0
          assert.ok(config.padding >= 0,
            `padding ${config.padding} is negative`);

          // When input is invalid (non-numeric or out-of-range), defaults should be applied
          // Verify defaults: threshold=0.85, gapTolerance=0.5, padding=0.25
          const thresholdRaw = env.HYBRID_CONFIDENCE_THRESHOLD;
          if (thresholdRaw !== undefined && thresholdRaw !== '') {
            const parsed = parseFloat(thresholdRaw);
            if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) {
              assert.equal(config.threshold, 0.85,
                'threshold should default to 0.85 on invalid input');
            }
          }

          const gapRaw = env.HYBRID_GAP_TOLERANCE;
          if (gapRaw !== undefined && gapRaw !== '') {
            const parsed = parseFloat(gapRaw);
            if (!Number.isFinite(parsed) || parsed < 0) {
              assert.equal(config.gapTolerance, 0.5,
                'gapTolerance should default to 0.5 on invalid input');
            }
          }

          const paddingRaw = env.HYBRID_PADDING;
          if (paddingRaw !== undefined && paddingRaw !== '') {
            const parsed = parseFloat(paddingRaw);
            if (!Number.isFinite(parsed) || parsed < 0) {
              assert.equal(config.padding, 0.25,
                'padding should default to 0.25 on invalid input');
            }
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});
