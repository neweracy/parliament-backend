/**
 * Node Transcription — Backend Gateway
 *
 * Express API server that terminates client requests, authenticates them with
 * JWT session tokens, calls the ASR providers (Deepgram and Khaya AI), and runs
 * transcript post-processing.
 *
 * Endpoints:
 * - POST /api/transcription          — Deepgram transcription + post-processing
 * - POST /api/transcription/hybrid   — Deepgram + Khaya AI hybrid correction
 * - POST /api/khaya/transcription    — Khaya AI transcription
 * - GET  /api/khaya/languages        — Khaya-supported languages
 * - GET  /api/session                — Issue a JWT session token
 * - GET  /api/metadata               — App metadata from deepgram.toml
 * - GET  /api/audio-proxy            — CORS-friendly remote audio proxy
 * - GET  /health                     — Health check
 *
 * Post-processing is selected by POSTPROCESS_MODE (js | python | off).
 */

require("dotenv").config({ override: true });

// --- Sentry error monitoring (must init before other imports that may throw) ---
const Sentry = require("@sentry/node");

if (process.env.SENTRY_DSN) {
  Sentry.init({
    dsn: process.env.SENTRY_DSN,
    environment: process.env.NODE_ENV || "development",
    release: require("./package.json").version,
    tracesSampleRate: parseFloat(process.env.SENTRY_TRACES_SAMPLE_RATE || "0.1"),
    // Don't send PII (emails, IPs) unless explicitly opted in
    sendDefaultPii: false,
  });
}

// --- Third-party ---
const { createClient } = require("@deepgram/sdk");
const express = require("express");
const jwt = require("jsonwebtoken");
const multer = require("multer");
const swaggerUi = require("swagger-ui-express");
const toml = require("toml");

// --- Node built-ins ---
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { Readable } = require("stream");

// --- Local: correction pipeline ---
const { correctLocations } = require("./lib/location-correction");
const { correctWordsWalk } = require("./lib/location-correction/word-walk");
const { postProcessWithBedrock, isBedrockConfigured } = require("./lib/location-correction/bedrock-postprocess");
const { correctYears, correctYearsInText } = require("./lib/location-correction/year-correction");
const { postprocess } = require("./lib/postprocess-client");
const { degradedResponse, mergeSuccess, logDegraded } = require("./lib/postprocess-mode");

// --- Local: database ---
const db = require("./lib/db");

// --- Local: Redis caching ---
const { getClient, disconnect, healthCheck: redisHealthCheck } = require("./lib/redis-client");
const { createCache } = require("./lib/cache");

const cache = createCache(getClient());

// --- Local: WebSocket real-time updates ---
const { initWebSocket } = require("./lib/ws-server");

// --- Local: routes and providers ---
const khayaRoutes = require("./routes/khaya");
const hybridRoutes = require("./routes/hybrid");
const sittingsRoutes = require("./routes/sittings");
const recordsRoutes = require("./routes/records");
const audioRoutes = require("./routes/audio");
const transcriptionRoutes = require("./routes/transcription");
const transcriptRoutes = require("./routes/transcript");
const searchRoutes = require("./routes/search");
const askRoutes = require("./routes/ask");
const dashboardRoutes = require("./routes/dashboard");
const settingsRoutes = require("./routes/settings");
const dictionaryRoutes = require("./routes/dictionary");
const usersRoutes = require("./routes/users");
const accountRoutes = require("./routes/account");
const khayaProvider = require("./providers/khaya");
const { sliceAndConcatAudio } = require("./lib/hybrid/audio-slicer");

// --- Local: authentication & RBAC ---
const { createCognitoAuth } = require("./middleware/cognito-auth");
const { loadPermissions } = require("./lib/rbac-config");
const requirePermission = require("./middleware/require-permission");
const authRoutes = require("./routes/auth");
const { resolveAuthMode, resolveSessionSecret, resolveBcryptCost, generateDummyHash, resolveJwtLifetime } = require("./lib/auth-config");

// --- Local: package metadata ---
const { version: APP_VERSION } = require("./package.json");

// ============================================================================
// POSTPROCESS MODE — js (default) | python | off
// ============================================================================

const VALID_POSTPROCESS_MODES = ['js', 'python', 'off'];

/**
 * Resolves POSTPROCESS_MODE from the environment.
 * Unknown values warn once and fall back to 'js'.
 *
 * @returns {'js' | 'python' | 'off'}
 */
