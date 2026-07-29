'use strict';

/**
 * Configuration Consistency Check
 *
 * Verifies that every environment variable read by source code is documented
 * in the corresponding Config_Template (sample.env), and that code defaults
 * agree with template values.
 *
 * Validates: Requirements 5.1, 5.2, 5.3, 5.7
 *
 * @module test/consistency/config
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const ROOT = path.resolve(__dirname, '../..');
const GATEWAY_SAMPLE_ENV = path.join(ROOT, 'sample.env');
const SERVICE_SAMPLE_ENV = path.join(ROOT, 'services', 'postprocess', 'sample.env');
const SERVICE_CONFIG_PY = path.join(ROOT, 'services', 'postprocess', 'app', 'config.py');

// ---------------------------------------------------------------------------
// Source scan directories
// ---------------------------------------------------------------------------

const GATEWAY_GLOBS = [
  path.join(ROOT, 'server.js'),
];

const GATEWAY_DIRS = [
  path.join(ROOT, 'lib'),
  path.join(ROOT, 'routes'),
  path.join(ROOT, 'providers'),
];

// ---------------------------------------------------------------------------
// Exemption patterns — secrets and placeholders are not compared for defaults
// ---------------------------------------------------------------------------

const SECRET_PATTERNS = [
  /^%.*%$/,            // %api_key%, %session_secret%
  /^your-.*-here$/,    // your-postprocess-token-here
  /^your-.*$/,         // your-service-token-here
  /^postgresql\+psycopg:\/\//,  // database URLs
];

/**
 * Returns true if a template value is a placeholder/secret that should
 * be exempt from code-default comparison.
 */
function isExempt(value) {
  if (!value) return true;
  return SECRET_PATTERNS.some(p => p.test(value));
}

// ---------------------------------------------------------------------------
// File scanning utilities
// ---------------------------------------------------------------------------

/**
 * Recursively collect .js files from a directory, excluding node_modules.
 */
function collectJsFiles(dir) {
  const results = [];
  if (!fs.existsSync(dir)) return results;
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === 'node_modules' || entry.name === '.git') continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...collectJsFiles(fullPath));
    } else if (entry.name.endsWith('.js')) {
      results.push(fullPath);
    }
  }
  return results;
}

/**
 * Discover env var reads from gateway JS source files.
 * Matches:
 *   process.env.VAR_NAME
 *   env.VAR_NAME (for injected env objects like in lib/hybrid/config.js)
 *
 * Returns Map<varName, Set<sourceFile>>
 */
function discoverGatewayEnvVars() {
  const discovered = new Map();

  const files = [...GATEWAY_GLOBS];
  for (const dir of GATEWAY_DIRS) {
    files.push(...collectJsFiles(dir));
  }

  const processEnvRe = /process\.env\.([A-Z_][A-Z0-9_]*)/g;
  const envRe = /\benv\.([A-Z_][A-Z0-9_]*)/g;

  for (const filePath of files) {
    const content = fs.readFileSync(filePath, 'utf8');
    const relPath = path.relative(ROOT, filePath);

    for (const re of [processEnvRe, envRe]) {
      re.lastIndex = 0;
      let match;
      while ((match = re.exec(content)) !== null) {
        const varName = match[1];
        if (!discovered.has(varName)) {
          discovered.set(varName, new Set());
        }
        discovered.get(varName).add(relPath);
      }
    }
  }

  return discovered;
}

/**
 * Discover env var names from the Python Settings class in app/config.py.
 * Takes the upper-cased field names as the authoritative list.
 *
 * Returns Map<varName, Set<sourceFile>>
 */
