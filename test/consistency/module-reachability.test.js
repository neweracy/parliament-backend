'use strict';

/**
 * Module-reachability smoke check.
 *
 * Asserts that no exported module under `lib/` or `services/postprocess/app/`
 * is referenced by no other source file and no test file. If a module is
 * exported but never required/imported anywhere, it is either dead code or
 * a wiring gap that needs attention.
 *
 * Requirements: 4.5
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');

/**
 * Recursively collect all `.js` files under a directory, excluding
 * `node_modules` and hidden directories.
 */
function collectFiles(dir, ext = '.js') {
  const results = [];
  if (!fs.existsSync(dir)) return results;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectFiles(full, ext));
    } else if (entry.name.endsWith(ext)) {
      results.push(full);
    }
  }
  return results;
}

/**
 * Collect all `.py` files under a directory.
 */
function collectPyFiles(dir) {
  return collectFiles(dir, '.py');
}

/**
 * Get all JS source and test files in the project.
 */
function getAllJsFiles() {
  const dirs = ['lib', 'routes', 'providers', 'test', 'bench', 'scripts'];
  const files = [path.join(ROOT, 'server.js')];
  for (const d of dirs) {
    files.push(...collectFiles(path.join(ROOT, d)));
  }
  return files;
}

/**
 * Get all Python source and test files in the project.
 */
function getAllPyFiles() {
  const appDir = path.join(ROOT, 'services/postprocess/app');
  const testDir = path.join(ROOT, 'services/postprocess/tests');
  const scriptDir = path.join(ROOT, 'services/postprocess/scripts');
  return [
    ...collectPyFiles(appDir),
    ...collectPyFiles(testDir),
    ...collectPyFiles(scriptDir),
  ];
}

describe('Module reachability — lib/', () => {
  const libDir = path.join(ROOT, 'lib');
  const libModules = collectFiles(libDir);
  const allJsFiles = getAllJsFiles();

  for (const modulePath of libModules) {
    const relativePath = path.relative(ROOT, modulePath);
    const baseName = path.basename(modulePath, '.js');

    it(`${relativePath} is referenced by at least one other file`, () => {
      // Check if any other file references this module
      let referenced = false;

      for (const file of allJsFiles) {
        if (file === modulePath) continue;
        const content = fs.readFileSync(file, 'utf8');

        // Check for require() references to this module
        // Match various forms: require('./path'), require('../path'), etc.
        if (
          content.includes(`/${baseName}'`) ||
          content.includes(`/${baseName}"`) ||
          content.includes(`/${baseName}\``)
        ) {
          referenced = true;
          break;
        }
      }

      assert.ok(
        referenced,
        `Module ${relativePath} is not referenced by any other source or test file`
      );
    });
  }
});

describe('Module reachability — services/postprocess/app/', () => {
  const appDir = path.join(ROOT, 'services/postprocess/app');
  if (!fs.existsSync(appDir)) return;

  const appModules = collectPyFiles(appDir).filter(
    f => !f.endsWith('__init__.py')
  );
  const allPyFiles = getAllPyFiles();

  for (const modulePath of appModules) {
    const relativePath = path.relative(ROOT, modulePath);
    const baseName = path.basename(modulePath, '.py');

    // Skip __init__.py and conftest.py
    if (baseName === '__init__' || baseName === 'conftest') continue;

    it(`${relativePath} is referenced by at least one other file`, () => {
      let referenced = false;

      for (const file of allPyFiles) {
        if (file === modulePath) continue;
        const content = fs.readFileSync(file, 'utf8');

        // Check for Python import references
        if (
          content.includes(`import ${baseName}`) ||
          content.includes(`from app.`) && content.includes(baseName)
        ) {
          referenced = true;
          break;
        }
      }

      assert.ok(
        referenced,
        `Module ${relativePath} is not referenced by any other source or test file`
      );
    });
  }
});
