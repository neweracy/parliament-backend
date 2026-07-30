'use strict';

/**
 * Benchmark_Harness driver.
 *
 * Usage:
 *   node bench/harness.js                  — measure and compare against baseline
 *   node bench/harness.js --record-baseline — append a new pre-refactor entry
 *
 * Measures:
 *   - JS rule-stage correction at 100/1000/10000 words (median-of-5)
 *   - PY rule-stage correction at 100/1000/10000 words (via child_process)
 *   - PY Match_Index build (via child_process)
 *   - Frontend bundle gzip sizes (optional — skipped if frontend/ missing)
 *
 * Results are stored in bench/results/baseline.json as an append-only
 * measurements array.
 *
 * @module bench/harness
 */

const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { execSync, spawnSync } = require('node:child_process');
const { getMachineIdentity } = require('./machine');

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RESULTS_DIR = path.join(__dirname, 'results');
const RESULTS_FILE = path.join(RESULTS_DIR, 'baseline.json');
const WORD_COUNTS = [100, 1000, 10000];
const WARM_UP_RUNS = 2;
const MEASURED_RUNS = 5;

// Threshold constants for comparison
const RULE_STAGE_REGRESSION_FAIL_PERCENT = 10;
const FRONTEND_CHUNK_REPORT_PERCENT = 5;

// Absolute budget proposals (reporting only, not assertions)
const PROPOSED_RULE_STAGE_MS = 500;
const PROPOSED_INDEX_BUILD_MS = 3000;
const PROPOSED_ENTRY_CHUNK_BYTES = 250 * 1024;

// ---------------------------------------------------------------------------
// Seeded Transcript Generator
// ---------------------------------------------------------------------------

/**
 * Mulberry32 PRNG — deterministic 32-bit generator.
 * @param {number} seed
 * @returns {function(): number} Returns values in [0, 1)
 */
function mulberry32(seed) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Word pools matching the 70/25/5 distribution from test_performance.py
const SHORT_WORDS = [
  'the', 'of', 'and', 'to', 'in', 'a', 'is', 'it', 'was', 'for',
  'on', 'are', 'be', 'at', 'one', 'or', 'had', 'not', 'but', 'all',
  'we', 'do', 'how', 'so', 'an', 'if', 'no', 'up', 'out', 'its',
  'my', 'he', 'as', 'by', 'go', 'mr', 'us', 'has', 'him', 'her',
  'may', 'did', 'you', 'our', 'she', 'can', 'sir', 'two', 'new',
  'now', 'say', 'get', 'let', 'see', 'per', 'set', 'own', 'put',
];

const MEDIUM_WORDS = [
  'this', 'from', 'that', 'with', 'have', 'been', 'were', 'will',
  'also', 'more', 'very', 'some', 'when', 'them', 'than', 'made',
  'time', 'year', 'what', 'much', 'like', 'said', 'just', 'only',
  'most', 'such', 'take', 'many', 'must', 'over', 'make', 'each',
  'come', 'last', 'long', 'same', 'well', 'back', 'even', 'good',
  'give', 'work', 'call', 'need', 'want', 'look', 'help', 'here',
];

const ENTITY_ADJACENT = [
  'house', 'chair', 'right', 'order', 'point', 'state', 'local',
  'paper', 'water', 'issue', 'floor', 'voice', 'clear', 'place',
  'after', 'under', 'about', 'above', 'bring', 'whole', 'eight',
];

/**
 * Generate a fixed-seed synthetic transcript of the given word count.
 * Reproduces the 70/25/5 token distribution (common/medium/entity-adjacent).
 *
 * @param {number} wordCount
 * @param {number} [seed=42]
 * @returns {{ transcript: string, words: Array<{word: string, start: number, end: number, confidence: number}> }}
 */
function generateTranscript(wordCount, seed = 42) {
  const rng = mulberry32(seed);
  const wordsList = [];

  for (let i = 0; i < wordCount; i++) {
    const r = rng();
    if (r < 0.70) {
      wordsList.push(SHORT_WORDS[Math.floor(rng() * SHORT_WORDS.length)]);
    } else if (r < 0.95) {
      wordsList.push(MEDIUM_WORDS[Math.floor(rng() * MEDIUM_WORDS.length)]);
    } else {
      wordsList.push(ENTITY_ADJACENT[Math.floor(rng() * ENTITY_ADJACENT.length)]);
    }
  }

  const words = [];
  let pos = 0;
  for (const w of wordsList) {
    const duration = 0.2 + rng() * 0.3;
    words.push({
      word: w,
      start: Number(pos.toFixed(3)),
      end: Number((pos + duration).toFixed(3)),
      confidence: Number((0.5 + rng() * 0.5).toFixed(3)),
    });
    pos += duration + 0.05;
  }

  return { transcript: wordsList.join(' '), words };
}