function resolvePostprocessMode() {
  const raw = process.env.POSTPROCESS_MODE;

  if (!raw) return 'js';
  if (VALID_POSTPROCESS_MODES.includes(raw)) return raw;

  console.warn(`[config] unsupported POSTPROCESS_MODE=${raw}, falling back to js`);
  return 'js';
}

const POSTPROCESS_MODE = resolvePostprocessMode();

// ============================================================================
// CONFIGURATION - Customize these values for your needs
// ============================================================================

/**
 * Default transcription model to use when none is specified
 * Options: "nova-3", "nova-2", "nova", "enhanced", "base"
 * See: https://developers.deepgram.com/docs/models-languages-overview
 */
const DEFAULT_MODEL = "nova-3";

/**
 * Server configuration - These can be overridden via environment variables
 */
const CONFIG = {
  port: process.env.PORT || 8081,
  host: process.env.HOST || "0.0.0.0",
};

// ============================================================================
// AUTH CONFIGURATION — validated at startup before any listener binds
// ============================================================================

/**
 * Resolve auth mode first — this may terminate the process.
 * Must run before any express setup or listener binding.
 */
const AUTH_MODE = resolveAuthMode(process.env);

/**
 * Session secret for signing JWTs.
 * In legacy mode: required, validated (encoding, length, prohibited values).
 * In cognito mode: not used for local JWT signing, but validated if present.
 */
const SESSION_SECRET = AUTH_MODE === 'legacy'
  ? resolveSessionSecret(process.env)
  : (process.env.SESSION_SECRET ? resolveSessionSecret(process.env) : null);

/**
 * JWT lifetime in seconds (1–3600, default 900).
 * Validated at startup; replaces the previous hardcoded "1h".
 */
const JWT_LIFETIME = resolveJwtLifetime(process.env);

/**
 * Bcrypt cost factor (12–14, default 12) and timing-equalization dummy hash.
 * Used by the auth login route for credential verification.
 */
const BCRYPT_COST = resolveBcryptCost(process.env);
const DUMMY_HASH = generateDummyHash(BCRYPT_COST);

/**
 * Express middleware that validates JWT from Authorization header.
 * Returns 401 with JSON error if token is missing or invalid.
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

    // Pin algorithm to HS256; validate issuer, audience, and clock skew
    const payload = jwt.verify(token, SESSION_SECRET, {
      algorithms: ['HS256'],
      issuer: 'parliament-gateway',
      audience: 'hansard-spa',
      clockTolerance: 30, // ±30s skew for iat/exp
    });

    // Validate iat: must be present and exp must not exceed iat + 3600s
    if (typeof payload.iat !== 'number' || typeof payload.exp !== 'number') {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "INVALID_TOKEN",
          message: "Invalid session token",
        },
      });
    }
    if (payload.exp <= payload.iat || (payload.exp - payload.iat) > 3600) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "INVALID_TOKEN",
          message: "Invalid session token",
        },
      });
    }

    // Validate sub: non-empty string, 1–128 UTF-8 bytes
    if (!payload.sub || typeof payload.sub !== 'string' || Buffer.byteLength(payload.sub, 'utf8') > 128) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "INVALID_TOKEN",
          message: "Invalid session token",
        },
      });
    }

    // Validate jti: non-empty ASCII string, 16–128 characters
    if (
      !payload.jti ||
      typeof payload.jti !== 'string' ||
      payload.jti.length < 16 ||
      payload.jti.length > 128
    ) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "INVALID_TOKEN",
          message: "Invalid session token",
        },
      });
    }

    // Validate role: must be in the server allowlist
    const { ROLE_PERMISSIONS, ALLOWLISTED_ROLES } = require("./routes/auth");
    if (!payload.role || !ALLOWLISTED_ROLES.has(payload.role)) {
      return res.status(401).json({
        error: {
          type: "AuthenticationError",
          code: "INVALID_TOKEN",
          message: "Invalid session token",
        },
      });
    }

    // Derive permissions exclusively from server-side map; never from JWT or request
    const permissions = ROLE_PERMISSIONS[payload.role] || [];
    req.user = {
      userId: payload.sub,
      email: payload.email || '',
      name: payload.name || '',
      role: payload.role,
      permissions,
    };

    next();
  } catch (_err) {
    return res.status(401).json({
      error: {
        type: "AuthenticationError",
        code: "INVALID_TOKEN",
        message: "Invalid session token",
      },
    });
  }
}

// ============================================================================
// AUTH MIDDLEWARE — select based on validated AUTH_MODE
// ============================================================================

/**
 * The active authentication middleware passed to route factories.
 * In 'cognito' mode, this validates Cognito JWTs and attaches req.user with
 * userId, email, name, role, and permissions. In 'legacy' mode, this is the
 * existing requireSession middleware.
 */