function discoverServiceEnvVars() {
  const discovered = new Map();
  const content = fs.readFileSync(SERVICE_CONFIG_PY, 'utf8');
  const relPath = path.relative(ROOT, SERVICE_CONFIG_PY);

  // Extract the Settings class body (from "class Settings" to the next class or end)
  const classRe = /^class Settings\(BaseSettings\):.*?\n([\s\S]*?)(?=\n(?:class |def (?!_))|\Z)/m;
  const classMatch = classRe.exec(content);
  if (!classMatch) return discovered;

  const classBody = classMatch[1];

  // Match field definitions: exactly 4 spaces, a lowercase identifier, colon, type annotation
  // Excludes methods (def), decorators (@), comments (#), and non-field lines
  const fieldRe = /^ {4}([a-z][a-z0-9_]*)\s*:\s*(?:str|int|float|bool)(?:\s*\|[^=]*)?\s*=/gm;
  let match;
  while ((match = fieldRe.exec(classBody)) !== null) {
    const fieldName = match[1];
    // Skip private/internal fields and the model_config
    if (fieldName.startsWith('_') || fieldName === 'model_config') continue;
    const varName = fieldName.toUpperCase();
    if (!discovered.has(varName)) {
      discovered.set(varName, new Set());
    }
    discovered.get(varName).add(relPath);
  }

  return discovered;
}

/**
 * Parse a sample.env file into a Map<varName, value>.
 * Only non-commented, non-empty lines with = are included.
 */
function parseEnvTemplate(filePath) {
  const template = new Map();
  if (!fs.existsSync(filePath)) return template;
  const lines = fs.readFileSync(filePath, 'utf8').split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx === -1) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const value = trimmed.slice(eqIdx + 1).trim();
    template.set(key, value);
  }
  return template;
}

/**
 * Extract code defaults from gateway JS source files.
 * Looks for patterns like:
 *   process.env.VAR || 'default'
 *   process.env.VAR || "default"
 *   process.env.VAR || number
 *   env.VAR_NAME (string after ||)
 *
 * Returns Map<varName, string>
 */
function discoverGatewayDefaults() {
  const defaults = new Map();

  const files = [...GATEWAY_GLOBS];
  for (const dir of GATEWAY_DIRS) {
    files.push(...collectJsFiles(dir));
  }

  // Match: process.env.VAR || 'value' or process.env.VAR || "value" or process.env.VAR || number
  const defaultRe = /(?:process\.env|env)\.([A-Z_][A-Z0-9_]*)\s*\|\|\s*(?:['"]([^'"]*?)['"]|(\d+(?:\.\d+)?))/g;
  // Match: Number(process.env.VAR) || number
  const numberDefaultRe = /Number\(process\.env\.([A-Z_][A-Z0-9_]*)\)\s*\|\|\s*(\d+)/g;

  for (const filePath of files) {
    const content = fs.readFileSync(filePath, 'utf8');

    for (const re of [defaultRe, numberDefaultRe]) {
      re.lastIndex = 0;
      let match;
      while ((match = re.exec(content)) !== null) {
        const varName = match[1];
        // For defaultRe: group 2 is string, group 3 is number
        // For numberDefaultRe: group 2 is number
        const value = match[2] !== undefined ? match[2] : match[3];
        if (value !== undefined) {
          defaults.set(varName, value);
        }
      }
    }
  }

  // Also extract defaults from lib/hybrid/config.js DEFAULTS object
  const hybridConfigPath = path.join(ROOT, 'lib', 'hybrid', 'config.js');
  if (fs.existsSync(hybridConfigPath)) {
    const hybridContent = fs.readFileSync(hybridConfigPath, 'utf8');
    const hybridDefaults = {
      HYBRID_CONFIDENCE_THRESHOLD: /threshold:\s*([\d.]+)/.exec(hybridContent)?.[1],
      HYBRID_GAP_TOLERANCE: /gapTolerance:\s*([\d.]+)/.exec(hybridContent)?.[1],
      HYBRID_PADDING: /padding:\s*([\d.]+)/.exec(hybridContent)?.[1],
      HYBRID_MAX_CALLS_PER_MODEL: /maxCallsPerModel:\s*(\d+)/.exec(hybridContent)?.[1],
    };
    for (const [k, v] of Object.entries(hybridDefaults)) {
      if (v) defaults.set(k, v);
    }
  }

  return defaults;
}

