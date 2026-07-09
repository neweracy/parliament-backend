/**
 * Live Hybrid confidence-transcription check (real API calls).
 *
 * Runs the REAL hybrid pipeline end-to-end against an audio file:
 * Deepgram (Primary_Engine) → low-confidence detection → ffmpeg slicing →
 * Khaya (Correction_Engine) Language_Race → reassembly, and asserts the
 * returned Unified_Transcript carries a populated `correctionStats` block.
 *
 * This is a manual diagnostic tool — it is intentionally NOT part of the
 * hermetic `node --test` suite (`pnpm test`), because it hits the live
 * Deepgram + Khaya APIs and shells out to the bundled ffmpeg binary.
 *
 * Usage:
 *   node scripts/test-hybrid-live.js [audioFile] [--save]
 *
 * Examples:
 *   node scripts/test-hybrid-live.js
 *   node scripts/test-hybrid-live.js ./test_audio/clip.mp3
 *   node scripts/test-hybrid-live.js ./test_audio/clip.mp3 --save
 *
 * Notes:
 *   - Requires BOTH DEEPGRAM_API_KEY and KHAYA_API_KEY in your environment
 *     or .env.
 *   - Defaults to test_audio/25-must-know-twi-phrases.mp3 when no file is given.
 *   - Pass --save to write the full Unified_Transcript to transcripts/.
 */

require("dotenv").config();
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");

const { createClient } = require("@deepgram/sdk");
const { runHybridPipeline } = require("../lib/hybrid/pipeline");
const { loadHybridConfig } = require("../lib/hybrid/config");
const { sliceAndConcatAudio } = require("../lib/hybrid/audio-slicer");
const khaya = require("../providers/khaya");

const DEFAULT_AUDIO_FILE = "test_audio/25-must-know-twi-phrases.mp3";

/**
 * Verifies the pipeline result is a valid Unified_Transcript with populated
 * correction statistics. Throws an AssertionError on the first mismatch.
 * @param {object} result
 */
function verifyUnifiedTranscript(result) {
  assert.ok(result && typeof result === "object", "result must be an object");
  assert.strictEqual(typeof result.transcript, "string", "result.transcript must be a string");
  assert.ok(Array.isArray(result.segments), "result.segments must be an array");
  assert.ok(result.metadata && typeof result.metadata === "object", "result.metadata must be an object");

  const stats = result.metadata.correctionStats;
  assert.ok(stats && typeof stats === "object", "metadata.correctionStats must exist");
  assert.strictEqual(typeof stats.segmentsDetected, "number", "correctionStats.segmentsDetected must be numeric");
  assert.strictEqual(typeof stats.corrected, "boolean", "correctionStats.corrected must be boolean");
  assert.strictEqual(typeof stats.correctionSkipped, "boolean", "correctionStats.correctionSkipped must be boolean");
}

async function main() {
  const args = process.argv.slice(2);
  const save = args.includes("--save");
  const positional = args.filter((a) => !a.startsWith("--"));
  const audioFile = positional[0] || DEFAULT_AUDIO_FILE;

  if (!process.env.DEEPGRAM_API_KEY) {
    console.error("DEEPGRAM_API_KEY is not set. Add it to your .env or environment.");
    process.exitCode = 1;
    return;
  }
  if (!process.env.KHAYA_API_KEY) {
    console.error("KHAYA_API_KEY is not set. Add it to your .env or environment.");
    process.exitCode = 1;
    return;
  }
  if (!fs.existsSync(audioFile)) {
    console.error(`Audio file not found: ${audioFile}`);
    process.exitCode = 1;
    return;
  }

  const buffer = fs.readFileSync(audioFile);
  console.log(`File: ${audioFile} (${(buffer.length / 1024 / 1024).toFixed(2)} MB)`);

  // Build the REAL deps object, mirroring how server.js wires the pipeline.
  const deepgram = createClient(process.env.DEEPGRAM_API_KEY);
  const deps = {
    transcribePrimary: async ({ buffer: buf, mimetype }) =>
      deepgram.listen.prerecorded.transcribeFile(buf, {
        model: "nova-3",
        mimetype,
        punctuate: true,
      }),
    khayaTranscribe: (buf, mime, lang) => khaya.transcribe(buf, mime, lang),
    sliceAndConcatAudio: (buf, ranges) => sliceAndConcatAudio(buf, ranges),
    khayaConfigured: () => Boolean(khaya.getApiKey()),
  };

  const config = loadHybridConfig();
  console.log("Config:", config);

  console.log("\nRunning hybrid pipeline (live Deepgram + Khaya)…");
  const started = Date.now();
  let result;
  try {
    result = await runHybridPipeline({ buffer, mimetype: "audio/mpeg" }, deps, config);
  } catch (err) {
    console.error(`\n❌ Pipeline failed: ${err.type || "Error"} ${err.code || ""}: ${err.message}`);
    process.exitCode = 1;
    return;
  }
  const secs = ((Date.now() - started) / 1000).toFixed(1);

  try {
    verifyUnifiedTranscript(result);
  } catch (err) {
    console.error(`\n❌ Result is not a valid Unified_Transcript: ${err.message}`);
    process.exitCode = 1;
    return;
  }

  const transcript = result.transcript || "";
  const stats = result.metadata.correctionStats;

  console.log(`\n✅ SUCCESS in ${secs}s`);
  console.log(`   Transcript preview: ${transcript.slice(0, 200)}${transcript.length > 200 ? "…" : ""}`);
  console.log(`   Segments: ${result.segments.length}`);
  console.log(`   Corrected: ${stats.corrected} (language: ${stats.language || "none"})`);
  if (stats.correctionSkipped) {
    console.log(
      "\n⚠️  Correction was SKIPPED — every Khaya language call failed or returned empty.\n" +
      "   The transcript above is the raw Deepgram (primary) output with no Ghanaian-language\n" +
      "   correction. Common cause: Khaya call-volume quota exhausted (HTTP 403/429)."
    );
  }
  console.log("\n=== correctionStats ===");
  console.log(JSON.stringify(stats, null, 2));
  console.log("\n=== resolved config ===");
  console.log(JSON.stringify(result.metadata.config, null, 2));

  if (save) {
    const outDir = path.join(process.cwd(), "transcripts");
    fs.mkdirSync(outDir, { recursive: true });
    const base = path.basename(audioFile, path.extname(audioFile));
    const outFile = path.join(outDir, `hybrid.${base}.txt`);
    fs.writeFileSync(outFile, transcript, "utf-8");
    console.log(`\n   Saved transcript to ${path.relative(process.cwd(), outFile)}`);
  }
}

main().catch((err) => {
  console.error("Unexpected error:", err);
  process.exitCode = 1;
});