let authMiddleware;

if (AUTH_MODE === 'cognito') {
  const userPoolId = process.env.COGNITO_USER_POOL_ID;
  const region = process.env.COGNITO_REGION;
  const appClientId = process.env.COGNITO_APP_CLIENT_ID;

  if (!userPoolId || !region || !appClientId) {
    console.error('\n❌ ERROR: AUTH_MODE=cognito requires the following env vars:');
    console.error('   COGNITO_USER_POOL_ID, COGNITO_REGION, COGNITO_APP_CLIENT_ID\n');
    process.exit(1);
  }

  authMiddleware = createCognitoAuth({ userPoolId, region, appClientId });
} else {
  authMiddleware = requireSession;
}

// ============================================================================
// API KEY LOADING - Load Deepgram API key from .env or config.json
// ============================================================================

/**
 * Loads the Deepgram API key from environment variables or config.json
 * Priority: DEEPGRAM_API_KEY env var > config.json > error
 */
function loadApiKey() {
  // Try environment variable first (recommended)
  let apiKey = process.env.DEEPGRAM_API_KEY;

  // Fall back to config.json if it exists
  if (!apiKey) {
    try {
      const config = require("./config.json");
      apiKey = config.dgKey;
    } catch (_err) {
      // config.json doesn't exist or is invalid - that's ok
    }
  }

  // Exit with helpful error if no API key found
  if (!apiKey) {
    console.error("\n❌ ERROR: Deepgram API key not found!\n");
    console.error("Please set your API key using one of these methods:\n");
    console.error("1. Create a .env file (recommended):");
    console.error("   DEEPGRAM_API_KEY=your_api_key_here\n");
    console.error("2. Environment variable:");
    console.error("   export DEEPGRAM_API_KEY=your_api_key_here\n");
    console.error("3. Create a config.json file:");
    console.error("   cp config.json.example config.json");
    console.error("   # Then edit config.json with your API key\n");
    console.error("Get your API key at: https://console.deepgram.com\n");
    process.exit(1);
  }

  return apiKey;
}

const apiKey = loadApiKey();

// ============================================================================
// SETUP - Initialize Express, Deepgram, and middleware
// ============================================================================

// Initialize Deepgram client
const deepgram = createClient(apiKey);

// Configure Multer for file uploads (stores files in memory)
const storage = multer.memoryStorage();
const upload = multer({ storage: storage });

// Initialize Express app
const app = express();

// CORS: exact-origin policy from FRONTEND_ORIGINS (Req 13.9–13.16)
const createCorsPolicy = require("./middleware/cors-policy");
app.use(createCorsPolicy());

/**
 * Global JSON body limit.
 *
 * Was '2kb', which silently broke POST /api/ask: the frontend forwards
 * conversationHistory on every turn, so the body outgrew 2KB after about three
 * exchanges and the parser returned 413 before the handler's own .slice(-20)
 * trim could run. 256kb fits a 20-message history while still bounding the
 * request.
 *
 * The login route is excluded on purpose. It mounts its own 2KB parser so
 * credential payloads stay tightly bounded; letting this parser consume that
 * body first makes the route-level limit and its strict:false setting dead code.
 */
const JSON_BODY_LIMIT = process.env.JSON_BODY_LIMIT || '256kb';
const LOGIN_PATH = '/api/auth/login';
const globalJsonParser = express.json({ limit: JSON_BODY_LIMIT, strict: true });

app.use((req, res, next) => {
  if (req.path === LOGIN_PATH) return next();
  return globalJsonParser(req, res, next);
});

// Security headers: CSP, X-Content-Type-Options, Referrer-Policy, X-Frame-Options,
// HSTS, and auth cache controls (Req 14.1–14.12)
const createSecurityHeaders = require("./middleware/security-headers");
app.use(createSecurityHeaders({
  authMode: AUTH_MODE,
  cognitoDomain: process.env.COGNITO_DOMAIN,
  isProduction: process.env.NODE_ENV === 'production',
}));

// ============================================================================
// API DOCS - Swagger UI serving the OpenAPI spec
// ============================================================================

/**
 * Load and serve the OpenAPI spec via Swagger UI at /docs
 */
