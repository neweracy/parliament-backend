'use strict';

/**
 * Property tests for the Sittings API pagination and filters.
 *
 * Property 2: Pagination correctness
 * Property 3: Filter predicate satisfaction
 *
 * Uses fast-check to generate arbitrary sittings datasets and query parameters,
 * then exercises the actual route handler via supertest + express with a mock db.
 *
 * Validates: Requirements 2.1, 2.2
 *
 * @module test/properties/sittings.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');
const express = require('express');
const request = require('supertest');

const sittingsRoutes = require('../../routes/sittings');

// ─── Constants ───────────────────────────────────────────────────────────────

const VALID_STATUSES = ['Active', 'Completed', 'Archived'];
const VALID_SESSION_TYPES = ['Plenary', 'Committee', 'Special Sitting'];
const VALID_PRIORITIES = ['High', 'Medium', 'Low'];

// ─── Generators ──────────────────────────────────────────────────────────────

/**
 * Generator for an ISO date string in YYYY-MM-DD format within a realistic range.
 */
const isoDateArb = fc.integer({ min: 0, max: 3650 }).map(dayOffset => {
  const base = new Date('2020-01-01');
  base.setDate(base.getDate() + dayOffset);
  return base.toISOString().slice(0, 10);
});

/**
 * Generator for a full ISO datetime string within a realistic range.
 */
const isoDateTimeArb = fc.integer({ min: 0, max: 3650 * 24 * 60 }).map(minuteOffset => {
  const base = new Date('2020-01-01T00:00:00.000Z');
  base.setMinutes(base.getMinutes() + minuteOffset);
  return base.toISOString();
});

/**
 * Generator for a realistic sitting row as returned by the database.
 */
const sittingRowArb = fc.record({
  id: fc.integer({ min: 1, max: 10000 }),
  title: fc.string({ minLength: 1, maxLength: 100 }).filter(s => s.trim().length > 0),
  description: fc.option(fc.string({ maxLength: 200 }), { nil: null }),
  session_type: fc.constantFrom(...VALID_SESSION_TYPES),
  committee: fc.option(fc.string({ minLength: 1, maxLength: 50 }), { nil: null }),
  presiding_officer: fc.string({ minLength: 1, maxLength: 80 }).filter(s => s.trim().length > 0),
  parliament: fc.option(fc.string({ minLength: 1, maxLength: 50 }), { nil: null }),
  date_from: isoDateArb,
  date_to: isoDateArb,
  status: fc.constantFrom(...VALID_STATUSES),
  priority: fc.constantFrom(...VALID_PRIORITIES),
  participants: fc.integer({ min: 0, max: 500 }),
  topic: fc.option(fc.string({ maxLength: 100 }), { nil: null }),
  order_paper_ref: fc.option(fc.string({ maxLength: 30 }), { nil: null }),
  created_at: isoDateTimeArb,
  updated_at: isoDateTimeArb,
});

/**
 * Generator for a non-empty array of sitting rows with unique IDs.
 */
const sittingsDatasetArb = fc.array(sittingRowArb, { minLength: 1, maxLength: 30 })
  .map(rows => {
    // Ensure unique IDs and date_from <= date_to
    return rows.map((row, i) => {
      const dateFrom = row.date_from;
      const dateTo = row.date_to;
      return {
        ...row,
        id: i + 1,
        date_from: dateFrom <= dateTo ? dateFrom : dateTo,
        date_to: dateFrom <= dateTo ? dateTo : dateFrom,
      };
    });
  });

/**
 * Generator for valid pagination parameters.
 */
const paginationArb = fc.record({
  page: fc.integer({ min: 1, max: 10 }),
  pageSize: fc.integer({ min: 1, max: 100 }),
});

/**
 * Generator for optional filter parameters.
 */
const filtersArb = fc.record({
  status: fc.option(fc.constantFrom(...VALID_STATUSES), { nil: undefined }),
  sessionType: fc.option(fc.constantFrom(...VALID_SESSION_TYPES), { nil: undefined }),
  dateFrom: fc.option(isoDateArb, { nil: undefined }),
  dateTo: fc.option(isoDateArb, { nil: undefined }),
});

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * No-op auth middleware.
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
      'upload_audio',
    ],
  };
  next();
}

/**
 * Applies the same filter logic that the route uses:
 * - Excludes Archived unless status filter explicitly requests it
 * - Filters by sessionType, dateFrom, dateTo
 */
function applyFilters(sittings, filters) {
  return sittings.filter(s => {
    // Status filter
    if (filters.status) {
      if (s.status !== filters.status) return false;
    } else {
      if (s.status === 'Archived') return false;
    }

    // Session type filter
    if (filters.sessionType) {
      if (s.session_type !== filters.sessionType) return false;
    }

    // Date range filters
    if (filters.dateFrom) {
      if (s.date_from < filters.dateFrom) return false;
    }

    if (filters.dateTo) {
      if (s.date_to > filters.dateTo) return false;
    }

    return true;
  });
}

