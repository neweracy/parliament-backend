'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { sliceAudio, sliceAndConcatAudio } = require('../../lib/hybrid/audio-slicer');

const FIXTURE_PATH = path.join(__dirname, '..', '..', 'test_audio', '25-must-know-twi-phrases.mp3');

// The slicer names its temp files with these prefixes inside os.tmpdir().
const TEMP_PREFIXES = ['hybrid-slice-in-', 'hybrid-slice-out-', 'hybrid-concat-in-', 'hybrid-concat-out-'];

/**
 * Counts leftover slicer temp files currently present in os.tmpdir().
 *
 * @returns {number}
 */
function countSlicerTempFiles() {
  const entries = fs.readdirSync(os.tmpdir());
  return entries.filter((name) => TEMP_PREFIXES.some((prefix) => name.startsWith(prefix))).length;
}

describe('lib/hybrid/audio-slicer - sliceAudio', () => {
  describe('real ffmpeg success path', () => {
    it('produces an audio/mpeg buffer from a real fixture slice', async () => {
      const source = fs.readFileSync(FIXTURE_PATH);
      assert.ok(source.length > 0, 'fixture should be readable and non-empty');

      const result = await sliceAudio(source, 0, 1);

      assert.equal(result.mimetype, 'audio/mpeg');
      assert.ok(Buffer.isBuffer(result.buffer), 'result.buffer should be a Buffer');
      assert.ok(result.buffer.length > 0, 'sliced buffer should be non-empty');
    });

    it('cleans up temp files after a successful slice', async () => {
      const source = fs.readFileSync(FIXTURE_PATH);
      const before = countSlicerTempFiles();

      await sliceAudio(source, 0, 1);

      const after = countSlicerTempFiles();
      assert.equal(after, before, 'no leftover slicer temp files should remain after success');
    });
  });

  describe('failure path - bad binary path', () => {
    it('throws AudioSliceError when the ffmpeg path does not exist', async () => {
      const source = fs.readFileSync(FIXTURE_PATH);

      await assert.rejects(
        () => sliceAudio(source, 0, 1, '/nonexistent/ffmpeg/path'),
        (error) => {
          assert.equal(error.type, 'AudioSliceError');
          return true;
        }
      );
    });

    it('cleans up temp files even when slicing fails', async () => {
      const source = fs.readFileSync(FIXTURE_PATH);
      const before = countSlicerTempFiles();

      await assert.rejects(() => sliceAudio(source, 0, 1, '/nonexistent/ffmpeg/path'), {
        type: 'AudioSliceError',
      });

      const after = countSlicerTempFiles();
      assert.equal(after, before, 'no leftover slicer temp files should remain after failure');
    });
  });

  describe('failure path - unresolved binary', () => {
    it('throws AudioSliceError when the ffmpeg path is null', async () => {
      const source = fs.readFileSync(FIXTURE_PATH);

      await assert.rejects(
        () => sliceAudio(source, 0, 1, null),
        (error) => {
          assert.equal(error.type, 'AudioSliceError');
          return true;
        }
      );
    });
  });
});

describe('lib/hybrid/audio-slicer - sliceAndConcatAudio', () => {
  it('concatenates multiple ranges into a single audio/mpeg buffer', async () => {
    const source = fs.readFileSync(FIXTURE_PATH);

    const result = await sliceAndConcatAudio(source, [
      { start: 0, end: 1 },
      { start: 2, end: 3 },
      { start: 4, end: 5 },
    ]);

    assert.equal(result.mimetype, 'audio/mpeg');
    assert.ok(Buffer.isBuffer(result.buffer), 'result.buffer should be a Buffer');
    assert.ok(result.buffer.length > 0, 'concatenated buffer should be non-empty');
  });

  it('works with a single range', async () => {
    const source = fs.readFileSync(FIXTURE_PATH);
    const result = await sliceAndConcatAudio(source, [{ start: 0, end: 1 }]);
    assert.equal(result.mimetype, 'audio/mpeg');
    assert.ok(result.buffer.length > 0);
  });

  it('cleans up temp files after a successful concat', async () => {
    const source = fs.readFileSync(FIXTURE_PATH);
    const before = countSlicerTempFiles();

    await sliceAndConcatAudio(source, [
      { start: 0, end: 1 },
      { start: 2, end: 3 },
    ]);

    const after = countSlicerTempFiles();
    assert.equal(after, before, 'no leftover temp files should remain after concat');
  });

  it('throws AudioSliceError when given no ranges', async () => {
    const source = fs.readFileSync(FIXTURE_PATH);
    await assert.rejects(
      () => sliceAndConcatAudio(source, []),
      (error) => {
        assert.equal(error.type, 'AudioSliceError');
        return true;
      }
    );
  });

  it('throws AudioSliceError when the ffmpeg path is unresolved', async () => {
    const source = fs.readFileSync(FIXTURE_PATH);
    await assert.rejects(
      () => sliceAndConcatAudio(source, [{ start: 0, end: 1 }], null),
      (error) => {
        assert.equal(error.type, 'AudioSliceError');
        return true;
      }
    );
  });
});
