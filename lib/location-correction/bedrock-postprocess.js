/**
 * Bedrock LLM Post-Processing — uses Claude via Amazon Bedrock to refine
 * transcript accuracy beyond what the rule-based correction can achieve.
 *
 * The datasets (locations, persons, parties, MPs) are injected into the
 * system prompt as a compact reference, so Claude can make informed
 * corrections with real knowledge of valid Ghanaian entities.
 *
 * Architecture:
 *   Deepgram ASR → Rule-based correction (instant) → Bedrock LLM (1-3s)
 */

'use strict';

const {
  BedrockRuntimeClient,
  InvokeModelCommand,
} = require('@aws-sdk/client-bedrock-runtime');
const { ALL_PERSONS } = require('./persons-dataset');
const { ALL_MPS } = require('./mps-dataset');
const { ALL_PARTIES } = require('./parties-dataset');
const { getRegions } = require('ghana-locations');
const { SUPPLEMENTARY_LOCATIONS } = require('./index');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const LOW_CONFIDENCE_THRESHOLD = 0.85;
const CONTEXT_WINDOW = 30;
const MODEL_ID = process.env.BEDROCK_MODEL_ID || 'us.anthropic.claude-haiku-4-5-20251001-v1:0';
const MAX_TOKENS = 4096;

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

let _client = null;

function getClient() {
  if (_client) return _client;
  _client = new BedrockRuntimeClient({
    region: process.env.AWS_REGION || 'us-east-1',
    credentials: {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID,
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
    },
  });
  return _client;
}

function isBedrockConfigured() {
  return !!(process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY);
}

// ---------------------------------------------------------------------------
// Build compact dataset reference for the system prompt (cached)
// ---------------------------------------------------------------------------

let _datasetReference = null;

function buildDatasetReference() {
  if (_datasetReference) return _datasetReference;

  // Regions (16)
  const regions = getRegions().map(r => r.name);

  // Key cities (top cities per region, max ~80 total)
  const cities = [];
  for (const r of getRegions()) {
    cities.push(...r.cities.slice(0, 5));
  }

  // Supplementary locations (constituencies, districts)
  const suppl = SUPPLEMENTARY_LOCATIONS.map(l => l.canonical);

  // Presidents & key officials (compact: "Name (Role)")
  const officials = ALL_PERSONS.map(p => `${p.canonical} [${p.role.split('(')[0].trim()}]`);

  // Current MPs (just names — compact list, ~80 entries)
  const mps = ALL_MPS.slice(0, 80).map(mp => `${mp.name} (${mp.constituency})`);

  // Parties (name + abbreviation)
  const parties = ALL_PARTIES.map(p => `${p.canonical} (${p.abbr})`);

  _datasetReference = `
<REFERENCE_DATA>
<REGIONS>
${regions.join(', ')}
</REGIONS>

<KEY_CITIES>
${cities.join(', ')}
</KEY_CITIES>

<CONSTITUENCIES_DISTRICTS>
${suppl.join(', ')}
</CONSTITUENCIES_DISTRICTS>

<OFFICIALS>
${officials.join('\n')}
</OFFICIALS>

<CURRENT_MPS>
${mps.join('\n')}
</CURRENT_MPS>

<POLITICAL_PARTIES>
${parties.join('\n')}
</POLITICAL_PARTIES>
</REFERENCE_DATA>`;

  return _datasetReference;
}

// ---------------------------------------------------------------------------
// System prompt with dataset context
// ---------------------------------------------------------------------------

function getSystemPrompt() {
  const reference = buildDatasetReference();

  return `You are a post-processing assistant for Ghanaian parliamentary transcripts (Hansard).

Your job: Fix proper nouns in ASR output using the reference data below. The transcript has already been partially corrected by a rule-based system, but some low-confidence words remain incorrect.

${reference}

CORRECTION RULES:
1. Use the reference data above as your source of truth for valid names, locations, parties
2. Fix misspelled proper nouns to their correct form from the reference data
3. Apply proper capitalization to all names, titles, places, and party names
4. If a word sounds phonetically similar to a name in the reference data, correct it — BUT only when context strongly suggests a proper noun was intended
5. "honorable" or "hon" before a name = MP title, capitalize: "Honorable"
6. Party abbreviations (NDC, NPP, CPP) should stay as abbreviations, properly capitalized
7. Do NOT change words that are already correct
8. Do NOT add or remove words — only fix spelling/capitalization
9. Do NOT add punctuation or restructure sentences
10. Do NOT convert common English words into location/entity names. For example: "general" must NOT become "Central", "nation" must NOT become "Nanton", "several" must NOT become a location. Words like "attorney general", "general election", "in general" are everyday English phrases — leave them unchanged.
11. Do NOT convert spoken numbers into numeric year format. For example: "twenty six" must NOT become "2006", "nineteen ninety two" must NOT become "1992". Year conversion is handled by a separate system — leave all number words as-is.
12. Do NOT replace parts of a person's name with a location name. For example: "Dankwa" in "Nana Addo Dankwa Akufo-Addo" must NOT become "Tarkwa". "Dramani" in "John Dramani Mahama" must NOT become "Damongo" or "Shama". Names already corrected by the rule-based system are authoritative — preserve them.
13. Return ONLY the corrected text with [Segment N]: labels matching the input format`;
}

