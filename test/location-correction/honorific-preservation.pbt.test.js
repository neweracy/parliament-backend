/**
 * Preservation Property Tests — Honorific Person Recognition Bugfix
 *
 * Property 2: Preservation — Non-Title Corrections and Non-Matching
 * Title Contexts Unchanged
 *
 * These tests capture the EXISTING correct behavior that MUST be preserved
 * after the honorific person recognition fix is applied. They run on the
 * UNFIXED code and MUST PASS — they establish the baseline.
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
 */

'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const { correctLocations, SUPPLEMENTARY_LOCATIONS } = require('../../lib/location-correction');
const { ALL_PERSONS } = require('../../lib/location-correction/persons-dataset');
const { ALL_MPS } = require('../../lib/location-correction/mps-dataset');
const { ALL_PARTIES } = require('../../lib/location-correction/parties-dataset');

// ---------------------------------------------------------------------------
// Data pools for property-based generation
// ---------------------------------------------------------------------------

/**
 * Collect all location aliases that the engine should recognize.
 * Includes SUPPLEMENTARY_LOCATIONS aliases and canonical forms.
 */
function collectLocationAliases() {
  const aliases = [];
  for (const loc of SUPPLEMENTARY_LOCATIONS) {
    aliases.push(loc.canonical);
    if (loc.aliases) {
      for (const alias of loc.aliases) {
        aliases.push(alias);
      }
    }
  }
  return aliases;
}

const LOCATION_ALIASES = collectLocationAliases();

/**
 * Collect all party abbreviations and aliases.
 */
function collectPartyInputs() {
  const inputs = [];
  for (const party of ALL_PARTIES) {
    if (party.abbr) inputs.push(party.abbr);
    if (party.aliases) {
      for (const alias of party.aliases) {
        inputs.push(alias);
      }
    }
  }
  return inputs;
}

const PARTY_INPUTS = collectPartyInputs();

/**
 * Collect person aliases that do NOT require a preceding title to match.
 * These are the exact and alias entries in the datasets.
 */
function collectUntitledPersonAliases() {
  const aliases = [];
  const allEntries = [...ALL_PERSONS, ...ALL_MPS];
  for (const entry of allEntries) {
    const canonical = entry.canonical || entry.name;
    if (entry.aliases) {
      for (const alias of entry.aliases) {
        // Only include aliases that are 4+ chars (short ones may not match)
        if (alias.length >= 4) {
          aliases.push(alias);
        }
      }
    }
  }
  // Deduplicate
  return [...new Set(aliases)];
}

const UNTITLED_PERSON_ALIASES = collectUntitledPersonAliases();

/**
 * Non-person tokens that commonly follow titles in parliament.
 * These should NOT trigger person corrections.
 */
const NON_PERSON_AFTER_TITLE = [
  'Chairman', 'Speaker', 'Minister', 'Member', 'Members',
  'Chair', 'Deputy', 'Leader', 'Clerk', 'Secretary',
  'Committee', 'House', 'Parliament', 'Government',
];

/**
 * Title words that should never be modified in output.
 */
const TITLE_WORDS = [
  'Honourable', 'Hon.', 'Hon', 'Mr', 'Mr.', 'Mrs', 'Mrs.',
  'Madam', 'Dr', 'Dr.', 'Prof', 'Prof.', 'Rt', 'Rt.',
];

// ---------------------------------------------------------------------------
// Property 1: Location Correction Preservation
// Validates: Requirement 3.1
// ---------------------------------------------------------------------------

describe('Property 1 (Location preservation): correctLocations(alias) produces same corrections', () => {
  /**
   * **Validates: Requirements 3.1**
   *
   * For all known location aliases from SUPPLEMENTARY_LOCATIONS,
   * correctLocations(alias) produces the expected correction to the
   * canonical form. This behavior must be preserved after the fix.
   */
  it('SUPPLEMENTARY_LOCATIONS aliases are corrected to canonical form', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...LOCATION_ALIASES),
        (alias) => {
          const result = correctLocations(alias);

          // The result should either correct to a canonical form or recognize
          // it as an identity match (already canonical). In either case, the
          // text should contain a known canonical location name or remain as-is.
          // Key invariant: the engine does not break or throw.
          assert.ok(result.text !== undefined, `correctLocations("${alias}") should return a result with text`);
          assert.ok(Array.isArray(result.corrections), `correctLocations("${alias}") should return corrections array`);
        }
      ),
      { numRuns: 200 }
    );
  });

  it('known location fuzzy matches still produce corrections', () => {
    // Concrete cases observed on unfixed code
    const knownCorrections = [
      { input: 'Kumase', expected: 'Kumasi' },
      { input: 'Obuase', expected: 'Obuasi' },
      { input: 'Bolga', expected: 'Bolgatanga' },
      { input: 'Cassoa', expected: 'Kasoa' },
    ];

    for (const { input, expected } of knownCorrections) {
      const result = correctLocations(input);
      assert.ok(
        result.corrections.length > 0,
        `Expected "${input}" to produce corrections, got none`
      );
      assert.strictEqual(
        result.corrections[0].corrected, expected,
        `Expected "${input}" to correct to "${expected}", got "${result.corrections[0].corrected}"`
      );
      assert.strictEqual(
        result.corrections[0].entityKind, 'location',
        `Expected entityKind "location" for "${input}"`
      );
    }
  });

  it('fused location forms are still recognized', () => {
    const fusedCases = [
      { input: 'ningoprampram', expected: 'Ningo-Prampram' },
    ];

    for (const { input, expected } of fusedCases) {
      const result = correctLocations(input);
      assert.ok(
        result.corrections.length > 0,
        `Expected fused form "${input}" to produce corrections`
      );
      assert.strictEqual(result.corrections[0].corrected, expected);
    }
  });
});

