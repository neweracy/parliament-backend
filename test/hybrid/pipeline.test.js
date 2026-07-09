'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { runHybridPipeline } = require('../../lib/hybrid/pipeline');

const CONFIG = { threshold: 0.85, gapTolerance: 0.5, padding: 0.25, maxCallsPerModel: 3 };

/**
 * Builds a raw Deepgram-shaped response from an ordered word list.
 * Each word is { word, start, end, confidence }.
 */
function makeDeepgramResponse(words, { duration = 5.0, transcript } = {}) {
  const text = transcript !== undefined ? transcript : words.map((w) => w.word).join(' ');
  return {
    result: {
      metadata: { duration, model_name: 'nova-3' },
      results: {
        channels: [
          {
            alternatives: [
              {
                transcript: text,
                words: words.map((w) => ({
                  word: w.word,
                  start: w.start,
                  end: w.end,
                  confidence: w.confidence,
                })),
              },
            ],
          },
        ],
      },
    },
  };
}

/** A combined-slice fake that returns a single concatenated clip. */
function fakeSliceAndConcat() {
  return async (_buffer, ranges) => ({
    buffer: Buffer.from(`concat@${ranges.length}`),
    mimetype: 'audio/mpeg',
  });
}

const INPUT = { buffer: Buffer.from('audio bytes'), mimetype: 'audio/mpeg' };

