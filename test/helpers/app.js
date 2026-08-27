/**
 * Test helper: in-test Express app builder.
 *
 * Constructs a minimal Express app that mounts the real Khaya router with a
 * production-faithful requireSession middleware and an in-memory multer instance.
 * Does NOT import server.js (it binds a port and loads the Deepgram client).
 *
 * This helper is the single source of truth for the test auth middleware.
 */

const express = require("express");
const multer = require("multer");
const jwt = require("jsonwebtoken");
const createRouter = require("../../routes/khaya");

/**
 * Builds an Express app that mounts the real Khaya router with a
 * production-faithful requireSession and an in-memory multer instance.
 * @param {{ secret: string }} opts
 * @returns {import('express').Express}
 */
function buildApp({ secret }) {
  const app = express();

  /**
   * requireSession middleware — identical behavior to server.js.
   * Uses the provided `secret` as SESSION_SECRET for JWT verification.
   */
  function requireSession(req, res, next) {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith("Bearer ")) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "MISSING_TOKEN",
          message: "Authorization header with Bearer token is required",
        },
      });
    }

    try {
      const token = authHeader.slice(7);
      const decoded = jwt.verify(token, secret);
      // Set req.user so requirePermission middleware has a valid identity
      req.user = { role: "Admin", ...decoded };
      next();
    } catch (err) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "INVALID_TOKEN",
          message:
            err.name === "TokenExpiredError"
              ? "Session expired, please refresh the page"
              : "Invalid session token",
        },
      });
    }
  }

  const upload = multer({ storage: multer.memoryStorage() });

  app.use("/api/khaya", createRouter(requireSession, upload));

  return app;
}

module.exports = buildApp;
