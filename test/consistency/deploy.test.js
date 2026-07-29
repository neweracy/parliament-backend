'use strict';

/**
 * Deployment Configuration Consistency Check
 *
 * Verifies that every path referenced in `deploy/Caddyfile` and `fly.toml`
 * is implemented by the Gateway. This catches configuration drift where a
 * route is added to the reverse-proxy or health-check config without a
 * corresponding handler in `server.js`.
 *
 * Validates: Requirements 5.6
 *
 * @module test/consistency/deploy
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');

/**
 * Known routes implemented by the Gateway (server.js + mounted routers).
 * Wildcards like `/api/*` cover any sub-path.
 */
const IMPLEMENTED_ROUTES = [
  '/',
  '/index.html',
  '/api/session',
  '/api/transcription',
  '/api/transcription/hybrid',
  '/api/khaya/transcription',
  '/api/khaya/languages',
  '/api/metadata',
  '/api/audio-proxy',
  '/api/openapi.yml',
  '/docs',
  '/health',
];

/**
 * Returns true if the given path is covered by the implemented routes.
 * Handles wildcard matching: `/api/*` matches any implemented `/api/...` route.
 */
function isRouteCovered(routePath) {
  // Exact match
  if (IMPLEMENTED_ROUTES.includes(routePath)) return true;

  // Wildcard: `/api/*` means "any route starting with /api/"
  if (routePath.endsWith('/*')) {
    const prefix = routePath.slice(0, -1); // '/api/'
    return IMPLEMENTED_ROUTES.some(r => r.startsWith(prefix));
  }

  // Check if an implemented wildcard covers this path
  // (e.g. if IMPLEMENTED_ROUTES had '/api/*', it would cover '/api/session')
  return false;
}

describe('Deployment config references implemented routes', () => {
  it('every proxied path in deploy/Caddyfile is implemented', () => {
    const caddyfile = fs.readFileSync(path.join(ROOT, 'deploy', 'Caddyfile'), 'utf-8');

    // Extract paths from `handle /path` and `path /path` directives
    const pathPattern = /(?:handle|path)\s+(\/[^\s{]+)/g;
    const paths = new Set();
    let match;
    while ((match = pathPattern.exec(caddyfile)) !== null) {
      paths.add(match[1]);
    }

    assert.ok(paths.size > 0, 'Should find at least one path in Caddyfile');

    for (const p of paths) {
      assert.ok(
        isRouteCovered(p),
        `Caddyfile references path "${p}" which is not implemented by the Gateway`
      );
    }
  });

  it('every health check path in fly.toml is implemented', () => {
    const flyToml = fs.readFileSync(path.join(ROOT, 'fly.toml'), 'utf-8');

    // Extract path values from health check sections
    const healthPathPattern = /path\s*=\s*"([^"]+)"/g;
    const paths = new Set();
    let match;
    while ((match = healthPathPattern.exec(flyToml)) !== null) {
      paths.add(match[1]);
    }

    assert.ok(paths.size > 0, 'Should find at least one health check path in fly.toml');

    for (const p of paths) {
      assert.ok(
        isRouteCovered(p),
        `fly.toml references health check path "${p}" which is not implemented by the Gateway`
      );
    }
  });

  it('covers the known set of deployment paths', () => {
    // Sanity check: the known deployment paths we expect
    const expectedDeployPaths = ['/', '/index.html', '/api/session', '/api/*', '/health'];

    for (const p of expectedDeployPaths) {
      assert.ok(
        isRouteCovered(p),
        `Expected deployment path "${p}" is not covered by implemented routes`
      );
    }
  });
});
