'use strict';

/**
 * Documentation-currency guard.
 *
 * Asserts every file path listed in AGENTS.md, CLAUDE.md, and the files
 * under .kiro/steering/ exists on disk. Focuses on paths in structured
 * listings (markdown table cells, definition rows) which are the most
 * likely to go stale after file moves or renames.
 *
 * Requirements: 11.1
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');

/**
 * Extract file paths from markdown table rows.
 * Targets the "Key Files" and "File Layout" tables where paths are in the
 * first pipe-delimited column wrapped in backticks: | `path/to/file` | purpose |
 */
function extractTablePaths(content) {
  const paths = new Set();

  // Match table rows with backtick-quoted paths in the first column
  // Pattern: | `some/path.ext` | description |
  const tableRowPattern = /^\|\s*`([^`]+)`\s*\|/gm;
  let match;
  while ((match = tableRowPattern.exec(content)) !== null) {
    const candidate = match[1].trim();
    if (isProjectPath(candidate)) {
      paths.add(candidate);
    }
  }

  return [...paths];
}

/**
 * Determine if a string is a concrete project file/directory path
 * (not a pattern, code reference, URL, or command).
 */
function isProjectPath(str) {
  // Must not be empty or very short
  if (!str || str.length < 3) return false;

  // Skip URLs
  if (str.startsWith('http') || str.startsWith('//')) return false;

  // Skip API routes (start with /)
  if (str.startsWith('/')) return false;

  // Skip things with spaces (commands, descriptions)
  if (str.includes(' ')) return false;

  // Skip env vars and placeholders
  if (str.startsWith('$') || str.includes('${') || str.includes('%')) return false;

  // Skip glob patterns
  if (str.includes('*')) return false;

  // Skip code references (contain :: or -> or parentheses)
  if (str.includes('::') || str.includes('->') || str.includes('(') || str.includes(')')) return false;

  // Skip things that look like function/method calls
  if (str.includes('[') || str.includes(']') || str.includes('{') || str.includes('}')) return false;

  // Skip JSX/HTML-looking items
  if (str.includes('<') || str.includes('>')) return false;

  // Skip semver or version-like strings
  if (/^\d+\.\d+\.\d+/.test(str)) return false;
  if (/^v\d/.test(str)) return false;

  // Skip package names with @ scope
  if (str.startsWith('@') && !str.includes('/src/') && !str.includes('/app/')) return false;

  // Must look like a path (has / or . extension)
  const hasSlash = str.includes('/');
  const hasExtension = /\.\w{1,10}$/.test(str);
  const endsWithSlash = str.endsWith('/');

  if (!hasSlash && !hasExtension) return false;

  // If it's just a bare filename with extension but no directory, check if
  // it's a known project root file
  if (!hasSlash && hasExtension) {
    const rootFiles = [
      'server.js', 'Makefile', 'deepgram.toml', 'sample.env',
      'package.json', 'AGENTS.md', 'CLAUDE.md',
    ];
    return rootFiles.includes(str);
  }

  return hasSlash || hasExtension || endsWithSlash;
}

describe('Documentation currency — referenced paths exist on disk', () => {
  const docFiles = ['AGENTS.md', 'CLAUDE.md'];

  // Add .kiro/steering/ files
  const steeringDir = path.join(ROOT, '.kiro', 'steering');
  if (fs.existsSync(steeringDir)) {
    for (const f of fs.readdirSync(steeringDir)) {
      if (f.endsWith('.md')) {
        docFiles.push(path.join('.kiro', 'steering', f));
      }
    }
  }

  for (const docFile of docFiles) {
    const docPath = path.resolve(ROOT, docFile);
    if (!fs.existsSync(docPath)) continue;

    const content = fs.readFileSync(docPath, 'utf8');
    const refs = extractTablePaths(content);

    // Only test files that have table-based path references
    if (refs.length === 0) continue;

    describe(docFile, () => {
      it(`all ${refs.length} table-listed paths exist`, () => {
        const missing = [];
        for (const ref of refs) {
          const resolved = path.resolve(ROOT, ref);
          if (!fs.existsSync(resolved)) {
            missing.push(ref);
          }
        }

        assert.deepEqual(
          missing,
          [],
          `Missing paths in ${docFile}:\n  ${missing.join('\n  ')}\n\n` +
          `These paths are referenced in a documentation table but do not exist on disk.\n` +
          `Either the file was moved/deleted, or the documentation needs updating.`
        );
      });
    });
  }
});
