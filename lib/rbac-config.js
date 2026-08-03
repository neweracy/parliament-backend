/**
 * Role-Permission Registry (RBAC Configuration).
 *
 * Loads role → permissions[] mappings from the `rbac_config` database table
 * and caches them in-memory with a 60-second TTL. Automatically reloads when
 * the data has been modified (checked via `updated_at`).
 *
 * Usage:
 *   const rbac = require('./lib/rbac-config');
 *   await rbac.loadPermissions();
 *   const perms = rbac.getPermissions('Editor'); // ['edit_record', ...]
 *
 * @module lib/rbac-config
 */

"use strict";

const { query } = require("./db");

/** @type {Map<string, string[]>} role → permissions array */
let permissionsCache = new Map();

/** Timestamp (epoch ms) of the last successful load */
let lastLoadedAt = 0;

/** The most recent `updated_at` value seen in the database */
let lastUpdatedAt = null;

/** Cache TTL in milliseconds (60 seconds) */
const CACHE_TTL_MS = 60_000;

/**
 * Load all role-permission mappings from the `rbac_config` table into memory.
 *
 * After loading, verifies that the Admin role's permissions are a superset of
 * all other roles' permissions. If not, the Admin set is augmented (logged as
 * a warning).
 *
 * @returns {Promise<Map<string, string[]>>} The populated permissions map.
 */
async function loadPermissions() {
  const result = await query(
    "SELECT role, permissions, updated_at FROM rbac_config ORDER BY role"
  );

  const newCache = new Map();
  let maxUpdatedAt = null;

  for (const row of result.rows) {
    const perms = Array.isArray(row.permissions) ? row.permissions : [];
    newCache.set(row.role, perms);

    const rowUpdated = row.updated_at ? new Date(row.updated_at) : null;
    if (rowUpdated && (!maxUpdatedAt || rowUpdated > maxUpdatedAt)) {
      maxUpdatedAt = rowUpdated;
    }
  }

  // Ensure Admin is a superset of all other roles
  ensureAdminSuperset(newCache);

  permissionsCache = newCache;
  lastLoadedAt = Date.now();
  lastUpdatedAt = maxUpdatedAt ? maxUpdatedAt.toISOString() : null;

  return permissionsCache;
}

/**
 * Return the cached permissions array for the given role.
 *
 * @param {string} role - One of: Admin, Chief Editor, Supervisor, Editor, Viewer
 * @returns {string[]} The permissions for that role, or an empty array if unknown.
 */
function getPermissions(role) {
  return permissionsCache.get(role) || [];
}

/**
 * Check whether the cache is stale (older than 60s). If so, query the
 * database for `MAX(updated_at)` in `rbac_config`. If the value differs
 * from what we last saw, reload the full permissions set.
 *
 * This is designed to be called on each request (or periodically) without
 * causing excessive database load — the staleness check is a cheap single-row
 * query, and a full reload only happens when data actually changed.
 *
 * @returns {Promise<void>}
 */
async function reloadIfStale() {
  const elapsed = Date.now() - lastLoadedAt;
  if (elapsed < CACHE_TTL_MS) {
    return; // Cache is still fresh
  }

  try {
    const result = await query(
      "SELECT MAX(updated_at) AS max_updated FROM rbac_config"
    );

    const dbMaxUpdated = result.rows[0]?.max_updated
      ? new Date(result.rows[0].max_updated).toISOString()
      : null;

    if (dbMaxUpdated !== lastUpdatedAt) {
      await loadPermissions();
    } else {
      // Data unchanged — just reset the timer so we don't check again for 60s
      lastLoadedAt = Date.now();
    }
  } catch (err) {
    console.error("[rbac-config] Failed to check staleness:", err.message);
    // On error, keep serving from the existing cache rather than crashing.
    // Reset the timer to avoid hammering the DB on repeated failures.
    lastLoadedAt = Date.now();
  }
}

/**
 * Ensure the Admin role contains every permission present in any other role.
 * Logs a warning if permissions had to be added.
 *
 * @param {Map<string, string[]>} cache
 */
function ensureAdminSuperset(cache) {
  const adminPerms = new Set(cache.get("Admin") || []);
  const allOtherPerms = new Set();

  for (const [role, perms] of cache) {
    if (role === "Admin") continue;
    for (const p of perms) {
      allOtherPerms.add(p);
    }
  }

  const missing = [];
  for (const p of allOtherPerms) {
    if (!adminPerms.has(p)) {
      missing.push(p);
      adminPerms.add(p);
    }
  }

  if (missing.length > 0) {
    console.warn(
      `[rbac-config] Admin role was missing ${missing.length} permission(s) ` +
        `present in other roles: ${missing.join(", ")}. Added automatically.`
    );
    cache.set("Admin", [...adminPerms]);
  }
}

module.exports = { loadPermissions, getPermissions, reloadIfStale };