// ---------------------------------------------------------------------------
// Core LLM call
// ---------------------------------------------------------------------------

async function invokeClaudeBedrock(systemPrompt, userMessage) {
  const client = getClient();

  const body = JSON.stringify({
    anthropic_version: 'bedrock-2023-05-31',
    max_tokens: MAX_TOKENS,
    system: systemPrompt,
    messages: [{ role: 'user', content: userMessage }],
  });

  const command = new InvokeModelCommand({
    modelId: MODEL_ID,
    contentType: 'application/json',
    accept: 'application/json',
    body,
  });

  const response = await client.send(command);
  const responseBody = JSON.parse(new TextDecoder().decode(response.body));
  return responseBody.content?.[0]?.text ?? '';
}

// ---------------------------------------------------------------------------
// Segment extraction
// ---------------------------------------------------------------------------

function extractLowConfidenceSegments(words) {
  const segments = [];
  let i = 0;

  while (i < words.length) {
    if (words[i].confidence !== undefined && words[i].confidence < LOW_CONFIDENCE_THRESHOLD) {
      let start = i;
      let end = i;
      while (end + 1 < words.length &&
             words[end + 1].confidence !== undefined &&
             words[end + 1].confidence < LOW_CONFIDENCE_THRESHOLD) {
        end++;
      }

      const contextStart = Math.max(0, start - CONTEXT_WINDOW);
      const contextEnd = Math.min(words.length - 1, end + CONTEXT_WINDOW);

      segments.push({
        segment: words.slice(contextStart, contextEnd + 1).map(w => w.word).join(' '),
        startIdx: contextStart,
        endIdx: contextEnd,
      });

      i = end + 1;
    } else {
      i++;
    }
  }

  // Deduplicate overlapping segments
  const merged = [];
  for (const seg of segments) {
    if (merged.length > 0 && seg.startIdx <= merged[merged.length - 1].endIdx) {
      // Merge with previous
      const prev = merged[merged.length - 1];
      prev.endIdx = Math.max(prev.endIdx, seg.endIdx);
      prev.segment = words.slice(prev.startIdx, prev.endIdx + 1).map(w => w.word).join(' ');
    } else {
      merged.push({ ...seg });
    }
  }

  return merged;
}

// ---------------------------------------------------------------------------
// Main post-processing function
// ---------------------------------------------------------------------------

/**
 * Splits transcript into chunks of ~300 array entries each for Bedrock processing.
 * This ensures the entire transcript gets reviewed, not just low-confidence spots.
 *
 * Each word entry may contain multi-word text (e.g. merged person names like
 * "Samuel Okudzeto Ablakwa") so the segment text and the words[] array are
 * tracked in tandem via a tokenMap that records, for each whitespace-split
 * token position, which words[] array index it belongs to.
 */
function chunkWords(words, chunkSize = 300) {
  const chunks = [];
  for (let i = 0; i < words.length; i += chunkSize) {
    const slice = words.slice(i, i + chunkSize);
    // Build a map from each whitespace token position → words[] array index.
    // This handles entries whose .word contains spaces (multi-word merges).
    const tokenMap = [];
    for (let k = 0; k < slice.length; k++) {
      const tokenCount = (slice[k].word || '').split(/\s+/).filter(Boolean).length || 1;
      for (let t = 0; t < tokenCount; t++) {
        tokenMap.push(i + k); // absolute index in words[]
      }
    }
    chunks.push({
      segment: slice.map(w => w.word).join(' '),
      startIdx: i,
      endIdx: Math.min(i + chunkSize - 1, words.length - 1),
      tokenMap,
    });
  }
  return chunks;
}

/**
 * Aligns two word-length-mismatched sequences via LCS (longest common
 * subsequence) on lowercased tokens, then applies word-level corrections
 * only for 1:1 aligned pairs that differ (skips inserted/deleted words).
 * This prevents an entire chunk's corrections from being discarded just
 * because Claude added or dropped one stray word.
 *
 * @returns {number} count of corrections applied
 */
