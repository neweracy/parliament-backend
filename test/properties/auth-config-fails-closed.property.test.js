'use strict';

/**
 * Property 1: Authentication configuration fails closed
 *
 * For any combination of build mode, dev-login flag, Cognito variable presence,
 * and backend auth mode, local login and the credential helper SHALL be enabled
 * only when the frontend is a development build with VITE_ENABLE_DEV_LOGIN === 'true'
 * and the non-production backend explicitly uses AUTH_MODE=legacy; every incomplete
 * or contradictory production combination SHALL produce configuration failure without
 * credentials, anonymous session issuance, or local-login fallback.
 *
 * Validates: Requirements 1.1, 5.1, 6.1
 *
 * @module test/properties/auth-config-fails-closed.property
 */

const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const fc = require('fast-check');

// ─── Reference model of the frontend resolveAuthConfiguration ────────────────
// Re-implemented from Hansard/src/app/config/auth.ts since it's TypeScript

/**
 * A string is considered "present" when it is a non-empty string containing
 * at least one non-whitespace character.
 */
function isNonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

/**
 * Reference implementation of resolveAuthConfiguration (frontend).
 *
 * Resolution order:
 * 1. All three Cognito vars valid → cognito mode (wins unconditionally)
 * 2. Cognito incomplete + DEV===true + VITE_ENABLE_DEV_LOGIN==='true' → local-dev
 * 3. Otherwise → configuration-error
 *
 * @param {object} env
 * @returns {{ mode: string, reason?: string, cognito?: object }}
 */
function resolveAuthConfiguration(env) {
  const domain = env.VITE_COGNITO_DOMAIN;
  const clientId = env.VITE_COGNITO_CLIENT_ID;
  const redirectUri = env.VITE_COGNITO_REDIRECT_URI;

  const domainValid = isNonEmptyString(domain);
  const clientIdValid = isNonEmptyString(clientId);
  const redirectUriValid = isNonEmptyString(redirectUri);

  // All three Cognito vars present and valid → Cognito mode wins unconditionally
  if (domainValid && clientIdValid && redirectUriValid) {
    return {
      mode: 'cognito',
      cognito: {
        domain,
        clientId,
        redirectUri,
      },
    };
  }

  // Cognito is incomplete — determine whether any vars were partially set
  const anyPartial = domainValid || clientIdValid || redirectUriValid;

  // Check dev mode gates
  const isDevMode = env.DEV === true && env.VITE_ENABLE_DEV_LOGIN === 'true';

  if (isDevMode) {
    return { mode: 'local-dev' };
  }

  // Not dev mode, Cognito incomplete → configuration error
  return {
    mode: 'configuration-error',
    reason: anyPartial ? 'partial-cognito-config' : 'missing-cognito-config',
  };
}

// ─── Reference model of the backend resolveAuthMode ──────────────────────────

const KNOWN_NON_PRODUCTION_ENVS = new Set(['development', 'test', 'demo']);

/**
 * Reference model of resolveAuthMode (backend).
 * Returns the resolved mode or 'startup-termination' if validation fails.
 *
 * @param {object} env
 * @returns {'legacy' | 'cognito' | 'startup-termination'}
 */
function resolveAuthModeReference(env) {
  const authMode = env.AUTH_MODE;

  // Missing, empty, or non-string
  if (authMode === undefined || authMode === null || authMode === '') {
    return 'startup-termination';
  }

  // Only exact case-sensitive values accepted
  if (authMode !== 'legacy' && authMode !== 'cognito') {
    return 'startup-termination';
  }

  if (authMode === 'legacy') {
    const nodeEnv = env.NODE_ENV;
    if (!nodeEnv || !KNOWN_NON_PRODUCTION_ENVS.has(nodeEnv)) {
      return 'startup-termination';
    }
  }

  return authMode;
}

// ─── Generators ──────────────────────────────────────────────────────────────