/**
 * Creates a mock DB that simulates filtering, pagination, and counting
 * over a known in-memory dataset. This mock replicates the route's SQL logic.
 */
function createInMemoryDb(allSittings) {
  return {
    query(text, params) {
      // Parse the query to determine what filters/pagination to apply
      const isCount = text.includes('COUNT(*)');

      // Extract filter logic from params based on the query structure
      let filtered = [...allSittings];

      // The route builds conditions dynamically. We replicate the filter logic
      // by examining what the query text contains and using params.
      let paramIdx = 0;

      if (text.includes('status !=')) {
        const excludeStatus = params[paramIdx++];
        filtered = filtered.filter(s => s.status !== excludeStatus);
      } else if (text.includes('status =')) {
        const matchStatus = params[paramIdx++];
        filtered = filtered.filter(s => s.status === matchStatus);
      }

      if (text.includes('session_type =')) {
        const matchType = params[paramIdx++];
        filtered = filtered.filter(s => s.session_type === matchType);
      }

      if (text.includes('date_from >=')) {
        const dateFrom = params[paramIdx++];
        filtered = filtered.filter(s => s.date_from >= dateFrom);
      }

      if (text.includes('date_to <=')) {
        const dateTo = params[paramIdx++];
        filtered = filtered.filter(s => s.date_to <= dateTo);
      }

      if (isCount) {
        return Promise.resolve({ rows: [{ total: String(filtered.length) }] });
      }

      // For data query: sort by created_at DESC, apply LIMIT and OFFSET
      filtered.sort((a, b) => b.created_at.localeCompare(a.created_at));

      const limit = params[paramIdx++];
      const offset = params[paramIdx++];
      const sliced = filtered.slice(offset, offset + limit);

      return Promise.resolve({ rows: sliced });
    },
  };
}

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * Validates: Requirements 2.1, 2.2
 */
describe('Property 2: Pagination correctness', () => {
  it('response contains at most pageSize items', async () => {
    await fc.assert(
      fc.asyncProperty(sittingsDatasetArb, paginationArb, filtersArb, async (sittings, pagination, filters) => {
        const db = createInMemoryDb(sittings);
        const app = express();
        app.use(express.json());
        app.use(sittingsRoutes(passThrough, db));

        const query = new URLSearchParams();
        query.set('page', String(pagination.page));
        query.set('pageSize', String(pagination.pageSize));
        if (filters.status) query.set('status', filters.status);
        if (filters.sessionType) query.set('sessionType', filters.sessionType);
        if (filters.dateFrom) query.set('dateFrom', filters.dateFrom);
        if (filters.dateTo) query.set('dateTo', filters.dateTo);

        const res = await request(app).get(`/api/sittings?${query.toString()}`);

        assert.equal(res.status, 200);
        assert.ok(
          res.body.data.length <= pagination.pageSize,
          `Expected at most ${pagination.pageSize} items, got ${res.body.data.length}`
        );
      }),
      { numRuns: 50 }
    );
  });

  it('response items are the correct contiguous slice at offset (page-1)*pageSize', async () => {
    await fc.assert(
      fc.asyncProperty(sittingsDatasetArb, paginationArb, filtersArb, async (sittings, pagination, filters) => {
        const db = createInMemoryDb(sittings);
        const app = express();
        app.use(express.json());
        app.use(sittingsRoutes(passThrough, db));

        const query = new URLSearchParams();
        query.set('page', String(pagination.page));
        query.set('pageSize', String(pagination.pageSize));
        if (filters.status) query.set('status', filters.status);
        if (filters.sessionType) query.set('sessionType', filters.sessionType);
        if (filters.dateFrom) query.set('dateFrom', filters.dateFrom);
        if (filters.dateTo) query.set('dateTo', filters.dateTo);

        const res = await request(app).get(`/api/sittings?${query.toString()}`);

        assert.equal(res.status, 200);

        // Compute expected slice from the reference implementation
        const filtered = applyFilters(sittings, filters);
        filtered.sort((a, b) => b.created_at.localeCompare(a.created_at));

        const offset = (pagination.page - 1) * pagination.pageSize;
        const expectedSlice = filtered.slice(offset, offset + pagination.pageSize);

        assert.equal(
          res.body.data.length,
          expectedSlice.length,
          `Expected ${expectedSlice.length} items for page ${pagination.page}, got ${res.body.data.length}`
        );

        // Verify the IDs match the expected slice
        const responseIds = res.body.data.map(s => s.id);
        const expectedIds = expectedSlice.map(s => s.id);
        assert.deepEqual(responseIds, expectedIds, 'Response IDs should match the expected offset slice');
      }),
      { numRuns: 50 }
    );
  });

  it('total field equals the count of non-archived sittings matching filters', async () => {
    await fc.assert(
      fc.asyncProperty(sittingsDatasetArb, paginationArb, filtersArb, async (sittings, pagination, filters) => {
        const db = createInMemoryDb(sittings);
        const app = express();
        app.use(express.json());
        app.use(sittingsRoutes(passThrough, db));

        const query = new URLSearchParams();
        query.set('page', String(pagination.page));
        query.set('pageSize', String(pagination.pageSize));
        if (filters.status) query.set('status', filters.status);
        if (filters.sessionType) query.set('sessionType', filters.sessionType);
        if (filters.dateFrom) query.set('dateFrom', filters.dateFrom);
        if (filters.dateTo) query.set('dateTo', filters.dateTo);

        const res = await request(app).get(`/api/sittings?${query.toString()}`);

        assert.equal(res.status, 200);

        const expectedTotal = applyFilters(sittings, filters).length;
        assert.equal(
          res.body.total,
          expectedTotal,
          `Expected total ${expectedTotal}, got ${res.body.total}`
        );
      }),
      { numRuns: 50 }
    );
  });
});

