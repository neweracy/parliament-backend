/**
 * File-size smoke check — asserts no executable-logic file under lib/ or
 * services/postprocess/app/ exceeds 500 lines (non-blank, non-comment).
 *
 * Dataset-only files (ministers-dataset.js, persons-dataset.js,
 * mps-dataset.js, parties-dataset.js) are exempt because they contain
 * only data arrays with no executable logic.
 *
 * Requirements: 6.5, 6.6
 */

'use strict';

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const MAX_LINES = 500;

// Dataset-only files exempt from the size limit (no executable logic)
const EXEMPT_PATTERNS = [
  'ministers-dataset.js',
  'persons-dataset.js',
  'mps-dataset.js',
  'parties-dataset.js',
];

function isExempt(filePath) {
  const basename = path.basename(filePath);
  return EXEMPT_PATTERNS.includes(basename);
}

/**
 * Count non-blank, non-comment lines in a file.
 * For JS: single-line comments (//) and block comments are excluded.
 * For Python: single-line comments (#) are excluded.
 */
function countExecutableLines(filePath) {
  const content = fs.readFileSync(filePath, 'utf8');
  const lines = content.split('\n');
  const ext = path.extname(filePath);
  let count = 0;
  let inBlockComment = false;

  for (const line of lines) {
    const trimmed = line.trim();

    // Skip blank lines
    if (trimmed === '') continue;

    if (ext === '.js') {
      // Handle JS block comments
      if (inBlockComment) {
        if (trimmed.includes('*/')) {
          inBlockComment = false;
        }
        continue;
      }
      if (trimmed.startsWith('/*')) {
        if (!trimmed.includes('*/')) {
          inBlockComment = true;
        }
        continue;
      }
      if (trimmed.startsWith('//')) continue;
      // Lines that are just a * inside a JSDoc block
      if (trimmed.startsWith('*')) continue;
    } else if (ext === '.py') {
      // Skip Python comments
      if (trimmed.startsWith('#')) continue;
      // Skip triple-quote docstrings (simple heuristic — count whole lines)
      if (trimmed.startsWith('"""') || trimmed.startsWith("'''")) {
        // If it opens and closes on the same line, skip just this line
        const quote = trimmed.slice(0, 3);
        if (trimmed.indexOf(quote, 3) !== -1) continue;
        // Multi-line docstring — skip until close
        inBlockComment = true;
        continue;
      }
      if (inBlockComment) {
        if (trimmed.includes('"""') || trimmed.includes("'''")) {
          inBlockComment = false;
        }
        continue;
      }
    }

    count++;
  }
  return count;
}

/**
 * Walk a directory recursively and collect all .js or .py files.
 */
function walkFiles(dir, extensions) {
  const results = [];
  if (!fs.existsSync(dir)) return results;

  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // Skip __pycache__, node_modules, .git
      if (entry.name === '__pycache__' || entry.name === 'node_modules' || entry.name.startsWith('.')) {
        continue;
      }
      results.push(...walkFiles(fullPath, extensions));
    } else if (entry.isFile()) {
      const ext = path.extname(entry.name);
      if (extensions.includes(ext)) {
        results.push(fullPath);
      }
    }
  }
  return results;
}

describe('file-size limits', () => {
  const libFiles = walkFiles('lib', ['.js']);
  const pyFiles = walkFiles(path.join('services', 'postprocess', 'app'), ['.py']);
  const allFiles = [...libFiles, ...pyFiles];

  it('no executable-logic file exceeds 500 lines', () => {
    const violations = [];

    for (const filePath of allFiles) {
      if (isExempt(filePath)) continue;

      const lineCount = countExecutableLines(filePath);
      if (lineCount > MAX_LINES) {
        violations.push({ file: filePath, lines: lineCount });
      }
    }

    if (violations.length > 0) {
      const details = violations
        .map(v => `  ${v.file}: ${v.lines} lines`)
        .join('\n');
      assert.fail(
        `Files exceeding ${MAX_LINES} executable lines:\n${details}`
      );
    }
  });

  it('ministers-dataset.js is exempt at 1049 lines', () => {
    const datasetPath = path.join('lib', 'location-correction', 'ministers-dataset.js');
    assert.ok(fs.existsSync(datasetPath), 'ministers-dataset.js should exist');

    const lineCount = countExecutableLines(datasetPath);
    assert.ok(
      lineCount > MAX_LINES,
      `ministers-dataset.js should exceed ${MAX_LINES} lines (actual: ${lineCount}) — proving exemption works`
    );
    assert.ok(
      isExempt(datasetPath),
      'ministers-dataset.js should be in the exempt list'
    );
  });
});