function loadOpenApiSpec() {
  const specPath = path.join(__dirname, "contracts", "interfaces", "transcription", "openapi.yml");
  if (!fs.existsSync(specPath)) return null;

  // Served as raw YAML at /api/openapi.yml; Swagger UI fetches it by URL rather
  // than being handed a parsed object, so no YAML parser is needed here.
  return fs.readFileSync(specPath, "utf-8");
}

const openApiYaml = loadOpenApiSpec();
if (openApiYaml) {
  app.use("/docs", swaggerUi.serve, swaggerUi.setup(null, {
    swaggerOptions: { url: "/api/openapi.yml" },
  }));
  app.get("/api/openapi.yml", (_req, res) => {
    res.type("text/yaml").send(openApiYaml);
  });
}

// ============================================================================
// HELPER FUNCTIONS - Modular logic for easier understanding and testing
// ============================================================================

/**
 * Validates that either a file or URL was provided in the request
 * @param {Object} file - Multer file object
 * @param {string} url - URL string from request body
 * @returns {Object|null} - Request object for Deepgram, or null if invalid
 */
function validateTranscriptionInput(file, url) {
  // URL-based transcription
  if (url) {
    return { url };
  }

  // File-based transcription
  if (file) {
    return { buffer: file.buffer, mimetype: file.mimetype };
  }

  // Neither provided
  return null;
}

/**
 * Sends a transcription request to Deepgram
 * @param {Object} dgRequest - Request object with url OR buffer+mimetype
 * @param {string} model - Model name to use (e.g., "nova-3")
 * @returns {Promise<Object>} - Deepgram API response
 */
async function transcribeAudio(dgRequest, model = DEFAULT_MODEL) {
  // URL transcription
  if (dgRequest.url) {
    return await deepgram.listen.prerecorded.transcribeUrl(
      { url: dgRequest.url },
      { model }
    );
  }

  // File transcription
  return await deepgram.listen.prerecorded.transcribeFile(dgRequest.buffer, {
    model,
    mimetype: dgRequest.mimetype,
  });
}

/**
 * Primary transcription wrapper for the hybrid confidence pipeline.
 *
 * Unlike the generic transcribeAudio helper, this always requests punctuated
 * output so the Unified_Transcript reads naturally. Deepgram returns per-word
 * `confidence`, `start`, and `end` in its default word output, which the hybrid
 * pipeline relies on via extractWords. Returns the raw SDK response (has a
 * `.result` property).
 *
 * @param {{ buffer: Buffer, mimetype: string }} req - Audio buffer + MIME type
 * @returns {Promise<Object>} - Raw Deepgram API response
 */
async function transcribePrimaryForHybrid({ buffer, mimetype }) {
  return await deepgram.listen.prerecorded.transcribeFile(buffer, {
    model: DEFAULT_MODEL,
    mimetype,
    punctuate: true,
  });
}

/**
 * Extracts the raw Deepgram result from the transcription response.
 * Throws when the alternatives array is empty (ASR failure, not post-processing failure).
 *
 * @param {Object} transcriptionResponse - Raw Deepgram API response
 * @param {string} modelName - Name of model used for transcription
 * @returns {{ rawTranscript: string, rawWords: Array, meta: Object, duration: number|undefined }}
 */
function extractDeepgramResult(transcriptionResponse, modelName) {
  const transcription = transcriptionResponse.result;
  const result = transcription?.results?.channels?.[0]?.alternatives?.[0];

  if (!result) {
    throw new Error("No transcription results returned from Deepgram");
  }

  const rawTranscript = result.transcript || "";
  const rawWords = (result.words || []).map(w => ({ ...w }));

  const meta = {
    model_uuid: transcription.metadata?.model_uuid,
    request_id: transcription.metadata?.request_id,
    model_name: modelName,
  };

  const duration = transcription.metadata?.duration;

  return { rawTranscript, rawWords, meta, duration };
}

/**
 * Legacy JavaScript post-processing pipeline.
 * Runs the rule-based correction engine and optional Bedrock LLM pass.
 * This is the existing pipeline, preserved exactly for POSTPROCESS_MODE=js.
 *
 * @param {string} rawTranscript - Raw transcript text from Deepgram
 * @param {Array} rawWords - Raw words array from Deepgram (already shallow-copied)
 * @param {Object} meta - Gateway-owned metadata (model_uuid, request_id, model_name)
 * @param {number|undefined} duration - Duration from Deepgram metadata
 * @returns {Object} - Formatted response object
 */