/**
 * Validates: Requirements 2.1, 2.2
 */
describe('Property 3: Filter predicate satisfaction', () => {
  it('every returned sitting matches all applied filters simultaneously', async () => {
    await fc.assert(
      fc.asyncProperty(sittingsDatasetArb, paginationArb, filtersArb, async (sittings, pagination, filters) => {
        const db = createInMemoryDb(sittings);
        const app = express();
        app.use(express.json());
        app.use(sittingsRoutes(passThrough, db));

        const query = new URLSearchParams();
        query.set('page', String(pagination.page));
        query.set('pageSize', String(pagination.pageSize));
        if (filters.status) query.set('status', filters.status);
        if (filters.sessionType) query.set('sessionType', filters.sessionType);
        if (filters.dateFrom) query.set('dateFrom', filters.dateFrom);
        if (filters.dateTo) query.set('dateTo', filters.dateTo);

        const res = await request(app).get(`/api/sittings?${query.toString()}`);

        assert.equal(res.status, 200);

        for (const sitting of res.body.data) {
          // Status filter check
          if (filters.status) {
            assert.equal(
              sitting.status, filters.status,
              `Sitting ${sitting.id} status "${sitting.status}" doesn't match filter "${filters.status}"`
            );
          } else {
            // When no status filter, Archived should be excluded
            assert.notEqual(
              sitting.status, 'Archived',
              `Sitting ${sitting.id} should not be Archived when no status filter is applied`
            );
          }

          // Session type filter check
          if (filters.sessionType) {
            assert.equal(
              sitting.sessionType, filters.sessionType,
              `Sitting ${sitting.id} sessionType "${sitting.sessionType}" doesn't match filter "${filters.sessionType}"`
            );
          }

          // Date range filter checks
          if (filters.dateFrom) {
            assert.ok(
              sitting.dateFrom >= filters.dateFrom,
              `Sitting ${sitting.id} dateFrom "${sitting.dateFrom}" is before filter dateFrom "${filters.dateFrom}"`
            );
          }

          if (filters.dateTo) {
            assert.ok(
              sitting.dateTo <= filters.dateTo,
              `Sitting ${sitting.id} dateTo "${sitting.dateTo}" is after filter dateTo "${filters.dateTo}"`
            );
          }
        }
      }),
      { numRuns: 50 }
    );
  });
});

// ─── Property 4: Create sitting round-trip ───────────────────────────────────

/**
 * Generator for a valid CreateSittingDTO body.
 * Mirrors the fields accepted by POST /api/sittings.
 */
const createSittingDtoArb = fc.record({
  title: fc.string({ minLength: 1, maxLength: 80 }).filter(s => s.trim().length > 0),
  description: fc.option(fc.string({ maxLength: 200 }), { nil: undefined }),
  sessionType: fc.constantFrom(...VALID_SESSION_TYPES),
  committee: fc.option(fc.string({ minLength: 1, maxLength: 50 }), { nil: undefined }),
  presidingOfficer: fc.string({ minLength: 1, maxLength: 80 }).filter(s => s.trim().length > 0),
  parliament: fc.option(fc.string({ minLength: 1, maxLength: 50 }), { nil: undefined }),
  dateFrom: isoDateArb,
  dateTo: isoDateArb,
  priority: fc.option(fc.constantFrom(...VALID_PRIORITIES), { nil: undefined }),
  participants: fc.option(fc.integer({ min: 0, max: 500 }), { nil: undefined }),
  topic: fc.option(fc.string({ maxLength: 100 }), { nil: undefined }),
  orderPaperRef: fc.option(fc.string({ maxLength: 30 }), { nil: undefined }),
}).map(dto => {
  // Ensure dateFrom <= dateTo
  if (dto.dateFrom > dto.dateTo) {
    const tmp = dto.dateFrom;
    dto.dateFrom = dto.dateTo;
    dto.dateTo = tmp;
  }
  return dto;
});