function applyAlignedCorrections(words, startIdx, originalWords, correctedWords) {
  const n = originalWords.length;
  const m = correctedWords.length;
  const lower = (arr) => arr.map(w => w.toLowerCase());
  const a = lower(originalWords);
  const b = lower(correctedWords);

  // Standard LCS DP table (small chunks, ~300 words — fine for O(n*m))
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Walk back through the DP table to recover aligned pairs
  let i = n, j = m;
  const pairs = []; // [originalIdx, correctedIdx]
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      pairs.push([i - 1, j - 1]);
      i--; j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i--; // original word was deleted/replaced — no direct pair
    } else {
      j--; // corrected word was inserted — no direct pair
    }
  }
  pairs.reverse();

  let applied = 0;

  // Anchor pairs are matched on lowercase text, so a pure capitalization
  // change (e.g. "ndc" -> "NDC") still counts as an anchor — apply the fix
  // directly at these points since alignment there is unambiguous.
  for (const [oi, ci] of pairs) {
    const wordIdx = startIdx + oi;
    if (wordIdx < words.length && originalWords[oi] !== correctedWords[ci]) {
      words[wordIdx] = {
        ...words[wordIdx],
        word: correctedWords[ci],
        bedrockCorrected: true,
      };
      applied++;
    }
  }

  // The words we ALSO care about are unmatched original words that sit
  // between two matched anchors with a same-sized gap of unmatched
  // corrected words — treat those as 1:1 replacements.
  let prevOrig = -1;
  let prevCorr = -1;
  const boundaries = [...pairs, [n, m]]; // sentinel end

  for (const [oi, ci] of boundaries) {
    const origGap = oi - prevOrig - 1;
    const corrGap = ci - prevCorr - 1;
    if (origGap > 0 && origGap === corrGap) {
      // Same-sized gap on both sides — safe to treat as word-for-word correction
      for (let k = 0; k < origGap; k++) {
        const origIdx = prevOrig + 1 + k;
        const corrIdx = prevCorr + 1 + k;
        const wordIdx = startIdx + origIdx;
        if (
          wordIdx < words.length &&
          originalWords[origIdx] !== correctedWords[corrIdx]
        ) {
          words[wordIdx] = {
            ...words[wordIdx],
            word: correctedWords[corrIdx],
            bedrockCorrected: true,
          };
          applied++;
        }
      }
    }
    // Gaps of different sizes mean an insertion/deletion happened there —
    // skip that span rather than risk misaligning subsequent words.
    prevOrig = oi;
    prevCorr = ci;
  }

  return applied;
}

/**
 * Token-map-aware variant of LCS alignment. Instead of using a flat startIdx
 * offset, uses the tokenMap to resolve each original token position back to
 * the correct words[] array index. This handles multi-word entries (where one
 * words[] entry expands to multiple tokens) correctly.
 *
 * When multiple corrected tokens map to the same words[] entry, they are
 * joined with a space and written as a single replacement.
 *
 * @param {Array} words        The master words[] array (mutated in place).
 * @param {string[]} originalTokens  Whitespace-split tokens from chunk.segment.
 * @param {string[]} correctedTokens Whitespace-split tokens from Bedrock response.
 * @param {number[]} tokenMap   Maps each originalTokens index → words[] array index.
 * @returns {number} count of corrections applied
 */
function applyAlignedCorrectionsWithMap(words, originalTokens, correctedTokens, tokenMap) {
  const n = originalTokens.length;
  const m = correctedTokens.length;
  const a = originalTokens.map(w => w.toLowerCase());
  const b = correctedTokens.map(w => w.toLowerCase());

  // Standard LCS DP
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Walk back to recover aligned pairs
  let i = n, j = m;
  const pairs = [];
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      pairs.push([i - 1, j - 1]);
      i--; j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }
  pairs.reverse();

  let applied = 0;

  // Apply anchor-point corrections via tokenMap
  for (const [oi, ci] of pairs) {
    if (oi < tokenMap.length) {
      const wordIdx = tokenMap[oi];
      if (wordIdx < words.length && originalTokens[oi] !== correctedTokens[ci]) {
        // Check if this token is part of a multi-token entry; if so, we need
        // to reconstruct the full multi-word value from all its tokens
        const entryStart = tokenMap.indexOf(wordIdx);
        const entryEnd = tokenMap.lastIndexOf(wordIdx);
        if (entryStart === oi && entryEnd === oi) {
          // Single-token entry — simple replacement
          words[wordIdx] = { ...words[wordIdx], word: correctedTokens[ci], bedrockCorrected: true };
          applied++;
        }
        // Multi-token entries are handled in the gap logic below
      }
    }
  }

  // Handle same-sized gaps between anchors (word-for-word corrections)
  let prevOrig = -1;
  let prevCorr = -1;
  const boundaries = [...pairs, [n, m]];

  for (const [oi, ci] of boundaries) {
    const origGap = oi - prevOrig - 1;
    const corrGap = ci - prevCorr - 1;
    if (origGap > 0 && origGap === corrGap) {
      for (let k = 0; k < origGap; k++) {
        const origIdx = prevOrig + 1 + k;
        const corrIdx = prevCorr + 1 + k;
        if (origIdx < tokenMap.length) {
          const wordIdx = tokenMap[origIdx];
          if (wordIdx < words.length && originalTokens[origIdx] !== correctedTokens[corrIdx]) {
            // For multi-token entries, only apply if this is the first token
            // of that entry to avoid writing partial replacements
            const entryStart = tokenMap.indexOf(wordIdx);
            if (origIdx === entryStart) {
              words[wordIdx] = { ...words[wordIdx], word: correctedTokens[corrIdx], bedrockCorrected: true };
              applied++;
            }
          }
        }
      }
    }
    prevOrig = oi;
    prevCorr = ci;
  }

  return applied;
}