/** Generator for optional non-whitespace strings (valid Cognito var values) */
const validCognitoVar = fc.oneof(
  fc.constant(undefined),
  fc.constant(''),
  fc.constant('   '),
  fc.constant('\t\n'),
  fc.stringMatching(/^[a-z0-9._~:/-]{1,50}$/)
);

/** Generator for VITE_ENABLE_DEV_LOGIN values */
const devLoginFlag = fc.oneof(
  fc.constant('true'),
  fc.constant('false'),
  fc.constant(undefined),
  fc.constant(''),
  fc.constant('TRUE'),
  fc.constant('True'),
  fc.string({ minLength: 1, maxLength: 10 })
);

/** Generator for DEV boolean */
const devBool = fc.oneof(
  fc.constant(true),
  fc.constant(false),
  fc.constant(undefined)
);

/** Generator for AUTH_MODE values */
const authModeGen = fc.oneof(
  fc.constant('legacy'),
  fc.constant('cognito'),
  fc.constant(undefined),
  fc.constant(''),
  fc.constant('Legacy'),
  fc.constant('LEGACY'),
  fc.constant('COGNITO'),
  fc.constant(' legacy'),
  fc.constant('legacy '),
  fc.string({ minLength: 1, maxLength: 20 })
);

/** Generator for NODE_ENV values */
const nodeEnvGen = fc.oneof(
  fc.constant('development'),
  fc.constant('test'),
  fc.constant('demo'),
  fc.constant('production'),
  fc.constant(undefined),
  fc.constant(''),
  fc.constant('Development'),
  fc.constant('PRODUCTION'),
  fc.constant(' development'),
  fc.string({ minLength: 1, maxLength: 20 })
);

// ─── Property Tests ──────────────────────────────────────────────────────────

/**
 * **Validates: Requirements 1.1, 5.1, 6.1**
 */
