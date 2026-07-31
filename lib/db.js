/**
 * PostgreSQL connection pool for the Gateway.
 *
 * Configured from DATABASE_URL environment variable.
 * Exports a shared Pool instance and a convenience query helper.
 *
 * @module lib/db
 */

"use strict";

const { Pool } = require("pg");

const rawUrl = process.env.DATABASE_URL || "";

if (!rawUrl) {
  console.warn(
    "[db] DATABASE_URL is not set. Database-backed endpoints (sittings, " +
      "records, transcripts, dashboard, settings) will fail until it is " +
      "configured in transcript-end/.env"
  );
}

/**
 * SQLAlchemy-style URLs used by the Postprocessing Service carry a driver
 * suffix (e.g. postgresql+psycopg://). node-postgres does not understand it
 * and silently parses the credentials wrong, which surfaces later as an
 * opaque "client password must be a string" SASL error. Normalise it here so
 * the same URL can be shared between both services.
 */
const connectionString = rawUrl.replace(/^postgresql\+\w+:\/\//, "postgresql://");

const pool = new Pool({
  connectionString,
  max: 20,
  idleTimeoutMillis: 30000,
});

// Surface pool-level failures instead of letting them take down the process.
pool.on("error", (err) => {
  console.error("[db] Unexpected idle client error:", err.message);
});

module.exports = { pool, query: (text, params) => pool.query(text, params) };