/**
 * Creates a mock DB for the create round-trip test.
 * - INSERT...RETURNING * simulates a DB INSERT returning the row with a generated id
 * - SELECT by id returns the same stored row
 * - SELECT records returns empty (no records yet)
 */
function createRoundTripDb() {
  let storedRow = null;
  let nextId = 1;

  return {
    query(text, params) {
      // INSERT INTO sitting ... RETURNING *
      if (text.includes('INSERT INTO sitting')) {
        const now = new Date().toISOString();
        storedRow = {
          id: nextId++,
          title: params[0],
          description: params[1],
          session_type: params[2],
          committee: params[3],
          presiding_officer: params[4],
          parliament: params[5],
          date_from: params[6],
          date_to: params[7],
          priority: params[8],
          participants: params[9],
          topic: params[10],
          order_paper_ref: params[11],
          status: 'Active',
          created_at: now,
          updated_at: now,
        };
        return Promise.resolve({ rows: [storedRow] });
      }

      // SELECT * FROM sitting WHERE id = $1
      if (text.includes('FROM sitting WHERE id')) {
        if (storedRow && String(storedRow.id) === String(params[0])) {
          return Promise.resolve({ rows: [storedRow] });
        }
        return Promise.resolve({ rows: [] });
      }

      // SELECT * FROM hansard_record WHERE sitting_id = $1
      if (text.includes('hansard_record')) {
        return Promise.resolve({ rows: [] });
      }

      return Promise.resolve({ rows: [] });
    },
  };
}

/**
 * Validates: Requirements 2.3
 */
describe('Property 4: Create sitting round-trip', () => {
  it('POST then GET yields matching fields with server-generated ID', async () => {
    await fc.assert(
      fc.asyncProperty(createSittingDtoArb, async (dto) => {
        const db = createRoundTripDb();
        const app = express();
        app.use(express.json());
        app.use(sittingsRoutes(passThrough, db));

        // POST to create a sitting
        const postRes = await request(app)
          .post('/api/sittings')
          .send(dto);

        assert.equal(postRes.status, 201, `Expected 201, got ${postRes.status}: ${JSON.stringify(postRes.body)}`);

        const created = postRes.body;

        // Verify server-generated ID is present and not in the input
        assert.ok(created.id !== undefined && created.id !== null, 'Response must have a server-generated id');
        assert.ok(!('id' in dto), 'Input DTO should not contain an id field');

        // GET the created sitting by ID
        const getRes = await request(app)
          .get(`/api/sittings/${created.id}`);

        assert.equal(getRes.status, 200, `Expected 200, got ${getRes.status}`);

        const fetched = getRes.body;

        // Assert all input fields match the fetched response
        assert.equal(fetched.title, dto.title, 'title mismatch');
        assert.equal(fetched.sessionType, dto.sessionType, 'sessionType mismatch');
        assert.equal(fetched.presidingOfficer, dto.presidingOfficer, 'presidingOfficer mismatch');
        assert.equal(fetched.dateFrom, dto.dateFrom, 'dateFrom mismatch');
        assert.equal(fetched.dateTo, dto.dateTo, 'dateTo mismatch');

        // Optional fields: compare with defaults applied by the route
        assert.equal(fetched.description, dto.description || null, 'description mismatch');
        assert.equal(fetched.committee, dto.committee || null, 'committee mismatch');
        assert.equal(fetched.parliament, dto.parliament || null, 'parliament mismatch');
        assert.equal(fetched.priority, dto.priority || 'Medium', 'priority mismatch');
        assert.equal(fetched.participants, dto.participants || 0, 'participants mismatch');
        assert.equal(fetched.topic, dto.topic || null, 'topic mismatch');
        assert.equal(fetched.orderPaperRef, dto.orderPaperRef || null, 'orderPaperRef mismatch');

        // Verify the id matches between POST response and GET response
        assert.equal(fetched.id, created.id, 'GET id should match POST id');

        // Verify records array is present (empty for new sitting)
        assert.ok(Array.isArray(fetched.records), 'GET response should include records array');
        assert.equal(fetched.records.length, 0, 'New sitting should have no records');
      }),
      { numRuns: 50 }
    );
  });
});