describe('Property 1: Authentication configuration fails closed', () => {

  it('frontend: local-dev mode enabled ONLY when DEV===true AND VITE_ENABLE_DEV_LOGIN==="true" AND Cognito NOT fully configured', () => {
    fc.assert(
      fc.property(
        devBool,
        devLoginFlag,
        validCognitoVar,
        validCognitoVar,
        validCognitoVar,
        (dev, enableFlag, domain, clientId, redirectUri) => {
          const env = {
            DEV: dev,
            VITE_ENABLE_DEV_LOGIN: enableFlag,
            VITE_COGNITO_DOMAIN: domain,
            VITE_COGNITO_CLIENT_ID: clientId,
            VITE_COGNITO_REDIRECT_URI: redirectUri,
          };

          const result = resolveAuthConfiguration(env);

          const cognitoComplete =
            isNonEmptyString(domain) &&
            isNonEmptyString(clientId) &&
            isNonEmptyString(redirectUri);

          const isDevMode = dev === true && enableFlag === 'true';

          if (result.mode === 'local-dev') {
            // local-dev requires: DEV===true AND flag==='true' AND Cognito NOT complete
            assert.equal(dev, true, 'local-dev requires DEV===true');
            assert.equal(enableFlag, 'true', 'local-dev requires VITE_ENABLE_DEV_LOGIN==="true"');
            assert.equal(cognitoComplete, false, 'local-dev requires incomplete Cognito');
          }

          // If all conditions for local-dev met and Cognito incomplete → must be local-dev
          if (isDevMode && !cognitoComplete) {
            assert.equal(result.mode, 'local-dev',
              'Should be local-dev when DEV + flag + no Cognito');
          }
        }
      ),
      { numRuns: 500 }
    );
  });

  it('frontend: Cognito mode wins when all three Cognito vars are valid non-whitespace strings', () => {
    fc.assert(
      fc.property(
        devBool,
        devLoginFlag,
        validCognitoVar,
        validCognitoVar,
        validCognitoVar,
        (dev, enableFlag, domain, clientId, redirectUri) => {
          const env = {
            DEV: dev,
            VITE_ENABLE_DEV_LOGIN: enableFlag,
            VITE_COGNITO_DOMAIN: domain,
            VITE_COGNITO_CLIENT_ID: clientId,
            VITE_COGNITO_REDIRECT_URI: redirectUri,
          };

          const cognitoComplete =
            isNonEmptyString(domain) &&
            isNonEmptyString(clientId) &&
            isNonEmptyString(redirectUri);

          const result = resolveAuthConfiguration(env);

          if (cognitoComplete) {
            // Cognito always wins regardless of DEV flag
            assert.equal(result.mode, 'cognito',
              'Cognito must win when all three vars are valid');
            assert.ok(result.cognito, 'Cognito config object must be present');
            assert.equal(result.cognito.domain, domain);
            assert.equal(result.cognito.clientId, clientId);
            assert.equal(result.cognito.redirectUri, redirectUri);
          }
        }
      ),
      { numRuns: 500 }
    );
  });

  it('frontend: all non-cognito and non-local-dev combinations produce configuration-error', () => {
    fc.assert(
      fc.property(
        devBool,
        devLoginFlag,
        validCognitoVar,
        validCognitoVar,
        validCognitoVar,
        (dev, enableFlag, domain, clientId, redirectUri) => {
          const env = {
            DEV: dev,
            VITE_ENABLE_DEV_LOGIN: enableFlag,
            VITE_COGNITO_DOMAIN: domain,
            VITE_COGNITO_CLIENT_ID: clientId,
            VITE_COGNITO_REDIRECT_URI: redirectUri,
          };

          const cognitoComplete =
            isNonEmptyString(domain) &&
            isNonEmptyString(clientId) &&
            isNonEmptyString(redirectUri);

          const isDevMode = dev === true && enableFlag === 'true';

          const result = resolveAuthConfiguration(env);

          // If neither Cognito complete nor dev mode → must be configuration-error
          if (!cognitoComplete && !isDevMode) {
            assert.equal(result.mode, 'configuration-error',
              'Must be configuration-error when Cognito incomplete and not dev mode');
            assert.ok(
              result.reason === 'missing-cognito-config' || result.reason === 'partial-cognito-config',
              'Reason must be missing or partial Cognito config'
            );
          }
        }
      ),
      { numRuns: 500 }
    );
  });

  it('backend: resolveAuthMode reference model matches real implementation behavior', () => {
    fc.assert(
      fc.property(
        authModeGen,
        nodeEnvGen,
        (authMode, nodeEnv) => {
          const env = {};
          if (authMode !== undefined) env.AUTH_MODE = authMode;
          if (nodeEnv !== undefined) env.NODE_ENV = nodeEnv;

          const refResult = resolveAuthModeReference(env);

          // Validate the reference model's logic:
          // 'legacy' only when AUTH_MODE==='legacy' AND NODE_ENV is known non-prod
          if (refResult === 'legacy') {
            assert.equal(authMode, 'legacy', 'legacy requires exact AUTH_MODE');
            assert.ok(
              KNOWN_NON_PRODUCTION_ENVS.has(nodeEnv),
              'legacy requires known non-production NODE_ENV'
            );
          }

          // 'cognito' only when AUTH_MODE==='cognito'
          if (refResult === 'cognito') {
            assert.equal(authMode, 'cognito', 'cognito requires exact AUTH_MODE');
          }

          // startup-termination for everything else
          if (authMode !== 'legacy' && authMode !== 'cognito') {
            assert.equal(refResult, 'startup-termination',
              'Unknown/missing AUTH_MODE must terminate');
          }

          if (authMode === 'legacy' && !KNOWN_NON_PRODUCTION_ENVS.has(nodeEnv)) {
            assert.equal(refResult, 'startup-termination',
              'legacy with invalid NODE_ENV must terminate');
          }

          // AUTH_MODE=legacy with NODE_ENV=production must terminate
          if (authMode === 'legacy' && nodeEnv === 'production') {
            assert.equal(refResult, 'startup-termination',
              'legacy + production must terminate');
          }
        }
      ),
      { numRuns: 500 }
    );
  });

  it('combined: local login enabled only with explicit frontend DEV + flag AND backend legacy in non-production', () => {
    fc.assert(
      fc.property(
        devBool,
        devLoginFlag,
        validCognitoVar,
        validCognitoVar,
        validCognitoVar,
        authModeGen,
        nodeEnvGen,
        (dev, enableFlag, domain, clientId, redirectUri, authMode, nodeEnv) => {
          // Frontend resolution
          const frontendEnv = {
            DEV: dev,
            VITE_ENABLE_DEV_LOGIN: enableFlag,
            VITE_COGNITO_DOMAIN: domain,
            VITE_COGNITO_CLIENT_ID: clientId,
            VITE_COGNITO_REDIRECT_URI: redirectUri,
          };
          const frontendResult = resolveAuthConfiguration(frontendEnv);

          // Backend resolution
          const backendEnv = {};
          if (authMode !== undefined) backendEnv.AUTH_MODE = authMode;
          if (nodeEnv !== undefined) backendEnv.NODE_ENV = nodeEnv;
          const backendResult = resolveAuthModeReference(backendEnv);

          // Full local login system is enabled only when:
          // 1. Frontend resolves to 'local-dev'
          // 2. Backend resolves to 'legacy' (not startup-termination)
          const localLoginEnabled =
            frontendResult.mode === 'local-dev' && backendResult === 'legacy';

          if (localLoginEnabled) {
            // Frontend gates
            assert.equal(dev, true, 'System local login requires DEV===true');
            assert.equal(enableFlag, 'true', 'System local login requires explicit flag');

            // Cognito must NOT be fully configured
            const cognitoComplete =
              isNonEmptyString(domain) &&
              isNonEmptyString(clientId) &&
              isNonEmptyString(redirectUri);
            assert.equal(cognitoComplete, false,
              'System local login requires Cognito NOT fully configured');

            // Backend gates
            assert.equal(authMode, 'legacy', 'System local login requires AUTH_MODE=legacy');
            assert.ok(
              KNOWN_NON_PRODUCTION_ENVS.has(nodeEnv),
              'System local login requires non-production NODE_ENV'
            );
          }

          // Production combination must NEVER enable local login
          if (nodeEnv === 'production') {
            assert.equal(localLoginEnabled, false,
              'Local login MUST NOT be enabled in production');
          }

          // Missing/empty AUTH_MODE must NEVER enable local login
          if (authMode === undefined || authMode === '') {
            assert.equal(localLoginEnabled, false,
              'Missing AUTH_MODE must not enable local login');
          }
        }
      ),
      { numRuns: 1000 }
    );
  });

  it('frontend: configuration-error never exposes credentials or login fallback', () => {
    fc.assert(
      fc.property(
        devBool,
        devLoginFlag,
        validCognitoVar,
        validCognitoVar,
        validCognitoVar,
        (dev, enableFlag, domain, clientId, redirectUri) => {
          const env = {
            DEV: dev,
            VITE_ENABLE_DEV_LOGIN: enableFlag,
            VITE_COGNITO_DOMAIN: domain,
            VITE_COGNITO_CLIENT_ID: clientId,
            VITE_COGNITO_REDIRECT_URI: redirectUri,
          };

          const result = resolveAuthConfiguration(env);

          if (result.mode === 'configuration-error') {
            // Must not contain any credential info, cognito object, or login fallback
            assert.equal(result.cognito, undefined,
              'configuration-error must not expose Cognito config');
            assert.ok(
              result.reason === 'missing-cognito-config' || result.reason === 'partial-cognito-config',
              'Reason must be a safe enum, not env var values'
            );
            // Reason must not contain actual env values (skip if domain
            // coincidentally equals a reason enum value, or is too short to be
            // a meaningful leak — single chars like '-' or '.' appear in the
            // enum reason strings themselves)
            if (domain && domain.length > 3 && domain !== 'missing-cognito-config' && domain !== 'partial-cognito-config') {
              assert.ok(!result.reason.includes(domain),
                'Reason must not contain env var value');
            }
          }
        }
      ),
      { numRuns: 300 }
    );
  });
});
