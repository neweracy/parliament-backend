'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { extractWords } = require('../../lib/hybrid/deepgram-words');

describe('lib/hybrid/deepgram-words - extractWords', () => {
  describe('normal word array', () => {
    it('returns words preserving word, start, end, and confidence', () => {
      const response = {
        result: {
          metadata: { duration: 5.2 },
          results: {
            channels: [
              {
                alternatives: [
                  {
                    transcript: 'hello world',
                    words: [
                      { word: 'hello', start: 0.0, end: 0.5, confidence: 0.99 },
                      { word: 'world', start: 0.6, end: 1.1, confidence: 0.95 },
                    ],
                  },
                ],
              },
            ],
          },
        },
      };

      const { words, duration, transcript } = extractWords(response);

      assert.equal(words.length, 2);
      assert.deepEqual(words[0], { word: 'hello', start: 0.0, end: 0.5, confidence: 0.99 });
      assert.deepEqual(words[1], { word: 'world', start: 0.6, end: 1.1, confidence: 0.95 });
      assert.equal(duration, 5.2);
      assert.equal(transcript, 'hello world');
    });

    it('preserves word order from the response', () => {
      const response = {
        result: {
          metadata: { duration: 3.0 },
          results: {
            channels: [
              {
                alternatives: [
                  {
                    transcript: 'one two three',
                    words: [
                      { word: 'one', start: 0.0, end: 0.3, confidence: 0.88 },
                      { word: 'two', start: 0.4, end: 0.7, confidence: 0.76 },
                      { word: 'three', start: 0.8, end: 1.2, confidence: 0.92 },
                    ],
                  },
                ],
              },
            ],
          },
        },
      };

      const { words } = extractWords(response);

      assert.equal(words[0].word, 'one');
      assert.equal(words[1].word, 'two');
      assert.equal(words[2].word, 'three');
    });

    it('extracts duration from metadata', () => {
      const response = {
        result: {
          metadata: { duration: 12.5 },
          results: {
            channels: [
              {
                alternatives: [
                  {
                    transcript: 'test',
                    words: [
                      { word: 'test', start: 0.0, end: 0.5, confidence: 0.9 },
                    ],
                  },
                ],
              },
            ],
          },
        },
      };

      const { duration } = extractWords(response);
      assert.equal(duration, 12.5);
    });

    it('returns duration 0 when metadata.duration is absent', () => {
      const response = {
        result: {
          metadata: {},
          results: {
            channels: [
              {
                alternatives: [
                  {
                    transcript: 'test',
                    words: [
                      { word: 'test', start: 0.0, end: 0.5, confidence: 0.9 },
                    ],
                  },
                ],
              },
            ],
          },
        },
      };

      const { duration } = extractWords(response);
      assert.equal(duration, 0);
    });

    it('returns empty string transcript when transcript field is absent', () => {
      const response = {
        result: {
          metadata: { duration: 1.0 },
          results: {
            channels: [
              {
                alternatives: [
                  {
                    words: [
                      { word: 'hi', start: 0.0, end: 0.3, confidence: 0.85 },
                    ],
                  },
                ],
              },
            ],
          },
        },
      };

      const { transcript } = extractWords(response);
      assert.equal(transcript, '');
    });
  });

  describe('missing words throws TranscriptionError', () => {
    it('throws when words array is absent', () => {
      const response = {
        result: {
          metadata: { duration: 2.0 },
          results: {
            channels: [
              {
                alternatives: [
                  { transcript: 'hello' },
                ],
              },
            ],
          },
        },
      };

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          assert.ok(err.message.length > 0);
          return true;
        }
      );
    });

    it('throws when words array is empty', () => {
      const response = {
        result: {
          metadata: { duration: 2.0 },
          results: {
            channels: [
              {
                alternatives: [
                  { transcript: '', words: [] },
                ],
              },
            ],
          },
        },
      };

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });

    it('throws when alternatives array is absent', () => {
      const response = {
        result: {
          metadata: { duration: 2.0 },
          results: {
            channels: [{}],
          },
        },
      };

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });

    it('throws when alternatives array is empty', () => {
      const response = {
        result: {
          metadata: { duration: 2.0 },
          results: {
            channels: [
              { alternatives: [] },
            ],
          },
        },
      };

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });
  });

  describe('empty channels throws TranscriptionError', () => {
    it('throws when channels array is empty', () => {
      const response = {
        result: {
          metadata: { duration: 2.0 },
          results: {
            channels: [],
          },
        },
      };

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });

    it('throws when channels is absent', () => {
      const response = {
        result: {
          metadata: { duration: 2.0 },
          results: {},
        },
      };

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });

    it('throws when results is absent', () => {
      const response = {
        result: {
          metadata: { duration: 2.0 },
        },
      };

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });

    it('throws when result is absent', () => {
      const response = {};

      assert.throws(
        () => extractWords(response),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });

    it('throws when deepgramResponse is null', () => {
      assert.throws(
        () => extractWords(null),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });

    it('throws when deepgramResponse is undefined', () => {
      assert.throws(
        () => extractWords(undefined),
        (err) => {
          assert.equal(err.type, 'TranscriptionError');
          return true;
        }
      );
    });
  });
});