describe('lib/hybrid/pipeline - runHybridPipeline (combined correction)', () => {
  describe('missing Khaya key (Req 11.2)', () => {
    it('rejects with ConfigurationError / MISSING_API_KEY and never calls transcribePrimary', async () => {
      let primaryCalled = false;
      const deps = {
        transcribePrimary: async () => {
          primaryCalled = true;
          return makeDeepgramResponse([]);
        },
        khayaTranscribe: async () => ({ transcript: 'x' }),
        sliceAndConcatAudio: fakeSliceAndConcat(),
        khayaConfigured: () => false,
      };

      await assert.rejects(
        () => runHybridPipeline(INPUT, deps, CONFIG),
        (err) => {
          assert.equal(err.type, 'ConfigurationError');
          assert.equal(err.code, 'MISSING_API_KEY');
          return true;
        }
      );
      assert.equal(primaryCalled, false);
    });
  });

  describe('no low-confidence words (Req 1.4)', () => {
    it('passes the primary transcript through untouched with zeroed stats', async () => {
      const words = [
        { word: 'hello', start: 0.0, end: 0.5, confidence: 0.99 },
        { word: 'world', start: 0.6, end: 1.0, confidence: 0.95 },
      ];
      let khayaCalled = false;
      const deps = {
        transcribePrimary: async () =>
          makeDeepgramResponse(words, { transcript: 'hello world' }),
        khayaTranscribe: async () => {
          khayaCalled = true;
          return { transcript: 'unused' };
        },
        sliceAndConcatAudio: fakeSliceAndConcat(),
        khayaConfigured: () => true,
      };

      const result = await runHybridPipeline(INPUT, deps, CONFIG);

      assert.equal(result.transcript, 'hello world');
      assert.equal(khayaCalled, false, 'no correction should run');
      assert.ok(result.segments.every((s) => s.corrected === false));
      assert.deepEqual(result.metadata.correctionStats, {
        segmentsDetected: 0,
        corrected: false,
        language: null,
        correctionSkipped: false,
      });
      assert.deepEqual(result.metadata.config, {
        threshold: 0.85,
        gapTolerance: 0.5,
        padding: 0.25,
        maxCallsPerModel: 3,
      });
    });
  });

  describe('successful combined correction (Req 1.3, 11.4)', () => {
    it('sends exactly three Khaya calls (one per language) regardless of segment count', async () => {
      // Two separate low-confidence segments (split by a high-confidence word).
      const words = [
        { word: 'aa', start: 0.0, end: 0.5, confidence: 0.3 },
        { word: 'sep', start: 0.6, end: 1.0, confidence: 0.99 },
        { word: 'bb', start: 2.0, end: 2.5, confidence: 0.3 },
      ];
      let khayaCalls = 0;
      const languagesSeen = [];
      const deps = {
        transcribePrimary: async () => makeDeepgramResponse(words),
        khayaTranscribe: async (_buf, _mime, lang) => {
          khayaCalls += 1;
          languagesSeen.push(lang);
          return { transcript: `corrected ${lang}` };
        },
        sliceAndConcatAudio: fakeSliceAndConcat(),
        khayaConfigured: () => true,
      };

      const result = await runHybridPipeline(INPUT, deps, CONFIG);

      // Two detected segments, under the cap of 3, become two batches — each
      // corrected with one call per language (2 batches × 3 languages = 6).
      assert.equal(khayaCalls, 6);
      assert.deepEqual([...new Set(languagesSeen)].sort(), ['ee', 'gaa', 'tw']);

      const stats = result.metadata.correctionStats;
      assert.equal(stats.segmentsDetected, 2);
      assert.equal(stats.corrected, true);
      assert.equal(stats.correctionSkipped, false);
      assert.equal(typeof stats.language, 'string');

      // Corrected segments (one per low-confidence bundle), tagged with the winning language.
      const corrected = result.segments.filter((s) => s.corrected);
      assert.equal(corrected.length, 2, 'one corrected segment per detected bundle');

      // High-confidence word is preserved verbatim.
      assert.ok(result.segments.some((s) => s.text === 'sep' && s.corrected === false));
    });

    it('caps Khaya calls at maxCallsPerModel batches per language', async () => {
      // Six separate low-confidence segments, cap of 3 → 3 batches → 9 calls.
      const words = [
        { word: 'a', start: 0.0, end: 0.4, confidence: 0.3 },
        { word: 's1', start: 1.0, end: 1.4, confidence: 0.99 },
        { word: 'b', start: 2.0, end: 2.4, confidence: 0.3 },
        { word: 's2', start: 3.0, end: 3.4, confidence: 0.99 },
        { word: 'c', start: 4.0, end: 4.4, confidence: 0.3 },
        { word: 's3', start: 5.0, end: 5.4, confidence: 0.99 },
        { word: 'd', start: 6.0, end: 6.4, confidence: 0.3 },
        { word: 's4', start: 7.0, end: 7.4, confidence: 0.99 },
        { word: 'e', start: 8.0, end: 8.4, confidence: 0.3 },
        { word: 's5', start: 9.0, end: 9.4, confidence: 0.99 },
        { word: 'f', start: 10.0, end: 10.4, confidence: 0.3 },
      ];
      let khayaCalls = 0;
      let concatCalls = 0;
      const deps = {
        transcribePrimary: async () => makeDeepgramResponse(words, { duration: 12 }),
        khayaTranscribe: async (_buf, _mime, lang) => {
          khayaCalls += 1;
          return { transcript: `corrected ${lang}` };
        },
        sliceAndConcatAudio: async (_buffer, ranges) => {
          concatCalls += 1;
          return { buffer: Buffer.from(`concat@${ranges.length}`), mimetype: 'audio/mpeg' };
        },
        khayaConfigured: () => true,
      };

      const result = await runHybridPipeline(INPUT, deps, CONFIG);

      assert.equal(result.metadata.correctionStats.segmentsDetected, 6);
      assert.equal(concatCalls, 3, 'six segments collapse into three batches');
      assert.equal(khayaCalls, 9, 'three batches × three languages');
    });

    it('feeds each batch of ranges into its own combined slice call', async () => {
      const words = [
        { word: 'aa', start: 0.0, end: 0.5, confidence: 0.3 },
        { word: 'sep', start: 0.6, end: 1.0, confidence: 0.99 },
        { word: 'bb', start: 2.0, end: 2.5, confidence: 0.3 },
      ];
      let concatCalls = 0;
      const allRanges = [];
      const deps = {
        transcribePrimary: async () => makeDeepgramResponse(words),
        khayaTranscribe: async (_buf, _mime, lang) => ({ transcript: `corrected ${lang}` }),
        sliceAndConcatAudio: async (_buffer, ranges) => {
          concatCalls += 1;
          allRanges.push(...ranges);
          return { buffer: Buffer.from('concat'), mimetype: 'audio/mpeg' };
        },
        khayaConfigured: () => true,
      };

      await runHybridPipeline(INPUT, deps, CONFIG);

      // Two segments under the cap become two batches (one range each).
      assert.equal(concatCalls, 2, 'one slice+concat call per batch');
      assert.equal(allRanges.length, 2, 'both detected ranges are sliced');
    });
  });

  describe('combined slice failure (Req 6.3)', () => {
    it('skips correction and returns the primary transcript when the slice fails', async () => {
      const words = [
        { word: 'hello', start: 0.0, end: 0.5, confidence: 0.99 },
        { word: 'wrld', start: 0.6, end: 1.0, confidence: 0.3 },
      ];
      const deps = {
        transcribePrimary: async () =>
          makeDeepgramResponse(words, { transcript: 'hello wrld' }),
        khayaTranscribe: async () => ({ transcript: 'should not be reached' }),
        sliceAndConcatAudio: async () => {
          throw new Error('ffmpeg concat failed');
        },
        khayaConfigured: () => true,
      };

      const result = await runHybridPipeline(INPUT, deps, CONFIG);

      assert.equal(result.transcript, 'hello wrld');
      const stats = result.metadata.correctionStats;
      assert.equal(stats.corrected, false);
      assert.equal(stats.correctionSkipped, true);
      assert.equal(stats.language, null);
      assert.ok(stats.segmentsDetected > 0);
    });
  });

  describe('Khaya unavailable for all languages (Req 11.1)', () => {
    it('reports correctionSkipped and returns the primary transcript', async () => {
      const words = [
        { word: 'hello', start: 0.0, end: 0.5, confidence: 0.99 },
        { word: 'wrld', start: 0.6, end: 1.0, confidence: 0.3 },
      ];
      const deps = {
        transcribePrimary: async () =>
          makeDeepgramResponse(words, { transcript: 'hello wrld' }),
        khayaTranscribe: async (_buf, _mime, lang) => {
          throw new Error(`${lang} unavailable`);
        },
        sliceAndConcatAudio: fakeSliceAndConcat(),
        khayaConfigured: () => true,
      };

      const result = await runHybridPipeline(INPUT, deps, CONFIG);

      assert.equal(result.transcript, 'hello wrld');
      const stats = result.metadata.correctionStats;
      assert.equal(stats.correctionSkipped, true);
      assert.equal(stats.corrected, false);
      assert.ok(stats.segmentsDetected > 0);
    });
  });

  describe('all languages return empty text', () => {
    it('skips correction when every language yields empty/whitespace transcripts', async () => {
      const words = [
        { word: 'hello', start: 0.0, end: 0.5, confidence: 0.99 },
        { word: 'wrld', start: 0.6, end: 1.0, confidence: 0.3 },
      ];
      const deps = {
        transcribePrimary: async () =>
          makeDeepgramResponse(words, { transcript: 'hello wrld' }),
        khayaTranscribe: async () => ({ transcript: '   ' }),
        sliceAndConcatAudio: fakeSliceAndConcat(),
        khayaConfigured: () => true,
      };

      const result = await runHybridPipeline(INPUT, deps, CONFIG);

      assert.equal(result.transcript, 'hello wrld');
      assert.equal(result.metadata.correctionStats.corrected, false);
      assert.equal(result.metadata.correctionStats.correctionSkipped, true);
    });
  });

  describe('TranscriptionError propagation', () => {
    it('rejects when the primary response has no word-level results', async () => {
      const deps = {
        transcribePrimary: async () => makeDeepgramResponse([]),
        khayaTranscribe: async () => ({ transcript: 'x' }),
        sliceAndConcatAudio: fakeSliceAndConcat(),
        khayaConfigured: () => true,
      };

      await assert.rejects(
        () => runHybridPipeline(INPUT, deps, CONFIG),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });
  });
});
