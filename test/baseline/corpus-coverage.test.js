'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const corpusPath = join(__dirname, '..', '..', 'fixtures', 'golden-corpus', 'corpus.json');
const corpus = JSON.parse(readFileSync(corpusPath, 'utf8'));

/**
 * Maps corpus category values to the eleven Requirement 1.3 categories.
 *
 * Requirement 1.3 mandates coverage of: locations, persons, ministers, MPs,
 * parties, spoken years, spoken decades, fused tokens, split tokens,
 * hyphenated tokens, and transcripts with no correctable entity.
 */
const REQUIRED_CATEGORIES = [
  'location',
  'person',
  'minister',
  'mp',
  'party',
  'year',
  'decade',
  'fused',
  'split',
  'hyphenated',
  'no_entity',
];

describe('Golden Corpus coverage (Requirement 1.3)', () => {
  it('contains at least 30 test cases', () => {
    assert.ok(
      corpus.cases.length >= 30,
      `Expected ≥30 corpus cases, got ${corpus.cases.length}`
    );
  });

  for (const category of REQUIRED_CATEGORIES) {
    it(`has at least one case in category "${category}"`, () => {
      const matches = corpus.cases.filter(c => c.category === category);
      assert.ok(
        matches.length >= 1,
        `Expected at least 1 case with category "${category}", found ${matches.length}`
      );
    });
  }

  it('every case has the required schema fields', () => {
    const required = ['id', 'description', 'category', 'input_transcript', 'input_words', 'expected_transcript', 'should_correct', 'expected_entities'];
    for (const c of corpus.cases) {
      for (const field of required) {
        assert.ok(
          field in c,
          `Case "${c.id}" is missing required field "${field}"`
        );
      }
    }
  });

  it('all case ids are unique', () => {
    const ids = corpus.cases.map(c => c.id);
    const unique = new Set(ids);
    assert.strictEqual(
      unique.size,
      ids.length,
      `Found duplicate ids: ${ids.filter((id, i) => ids.indexOf(id) !== i)}`
    );
  });
});
