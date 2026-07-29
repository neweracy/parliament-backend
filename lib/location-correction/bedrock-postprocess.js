/**
 * Bedrock LLM Post-Processing — Public Entry Point
 *
 * Uses Claude via Amazon Bedrock to refine transcript accuracy beyond
 * what the rule-based correction can achieve.
 *
 * Exports: postProcessWithBedrock, isBedrockConfigured
 */

'use strict';

const { isBedrockConfigured, invokeClaudeBedrock, MODEL_ID } = require('./bedrock/client');
const { getSystemPrompt } = require('./bedrock/prompt');
const { chunkWords, applyAlignedCorrectionsWithMap } = require('./bedrock/align');

// ---------------------------------------------------------------------------
// Main post-processing function
// ---------------------------------------------------------------------------

/**
 * Run Amazon Bedrock (Claude) LLM post-processing over the corrected transcript.
 *
 * @param {string} transcript - The rule-corrected transcript text
 * @param {Array<object>} words - The word array (mutated in place with corrections)
 * @returns {Promise<{transcript: string, words: Array<object>, bedrockCorrections: number}>}
 */
async function postProcessWithBedrock(transcript, words) {
  if (!isBedrockConfigured()) {
    return { transcript, words, bedrockCorrections: 0 };
  }

  if (!words || words.length === 0) {
    return { transcript, words, bedrockCorrections: 0 };
  }

  // Split transcript into ~300-word chunks
  const chunks = chunkWords(words, 300);
  const systemPrompt = getSystemPrompt();

  const MAX_PARALLEL = 3;
  let totalCorrections = 0;

  // Process in waves of MAX_PARALLEL concurrent calls
  for (let waveStart = 0; waveStart < chunks.length; waveStart += MAX_PARALLEL) {
    const wave = chunks.slice(waveStart, waveStart + MAX_PARALLEL);

    const promises = wave.map(async (chunk) => {
      const userMessage = `[Segment 1]: ${chunk.segment}`;
      try {
        const correctedText = await invokeClaudeBedrock(systemPrompt, userMessage);
        return { chunk, correctedText };
      } catch (err) {
        const region = process.env.AWS_REGION || 'us-east-1';
        const modelId = MODEL_ID;
        const reason = err.name === 'ResourceNotFoundException'
          ? 'model not found in region'
          : err.name === 'AccessDeniedException'
          ? 'access denied (check model access in region)'
          : err.name === 'ValidationException'
          ? 'validation error (model ID may be invalid)'
          : err.message || 'unknown';
        console.error(
          `[bedrock-postprocess] Invocation failure — region=${region}, ` +
          `model=${modelId}, reason=${reason}, error=${err.name || 'Error'}: ${err.message}`
        );
        return { chunk, correctedText: null };
      }
    });

    const results = await Promise.all(promises);

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
        console.error(
          `Bedrock chunk [${chunk.startIdx}-${chunk.endIdx}]: token count mismatch ` +
          `(original ${originalTokens.length}, corrected ${correctedTokens.length}) — using diff-based alignment`
        );
        const applied = applyAlignedCorrectionsWithMap(words, originalTokens, correctedTokens, tokenMap);
        totalCorrections += applied;
        continue;
      }

      // Token counts match — apply corrections token-by-token using tokenMap
      let tokenIdx = 0;
      while (tokenIdx < correctedTokens.length) {
        const wordIdx = tokenMap[tokenIdx];
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
};
