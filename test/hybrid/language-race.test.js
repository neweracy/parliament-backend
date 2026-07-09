'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { raceLanguages, CANDIDATE_LANGUAGES } = require('../../lib/hybrid/language-race');

describe('lib/hybrid/language-race - raceLanguages', () => {
  const sliceBuffer = Buffer.from('fake audio data');
  const mimetype = 'audio/mpeg';

  describe('exactly three calls per slice', () => {
    it('invokes khayaTranscribe exactly 3 times for one slice', async () => {
      let callCount = 0;
      const fakeKhaya = async () => {
        callCount++;
        return { transcript: 'hello' };
      };

      await raceLanguages(sliceBuffer, mimetype, fakeKhaya);
      assert.equal(callCount, 3);
    });
  });

  describe('languages passed to khayaTranscribe', () => {
    it('passes tw, ee, and gaa as language arguments', async () => {
      const languagesSeen = [];
      const fakeKhaya = async (_buf, _mime, lang) => {
        languagesSeen.push(lang);
        return { transcript: 'result' };
      };

      await raceLanguages(sliceBuffer, mimetype, fakeKhaya);
      assert.deepEqual(languagesSeen.sort(), ['ee', 'gaa', 'tw']);
    });

    it('CANDIDATE_LANGUAGES exports the expected set', () => {
      assert.deepEqual(CANDIDATE_LANGUAGES, ['tw', 'ee', 'gaa']);
    });
  });

  describe('all succeed', () => {
    it('returns three results with ok: true and transcripts', async () => {
      const fakeKhaya = async (_buf, _mime, lang) => {
        return { transcript: `text for ${lang}` };
      };

      const results = await raceLanguages(sliceBuffer, mimetype, fakeKhaya);

      assert.equal(results.length, 3);
      for (const result of results) {
        assert.equal(result.ok, true);
        assert.ok(result.transcript.length > 0);
        assert.ok(CANDIDATE_LANGUAGES.includes(result.language));
      }
    });

    it('preserves per-language transcript content', async () => {
      const fakeKhaya = async (_buf, _mime, lang) => {
        return { transcript: `transcript-${lang}` };
      };

      const results = await raceLanguages(sliceBuffer, mimetype, fakeKhaya);
      const byLang = Object.fromEntries(results.map((r) => [r.language, r]));

      assert.equal(byLang.tw.transcript, 'transcript-tw');
      assert.equal(byLang.ee.transcript, 'transcript-ee');
      assert.equal(byLang.gaa.transcript, 'transcript-gaa');
    });
  });

  describe('partial failure tolerance', () => {
    it('continues with successes when one language fails', async () => {
      const fakeKhaya = async (_buf, _mime, lang) => {
        if (lang === 'ee') throw new Error('Khaya ee timeout');
        return { transcript: `ok-${lang}` };
      };

      const results = await raceLanguages(sliceBuffer, mimetype, fakeKhaya);

      assert.equal(results.length, 3);

      const byLang = Object.fromEntries(results.map((r) => [r.language, r]));
      assert.equal(byLang.tw.ok, true);
      assert.equal(byLang.tw.transcript, 'ok-tw');
      assert.equal(byLang.gaa.ok, true);
      assert.equal(byLang.gaa.transcript, 'ok-gaa');
      assert.equal(byLang.ee.ok, false);
      assert.equal(byLang.ee.transcript, '');
    });

    it('continues with successes when two languages fail', async () => {
      const fakeKhaya = async (_buf, _mime, lang) => {
        if (lang === 'tw' || lang === 'gaa') throw new Error('network error');
        return { transcript: 'only ee works' };
      };

      const results = await raceLanguages(sliceBuffer, mimetype, fakeKhaya);

      assert.equal(results.length, 3);

      const byLang = Object.fromEntries(results.map((r) => [r.language, r]));
      assert.equal(byLang.ee.ok, true);
      assert.equal(byLang.ee.transcript, 'only ee works');
      assert.equal(byLang.tw.ok, false);
      assert.equal(byLang.gaa.ok, false);
    });

    it('failed results include the error', async () => {
      const fakeKhaya = async (_buf, _mime, lang) => {
        if (lang === 'gaa') throw new Error('service unavailable');
        return { transcript: 'ok' };
      };

      const results = await raceLanguages(sliceBuffer, mimetype, fakeKhaya);
      const gaaResult = results.find((r) => r.language === 'gaa');

      assert.equal(gaaResult.ok, false);
      assert.ok(gaaResult.error instanceof Error);
      assert.equal(gaaResult.error.message, 'service unavailable');
    });
  });

  describe('all three fail', () => {
    it('yields three { ok: false } results when all languages throw', async () => {
      const fakeKhaya = async (_buf, _mime, lang) => {
        throw new Error(`${lang} failed`);
      };

      const results = await raceLanguages(sliceBuffer, mimetype, fakeKhaya);

      assert.equal(results.length, 3);
      for (const result of results) {
        assert.equal(result.ok, false);
        assert.equal(result.transcript, '');
        assert.ok(result.error instanceof Error);
        assert.ok(CANDIDATE_LANGUAGES.includes(result.language));
      }
    });

    it('does not reject the promise when all languages fail', async () => {
      const fakeKhaya = async () => {
        throw new Error('total failure');
      };

      // Should resolve, not reject
      const results = await raceLanguages(sliceBuffer, mimetype, fakeKhaya);
      assert.ok(Array.isArray(results));
      assert.equal(results.length, 3);
    });
  });
});
