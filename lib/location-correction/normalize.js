/**
 * Normalization utilities for the Ghana Location Correction Engine.
 *
 * Exports: stripAll, levenshtein, phoneticKey
 */

'use strict';

/**
 * Strips all spaces, hyphens, and apostrophes — for fused-word matching.
 * "Ningo-Prampram" → "ningoprampram"
 * "Cape Coast" → "capecoast"
 */
function stripAll(s) {
  return s.replace(/[\s\-']/g, '').toLowerCase();
}

/**
 * Levenshtein distance between two strings.
 */
function levenshtein(a, b) {
  const al = a.length, bl = b.length;
  if (al === 0) return bl;
  if (bl === 0) return al;
  const row = Array.from({ length: bl + 1 }, (_, i) => i);
  for (let i = 1; i <= al; i++) {
    let prev = i;
    for (let j = 1; j <= bl; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      const val = Math.min(row[j] + 1, prev + 1, row[j - 1] + cost);
      row[j - 1] = prev;
      prev = val;
    }
    row[bl] = prev;
  }
  return row[bl];
}

/**
 * Generates a phonetic key optimized for West African/Ghanaian place names.
 * Collapses common ASR substitution patterns:
 * - double consonants → single (kk → k, ss → s)
 * - 'ph' → 'f', 'gh' → 'g' (except trailing)
 * - trailing vowels are less significant
 * - 'ei'/'ey' → 'e', 'ou'/'oo' → 'u'
 */
function phoneticKey(str) {
  let s = str.toLowerCase().replace(/[\s\-']/g, '');
  // Common substitutions in ASR
  s = s.replace(/ph/g, 'f');
  s = s.replace(/gh(?!$)/g, 'g'); // 'gh' not at end
  s = s.replace(/ck/g, 'k');
  s = s.replace(/ei|ey/g, 'e');
  s = s.replace(/ou|oo/g, 'u');
  s = s.replace(/aa/g, 'a');
  s = s.replace(/ee/g, 'e');
  s = s.replace(/ii/g, 'i');
  // Collapse double consonants
  s = s.replace(/(.)\1+/g, '$1');
  return s;
}

module.exports = { stripAll, levenshtein, phoneticKey };
