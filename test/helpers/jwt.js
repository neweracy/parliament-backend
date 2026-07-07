"use strict";

const jwt = require("jsonwebtoken");

/**
 * Signs a valid JWT token with the given secret that expires in 1 hour.
 * @param {string} secret - The signing secret
 * @returns {string} A valid JWT token
 */
function signValidToken(secret) {
  return jwt.sign({ iat: Math.floor(Date.now() / 1000) }, secret, {
    expiresIn: "1h",
  });
}

/**
 * Signs an already-expired JWT token with the given secret.
 * @param {string} secret - The signing secret
 * @returns {string} An expired JWT token
 */
function signExpiredToken(secret) {
  return jwt.sign({ iat: Math.floor(Date.now() / 1000) }, secret, {
    expiresIn: -1,
  });
}

/**
 * Returns a malformed string that is not a valid JWT.
 * @returns {string} A malformed token string
 */
function invalidToken() {
  return "not.a.valid.token";
}

module.exports = { signValidToken, signExpiredToken, invalidToken };