async function postProcessWithBedrock(transcript, words) {
  if (!isBedrockConfigured()) {
    return { transcript, words, bedrockCorrections: 0 };
  }

  if (!words || words.length === 0) {
    return { transcript, words, bedrockCorrections: 0 };
  }

  // Split transcript into ~300-word chunks (larger chunks = fewer API calls)
  const chunks = chunkWords(words, 300);
  const systemPrompt = getSystemPrompt();

  // Fire ALL chunk calls in parallel (max 3 concurrent) for speed.
  // Each call processes one chunk independently.
  const MAX_PARALLEL = 3;
  let totalCorrections = 0;

  // Process in waves of MAX_PARALLEL concurrent calls
  for (let waveStart = 0; waveStart < chunks.length; waveStart += MAX_PARALLEL) {
    const wave = chunks.slice(waveStart, waveStart + MAX_PARALLEL);

    // Launch all calls in this wave simultaneously
    const promises = wave.map(async (chunk, idx) => {
      const userMessage = `[Segment 1]: ${chunk.segment}`;
      try {
        const correctedText = await invokeClaudeBedrock(systemPrompt, userMessage);
        return { chunk, correctedText, idx };
      } catch (err) {
        console.error(`Bedrock chunk ${waveStart + idx + 1} failed:`, err.message);
        return { chunk, correctedText: null, idx };
      }
    });

    // Wait for all parallel calls in this wave to complete
    const results = await Promise.all(promises);

    // Apply corrections from each result
    for (const { chunk, correctedText } of results) {
      if (!correctedText) {
        console.error(`Bedrock chunk [${chunk.startIdx}-${chunk.endIdx}]: no response text (call failed)`);
        continue;
      }

      const corrected = correctedText.replace(/^\[Segment \d+\]:\s*/, '').trim();
      const correctedTokens = corrected.split(/\s+/);
      const originalTokens = chunk.segment.split(/\s+/);
      const tokenMap = chunk.tokenMap;

      if (correctedTokens.length !== originalTokens.length) {
        // Token count mismatch — use LCS-based alignment but map back
        // through tokenMap to the correct words[] indices.
        console.error(
          `Bedrock chunk [${chunk.startIdx}-${chunk.endIdx}]: token count mismatch ` +
          `(original ${originalTokens.length}, corrected ${correctedTokens.length}) — using diff-based alignment`
        );
        // Fall back to the tokenMap-aware alignment
        const applied = applyAlignedCorrectionsWithMap(words, originalTokens, correctedTokens, tokenMap);
        totalCorrections += applied;
        continue;
      }

      // Token counts match — apply corrections token-by-token using tokenMap
      // to resolve multi-word entries back to the correct words[] index.
      // Group consecutive tokens that belong to the same words[] entry.
      let tokenIdx = 0;
      while (tokenIdx < correctedTokens.length) {
        const wordIdx = tokenMap[tokenIdx];
        // Collect all tokens belonging to this words[] entry
        let endToken = tokenIdx;
        while (endToken + 1 < correctedTokens.length && tokenMap[endToken + 1] === wordIdx) {
          endToken++;
        }
        const originalSlice = originalTokens.slice(tokenIdx, endToken + 1).join(' ');
        const correctedSlice = correctedTokens.slice(tokenIdx, endToken + 1).join(' ');
        if (wordIdx < words.length && correctedSlice !== originalSlice) {
          words[wordIdx] = {
            ...words[wordIdx],
            word: correctedSlice,
            bedrockCorrected: true,
          };
          totalCorrections++;
        }
        tokenIdx = endToken + 1;
      }
    }
  }

  const newTranscript = words.map(w => w.word).join(' ');
  return { transcript: newTranscript, words, bedrockCorrections: totalCorrections };
}

module.exports = {
  postProcessWithBedrock,
  isBedrockConfigured,
  extractLowConfidenceSegments,
  invokeClaudeBedrock,
  buildDatasetReference,
  getSystemPrompt,
  applyAlignedCorrections,
  applyAlignedCorrectionsWithMap,
  LOW_CONFIDENCE_THRESHOLD,
  MODEL_ID,
};