/**
 * Extract code defaults from the Python Settings class.
 * Parses field definitions with defaults.
 *
 * Returns Map<varName, string>
 */
function discoverServiceDefaults() {
  const defaults = new Map();
  const content = fs.readFileSync(SERVICE_CONFIG_PY, 'utf8');

  // Match: field_name: type = default_value
  const fieldRe = /^\s{4}(\w+)\s*:\s*\S+\s*=\s*(.+)/gm;
  let match;
  while ((match = fieldRe.exec(content)) !== null) {
    const fieldName = match[1];
    if (fieldName.startsWith('_') || fieldName === 'model_config') continue;
    let value = match[2].trim();
    // Strip quotes and None
    if (value === 'None') continue;
    if (value === 'True' || value === 'true') { value = 'true'; }
    else if (value === 'False' || value === 'false') { value = 'false'; }
    else if (/^['"](.*)['"]$/.test(value)) { value = value.slice(1, -1); }
    defaults.set(fieldName.toUpperCase(), value);
  }

  return defaults;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Configuration Consistency', () => {
  describe('Gateway env-var discovery (Requirement 5.1, 5.7)', () => {
    const discovered = discoverGatewayEnvVars();
    const template = parseEnvTemplate(GATEWAY_SAMPLE_ENV);

    it('every env var read by gateway source must be in sample.env', () => {
      const missing = [];
      for (const [varName, sources] of discovered) {
        if (!template.has(varName)) {
          // Check if it's documented as a comment (e.g. # SESSION_SECRET=...)
          const envContent = fs.readFileSync(GATEWAY_SAMPLE_ENV, 'utf8');
          const commentedRe = new RegExp(`^#\\s*${varName}\\s*=`, 'm');
          if (!commentedRe.test(envContent)) {
            missing.push(`${varName} (read by: ${[...sources].join(', ')})`);
          }
        }
      }
      assert.deepEqual(missing, [],
        `Environment variables read by source but missing from sample.env:\n  ${missing.join('\n  ')}`
      );
    });

    it('template names not read by source are only warnings (advisory)', (t) => {
      const unused = [];
      for (const varName of template.keys()) {
        if (!discovered.has(varName)) {
          unused.push(varName);
        }
      }
      if (unused.length > 0) {
        t.diagnostic(`Advisory: template defines ${unused.length} vars not directly read by gateway source: ${unused.join(', ')}`);
      }
      // This is a warning, not a failure
    });
  });

  describe('Postprocess_Service env-var discovery (Requirement 5.1, 5.7)', () => {
    const discovered = discoverServiceEnvVars();
    const template = parseEnvTemplate(SERVICE_SAMPLE_ENV);

    it('every Settings field must be in services/postprocess/sample.env', () => {
      const missing = [];
      for (const [varName, sources] of discovered) {
        if (!template.has(varName)) {
          // Check if it's documented as a comment
          const envContent = fs.readFileSync(SERVICE_SAMPLE_ENV, 'utf8');
          const commentedRe = new RegExp(`^#\\s*${varName}\\s*=`, 'm');
          if (!commentedRe.test(envContent)) {
            missing.push(`${varName} (from: ${[...sources].join(', ')})`);
          }
        }
      }
      assert.deepEqual(missing, [],
        `Settings fields missing from services/postprocess/sample.env:\n  ${missing.join('\n  ')}`
      );
    });
  });

  describe('Code-default agreement (Requirement 5.2)', () => {
    it('gateway code defaults agree with sample.env values', () => {
      const defaults = discoverGatewayDefaults();
      const template = parseEnvTemplate(GATEWAY_SAMPLE_ENV);
      const mismatches = [];

      for (const [varName, codeDefault] of defaults) {
        if (!template.has(varName)) continue;
        const templateValue = template.get(varName);
        if (isExempt(templateValue)) continue;
        if (String(codeDefault) !== String(templateValue)) {
          mismatches.push(
            `${varName}: code default="${codeDefault}", template="${templateValue}"`
          );
        }
      }

      assert.deepEqual(mismatches, [],
        `Code defaults disagree with sample.env:\n  ${mismatches.join('\n  ')}`
      );
    });

    it('service code defaults agree with services/postprocess/sample.env values', () => {
      const defaults = discoverServiceDefaults();
      const template = parseEnvTemplate(SERVICE_SAMPLE_ENV);
      const mismatches = [];

      for (const [varName, codeDefault] of defaults) {
        if (!template.has(varName)) continue;
        const templateValue = template.get(varName);
        if (isExempt(templateValue)) continue;
        if (String(codeDefault) !== String(templateValue)) {
          mismatches.push(
            `${varName}: code default="${codeDefault}", template="${templateValue}"`
          );
        }
      }

      assert.deepEqual(mismatches, [],
        `Service code defaults disagree with sample.env:\n  ${mismatches.join('\n  ')}`
      );
    });
  });

  describe('Bedrock region/model-id resolution (Requirement 5.3)', () => {
    it('AWS_REGION and BEDROCK_MODEL_ID form a resolvable pair in sample.env', () => {
      const template = parseEnvTemplate(GATEWAY_SAMPLE_ENV);
      const region = template.get('AWS_REGION');
      const modelId = template.get('BEDROCK_MODEL_ID');

      assert.ok(region, 'AWS_REGION must be set in sample.env');
      assert.ok(modelId, 'BEDROCK_MODEL_ID must be set in sample.env');

      // Cross-region inference profile IDs are prefixed with a region code
      // (e.g. "us.anthropic..." resolves in us-* regions)
      const modelPrefix = modelId.split('.')[0];  // "us" from "us.anthropic..."
      const regionPrefix = region.split('-')[0];  // "us" from "us-east-1"

      assert.equal(modelPrefix, regionPrefix,
        `BEDROCK_MODEL_ID prefix "${modelPrefix}" does not match AWS_REGION ` +
        `prefix "${regionPrefix}" — the model will not resolve in ${region}. ` +
        `Model: ${modelId}, Region: ${region}`
      );
    });

    it('services/postprocess/sample.env region and model-id also form a resolvable pair', () => {
      const template = parseEnvTemplate(SERVICE_SAMPLE_ENV);
      const region = template.get('AWS_REGION');
      const modelId = template.get('BEDROCK_MODEL_ID');

      assert.ok(region, 'AWS_REGION must be set in services/postprocess/sample.env');
      assert.ok(modelId, 'BEDROCK_MODEL_ID must be set in services/postprocess/sample.env');

      const modelPrefix = modelId.split('.')[0];
      const regionPrefix = region.split('-')[0];

      assert.equal(modelPrefix, regionPrefix,
        `Service BEDROCK_MODEL_ID prefix "${modelPrefix}" does not match AWS_REGION ` +
        `prefix "${regionPrefix}". Model: ${modelId}, Region: ${region}`
      );
    });
  });
});


// ---------------------------------------------------------------------------
// deepgram.toml [test] command check (Requirement 5.8)
// ---------------------------------------------------------------------------

describe('deepgram.toml [test] command', () => {
  it('[test] command executes the repository test suite, not a placeholder', () => {
    const tomlPath = path.join(ROOT, 'deepgram.toml');
    const content = fs.readFileSync(tomlPath, 'utf8');

    // Extract the [test] section's command
    const testSectionRe = /\[test\]\s*\n(?:.*\n)*?command\s*=\s*(\[.*?\])/s;
    const match = testSectionRe.exec(content);
    assert.ok(match, 'deepgram.toml must have a [test] section with a command');

    const commandStr = match[1];

    // Must NOT contain the placeholder echo
    assert.ok(
      !commandStr.includes("echo 'No tests configured'"),
      `[test] command must not be the placeholder "echo 'No tests configured'"`
    );

    // Must reference the real test runner
    assert.ok(
      commandStr.includes('corepack pnpm test') || commandStr.includes('node --test'),
      `[test] command must run the repository test suite (expected "corepack pnpm test" or "node --test")`
    );
  });
});