async function legacyPostprocess(rawTranscript, rawWords, meta, duration) {
  // Preserve raw (unprocessed) transcript and words before any corrections
  const rawWordsOriginal = rawWords.map(w => ({ ...w }));

  // Run location correction on transcript text (handles multi-word joins)
  const correctedTranscript = correctLocations(rawTranscript);

  // Run location correction on individual words, including multi-word joins
  const words = correctWordsWalk(rawWords);

  // === Year/Date Correction (rule-based, runs on words array) ===
  const yearResult = correctYears(words);
  const yearCorrectedWords = yearResult.words;
  const yearTextResult = correctYearsInText(correctedTranscript.text);

  // Build response object
  const response = {
    transcript: yearTextResult.text,
    words: yearCorrectedWords,
    metadata: {
      ...meta,
    },
  };

  if (yearResult.corrections.length > 0) {
    response.metadata.year_corrections = yearResult.corrections.length;
  }

  // Record how many misspellings were actually fixed
  if (correctedTranscript.corrections.length > 0) {
    response.metadata.location_corrections = correctedTranscript.corrections.length;
  }

  // Build the full entities list from EVERY recognized mention — both
  // corrected misspellings and already correctly-spelled names (e.g.
  // "Greater Accra" spoken correctly still shows up here, it just isn't
  // counted as a "correction" since nothing needed fixing).
  if (correctedTranscript.entitiesFound?.length > 0) {
    const entityMap = new Map();
    for (const e of correctedTranscript.entitiesFound) {
      if (!entityMap.has(e.corrected)) {
        entityMap.set(e.corrected, {
          name: e.corrected,
          kind: e.entityKind,
          type: e.entityType,
          mentions: 0,
        });
      }
      entityMap.get(e.corrected).mentions++;
    }
    response.entities = Array.from(entityMap.values());
  }
  response.metadata._version = 'v5-bedrock';

  // Add optional fields if available
  if (duration !== undefined) {
    response.duration = duration;
  }

  // === Bedrock LLM Post-Processing (optional, runs on low-confidence words) ===
  if (isBedrockConfigured()) {
    try {
      const bedrockResult = await postProcessWithBedrock(
        response.transcript,
        response.words
      );
      response.transcript = bedrockResult.transcript;
      response.words = bedrockResult.words;
      if (bedrockResult.bedrockCorrections > 0) {
        response.metadata.bedrock_corrections = bedrockResult.bedrockCorrections;
      }
    } catch (err) {
      // Non-fatal — if Bedrock fails, we still return the rule-based result
      console.error('Bedrock post-processing error (non-fatal):', err.message);
    }
  }

  // Include raw (unprocessed) transcript for comparison view
  response.raw = {
    transcript: rawTranscript,
    words: rawWordsOriginal,
  };

  return response;
}

/**
 * Formats Deepgram's response into a simplified, consistent structure.
 * Acts as a mode dispatcher based on POSTPROCESS_MODE:
 * - 'off': returns degraded response (disabled)
 * - 'js': runs the existing JavaScript pipeline unchanged
 * - 'python': calls the Postprocessing_Service
 *
 * @param {Object} transcriptionResponse - Raw Deepgram API response
 * @param {string} modelName - Name of model used for transcription
 * @returns {Object} - Formatted response object
 */
async function formatTranscriptionResponse(transcriptionResponse, modelName) {
  const { rawTranscript, rawWords, meta, duration } = extractDeepgramResult(transcriptionResponse, modelName);

  if (POSTPROCESS_MODE === 'off') return degradedResponse(rawTranscript, rawWords, meta, duration, 'disabled');
  if (POSTPROCESS_MODE === 'js') return legacyPostprocess(rawTranscript, rawWords, meta, duration);

  // python mode — fetch custom dictionary for postprocess options
  const correlationId = crypto.randomUUID();
  let customDictionary = [];
  try {
    const settingsResult = await db.query("SELECT custom_dictionary FROM app_settings WHERE id = 1");
    customDictionary = settingsResult.rows[0]?.custom_dictionary || [];
  } catch (_err) {
    // Non-fatal — proceed without dictionary if DB lookup fails
    console.warn("[postprocess] Failed to fetch custom dictionary:", _err.message);
  }
  const options = customDictionary.length > 0 ? { customDictionary } : {};
  const result = await postprocess(rawTranscript, rawWords, options, correlationId);
  if (!result.ok) {
    logDegraded(result, correlationId);
    return degradedResponse(rawTranscript, rawWords, meta, duration, 'skipped');
  }
  return mergeSuccess(result.data, rawTranscript, rawWords, meta, duration);
}

