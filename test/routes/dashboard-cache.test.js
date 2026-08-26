'use strict';

/**
 * Dashboard caching unit tests.
 *
 * Tests cache hit, cache miss, and graceful degradation scenarios
 * for the GET /api/dashboard/stats endpoint.
 * All tests mock Redis — no running Redis instance required.
 *
 * Validates: Requirements 6.1, 6.2, 6.4, 9.4
 *
 * @module test/routes/dashboard-cache
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const request = require('supertest');

const dashboardRoutes = require('../../routes/dashboard');

/**
 * Creates a mock cache with an in-memory store and call tracking.
 * Mimics the interface of lib/cache.js.
 */
function createMockCache() {
  const store = new Map();
  const setCalls = [];
  const getCalls = [];

  return {
    key: (ns, ...parts) => `parliament:${ns}:${parts.join(':')}`,
    get: async (key) => {
      getCalls.push(key);
      return store.get(key) || null;
    },
    set: async (key, value, ttl) => {
      setCalls.push({ key, value, ttl });
      store.set(key, value);
      return true;
    },
    del: async (key) => {
      store.delete(key);
      return true;
    },
    invalidatePattern: async () => 0,
    hashParams: (obj) => 'mockhash',
    _store: store,
    _setCalls: setCalls,
    _getCalls: getCalls,
  };
}

/**
 * Creates a mock DB that returns valid dashboard stats data.
 * Tracks calls for verification.
 */
function createMockDb() {
  const calls = [];

  return {
    query: async (text, params) => {
      calls.push({ text, params });
      if (text.includes('COUNT(*) AS total FROM sitting')) {
        return { rows: [{ total: '10' }] };
      }
      if (text.includes('COUNT(*) AS total FROM hansard_record')) {
        return { rows: [{ total: '50' }] };
      }
      if (text.includes('GROUP BY status')) {
        return { rows: [{ status: 'Draft', count: '30' }, { status: 'Editing', count: '20' }] };
      }
      if (text.includes('SUM(duration_hours)')) {
        return { rows: [{ total: '25.5' }] };
      }
      if (text.includes('date_trunc')) {
        return { rows: [] };
      }
      if (text.includes('hr.id')) {
        return { rows: [] };
      }
      if (text.includes('assignee_name')) {
        return { rows: [] };
      }
      return { rows: [] };
    },
    getCalls() { return calls; },
  };
}

/**
 * A passthrough auth middleware that sets req.user with Admin permissions.
 * This satisfies both requireSession and requirePermission('view_records').
 */
function passThrough(req, res, next) {
  req.user = {
    userId: 'test-user',
    email: 'admin@test.com',
    name: 'Test Admin',
    role: 'Admin',
    permissions: [
      'system_config', 'view_records', 'manage_users',
      'create_sitting', 'edit_record', 'export_hansard',
    ],
  };
  next();
}

/**
 * Builds an Express app with the dashboard routes mounted.
 */
function buildApp(db, cache) {
  const app = express();
  app.use(express.json());
  app.use(dashboardRoutes(passThrough, db, cache));
  return app;
}

describe('GET /api/dashboard/stats — caching', () => {
  describe('cache hit', () => {
    it('returns cached stats without querying the database', async () => {
      const cache = createMockCache();
      const db = createMockDb();

      // Pre-populate cache with stats
      const cachedStats = {
        totalSittings: 10,
        totalRecords: 50,
        recordsByStatus: { Draft: 30, Editing: 20 },
        totalTranscriptionHours: 25.5,
        weeklyOutput: [],
        recentActivity: [],
        teamWorkload: [],
      };
      cache._store.set('parliament:dashboard:stats', cachedStats);

      const app = buildApp(db, cache);
      const res = await request(app).get('/api/dashboard/stats');

      assert.equal(res.status, 200);
      assert.deepEqual(res.body, cachedStats);

      // DB should NOT have been called
      assert.equal(db.getCalls().length, 0);
    });

    it('calls cache.get with the correct key', async () => {
      const cache = createMockCache();
      const db = createMockDb();

      // Pre-populate cache
      cache._store.set('parliament:dashboard:stats', { totalSittings: 5 });

      const app = buildApp(db, cache);
      await request(app).get('/api/dashboard/stats');

      assert.equal(cache._getCalls.length, 1);
      assert.equal(cache._getCalls[0], 'parliament:dashboard:stats');
    });
  });

  describe('cache miss', () => {
    it('queries DB and returns assembled stats when cache is empty', async () => {
      const cache = createMockCache();
      const db = createMockDb();

      const app = buildApp(db, cache);
      const res = await request(app).get('/api/dashboard/stats');

      assert.equal(res.status, 200);
      assert.equal(res.body.totalSittings, 10);
      assert.equal(res.body.totalRecords, 50);
      assert.deepEqual(res.body.recordsByStatus, { Draft: 30, Editing: 20 });
      assert.equal(res.body.totalTranscriptionHours, 25.5);
      assert.ok(Array.isArray(res.body.weeklyOutput));
      assert.ok(Array.isArray(res.body.recentActivity));
      assert.ok(Array.isArray(res.body.teamWorkload));

      // DB should have been queried (7 parallel queries)
      assert.ok(db.getCalls().length >= 7);
    });

    it('stores result in cache with 120s TTL after DB query', async () => {
      const cache = createMockCache();
      const db = createMockDb();

      const app = buildApp(db, cache);
      await request(app).get('/api/dashboard/stats');

      // Verify cache.set was called
      assert.equal(cache._setCalls.length, 1);

      const setCall = cache._setCalls[0];
      assert.equal(setCall.key, 'parliament:dashboard:stats');
      assert.equal(setCall.ttl, 120);

      // Verify the cached value has the expected shape
      assert.equal(setCall.value.totalSittings, 10);
      assert.equal(setCall.value.totalRecords, 50);
      assert.deepEqual(setCall.value.recordsByStatus, { Draft: 30, Editing: 20 });
      assert.equal(setCall.value.totalTranscriptionHours, 25.5);
    });
  });

  describe('graceful degradation (cache is null)', () => {
    it('queries DB directly and returns stats when cache is not provided', async () => {
      const db = createMockDb();

      // No cache provided (null)
      const app = buildApp(db, null);
      const res = await request(app).get('/api/dashboard/stats');

      assert.equal(res.status, 200);
      assert.equal(res.body.totalSittings, 10);
      assert.equal(res.body.totalRecords, 50);
      assert.deepEqual(res.body.recordsByStatus, { Draft: 30, Editing: 20 });
      assert.equal(res.body.totalTranscriptionHours, 25.5);

      // DB should have been queried
      assert.ok(db.getCalls().length >= 7);
    });

    it('does not throw when cache is undefined', async () => {
      const db = createMockDb();

      // Cache is undefined (route called without cache arg)
      const app = buildApp(db, undefined);
      const res = await request(app).get('/api/dashboard/stats');

      assert.equal(res.status, 200);
      assert.equal(res.body.totalSittings, 10);
    });

    it('queries DB directly and returns stats (no cache.set attempted)', async () => {
      const db = createMockDb();

      const app = buildApp(db, null);
      await request(app).get('/api/dashboard/stats');

      // DB was queried successfully
      assert.ok(db.getCalls().length >= 7);
    });
  });
});