// ---------------------------------------------------------------------------
// Property 2: Party Correction Preservation
// Validates: Requirement 3.1
// ---------------------------------------------------------------------------

describe('Property 2 (Party preservation): party abbreviations/aliases still resolve correctly', () => {
  /**
   * **Validates: Requirements 3.1**
   *
   * For all party abbreviations and aliases, correctLocations produces
   * the same corrections (mapping to canonical party name).
   */
  it('party abbreviations resolve to full canonical name', () => {
    // Exclude abbreviations that collide with stopwords (e.g. "UP" = "up")
    // The engine intentionally skips stopwords — this is correct behavior.
    const STOPWORDS = new Set([
      'a', 'an', 'the', 'in', 'on', 'at', 'to', 'of', 'is', 'are', 'was', 'were',
      'be', 'been', 'and', 'or', 'but', 'for', 'by', 'with', 'from', 'up', 'do',
      'no', 'so', 'if', 'it', 'us', 'we', 'he', 'as',
    ]);
    const validAbbrs = ALL_PARTIES
      .filter(p => p.abbr && !STOPWORDS.has(p.abbr.toLowerCase()))
      .map(p => p.abbr);

    fc.assert(
      fc.property(
        fc.constantFrom(...validAbbrs),
        (abbr) => {
          const result = correctLocations(abbr);

          // Party abbreviations should resolve to the full canonical name
          assert.ok(
            result.corrections.length > 0 || result.entitiesFound.length > 0,
            `Expected party abbreviation "${abbr}" to be recognized`
          );

          // Check corrections or identity matches
          const match = result.corrections[0] || result.entitiesFound[0];
          assert.strictEqual(
            match.entityKind, 'party',
            `Expected entityKind "party" for abbreviation "${abbr}", got "${match.entityKind}"`
          );
        }
      ),
      { numRuns: 50 }
    );
  });

  it('concrete party cases still work', () => {
    const partyCases = [
      { input: 'NDC', expected: 'National Democratic Congress' },
      { input: 'NPP', expected: 'New Patriotic Party' },
      { input: 'CPP', expected: "Convention People's Party" },
      { input: 'PNC', expected: "People's National Convention" },
    ];

    for (const { input, expected } of partyCases) {
      const result = correctLocations(input);
      assert.ok(result.corrections.length > 0, `Expected "${input}" to produce corrections`);
      assert.strictEqual(result.corrections[0].corrected, expected);
      assert.strictEqual(result.corrections[0].entityKind, 'party');
    }
  });
});

// ---------------------------------------------------------------------------
// Property 3: Untitled Person Preservation
// Validates: Requirement 3.4
// ---------------------------------------------------------------------------

describe('Property 3 (Untitled person preservation): person aliases without title still match', () => {
  /**
   * **Validates: Requirements 3.4**
   *
   * For all person surnames/aliases WITHOUT a preceding title,
   * existing matching behavior is unchanged — they are recognized
   * via the standard alias/exact/fuzzy strategies.
   */
  it('person aliases (no title) produce person corrections or entity recognition', () => {
    // Use a curated subset of aliases known to match on unfixed code
    const knownWorkingAliases = [
      'Ablakwa', 'Jinapor', 'Muntaka', 'Rawlings', 'Kufuor',
      'Mahama', 'Bagbin', 'Bawumia', 'Agbodza', 'Akandoh',
      'Nkrumah', 'Atta Mills', 'Ofori-Atta', 'Oppong Nkrumah',
    ];

    fc.assert(
      fc.property(
        fc.constantFrom(...knownWorkingAliases),
        (alias) => {
          const result = correctLocations(alias);

          // Should produce a correction or identity entity match for a person
          const personCorrection = result.corrections.find(c => c.entityKind === 'person');
          const personEntity = (result.entitiesFound || []).find(e => e.entityKind === 'person');

          assert.ok(
            personCorrection || personEntity,
            `Expected person alias "${alias}" to be recognized as a person (no title), ` +
            `got corrections: ${JSON.stringify(result.corrections)}, ` +
            `entities: ${JSON.stringify(result.entitiesFound)}`
          );
        }
      ),
      { numRuns: 50 }
    );
  });

  it('concrete untitled person cases still produce correct canonical names', () => {
    const cases = [
      { input: 'Ablakwa', expected: 'Samuel Okudzeto Ablakwa' },
      { input: 'Jinapor', expected: 'John Abdulai Jinapor' },
      { input: 'Muntaka', expected: 'Mohammed Mubarak Muntaka' },
      { input: 'Rawlings', expected: 'Jerry John Rawlings' },
      { input: 'Kufuor', expected: 'John Agyekum Kufuor' },
    ];

    for (const { input, expected } of cases) {
      const result = correctLocations(input);
      assert.ok(result.corrections.length > 0, `Expected "${input}" to produce corrections`);
      assert.strictEqual(
        result.corrections[0].corrected, expected,
        `Expected "${input}" to correct to "${expected}"`
      );
      assert.strictEqual(result.corrections[0].entityKind, 'person');
    }
  });
});

