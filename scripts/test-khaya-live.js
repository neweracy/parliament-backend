/**
 * Live Khaya AI transcription check (real API calls).
 *
 * Transcribes an audio file against BOTH ASR v2 and v3 using your real
 * KHAYA_API_KEY, and reports which version works. This is a manual diagnostic
 * tool — it is intentionally NOT part of the hermetic `node --test` suite,
 * because it hits the live GhanaNLP API and requires a valid key.
 *
 * Usage:
 *   node scripts/test-khaya-live.js <audioFile> [language] [--save]
 *
 * Examples:
 *   node scripts/test-khaya-live.js sample.mp3 tw
 *   node scripts/test-khaya-live.js ./test_audio/clip.mp3 tw --save
 *
 * Notes:
 *   - The API rejects large uploads (HTTP 413). Use a compressed audio file
 *     (e.g. mono 16kHz mp3), not a raw video file.
 *   - Requires KHAYA_API_KEY in your environment or .env.
 */

require("dotenv").config();
const fs = require("fs");
const path = require("path");

const VERSIONS = ["v2", "v3"];

/**
 * Loads a fresh copy of the provider bound to a specific ASR version.
 * The provider reads KHAYA_ASR_VERSION once at module load, so we clear the
 * require cache and set the env var before re-requiring.
 * @param {string} version
 * @returns {object} the khaya provider module
 */
function loadProvider(version) {
  process.env.KHAYA_ASR_VERSION = version;
  const resolved = require.resolve("../providers/khaya");
  delete require.cache[resolved];
  return require(resolved);
}

/**
 * Runs a transcription against one ASR version and prints the outcome.
 * @param {string} version
 * @param {Buffer} buffer
 * @param {string} language
 * @param {boolean} save
 */
async function runVersion(version, buffer, language, save) {
  const khaya = loadProvider(version);
  console.log(`\n=== ASR ${version} (language=${language}) ===`);
  const started = Date.now();
  try {
    const result = await khaya.transcribe(buffer, "audio/mpeg", language);
    const secs = ((Date.now() - started) / 1000).toFixed(1);
    const transcript = result.transcript || "";
    console.log(`✅ SUCCESS in ${secs}s — ${transcript.length} chars`);
    console.log(`   Preview: ${transcript.slice(0, 160)}${transcript.length > 160 ? "…" : ""}`);
    if (save) {
      const outDir = path.join(process.cwd(), "transcripts");
      fs.mkdirSync(outDir, { recursive: true });
      const outFile = path.join(outDir, `live.${version}.txt`);
      fs.writeFileSync(outFile, transcript, "utf-8");
      console.log(`   Saved to ${path.relative(process.cwd(), outFile)}`);
    }
    return { version, ok: true };
  } catch (err) {
    const secs = ((Date.now() - started) / 1000).toFixed(1);
    console.log(`❌ FAILED after ${secs}s — ${err.statusCode || "?"} ${err.code || ""}: ${err.message}`);
    return { version, ok: false, statusCode: err.statusCode, code: err.code };
  }
}

async function main() {
  const args = process.argv.slice(2);
  const save = args.includes("--save");
  const positional = args.filter((a) => !a.startsWith("--"));
  const audioFile = positional[0];
  const language = positional[1] || "tw";

  if (!audioFile) {
    console.error("Usage: node scripts/test-khaya-live.js <audioFile> [language] [--save]");
    process.exit(1);
  }
  if (!process.env.KHAYA_API_KEY) {
    console.error("KHAYA_API_KEY is not set. Add it to your .env or environment.");
    process.exit(1);
  }
  if (!fs.existsSync(audioFile)) {
    console.error(`Audio file not found: ${audioFile}`);
    process.exit(1);
  }

  const buffer = fs.readFileSync(audioFile);
  console.log(`File: ${audioFile} (${(buffer.length / 1024 / 1024).toFixed(2)} MB)`);

  const results = [];
  for (const version of VERSIONS) {
    results.push(await runVersion(version, buffer, language, save));
  }

  console.log("\n=== Summary ===");
  for (const r of results) {
    console.log(`  ${r.version}: ${r.ok ? "working" : `not working (${r.statusCode || "?"} ${r.code || ""})`}`);
  }
}

main().catch((err) => {
  console.error("Unexpected error:", err);
  process.exit(1);
});
