/**
 * Node Transcription Starter - Backend Server
 *
 * This is a simple Express server that provides a transcription API endpoint
 * powered by Deepgram's Speech-to-Text service. It's designed to be easily
 * modified and extended for your own projects.
 *
 * Key Features:
 * - Single API endpoint: POST /api/transcription
 * - Accepts both file uploads and URLs
 * - CORS enabled for frontend communication
 * - JWT session auth with rate limiting (production only)
 * - Pure API server (frontend served separately)
 */

require("dotenv").config({ override: true });

const { createClient } = require("@deepgram/sdk");
const cors = require("cors");
const crypto = require("crypto");
const express = require("express");
const fs = require("fs");
const jwt = require("jsonwebtoken");
const multer = require("multer");
const path = require("path");
const swaggerUi = require("swagger-ui-express");
const { correctLocations } = require("./lib/location-correction");
const { correctWordsWalk } = require("./lib/location-correction/word-walk");
const { postProcessWithBedrock, isBedrockConfigured } = require("./lib/location-correction/bedrock-postprocess");
const { correctYears, correctYearsInText } = require("./lib/location-correction/year-correction");
const { postprocess } = require("./lib/postprocess-client");
const { degradedResponse, mergeSuccess, logDegraded } = require("./lib/postprocess-mode");

// ============================================================================
// POSTPROCESS MODE — js (default) | python | off
// ============================================================================

const POSTPROCESS_MODE = ['js', 'python', 'off'].includes(process.env.POSTPROCESS_MODE)
  ? process.env.POSTPROCESS_MODE
  : (process.env.POSTPROCESS_MODE
      ? (console.warn(`[config] unsupported POSTPROCESS_MODE=${process.env.POSTPROCESS_MODE}, falling back to js`), 'js')
      : 'js');

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
// SESSION AUTH - JWT tokens for production security
// ============================================================================

/**
 * Session secret for signing JWTs.
 */
const SESSION_SECRET =
  process.env.SESSION_SECRET || crypto.randomBytes(32).toString("hex");

/** JWT expiry time (1 hour) */
const JWT_EXPIRY = "1h";

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
    jwt.verify(token, SESSION_SECRET);
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

// Enable CORS (wildcard is safe -- same-origin via Vite proxy / Caddy in production)
app.use(cors());

// ============================================================================
// API DOCS - Swagger UI serving the OpenAPI spec
// ============================================================================

/**
 * Load and serve the OpenAPI spec via Swagger UI at /docs
 */
function loadOpenApiSpec() {
  const specPath = path.join(__dirname, "contracts", "interfaces", "transcription", "openapi.yml");
  if (!fs.existsSync(specPath)) return null;

  const yaml = fs.readFileSync(specPath, "utf-8");
  // Use a basic approach: just serve the raw YAML via swagger-ui's yamlStr option
  return yaml;
}

const openApiYaml = loadOpenApiSpec();
if (openApiYaml) {
  app.use("/docs", swaggerUi.serve, swaggerUi.setup(null, {
    swaggerOptions: { url: "/api/openapi.yml" },
  }));
  app.get("/api/openapi.yml", (req, res) => {
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

  // python mode
  const correlationId = crypto.randomUUID();
  const result = await postprocess(rawTranscript, rawWords, {}, correlationId);
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
// SESSION ROUTES - Auth endpoints (unprotected)
// ============================================================================

/**
 * GET /api/session — Issues a signed JWT for session authentication.
 */
app.get("/api/session", (req, res) => {
  const token = jwt.sign(
    { iat: Math.floor(Date.now() / 1000) },
    SESSION_SECRET,
    { expiresIn: JWT_EXPIRY }
  );
  res.json({ token });
});

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
app.post("/api/transcription", requireSession, upload.single("file"), async (req, res) => {
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
app.get("/api/metadata", (req, res) => {
  try {
    const toml = require("toml");
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

/**
 * ADD YOUR CUSTOM ROUTES HERE
 *
 * Examples:
 * - POST /stt/transcribe-with-diarization
 * - POST /stt/summarize
 * - GET /health (health check endpoint)
 * - POST /webhooks/deepgram (callback endpoint)
 */

// ============================================================================
// KHAYA AI (GhanaNLP) ASR v3 - African Language Transcription
// ============================================================================

const khayaRoutes = require("./routes/khaya");
const khayaProvider = require("./providers/khaya");

app.use("/api/khaya", khayaRoutes(requireSession, upload));

// ============================================================================
// HYBRID CONFIDENCE TRANSCRIPTION - Deepgram + Khaya AI correction pipeline
// ============================================================================

const { sliceAndConcatAudio } = require("./lib/hybrid/audio-slicer");
const hybridRoutes = require("./routes/hybrid");

const hybridDeps = {
  transcribePrimary: transcribePrimaryForHybrid,
  khayaTranscribe: (buf, mime, lang) => khayaProvider.transcribe(buf, mime, lang),
  sliceAndConcatAudio: (buf, ranges) => sliceAndConcatAudio(buf, ranges),
  khayaConfigured: () => Boolean(khayaProvider.getApiKey()),
};

app.use("/api/transcription/hybrid", hybridRoutes(requireSession, upload, hybridDeps));

// ============================================================================
// AUDIO PROXY — allows the frontend WaveformPlayer to load remote audio
// that would otherwise be blocked by CORS (e.g. Deepgram static examples).
// ============================================================================

app.get("/api/audio-proxy", async (req, res) => {
  const audioUrl = req.query.url;
  if (!audioUrl) {
    return res.status(400).json(formatErrorResponse(new Error("Missing url query parameter"), 400));
  }

  try {
    const upstream = await fetch(audioUrl);
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
    const { Readable } = require("stream");
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

app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime_seconds: Math.floor(process.uptime()),
    postprocess_mode: POSTPROCESS_MODE,
    version: require('./package.json').version,
  });
});

// ============================================================================
// SERVER START
// ============================================================================

app.listen(CONFIG.port, CONFIG.host, () => {
  console.log("\n" + "=".repeat(70));
  console.log(`🚀 Backend API running at http://localhost:${CONFIG.port}`);
  console.log(`📡 GET  /api/session`);
  console.log(`📡 POST /api/transcription (auth required) [Deepgram]`);
  console.log(`📡 POST /api/transcription/hybrid (auth required) [Hybrid: Deepgram + Khaya]`);
  console.log(`📡 POST /api/khaya/transcription (auth required) [Khaya AI]`);
  console.log(`📡 GET  /api/khaya/languages`);
  console.log(`📡 GET  /api/metadata`);
  console.log(`📡 GET  /health`);
  if (openApiYaml) {
    console.log(`📖 API Docs at http://localhost:${CONFIG.port}/docs`);
  }
  if (!khayaProvider.getApiKey()) {
    console.log(`⚠️  KHAYA_API_KEY not set — Khaya AI endpoints will return 500`);
  }
  console.log("=".repeat(70) + "\n");
});
