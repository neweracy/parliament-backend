'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { loadHybridConfig } = require('../../lib/hybrid/config');

describe('lib/hybrid/config - loadHybridConfig', () => {
  describe('defaults when env vars are missing', () => {
    it('returns default threshold 0.85 when HYBRID_CONFIDENCE_THRESHOLD is absent', () => {
      const config = loadHybridConfig({});
      assert.equal(config.threshold, 0.85);
    });

    it('returns default gapTolerance 0.5 when HYBRID_GAP_TOLERANCE is absent', () => {
      const config = loadHybridConfig({});
      assert.equal(config.gapTolerance, 0.5);
    });

    it('returns default padding 0.25 when HYBRID_PADDING is absent', () => {
      const config = loadHybridConfig({});
      assert.equal(config.padding, 0.25);
    });

    it('returns default maxCallsPerModel 3 when HYBRID_MAX_CALLS_PER_MODEL is absent', () => {
      const config = loadHybridConfig({});
      assert.equal(config.maxCallsPerModel, 3);
    });

    it('parses a valid maxCallsPerModel override', () => {
      const config = loadHybridConfig({ HYBRID_MAX_CALLS_PER_MODEL: '5' });
      assert.equal(config.maxCallsPerModel, 5);
    });

    it('warns and defaults maxCallsPerModel on a non-integer or < 1 value', () => {
      const warnings = [];
      const zero = loadHybridConfig({ HYBRID_MAX_CALLS_PER_MODEL: '0' }, (m) => warnings.push(m));
      assert.equal(zero.maxCallsPerModel, 3);
      const frac = loadHybridConfig({ HYBRID_MAX_CALLS_PER_MODEL: '2.5' }, (m) => warnings.push(m));
      assert.equal(frac.maxCallsPerModel, 3);
      assert.equal(warnings.length, 2);
      assert.ok(warnings.every((w) => w.includes('HYBRID_MAX_CALLS_PER_MODEL')));
    });
  });

  describe('valid overrides', () => {
    it('parses a valid threshold within [0,1]', () => {
      const config = loadHybridConfig({ HYBRID_CONFIDENCE_THRESHOLD: '0.7' });
      assert.equal(config.threshold, 0.7);
    });

    it('accepts threshold at boundary 0', () => {
      const config = loadHybridConfig({ HYBRID_CONFIDENCE_THRESHOLD: '0' });
      assert.equal(config.threshold, 0);
    });

    it('accepts threshold at boundary 1', () => {
      const config = loadHybridConfig({ HYBRID_CONFIDENCE_THRESHOLD: '1' });
      assert.equal(config.threshold, 1);
    });

    it('parses a valid gapTolerance', () => {
      const config = loadHybridConfig({ HYBRID_GAP_TOLERANCE: '1.0' });
      assert.equal(config.gapTolerance, 1.0);
    });

    it('accepts gapTolerance at boundary 0', () => {
      const config = loadHybridConfig({ HYBRID_GAP_TOLERANCE: '0' });
      assert.equal(config.gapTolerance, 0);
    });

    it('parses a valid padding', () => {
      const config = loadHybridConfig({ HYBRID_PADDING: '0.5' });
      assert.equal(config.padding, 0.5);
    });

    it('accepts padding at boundary 0', () => {
      const config = loadHybridConfig({ HYBRID_PADDING: '0' });
      assert.equal(config.padding, 0);
    });

    it('parses all three overrides together', () => {
      const config = loadHybridConfig({
        HYBRID_CONFIDENCE_THRESHOLD: '0.9',
        HYBRID_GAP_TOLERANCE: '0.3',
        HYBRID_PADDING: '0.1',
      });
      assert.equal(config.threshold, 0.9);
      assert.equal(config.gapTolerance, 0.3);
      assert.equal(config.padding, 0.1);
    });
  });

  describe('invalid values trigger warning and use defaults', () => {
    it('warns and defaults threshold on non-numeric string', () => {
      const warnings = [];
      const config = loadHybridConfig(
        { HYBRID_CONFIDENCE_THRESHOLD: 'abc' },
        (msg) => warnings.push(msg)
      );
      assert.equal(config.threshold, 0.85);
      assert.equal(warnings.length, 1);
      assert.ok(warnings[0].includes('HYBRID_CONFIDENCE_THRESHOLD'));
    });

    it('warns and defaults threshold when value > 1', () => {
      const warnings = [];
      const config = loadHybridConfig(
        { HYBRID_CONFIDENCE_THRESHOLD: '1.5' },
        (msg) => warnings.push(msg)
      );
      assert.equal(config.threshold, 0.85);
      assert.equal(warnings.length, 1);
    });

    it('warns and defaults threshold when value < 0', () => {
      const warnings = [];
      const config = loadHybridConfig(
        { HYBRID_CONFIDENCE_THRESHOLD: '-0.1' },
        (msg) => warnings.push(msg)
      );
      assert.equal(config.threshold, 0.85);
      assert.equal(warnings.length, 1);
    });

    it('warns and defaults gapTolerance on negative value', () => {
      const warnings = [];
      const config = loadHybridConfig(
        { HYBRID_GAP_TOLERANCE: '-1' },
        (msg) => warnings.push(msg)
      );
      assert.equal(config.gapTolerance, 0.5);
      assert.equal(warnings.length, 1);
      assert.ok(warnings[0].includes('HYBRID_GAP_TOLERANCE'));
    });

    it('warns and defaults padding on Infinity', () => {
      const warnings = [];
      const config = loadHybridConfig(
        { HYBRID_PADDING: 'Infinity' },
        (msg) => warnings.push(msg)
      );
      assert.equal(config.padding, 0.25);
      assert.equal(warnings.length, 1);
      assert.ok(warnings[0].includes('HYBRID_PADDING'));
    });

    it('warns and defaults gapTolerance on NaN', () => {
      const warnings = [];
      const config = loadHybridConfig(
        { HYBRID_GAP_TOLERANCE: 'NaN' },
        (msg) => warnings.push(msg)
      );
      assert.equal(config.gapTolerance, 0.5);
      assert.equal(warnings.length, 1);
    });

    it('does not warn when value is missing (uses default silently)', () => {
      const warnings = [];
      loadHybridConfig({}, (msg) => warnings.push(msg));
      assert.equal(warnings.length, 0);
    });

    it('does not warn when value is empty string (uses default silently)', () => {
      const warnings = [];
      loadHybridConfig(
        { HYBRID_CONFIDENCE_THRESHOLD: '' },
        (msg) => warnings.push(msg)
      );
      assert.equal(warnings.length, 0);
    });
  });
});