/**
 * Formats error responses in a consistent structure
 * @param {Error} error - The error that occurred
 * @param {number} statusCode - HTTP status code to return
 * @returns {Object} - Formatted error response
 */
function formatErrorResponse(error, statusCode = 500) {
  return {
    statusCode,
    body: {
      error: {
        type: statusCode === 400 ? "ValidationError" : "TranscriptionError",
        code: statusCode === 400 ? "MISSING_INPUT" : "TRANSCRIPTION_FAILED",
        message: error.message || "An error occurred during transcription",
        details: {
          originalError: error.toString(),
        },
      },
    },
  };
}

// ============================================================================
// SESSION ROUTES - Auth endpoints (conditionally mounted based on AUTH_MODE)
// ============================================================================

if (AUTH_MODE === 'legacy') {
  // Local password authentication (public — no auth middleware)
  app.use(authRoutes(db, { sessionSecret: SESSION_SECRET, jwtLifetime: JWT_LIFETIME, bcryptCost: BCRYPT_COST, dummyHash: DUMMY_HASH }));

  // In legacy mode, the anonymous GET /api/session endpoint is disabled.
  // It previously minted tokens without credentials — now returns 410 Gone.
  app.get("/api/session", (_req, res) => {
    res.status(410).json({
      error: {
        type: "AuthenticationError",
        code: "ENDPOINT_REMOVED",
        message: "Anonymous session tokens are no longer issued. Use POST /api/auth/login.",
      },
    });
  });
}
// In cognito mode: neither authRoutes nor GET /api/session are mounted.
// Requests to those paths will naturally return 404.

// ============================================================================
// API ROUTES - Define your API endpoints here
// ============================================================================

/**
 * POST /api/transcription
 *
 * Main transcription endpoint. Accepts either:
 * - A file upload (multipart/form-data with 'file' field)
 * - A URL to audio file (form data with 'url' field)
 *
 * Optional parameters:
 * - model: Deepgram model to use (default: "nova-3")
 *
 * Protected by JWT session auth (requireSession middleware).
 */
app.post("/api/transcription", authMiddleware, requirePermission("upload_audio"), upload.single("file"), async (req, res) => {
  try {
    const { body, file } = req;
    const { url, model } = body;

    // Validate input - must have either file or URL
    const dgRequest = validateTranscriptionInput(file, url);
    if (!dgRequest) {
      const errorResponse = formatErrorResponse(
        new Error("Either file or url must be provided"),
        400
      );
      return res.status(errorResponse.statusCode).json(errorResponse.body);
    }

    // Send transcription request to Deepgram
    const transcriptionResponse = await transcribeAudio(
      dgRequest,
      model || DEFAULT_MODEL
    );

    // Format and return response
    const response = await formatTranscriptionResponse(
      transcriptionResponse,
      model || DEFAULT_MODEL
    );
    res.json(response);
  } catch (err) {
    console.error("Transcription error:", err);

    // Return formatted error response
    const errorResponse = formatErrorResponse(err);
    res.status(errorResponse.statusCode).json(errorResponse.body);
  }
});

/**
 * GET /api/metadata
 *
 * Returns metadata about this starter application from deepgram.toml
 * Required for standardization compliance
 */
app.get("/api/metadata", (_req, res) => {
  try {
    const tomlPath = path.join(__dirname, "deepgram.toml");
    const tomlContent = fs.readFileSync(tomlPath, "utf-8");
    const config = toml.parse(tomlContent);

    if (!config.meta) {
      return res.status(500).json({
        error: "INTERNAL_SERVER_ERROR",
        message: "Missing [meta] section in deepgram.toml",
      });
    }

    res.json(config.meta);
  } catch (error) {
    console.error("Error reading metadata:", error);
    res.status(500).json({
      error: "INTERNAL_SERVER_ERROR",
      message: "Failed to read metadata from deepgram.toml",
    });
  }
});

// ============================================================================
// KHAYA AI (GhanaNLP) ASR v3 - African Language Transcription
// ============================================================================

app.use("/api/khaya", khayaRoutes(authMiddleware, upload));

// ============================================================================
// HYBRID CONFIDENCE TRANSCRIPTION - Deepgram + Khaya AI correction pipeline
// ============================================================================

