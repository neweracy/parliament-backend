/**
 * Comprehensive test suite for the Ghana Location Correction Engine.
 *
 * Tests all error types: fused, split, hyphenated, spelling, phonetic,
 * region recognition, country recognition, and false-positive prevention.
 *
 * Target: ≥95% accuracy (pass rate on positive corrections + zero false positives).
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const { correctLocations, correctSingle } = require('../../lib/location-correction'); // eslint-disable-line no-unused-vars

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function expectCorrected(input, expected, minConfidence = 0.75) {
  const result = correctLocations(input);
  const corrected = result.text;
  assert.equal(
    corrected, expected,
    `Expected "${input}" → "${expected}", got "${corrected}"`
  );
  if (result.corrections.length > 0) {
    assert.ok(
      result.corrections[0].confidence >= minConfidence,
      `Confidence ${result.corrections[0].confidence} below ${minConfidence}`
    );
  }
}

function expectUnchanged(input) {
  const result = correctLocations(input);
  assert.equal(
    result.text, input,
    `Expected "${input}" to be unchanged, got "${result.text}" (corrections: ${JSON.stringify(result.corrections)})`
  );
}

// ---------------------------------------------------------------------------
// Tests: Country recognition
// ---------------------------------------------------------------------------

describe('Country: Ghana', () => {
  it('recognizes "Ghana" as identity', () => expectUnchanged('Ghana'));
  it('corrects "Gana" → "Ghana"', () => expectCorrected('Gana', 'Ghana'));
  it('corrects "Ghanna" → "Ghana"', () => expectCorrected('Ghanna', 'Ghana'));
  it('keeps "ghana" lowercase as identity', () => expectUnchanged('ghana'));
});

// ---------------------------------------------------------------------------
// Tests: Region recognition (all 16 regions)
// ---------------------------------------------------------------------------

describe('Regions: Identity (correct spelling)', () => {
  const regions = [
    'Greater Accra', 'Ashanti', 'Western', 'Western North', 'Central',
    'Eastern', 'Volta', 'Oti', 'Northern', 'Savannah', 'North East',
    'Upper East', 'Upper West', 'Bono', 'Bono East', 'Ahafo',
  ];
  for (const r of regions) {
    it(`recognizes "${r}" as correct`, () => expectUnchanged(r));
  }
});

describe('Regions: Spelling corrections', () => {
  it('"Ashante" → "Ashanti"', () => expectCorrected('Ashante', 'Ashanti'));
  it('"Upper Wes" → "Upper West"', () => expectCorrected('Upper Wes', 'Upper West'));
  it('"Savanna" → "Savannah"', () => expectCorrected('Savanna', 'Savannah'));
  it('"Nortern" → "Northern"', () => expectCorrected('Nortern', 'Northern'));
  it('"Eastren" → "Eastern"', () => expectCorrected('Eastren', 'Eastern'));
  it('"Greator Accra" → "Greater Accra"', () => expectCorrected('Greator Accra', 'Greater Accra'));
});

// ---------------------------------------------------------------------------
// Tests: Fused words
// ---------------------------------------------------------------------------

describe('Fused words', () => {
  it('"ningoprampram" → "Ningo-Prampram"', () => expectCorrected('ningoprampram', 'Ningo-Prampram'));
  it('"nyungoprampram" → "Ningo-Prampram"', () => expectCorrected('nyungoprampram', 'Ningo-Prampram'));
  it('"capecoast" → "Cape Coast"', () => expectCorrected('capecoast', 'Cape Coast'));
  it('"sekondtakoradi" → "Sekondi-Takoradi"', () => expectCorrected('sekondtakoradi', 'Sekondi-Takoradi'));
});

// ---------------------------------------------------------------------------
// Tests: Split words (word joins)
// ---------------------------------------------------------------------------

describe('Split words', () => {
  it('"pram pram" → "Prampram"', () => expectCorrected('pram pram', 'Prampram'));
  it('"Cape Cost" → "Cape Coast"', () => expectCorrected('Cape Cost', 'Cape Coast'));
  it('"sekondi takoradi" → "Sekondi-Takoradi"', () => expectCorrected('sekondi takoradi', 'Sekondi-Takoradi'));
});

// ---------------------------------------------------------------------------
// Tests: Hyphenated variants
// ---------------------------------------------------------------------------

describe('Hyphenated variants (identity)', () => {
  it('"Ningo-Prampram" unchanged', () => expectUnchanged('Ningo-Prampram'));
  it('"Sekondi-Takoradi" unchanged', () => expectUnchanged('Sekondi-Takoradi'));
});

// ---------------------------------------------------------------------------
// Tests: Spelling mistakes (fuzzy)
// ---------------------------------------------------------------------------

describe('Spelling mistakes', () => {
  it('"Kumase" → "Kumasi"', () => expectCorrected('Kumase', 'Kumasi'));
  it('"Accara" → "Accra"', () => expectCorrected('Accara', 'Accra'));
  it('"Obuase" → "Obuasi"', () => expectCorrected('Obuase', 'Obuasi'));
  it('"Tamalee" → "Tamale"', () => expectCorrected('Tamalee', 'Tamale'));
  it('"Bolgatangaa" → "Bolgatanga"', () => expectCorrected('Bolgatangaa', 'Bolgatanga'));
  it('"Techimaan" → "Techiman"', () => expectCorrected('Techimaan', 'Techiman'));
});

// ---------------------------------------------------------------------------
// Tests: Phonetic similarity
// ---------------------------------------------------------------------------

describe('Phonetic similarity', () => {
  it('"Koumasi" → "Kumasi"', () => expectCorrected('Koumasi', 'Kumasi'));
});

// ---------------------------------------------------------------------------
// Tests: In-context (full sentences)
// ---------------------------------------------------------------------------

describe('In-context corrections', () => {
  it('corrects location within a sentence', () => {
    expectCorrected(
      'the ningoprampram constituency',
      'the Ningo-Prampram constituency'
    );
  });
  it('corrects multiple locations in one sentence', () => {
    const result = correctLocations('He went from Kumase to Accara');
    assert.ok(result.text.includes('Kumasi'), `Expected Kumasi in: ${result.text}`);
    assert.ok(result.text.includes('Accra'), `Expected Accra in: ${result.text}`);
  });
  it('does not modify correct locations in context', () => {
    expectUnchanged('Kumasi is the capital of Ashanti');
  });
  it('corrects region spelling in context', () => {
    expectCorrected(
      'the Ashante region is beautiful',
      'the Ashanti region is beautiful'
    );
  });
  it('handles "fourth republic of Ghana"', () => {
    expectUnchanged('the fourth republic of Ghana');
  });
  it('corrects "fourth republic of Gana"', () => {
    expectCorrected(
      'the fourth republic of Gana',
      'the fourth republic of Ghana'
    );
  });
});

// ---------------------------------------------------------------------------
// Tests: False positive prevention
// ---------------------------------------------------------------------------

describe('False positives — no correction', () => {
  const safeWords = [
    'summer is here',
    'the party was great',
    'he traveled to the market',
    'they all went home',
    'the first time ever',
    'good morning everyone',
    'winter came early',
    'she said hello',
    'honorable members of parliament',
    'the committee met today',
    'distinguished son of the nation',
    'his passing reminds us',
    'democratic governance',
  ];
  for (const phrase of safeWords) {
    it(`"${phrase}" unchanged`, () => expectUnchanged(phrase));
  }

  // Phrases that now produce corrections after adding BB Carboo to dataset
  const nowCorrected = [
    { input: 'bb kabo affectionately known', corrected: 'B.B. Carboo' },
    { input: 'kabu lived a life', corrected: 'B.B. Carboo' },
  ];
  for (const { input, corrected } of nowCorrected) {
    it(`"${input}" corrects to person "${corrected}"`, () => {
      const result = correctLocations(input);
      assert.ok(
        result.corrections.some(c => c.corrected === corrected && c.entityKind === 'person'),
        `Expected "${input}" to correct to "${corrected}", got: ${JSON.stringify(result.corrections)}`
      );
    });
  }
});

// ---------------------------------------------------------------------------
// Tests: Edge cases
// ---------------------------------------------------------------------------

describe('Edge cases', () => {
  it('empty string', () => expectUnchanged(''));
  it('single char', () => expectUnchanged('a'));
  it('numbers', () => expectUnchanged('1234'));
  it('very short word "Ho" (a real city but too short for fuzzy)', () => {
    // "Ho" is only 2 chars — below correction threshold, remains unchanged
    expectUnchanged('Ho');
  });
  it('preserves punctuation', () => {
    const result = correctLocations('He visited Kumase.');
    assert.ok(result.text.endsWith('.'), 'Should preserve trailing period');
    assert.ok(result.text.includes('Kumasi'), 'Should correct Kumase');
  });
});

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

describe('Accuracy summary', () => {
  it('all positive corrections succeed (target ≥95%)', () => {
    const positives = [
      ['ningoprampram', 'Ningo-Prampram'],
      ['nyungoprampram', 'Ningo-Prampram'],
      ['Kumase', 'Kumasi'],
      ['Accara', 'Accra'],
      ['Cape Cost', 'Cape Coast'],
      ['Ashante', 'Ashanti'],
      ['Obuase', 'Obuasi'],
      ['sekondi takoradi', 'Sekondi-Takoradi'],
      ['pram pram', 'Prampram'],
      ['Koumasi', 'Kumasi'],
      ['Gana', 'Ghana'],
      ['Savanna', 'Savannah'],
      ['Upper Wes', 'Upper West'],
      ['Nortern', 'Northern'],
      ['Tamalee', 'Tamale'],
      ['Bolgatangaa', 'Bolgatanga'],
      ['Techimaan', 'Techiman'],
      ['Eastren', 'Eastern'],
      ['Ghanna', 'Ghana'],
      ['capecoast', 'Cape Coast'],
    ];
    let pass = 0;
    for (const [input, expected] of positives) {
      const result = correctLocations(input);
      if (result.text === expected) pass++;
      else console.log(`  FAIL: "${input}" → "${result.text}" (expected "${expected}")`);
    }
    const accuracy = (pass / positives.length * 100).toFixed(1);
    console.log(`  Accuracy: ${pass}/${positives.length} = ${accuracy}%`);
    assert.ok(pass / positives.length >= 0.95, `Accuracy ${accuracy}% below 95% target`);
  });
});
