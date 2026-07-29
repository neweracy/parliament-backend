'use strict';

/**
 * Record/Compare Separation Guard
 *
 * Asserts that no test file (JS or Python) imports the record-mode modules
 * or writes to `fixtures/golden-corpus/recorded/`. This is the mechanical
 * enforcement of Requirement 1.10: recorded outputs change only in a deliberate
 * record-mode invocation, never as a side-effect of running the test suite.
 *
 * Validates: Requirements 1.10
 *
 * @module test/consistency/baseline-integrity
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const ROOT = path.resolve(__dirname, '../..');
const JS_TEST_DIR = path.join(ROOT, 'test');
const PY_TEST_DIR = path.join(ROOT, 'services', 'postprocess', 'tests');

// ---------------------------------------------------------------------------
// Forbidden patterns
// ---------------------------------------------------------------------------

/**
 * JS forbidden import patterns — record-mode modules.
 * Matches require('...record.js') or require("...record.js") or
 * require('...record') without extension, and the record_baseline module.
 */
const JS_FORBIDDEN_IMPORTS = [
  /require\s*\(\s*['"][^'"]*record\.js['"]\s*\)/,
  /require\s*\(\s*['"][^'"]*record_baseline['"]\s*\)/,
  /require\s*\(\s*['"][^'"]*\/record['"]\s*\)/,
];

/**
 * JS forbidden write patterns — writing to golden-corpus/recorded/.
 */
const JS_FORBIDDEN_WRITES = [
  /(?:writeFileSync|writeFile)\s*\([^)]*golden-corpus\/recorded/,
  /fs\.write\s*\([^)]*golden-corpus\/recorded/,
  /golden-corpus\/recorded\/.*(?:writeFileSync|writeFile)/,
];

/**
 * Python forbidden import patterns — record_baseline module.
 */
const PY_FORBIDDEN_IMPORTS = [
  /from\s+scripts\.record_baseline/,
  /import\s+record_baseline/,
  /from\s+scripts\s+import\s+record_baseline/,
];

/**
 * Python forbidden write patterns — writing to golden-corpus/recorded/.
 */
const PY_FORBIDDEN_WRITES = [
  /(?:write_text|open\s*\()[^)]*golden-corpus\/recorded/,
  /golden-corpus\/recorded\/.*(?:write_text|open\s*\()/,
  /open\s*\([^)]*golden-corpus\/recorded[^)]*['"]w['"]/,
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Recursively collect files matching a pattern under a directory.
 * @param {string} dir - Directory to scan
 * @param {RegExp} pattern - Filename pattern to match
 * @returns {string[]} Absolute paths of matching files
 */
function collectFiles(dir, pattern) {
  const results = [];

  if (!fs.existsSync(dir)) {
    return results;
  }

  const entries = fs.readdirSync(dir, { withFileTypes: true });

  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);

    if (entry.isDirectory()) {
      // Skip node_modules and __pycache__
      if (entry.name === 'node_modules' || entry.name === '__pycache__') continue;
      results.push(...collectFiles(fullPath, pattern));
    } else if (pattern.test(entry.name)) {
      results.push(fullPath);
    }
  }

  return results;
}

/**
 * Check whether a line is a comment (JS or Python).
 * @param {string} line - Trimmed line content
 * @param {string} ext - File extension (.js or .py)
 * @returns {boolean}
 */
function isCommentLine(line, ext) {
  if (ext === '.js') {
    return line.startsWith('//') || line.startsWith('*') || line.startsWith('/*');
  }
  if (ext === '.py') {
    return line.startsWith('#');
  }
  return false;
}

/**
 * Check a file's content against a list of forbidden patterns.
 * Skips comment lines to avoid false positives from documentation.
 * @param {string} filePath - Absolute path to the file
 * @param {RegExp[]} patterns - Forbidden patterns
 * @returns {Array<{pattern: string, line: number, text: string}>} Violations found
 */
function checkForbiddenPatterns(filePath, patterns) {
  const content = fs.readFileSync(filePath, 'utf8');
  const lines = content.split('\n');
  const ext = path.extname(filePath);
  const violations = [];

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();

    // Skip comment lines — they cannot execute imports or writes
    if (isCommentLine(trimmed, ext)) continue;

    for (const pattern of patterns) {
      if (pattern.test(lines[i])) {
        violations.push({
          pattern: pattern.source,
          line: i + 1,
          text: trimmed,
        });
      }
    }
  }

  return violations;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Record/Compare Separation Guard', () => {
  // Collect all JS test files (test/**/*.test.js)
  const jsTestFiles = collectFiles(JS_TEST_DIR, /\.test\.js$/);

  // Collect all Python test files (services/postprocess/tests/**/*.py)
  const pyTestFiles = collectFiles(PY_TEST_DIR, /\.py$/);

  describe('JS test files do not import record-mode modules', () => {
    for (const filePath of jsTestFiles) {
      const relPath = path.relative(ROOT, filePath);

      it(`${relPath} does not import record-mode modules`, () => {
        const violations = checkForbiddenPatterns(filePath, JS_FORBIDDEN_IMPORTS);

        if (violations.length > 0) {
          const report = violations.map(v =>
            `  line ${v.line}: ${v.text}`
          ).join('\n');

          assert.fail(
            `${relPath} imports a record-mode module (violates Requirement 1.10):\n${report}`
          );
        }
      });
    }
  });

  describe('Python test files do not import record-mode modules', () => {
    for (const filePath of pyTestFiles) {
      const relPath = path.relative(ROOT, filePath);

      it(`${relPath} does not import record_baseline`, () => {
        const violations = checkForbiddenPatterns(filePath, PY_FORBIDDEN_IMPORTS);

        if (violations.length > 0) {
          const report = violations.map(v =>
            `  line ${v.line}: ${v.text}`
          ).join('\n');

          assert.fail(
            `${relPath} imports record_baseline (violates Requirement 1.10):\n${report}`
          );
        }
      });
    }
  });

  describe('No test file writes to fixtures/golden-corpus/recorded/', () => {
    for (const filePath of jsTestFiles) {
      const relPath = path.relative(ROOT, filePath);

      it(`${relPath} does not write to golden-corpus/recorded/`, () => {
        const violations = checkForbiddenPatterns(filePath, JS_FORBIDDEN_WRITES);

        if (violations.length > 0) {
          const report = violations.map(v =>
            `  line ${v.line}: ${v.text}`
          ).join('\n');

          assert.fail(
            `${relPath} writes to golden-corpus/recorded/ (violates Requirement 1.10):\n${report}`
          );
        }
      });
    }

    for (const filePath of pyTestFiles) {
      const relPath = path.relative(ROOT, filePath);

      it(`${relPath} does not write to golden-corpus/recorded/`, () => {
        const violations = checkForbiddenPatterns(filePath, PY_FORBIDDEN_WRITES);

        if (violations.length > 0) {
          const report = violations.map(v =>
            `  line ${v.line}: ${v.text}`
          ).join('\n');

          assert.fail(
            `${relPath} writes to golden-corpus/recorded/ (violates Requirement 1.10):\n${report}`
          );
        }
      });
    }
  });
});