const hybridDeps = {
  transcribePrimary: transcribePrimaryForHybrid,
  khayaTranscribe: (buf, mime, lang) => khayaProvider.transcribe(buf, mime, lang),
  sliceAndConcatAudio: (buf, ranges) => sliceAndConcatAudio(buf, ranges),
  khayaConfigured: () => Boolean(khayaProvider.getApiKey()),
};

app.use("/api/transcription/hybrid", hybridRoutes(authMiddleware, upload, hybridDeps));

// ============================================================================
// HANSARD CRUD ROUTES — Sittings, Records, and Audio
// ============================================================================

app.use(sittingsRoutes(authMiddleware, db));
app.use(recordsRoutes(authMiddleware, db));
app.use(audioRoutes(authMiddleware, db));
app.use(transcriptionRoutes(authMiddleware, db, cache));
app.use(transcriptRoutes(authMiddleware, db));
app.use(searchRoutes(authMiddleware, db, cache));
app.use(askRoutes(authMiddleware, db));
app.use(dashboardRoutes(authMiddleware, db, cache));
app.use(settingsRoutes(authMiddleware, db));
app.use(dictionaryRoutes(authMiddleware, db));
app.use(usersRoutes(authMiddleware, db));

// Self-service account routes. Separate from usersRoutes because these always
// act on req.user's own row and therefore need no manage_users permission.
app.use(accountRoutes(authMiddleware, db, { bcryptCost: BCRYPT_COST, authMode: AUTH_MODE }));

// ============================================================================
// AUDIO PROXY — allows the frontend WaveformPlayer to load remote audio
// that would otherwise be blocked by CORS (e.g. Deepgram static examples).
// ============================================================================

/**
 * Hostnames and IP ranges the proxy refuses to fetch.
 *
 * Without this the endpoint is a server-side request forgery primitive: any
 * caller could reach cloud instance metadata (169.254.169.254), the internal
 * Postprocessing Service, or Postgres, using the Gateway's network position.
 */
const BLOCKED_HOST_PATTERNS = [
  /^localhost$/i,
  /^127\./,                              // IPv4 loopback
  /^0\./,                                // "this network"
  /^10\./,                               // RFC1918 private
  /^192\.168\./,                         // RFC1918 private
  /^172\.(1[6-9]|2\d|3[01])\./,          // RFC1918 private
  /^169\.254\./,                         // link-local, incl. cloud metadata
  /^::1$/,                               // IPv6 loopback
  /^\[?::1\]?$/,
  /^f[cd][0-9a-f]{2}:/i,                 // IPv6 unique-local
  /^fe80:/i,                             // IPv6 link-local
  /\.internal$/i,
  /\.local$/i,
];

/**
 * Validates a proxy target, rejecting anything that is not a plain public
 * http(s) URL.
 *
 * @param {unknown} rawUrl
 * @returns {{ ok: true, url: URL } | { ok: false, reason: string }}
 */
function validateProxyTarget(rawUrl) {
  if (typeof rawUrl !== "string" || rawUrl.length === 0) {
    return { ok: false, reason: "Missing url query parameter" };
  }

  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return { ok: false, reason: "url is not a valid absolute URL" };
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return { ok: false, reason: "Only http and https URLs may be proxied" };
  }

  const host = parsed.hostname;
  if (BLOCKED_HOST_PATTERNS.some((pattern) => pattern.test(host))) {
    return { ok: false, reason: "Target host is not permitted" };
  }

  return { ok: true, url: parsed };
}

app.get("/api/audio-proxy", authMiddleware, requirePermission("view_records"), async (req, res) => {
  const validation = validateProxyTarget(req.query.url);
  if (!validation.ok) {
    return res.status(400).json(formatErrorResponse(new Error(validation.reason), 400));
  }
  const audioUrl = validation.url.toString();

  try {
    // redirect: 'error' so a public URL cannot 302 into a blocked internal host.
    const upstream = await fetch(audioUrl, { redirect: "error" });
    if (!upstream.ok) {
      return res.status(upstream.status).json(
        formatErrorResponse(new Error(`Upstream returned ${upstream.status}`), upstream.status)
      );
    }

    // Forward content-type and stream the body
    const contentType = upstream.headers.get("content-type") || "audio/mpeg";
    const contentLength = upstream.headers.get("content-length");
    res.setHeader("Content-Type", contentType);
    if (contentLength) res.setHeader("Content-Length", contentLength);
    res.setHeader("Cache-Control", "public, max-age=3600");

    // Use Node.js Readable stream from the fetch body
    const readable = Readable.fromWeb(upstream.body);
    readable.pipe(res);
  } catch (err) {
    if (!res.headersSent) {
      res.status(502).json(formatErrorResponse(err, 502));
    }
  }
});