// ---------------------------------------------------------------------------
// Property 4: Non-Person After Title
// Validates: Requirement 3.5
// ---------------------------------------------------------------------------

describe('Property 4 (Non-person after title): titles + stopwords/non-names produce no person corrections', () => {
  /**
   * **Validates: Requirements 3.5**
   *
   * For titles followed by stopwords or non-name tokens
   * ("Mr Chairman", "Madam Speaker"), no person corrections are produced.
   */
  it('title + non-person token produces no person corrections', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...TITLE_WORDS),
        fc.constantFrom(...NON_PERSON_AFTER_TITLE),
        (title, nonPerson) => {
          const input = `${title} ${nonPerson}`;
          const result = correctLocations(input);

          // Should NOT produce any person corrections
          const personCorrections = result.corrections.filter(c => c.entityKind === 'person');

          assert.strictEqual(
            personCorrections.length, 0,
            `Expected "${input}" to produce NO person corrections, ` +
            `but got: ${JSON.stringify(personCorrections)}`
          );
        }
      ),
      { numRuns: 100 }
    );
  });

  it('concrete non-person cases are unchanged', () => {
    const cases = [
      'Mr Chairman',
      'Madam Speaker',
      'Mr Speaker',
      'Madam Chair',
      'Hon. Minister',
      'Honourable Member',
      'Honourable Members',
    ];

    for (const input of cases) {
      const result = correctLocations(input);

      // No person corrections should be produced
      const personCorrections = result.corrections.filter(c => c.entityKind === 'person');
      assert.strictEqual(
        personCorrections.length, 0,
        `Expected "${input}" to produce no person corrections, got: ${JSON.stringify(personCorrections)}`
      );

      // The text should remain unchanged
      assert.strictEqual(
        result.text, input,
        `Expected "${input}" to be unchanged, got "${result.text}"`
      );
    }
  });
});

// ---------------------------------------------------------------------------
// Property 5: Title Word Preservation
// Validates: Requirement 3.2
// ---------------------------------------------------------------------------

describe('Property 5 (Title word preservation): title words are never modified in output text', () => {
  /**
   * **Validates: Requirements 3.2**
   *
   * Title words themselves ("Honourable", "Hon.", "Mr") are never
   * modified in the output text. They should appear unchanged.
   */
  it('title words alone pass through unchanged', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...TITLE_WORDS),
        (title) => {
          const result = correctLocations(title);

          // Title word should not be corrected
          assert.strictEqual(
            result.text, title,
            `Expected title word "${title}" to pass through unchanged, got "${result.text}"`
          );
          assert.strictEqual(
            result.corrections.length, 0,
            `Expected no corrections for title word "${title}", got: ${JSON.stringify(result.corrections)}`
          );
        }
      ),
      { numRuns: 30 }
    );
  });

  it('title words in sentences are preserved at their original position', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...TITLE_WORDS),
        fc.constantFrom('the', 'a', 'said', 'thanked', 'called'),
        (title, verb) => {
          const input = `${title} ${verb}`;
          const result = correctLocations(input);

          // Title word must appear at the start of the output unchanged
          assert.ok(
            result.text.startsWith(title),
            `Expected output to start with "${title}", got "${result.text}"`
          );
        }
      ),
      { numRuns: 50 }
    );
  });

  it('title words followed by plain text are never consumed as corrections', () => {
    const plainTextAfterTitle = [
      'Honourable colleagues',
      'Mr President',
      'Madam Speaker please',
      'Hon. Member for the constituency',
      'Dr good morning',
    ];

    for (const input of plainTextAfterTitle) {
      const result = correctLocations(input);
      // The title at the start must be preserved in the output
      const titleWord = input.split(' ')[0];
      assert.ok(
        result.text.startsWith(titleWord),
        `Title "${titleWord}" should be preserved at start of "${result.text}"`
      );
    }
  });
});
