/**
 * Dashboard Routes
 *
 * Express router for production statistics.
 * Mounts at /api/dashboard
 *
 * @module routes/dashboard
 */

"use strict";

const express = require("express");
const requirePermission = require("../middleware/require-permission");

/**
 * Derives avatar initials from a person's name (max 2 characters).
 * Used when a record has no explicit assignee_avatar stored.
 *
 * @param {string} name - Full name
 * @returns {string} Uppercase initials, e.g. "Ama Boateng" -> "AB"
 */
function initialsOf(name) {
  if (!name) return "?";
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

/**
 * Creates the Dashboard router.
 * @param {Function} requireSession - JWT auth middleware
 * @param {Object} db - Database client with query(text, params) helper
 * @returns {express.Router}
 */
module.exports = function dashboardRoutes(requireSession, db) {
  const router = express.Router();

  /**
   * GET /api/dashboard/stats
   *
   * Returns production statistics:
   * - totalSittings: count of all sittings
   * - totalRecords: count of all hansard records
   * - recordsByStatus: { status: count } for each status
   * - totalTranscriptionHours: sum of duration_hours across all records
   * - weeklyOutput: records created per week for the last 12 weeks, with
   *   the audio hours produced in that week
   * - recentActivity: most recently touched records, newest first
   * - teamWorkload: record counts grouped by assignee
   */
  router.get("/api/dashboard/stats", requireSession, requirePermission("view_records"), async (req, res) => {
    try {
      // Run all queries in parallel for better performance
      const [
        sittingsCount,
        recordsCount,
        statusBreakdown,
        hoursSum,
        weeklyData,
        activityData,
        workloadData,
      ] = await Promise.all([
        db.query("SELECT COUNT(*) AS total FROM sitting"),
        db.query("SELECT COUNT(*) AS total FROM hansard_record"),
        db.query("SELECT status, COUNT(*) AS count FROM hansard_record GROUP BY status"),
        db.query("SELECT COALESCE(SUM(duration_hours), 0) AS total FROM hansard_record"),
        db.query(
          `SELECT date_trunc('week', created_at) AS week,
                  COUNT(*) AS count,
                  COALESCE(SUM(duration_hours), 0) AS hours
           FROM hansard_record
           WHERE created_at >= now() - interval '12 weeks'
           GROUP BY 1
           ORDER BY 1`
        ),
        // Recent activity is derived from record state rather than a separate
        // audit table: the most recently updated records, newest first.
        db.query(
          `SELECT hr.id,
                  hr.title,
                  hr.status,
                  hr.updated_at,
                  hr.assignee_name,
                  hr.audio_file_name,
                  s.id AS sitting_id,
                  s.title AS sitting_title
           FROM hansard_record hr
           JOIN sitting s ON s.id = hr.sitting_id
           ORDER BY hr.updated_at DESC
           LIMIT 8`
        ),
        db.query(
          `SELECT assignee_name,
                  MAX(assignee_role) AS assignee_role,
                  MAX(assignee_avatar) AS assignee_avatar,
                  COUNT(*) AS records
           FROM hansard_record
           WHERE assignee_name IS NOT NULL
           GROUP BY assignee_name
           ORDER BY COUNT(*) DESC, assignee_name ASC
           LIMIT 10`
        ),
      ]);

      const totalSittings = parseInt(sittingsCount.rows[0].total, 10);
      const totalRecords = parseInt(recordsCount.rows[0].total, 10);

      // Build recordsByStatus object
      const recordsByStatus = {};
      for (const row of statusBreakdown.rows) {
        recordsByStatus[row.status] = parseInt(row.count, 10);
      }

      const totalTranscriptionHours = parseFloat(hoursSum.rows[0].total) || 0;

      // Format weekly output
      const weeklyOutput = weeklyData.rows.map((row) => ({
        week: row.week instanceof Date ? row.week.toISOString() : String(row.week),
        count: parseInt(row.count, 10),
        hours: parseFloat(row.hours) || 0,
      }));

      /**
       * Maps a record's current status to a human-readable activity phrase.
       * "Draft" is ambiguous on its own — with audio present it means
       * transcription finished, without it the record is still awaiting upload.
       */
      function describeActivity(row) {
        switch (row.status) {
          case "Transcribing":
            return { action: "is transcribing", kind: "transcribe" };
          case "Editing":
            return { action: "is editing", kind: "edit" };
          case "Under Review":
            return { action: "submitted for review", kind: "submit" };
          case "Certified":
            return { action: "certified", kind: "certify" };
          case "Published":
            return { action: "published", kind: "certify" };
          case "Draft":
            return row.audio_file_name
              ? { action: "completed transcription for", kind: "transcribe" }
              : { action: "created", kind: "upload" };
          default:
            return { action: "updated", kind: "edit" };
        }
      }

      const recentActivity = activityData.rows.map((row) => {
        const { action, kind } = describeActivity(row);
        return {
          recordId: String(row.id),
          sittingId: String(row.sitting_id),
          // Unassigned work is attributed to the system, not a fake person.
          user: row.assignee_name || "System",
          action,
          kind,
          target: row.title,
          sittingTitle: row.sitting_title,
          timestamp:
            row.updated_at instanceof Date
              ? row.updated_at.toISOString()
              : String(row.updated_at),
        };
      });

      const teamWorkload = workloadData.rows.map((row) => ({
        name: row.assignee_name,
        role: row.assignee_role || "Editor",
        avatar: row.assignee_avatar || initialsOf(row.assignee_name),
        records: parseInt(row.records, 10),
      }));

      res.json({
        totalSittings,
        totalRecords,
        recordsByStatus,
        totalTranscriptionHours,
        weeklyOutput,
        recentActivity,
        teamWorkload,
      });
    } catch (err) {
      console.error("GET /api/dashboard/stats error:", err);
      res.status(500).json({
        error: {
          type: "ServerError",
          code: "INTERNAL_ERROR",
          message: "Failed to retrieve dashboard statistics",
        },
      });
    }
  });

  return router;
};