// ============================================================================
// HEALTH CHECK — unauthenticated, no outbound calls, no secrets exposed
// ============================================================================

app.get('/health', async (_req, res) => {
  const redisHealth = await redisHealthCheck();
  res.json({
    status: 'ok',
    uptime_seconds: Math.floor(process.uptime()),
    postprocess_mode: POSTPROCESS_MODE,
    version: APP_VERSION,
    ws_clients: require("./lib/ws-server").getClientCount(),
    redis: redisHealth.state,
  });
});

// ============================================================================
// BODY-PARSER ERROR ENVELOPE — after all routes, before the Sentry handler
// ============================================================================

/**
 * Body-parser error envelope.
 *
 * Converts raw body-parser failures into this project's standard error shape so
 * clients get something actionable instead of a bare "Payload Too Large".
 */
app.use((err, _req, res, next) => {
  if (err && (err.type === 'entity.too.large' || err.status === 413)) {
    return res.status(413).json({
      error: {
        type: 'ValidationError',
        code: 'PAYLOAD_TOO_LARGE',
        message: 'Request body is too large. Start a new conversation or shorten your question.',
      },
    });
  }
  if (err && err.type === 'entity.parse.failed') {
    return res.status(400).json({
      error: {
        type: 'ValidationError',
        code: 'INVALID_JSON',
        message: 'Request body must be valid JSON',
      },
    });
  }
  return next(err);
});

// ============================================================================
// SENTRY ERROR HANDLER — must be registered after all routes, before listen
// ============================================================================

if (process.env.SENTRY_DSN) {
  Sentry.setupExpressErrorHandler(app);
}

// ============================================================================
// SERVER START
// ============================================================================

/**
 * Routes advertised in the startup banner.
 * Keep in sync when adding an endpoint — see the backend guide convention.
 */
const ADVERTISED_ROUTES = [
  ...(AUTH_MODE === 'legacy' ? [
    { method: "POST", path: "/api/auth/login", detail: "(legacy mode)" },
    { method: "GET", path: "/api/session", detail: "(disabled — returns 410)" },
  ] : []),
  { method: "POST", path: "/api/transcription", detail: "(auth required) [Deepgram]" },
  { method: "POST", path: "/api/transcription/hybrid", detail: "(auth required) [Hybrid: Deepgram + Khaya]" },
  { method: "POST", path: "/api/khaya/transcription", detail: "(auth required) [Khaya AI]" },
  { method: "GET", path: "/api/khaya/languages", detail: "" },
  { method: "GET", path: "/api/metadata", detail: "" },
  { method: "GET", path: "/health", detail: "" },
];

const server = app.listen(CONFIG.port, CONFIG.host, async () => {
  // Warm the RBAC permissions cache on startup
  try {
    await loadPermissions();
    console.log('[rbac] Permissions cache loaded successfully');
  } catch (err) {
    console.warn('[rbac] Failed to load permissions on startup:', err.message);
    console.warn('[rbac] Permissions will be loaded on first request');
  }

  const divider = "=".repeat(70);

  console.log(`\n${divider}`);
  console.log(`🚀 Backend API running at http://localhost:${CONFIG.port}`);

  for (const { method, path: routePath, detail } of ADVERTISED_ROUTES) {
    console.log(`📡 ${method.padEnd(4)} ${routePath}${detail ? ` ${detail}` : ""}`);
  }

  console.log(`⚙️  Post-processing mode: ${POSTPROCESS_MODE}`);
  console.log(`🔐 Auth mode: ${AUTH_MODE}`);

  if (openApiYaml) {
    console.log(`📖 API Docs at http://localhost:${CONFIG.port}/docs`);
  }
  if (!khayaProvider.getApiKey()) {
    console.log(`⚠️  KHAYA_API_KEY not set — Khaya AI endpoints will return 500`);
  }

  console.log(`${divider}\n`);

  // Initialize WebSocket server for live updates
  initWebSocket(server);
});

// ============================================================================
// GRACEFUL SHUTDOWN — close Redis connection on termination signals
// ============================================================================

process.on("SIGTERM", async () => {
  await disconnect();
  process.exit(0);
});

process.on("SIGINT", async () => {
  await disconnect();
  process.exit(0);
});

