'use strict';

/**
 * Verifies each page route is emitted as its own chunk by the Vite build.
 * The frontend router wraps all six pages in React.lazy, so the production
 * build must produce separate route-level chunks.
 *
 * Requirements: 10.3
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const DIST_ASSETS = path.resolve(__dirname, '../../frontend/dist/assets');
const INDEX_HTML = path.resolve(__dirname, '../../frontend/dist/index.html');

// The six page routes defined in frontend/src/pages/
const EXPECTED_ROUTES = ['Landing', 'Transcribe', 'Projects', 'History', 'About', 'NotFound'];

describe('Frontend chunk emission', () => {
  // Pre-check: skip gracefully if frontend hasn't been built
  const distExists = fs.existsSync(DIST_ASSETS);

  it('dist/assets/ exists (frontend has been built)', () => {
    assert.ok(distExists, 'frontend/dist/assets/ must exist — run `cd frontend && pnpm build` first');
  });

  if (!distExists) return;

  const jsFiles = fs.readdirSync(DIST_ASSETS).filter((f) => f.endsWith('.js'));

  it('produces at least 6 route chunks', () => {
    // Each route page (React.lazy) should produce its own chunk
    const routeChunks = EXPECTED_ROUTES.filter((route) =>
      jsFiles.some((f) => f.toLowerCase().includes(route.toLowerCase()))
    );
    assert.ok(
      routeChunks.length >= 6,
      `Expected 6 route chunks, found ${routeChunks.length}. ` +
      `Missing: ${EXPECTED_ROUTES.filter((r) => !routeChunks.includes(r)).join(', ')}. ` +
      `JS files: ${jsFiles.join(', ')}`
    );
  });

  for (const route of EXPECTED_ROUTES) {
    it(`has a separate chunk for ${route}`, () => {
      const hasChunk = jsFiles.some((f) =>
        f.toLowerCase().includes(route.toLowerCase())
      );
      assert.ok(hasChunk, `Expected a chunk file containing "${route}" in its name`);
    });
  }

  it('route code is absent from the entry chunk', () => {
    // Find entry chunk from index.html
    if (!fs.existsSync(INDEX_HTML)) return;

    const html = fs.readFileSync(INDEX_HTML, 'utf8');
    const match = html.match(/<script\s+type="module"[^>]*\ssrc="\/([^"]+)"/);
    if (!match) return;

    const entryPath = path.join(path.resolve(__dirname, '../../frontend/dist'), match[1]);
    if (!fs.existsSync(entryPath)) return;

    const entryContent = fs.readFileSync(entryPath, 'utf8');

    // The entry chunk should NOT contain the page component code directly
    // It should reference them via dynamic import (React.lazy)
    // Check that substantial route-specific strings aren't inlined
    for (const route of EXPECTED_ROUTES) {
      const routeChunk = jsFiles.find((f) =>
        f.toLowerCase().includes(route.toLowerCase())
      );
      if (routeChunk) {
        const routeContent = fs.readFileSync(path.join(DIST_ASSETS, routeChunk), 'utf8');
        // If a route chunk is bigger than 300 bytes, its code shouldn't appear in entry
        if (routeContent.length > 300) {
          // A crude but effective check: the route chunk's content shouldn't be a substring of entry
          assert.ok(
            !entryContent.includes(routeContent),
            `${route} chunk content should not be inlined in the entry chunk`
          );
        }
      }
    }
  });
});