// ---------------------------------------------------------------------------
// Timing helpers
// ---------------------------------------------------------------------------

/**
 * Measure a synchronous function using the median-of-5 protocol.
 * 2 discarded warm-ups, 5 measured runs, median is element 2 of sorted 5.
 *
 * @param {function(): void} fn
 * @returns {{ runs_ms: number[], median_ms: number }}
 */
function medianOf5(fn) {
  // Warm-up runs (discarded)
  for (let i = 0; i < WARM_UP_RUNS; i++) {
    fn();
  }

  // Measured runs
  const runs = [];
  for (let i = 0; i < MEASURED_RUNS; i++) {
    const start = process.hrtime.bigint();
    fn();
    const end = process.hrtime.bigint();
    const elapsedMs = Number(end - start) / 1_000_000;
    runs.push(Number(elapsedMs.toFixed(4)));
  }

  const sorted = [...runs].sort((a, b) => a - b);
  const median = sorted[2]; // Element 2 of sorted 5

  return { runs_ms: runs, median_ms: median };
}

// ---------------------------------------------------------------------------
// JS Rule-Stage Benchmark
// ---------------------------------------------------------------------------

/**
 * Benchmark the JS_Correction_Engine at the specified word counts.
 *
 * @returns {Object} Map of word count → { runs_ms, median_ms }
 */
function benchJsRuleStage() {
  const { correctLocations } = require('../lib/location-correction/index');
  const results = {};

  for (const wc of WORD_COUNTS) {
    const { transcript } = generateTranscript(wc);
    const measurement = medianOf5(() => {
      correctLocations(transcript);
    });
    results[String(wc)] = measurement;
  }

  return results;
}

// ---------------------------------------------------------------------------
// Python Script Invocation
// ---------------------------------------------------------------------------

/**
 * Invoke a Python bench script and parse its JSON stdout.
 * On failure or invalid JSON, returns { unavailable: true, error: string }.
 *
 * @param {string} scriptPath - Relative path to the Python script
 * @returns {Object}
 */
function invokePythonBench(scriptPath) {
  const fullPath = path.resolve(__dirname, '..', scriptPath);

  try {
    const result = spawnSync('python3', [fullPath], {
      encoding: 'utf8',
      timeout: 120_000,
      cwd: path.resolve(__dirname, '..'),
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    if (result.status !== 0) {
      return {
        unavailable: true,
        error: `Exit code ${result.status}: ${(result.stderr || '').trim()}`,
      };
    }

    const stdout = (result.stdout || '').trim();
    try {
      return JSON.parse(stdout);
    } catch {
      return {
        unavailable: true,
        error: `Invalid JSON output: ${stdout.slice(0, 200)}`,
      };
    }
  } catch (err) {
    return {
      unavailable: true,
      error: `Spawn failed: ${err.message}`,
    };
  }
}

// ---------------------------------------------------------------------------
// Frontend Bundle Measurement
// ---------------------------------------------------------------------------

/**
 * Build the frontend and measure chunk sizes.
 * Returns null if frontend/ doesn't exist or build fails (recorded as unavailable).
 *
 * @returns {Object|null}
 */
function measureFrontendBundles() {
  const frontendDir = path.resolve(__dirname, '..', 'frontend');

  if (!fs.existsSync(frontendDir)) {
    return { unavailable: true, error: 'frontend/ directory not found' };
  }

  // Run the build
  try {
    execSync('pnpm build', {
      cwd: frontendDir,
      encoding: 'utf8',
      timeout: 120_000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  } catch (err) {
    const output = (err.stdout || '') + '\n' + (err.stderr || '');
    return { unavailable: true, error: `Build failed: ${output.trim().slice(0, 500)}` };
  }

  const distDir = path.join(frontendDir, 'dist');
  const assetsDir = path.join(distDir, 'assets');
  const indexHtml = path.join(distDir, 'index.html');

  if (!fs.existsSync(assetsDir)) {
    return { unavailable: true, error: 'frontend/dist/assets/ not found after build' };
  }

  // Parse index.html to find entry chunk
  let entryChunkPath = null;
  if (fs.existsSync(indexHtml)) {
    const html = fs.readFileSync(indexHtml, 'utf8');
    const scriptMatch = html.match(/<script\s+type="module"[^>]*\ssrc="([^"]+)"/);
    if (scriptMatch) {
      entryChunkPath = scriptMatch[1].replace(/^\//, '');
    }
  }

  // Read page source files to identify route chunks
  const pagesDir = path.join(frontendDir, 'src', 'pages');
  const pageNames = [];
  if (fs.existsSync(pagesDir)) {
    for (const f of fs.readdirSync(pagesDir)) {
      const name = f.replace(/\.(tsx?|jsx?)$/, '').toLowerCase();
      if (name !== f) pageNames.push(name);
    }
  }

  // Measure all files in assets/
  const allChunks = [];
  const files = fs.readdirSync(assetsDir);

  for (const file of files) {
    const filePath = path.join(assetsDir, file);
    const stat = fs.statSync(filePath);
    if (!stat.isFile()) continue;

    const content = fs.readFileSync(filePath);
    const rawBytes = content.length;
    const gzipBytes = zlib.gzipSync(content, { level: 6 }).length;
    const relativePath = `assets/${file}`;

    allChunks.push({
      path: relativePath,
      raw_bytes: rawBytes,
      gzip_bytes: gzipBytes,
    });
  }

  // Classify roles
  let entry = null;
  const routes = [];

  for (const chunk of allChunks) {
    if (entryChunkPath && chunk.path === entryChunkPath) {
      entry = chunk;
    } else {
      // Check if it matches a route/page name
      const lowerPath = chunk.path.toLowerCase();
      const isRoute = pageNames.some((p) => lowerPath.includes(p));
      if (isRoute && lowerPath.endsWith('.js')) {
        routes.push({ ...chunk, role: 'route' });
      }
    }
  }

  return {
    entry: entry || { unavailable: true, error: 'Could not identify entry chunk' },
    routes,
    all_chunks: allChunks,
  };
}

// ---------------------------------------------------------------------------
// Results file management
// ---------------------------------------------------------------------------

/**
 * Load the current results file, or create a new one.
 * @returns {Object}
 */
function loadResults() {
  if (fs.existsSync(RESULTS_FILE)) {
    return JSON.parse(fs.readFileSync(RESULTS_FILE, 'utf8'));
  }
  return { schema_version: '1.0.0', measurements: [] };
}

/**
 * Save results to disk (append-only: never removes entries).
 * @param {Object} data
 */
function saveResults(data) {
  if (!fs.existsSync(RESULTS_DIR)) {
    fs.mkdirSync(RESULTS_DIR, { recursive: true });
  }
  fs.writeFileSync(RESULTS_FILE, JSON.stringify(data, null, 2) + '\n');
}

/**
 * Get the current git commit short hash.
 * @returns {string}
 */
function getGitCommit() {
  try {
    return execSync('git rev-parse --short HEAD', {
      encoding: 'utf8',
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    }).trim();
  } catch {
    return 'unknown';
  }
}

// ---------------------------------------------------------------------------
// Threshold comparison and reporting
// ---------------------------------------------------------------------------

/**
 * Compare a current measurement against a baseline entry.
 * Returns { passed: boolean, reports: string[] }
 *
 * @param {Object} current - Current measurement
 * @param {Object} baseline - Baseline measurement to compare against
 * @returns {{ passed: boolean, reports: string[] }}
 */
function compareResults(current, baseline) {
  const reports = [];
  let passed = true;

  // --- Rule-stage regression check (JS) ---
  if (current.js_rule_stage && baseline.js_rule_stage) {
    for (const wc of WORD_COUNTS) {
      const key = String(wc);
      const curMedian = current.js_rule_stage[key]?.median_ms;
      const baseMedian = baseline.js_rule_stage[key]?.median_ms;

      // eslint-disable-next-line eqeqeq
      if (curMedian != null && baseMedian != null && baseMedian > 0) {
        const pctChange = ((curMedian - baseMedian) / baseMedian) * 100;

        if (pctChange > RULE_STAGE_REGRESSION_FAIL_PERCENT) {
          reports.push(
            `FAIL: JS rule-stage ${wc}w regressed by ${pctChange.toFixed(1)}% ` +
            `(${baseMedian.toFixed(2)}ms → ${curMedian.toFixed(2)}ms, threshold: ${RULE_STAGE_REGRESSION_FAIL_PERCENT}%)`
          );
          passed = false;
        } else if (pctChange > 0) {
          reports.push(
            `WARN: JS rule-stage ${wc}w slower by ${pctChange.toFixed(1)}% ` +
            `(${baseMedian.toFixed(2)}ms → ${curMedian.toFixed(2)}ms)`
          );
        } else {
          reports.push(
            `OK: JS rule-stage ${wc}w: ${curMedian.toFixed(2)}ms ` +
            `(baseline: ${baseMedian.toFixed(2)}ms, ${pctChange.toFixed(1)}%)`
          );
        }
      }
    }
  }

  // --- Rule-stage regression check (PY) ---
  if (current.py_rule_stage && !current.py_rule_stage.unavailable &&
      baseline.py_rule_stage && !baseline.py_rule_stage.unavailable) {
    for (const wc of WORD_COUNTS) {
      const key = String(wc);
      const curMedian = current.py_rule_stage[key]?.median_ms;
      const baseMedian = baseline.py_rule_stage[key]?.median_ms;

      // eslint-disable-next-line eqeqeq
      if (curMedian != null && baseMedian != null && baseMedian > 0) {
        const pctChange = ((curMedian - baseMedian) / baseMedian) * 100;

        if (pctChange > RULE_STAGE_REGRESSION_FAIL_PERCENT) {
          reports.push(
            `FAIL: PY rule-stage ${wc}w regressed by ${pctChange.toFixed(1)}% ` +
            `(${baseMedian.toFixed(2)}ms → ${curMedian.toFixed(2)}ms, threshold: ${RULE_STAGE_REGRESSION_FAIL_PERCENT}%)`
          );
          passed = false;
        } else if (pctChange > 0) {
          reports.push(
            `WARN: PY rule-stage ${wc}w slower by ${pctChange.toFixed(1)}% ` +
            `(${baseMedian.toFixed(2)}ms → ${curMedian.toFixed(2)}ms)`
          );
        }
      }
    }
  }

  // --- Frontend chunk regression check ---
  if (current.frontend_chunks && !current.frontend_chunks.unavailable &&
      baseline.frontend_chunks && !baseline.frontend_chunks.unavailable) {
    const curEntry = current.frontend_chunks.entry;
    const baseEntry = baseline.frontend_chunks.entry;

    if (curEntry && !curEntry.unavailable && baseEntry && !baseEntry.unavailable) {
      const pctChange = ((curEntry.gzip_bytes - baseEntry.gzip_bytes) / baseEntry.gzip_bytes) * 100;

      if (pctChange > FRONTEND_CHUNK_REPORT_PERCENT) {
        reports.push(
          `REPORT: Entry chunk gzip grew by ${pctChange.toFixed(1)}% ` +
          `(${baseEntry.gzip_bytes} → ${curEntry.gzip_bytes} bytes, threshold: ${FRONTEND_CHUNK_REPORT_PERCENT}%)`
        );
      }
    }
  }

  // --- Absolute budget proposals (reporting only, never fail) ---
  if (current.js_rule_stage) {
    const median10k = current.js_rule_stage['10000']?.median_ms;
    // eslint-disable-next-line eqeqeq
    if (median10k != null) {
      if (median10k > PROPOSED_RULE_STAGE_MS) {
        reports.push(
          `PROPOSAL: JS rule-stage 10000w (${median10k.toFixed(2)}ms) exceeds proposed 500ms budget`
        );
      } else {
        reports.push(
          `PROPOSAL: JS rule-stage 10000w (${median10k.toFixed(2)}ms) within proposed 500ms budget ✓`
        );
      }
    }
  }

  if (current.py_index_build && !current.py_index_build.unavailable) {
    const median = current.py_index_build.median_ms;
    // eslint-disable-next-line eqeqeq
    if (median != null) {
      if (median > PROPOSED_INDEX_BUILD_MS) {
        reports.push(
          `PROPOSAL: Match_Index build (${median.toFixed(2)}ms) exceeds proposed 3000ms budget`
        );
      } else {
        reports.push(
          `PROPOSAL: Match_Index build (${median.toFixed(2)}ms) within proposed 3000ms budget ✓`
        );
      }
    }
  }

  if (current.frontend_chunks && !current.frontend_chunks.unavailable) {
    const entry = current.frontend_chunks.entry;
    if (entry && !entry.unavailable) {
      if (entry.gzip_bytes > PROPOSED_ENTRY_CHUNK_BYTES) {
        reports.push(
          `PROPOSAL: Entry chunk gzip (${entry.gzip_bytes} bytes) exceeds proposed 250KB budget`
        );
      } else {
        reports.push(
          `PROPOSAL: Entry chunk gzip (${entry.gzip_bytes} bytes) within proposed 250KB budget ✓`
        );
      }
    }
  }

  return { passed, reports };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Stubbed-Provider Call Counting (--count-calls mode)
// ---------------------------------------------------------------------------

/**
 * Async version of call counting that properly awaits Bedrock stubs.
 * Runs each Golden_Corpus fixture through the JS pipeline with stubbed
 * providers, counting Bedrock and Khaya invocations per fixture.
 *
 * @returns {Promise<Array<{fixture_id: string, bedrock_invocations: number, khaya_calls: number}>>}
 */
async function countProviderCallsAsync() {
  const { loadCorpus } = require('../fixtures/golden-corpus/loader');
  const { setClient } = require('../lib/location-correction/bedrock/client');
  const { postProcessWithBedrock } = require('../lib/location-correction/bedrock-postprocess');

  const corpus = loadCorpus();
  const results = [];

  for (const fixture of corpus) {
    let bedrockInvocations = 0;

    // Inject a counting stub via the bedrock/client.js seam
    const stubClient = {
      send: async (command) => {
        bedrockInvocations++;
        // Echo input back as unchanged text — mimics a no-op LLM response
        const bodyStr = typeof command.input?.body === 'string'
          ? command.input.body
          : new TextDecoder().decode(command.input?.body || new Uint8Array());
        let userMessage = '';
        try {
          const parsed = JSON.parse(bodyStr);
          userMessage = parsed.messages?.[0]?.content || '';
        } catch {
          userMessage = fixture.input_transcript;
        }
        const responseBody = JSON.stringify({
          content: [{ type: 'text', text: userMessage }],
        });
        return { body: new TextEncoder().encode(responseBody) };
      },
    };
    setClient(stubClient);

    // Simulate Bedrock being configured
    const origAccessKey = process.env.AWS_ACCESS_KEY_ID;
    const origSecretKey = process.env.AWS_SECRET_ACCESS_KEY;
    process.env.AWS_ACCESS_KEY_ID = 'stub-key';
    process.env.AWS_SECRET_ACCESS_KEY = 'stub-secret';

    try {
      await postProcessWithBedrock(fixture.input_transcript, fixture.input_words);
    } catch {
      // Non-fatal — count whatever was invoked before failure
    }

    // Restore env
    if (origAccessKey === undefined) {
      delete process.env.AWS_ACCESS_KEY_ID;
    } else {
      process.env.AWS_ACCESS_KEY_ID = origAccessKey;
    }
    if (origSecretKey === undefined) {
      delete process.env.AWS_SECRET_ACCESS_KEY;
    } else {
      process.env.AWS_SECRET_ACCESS_KEY = origSecretKey;
    }

    // Khaya calls: 0 in the JS pipeline (Khaya is only in hybrid route)
    results.push({
      fixture_id: fixture.id,
      bedrock_invocations: bedrockInvocations,
      khaya_calls: 0,
    });
  }

  // Reset client
  setClient(null);

  return results;
}

function main() {
  const args = process.argv.slice(2);
  const recordBaseline = args.includes('--record-baseline');
  const countCalls = args.includes('--count-calls');

  // --- Count-calls mode ---
  if (countCalls) {
    console.log('Counting provider calls per Golden_Corpus fixture...');
    countProviderCallsAsync().then((callResults) => {
      console.log('');
      console.log('Provider call counts per fixture:');
      for (const r of callResults) {
        console.log(`  ${r.fixture_id}: bedrock=${r.bedrock_invocations}, khaya=${r.khaya_calls}`);
      }

      // Persist into baseline.json
      const results = loadResults();
      const callEntry = {
        recorded_at: new Date().toISOString(),
        git_commit: getGitCommit(),
        call_counts: callResults,
      };

      // Append or update the call_budgets section
      if (!results.call_budgets) {
        results.call_budgets = [];
      }
      results.call_budgets.push(callEntry);
      saveResults(results);
      console.log(`\nCall counts recorded in ${RESULTS_FILE}`);
    }).catch((err) => {
      console.error('Call counting failed:', err.message);
      process.exit(1);
    });
    return;
  }

  const machine = getMachineIdentity();
  console.log(`Machine: ${machine.cpu_model} (${machine.cores} cores, ${(machine.total_memory_bytes / (1024 ** 3)).toFixed(1)} GB)`);
  console.log(`Machine ID: ${machine.id.slice(0, 12)}...`);
  console.log(`Node: ${machine.node_version}, Python: ${machine.python_version}`);
  console.log('');

  // --- Measure JS rule-stage ---
  console.log('Benchmarking JS rule-stage...');
  const jsRuleStage = benchJsRuleStage();
  for (const wc of WORD_COUNTS) {
    console.log(`  ${wc}w: median ${jsRuleStage[String(wc)].median_ms.toFixed(2)}ms`);
  }

  // --- Measure PY rule-stage ---
  console.log('Benchmarking PY rule-stage...');
  const pyRuleStage = invokePythonBench('services/postprocess/scripts/bench_rule_stage.py');
  if (pyRuleStage.unavailable) {
    console.log(`  Unavailable: ${pyRuleStage.error}`);
  } else {
    for (const wc of WORD_COUNTS) {
      const data = pyRuleStage[String(wc)];
      if (data) {
        console.log(`  ${wc}w: median ${data.median_ms.toFixed(2)}ms`);
      }
    }
  }

  // --- Measure PY index build ---
  console.log('Benchmarking PY Match_Index build...');
  const pyIndexBuild = invokePythonBench('services/postprocess/scripts/bench_index_build.py');
  if (pyIndexBuild.unavailable) {
    console.log(`  Unavailable: ${pyIndexBuild.error}`);
  } else {
    console.log(`  median ${pyIndexBuild.median_ms.toFixed(2)}ms (${pyIndexBuild.record_count} records)`);
  }

  // --- Measure frontend bundles ---
  console.log('Measuring frontend bundles...');
  const frontendChunks = measureFrontendBundles();
  if (frontendChunks.unavailable) {
    console.log(`  Unavailable: ${frontendChunks.error}`);
  } else if (frontendChunks.entry && !frontendChunks.entry.unavailable) {
    console.log(`  Entry chunk: ${frontendChunks.entry.gzip_bytes} bytes gzipped (${frontendChunks.entry.path})`);
    console.log(`  Route chunks: ${frontendChunks.routes.length}`);
  }

  console.log('');

  // --- Build measurement entry ---
  const measurement = {
    phase: recordBaseline ? 'pre-refactor' : 'post-refactor',
    recorded_at: new Date().toISOString(),
    git_commit: getGitCommit(),
    machine,
    js_rule_stage: jsRuleStage,
    py_rule_stage: pyRuleStage,
    py_index_build: pyIndexBuild,
    frontend_chunks: frontendChunks,
  };

  if (recordBaseline) {
    // --- Record baseline mode ---
    const results = loadResults();
    results.measurements.push(measurement);
    saveResults(results);
    console.log(`Baseline recorded in ${RESULTS_FILE}`);
    console.log(`Phase: pre-refactor, commit: ${measurement.git_commit}`);
    process.exit(0);
  }

  // --- Compare mode ---
  const results = loadResults();

  // Find a pre-refactor entry for this machine
  const baseline = results.measurements.find(
    (m) => m.phase === 'pre-refactor' && m.machine?.id === machine.id
  );

  if (!baseline) {
    console.log('No comparable baseline on this machine.');
    console.log(`Machine ID: ${machine.id}`);
    console.log('Run with --record-baseline to create one.');
    process.exit(0);
  }

  console.log(`Comparing against baseline from ${baseline.recorded_at} (commit: ${baseline.git_commit})`);
  console.log('');

  const { passed, reports } = compareResults(measurement, baseline);

  for (const r of reports) {
    console.log(`  ${r}`);
  }

  console.log('');
  if (passed) {
    console.log('All regression checks passed.');
  } else {
    console.log('REGRESSION DETECTED — one or more checks failed.');
    process.exit(1);
  }
}

main();
