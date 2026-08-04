'use strict';

/**
 * Unit tests for lib/auth-config.js — resolveAuthMode.
 *
 * Tests the strict AUTH_MODE resolver that accepts only case-sensitive
 * 'legacy' or 'cognito' and validates NODE_ENV for legacy mode.
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.10, 2.11
 *
 * @module test/lib/auth-config
 */

const { describe, it, beforeEach, afterEach, mock } = require('node:test');
const assert = require('node:assert/strict');

/**
 * Load a fresh auth-config module to avoid require cache issues.
 */
function loadAuthConfig() {
  const modulePath = require.resolve('../../lib/auth-config');
  delete require.cache[modulePath];
  return require('../../lib/auth-config');
}

describe('resolveAuthMode', () => {
  let originalExit;
  let exitCalled;
  let exitCode;

  beforeEach(() => {
    exitCalled = false;
    exitCode = null;
    // Mock process.exit to capture calls without actually terminating
    originalExit = process.exit;
    process.exit = mock.fn((code) => {
      exitCalled = true;
      exitCode = code;
      // Throw to halt execution (simulates process termination)
      throw new Error(`process.exit(${code})`);
    });
  });

  afterEach(() => {
    process.exit = originalExit;
  });

  // --- Valid configurations ---

  describe('valid AUTH_MODE=cognito', () => {
    it('returns "cognito" when AUTH_MODE is exactly "cognito"', () => {
      const { resolveAuthMode } = loadAuthConfig();
      const result = resolveAuthMode({ AUTH_MODE: 'cognito' });
      assert.equal(result, 'cognito');
      assert.equal(exitCalled, false);
    });

    it('returns "cognito" regardless of NODE_ENV value', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.equal(resolveAuthMode({ AUTH_MODE: 'cognito', NODE_ENV: 'production' }), 'cognito');
      assert.equal(resolveAuthMode({ AUTH_MODE: 'cognito', NODE_ENV: 'development' }), 'cognito');
      assert.equal(resolveAuthMode({ AUTH_MODE: 'cognito' }), 'cognito');
      assert.equal(exitCalled, false);
    });
  });

  describe('valid AUTH_MODE=legacy with non-production NODE_ENV', () => {
    it('returns "legacy" when NODE_ENV=development', () => {
      const { resolveAuthMode } = loadAuthConfig();
      const result = resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: 'development' });
      assert.equal(result, 'legacy');
      assert.equal(exitCalled, false);
    });

    it('returns "legacy" when NODE_ENV=test', () => {
      const { resolveAuthMode } = loadAuthConfig();
      const result = resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: 'test' });
      assert.equal(result, 'legacy');
      assert.equal(exitCalled, false);
    });

    it('returns "legacy" when NODE_ENV=demo', () => {
      const { resolveAuthMode } = loadAuthConfig();
      const result = resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: 'demo' });
      assert.equal(result, 'legacy');
      assert.equal(exitCalled, false);
    });
  });

  // --- Missing or empty AUTH_MODE ---

  describe('missing or empty AUTH_MODE causes termination', () => {
    it('exits when AUTH_MODE is undefined', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({}),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when AUTH_MODE is empty string', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: '' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when AUTH_MODE is null', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: null }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- Whitespace-padded AUTH_MODE ---

  describe('whitespace-padded AUTH_MODE causes termination', () => {
    it('exits when AUTH_MODE has leading whitespace', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: ' legacy' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when AUTH_MODE has trailing whitespace', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'cognito ' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when AUTH_MODE has surrounding whitespace', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: ' legacy ' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- Case-sensitivity ---

  describe('case-sensitive comparison rejects wrong case', () => {
    it('exits for "Legacy" (capitalized)', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'Legacy' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits for "LEGACY" (uppercase)', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'LEGACY' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits for "COGNITO" (uppercase)', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'COGNITO' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits for "Cognito" (capitalized)', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'Cognito' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- Unknown AUTH_MODE values ---

  describe('unknown AUTH_MODE values cause termination', () => {
    it('exits for arbitrary string', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'saml' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits for "local"', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'local' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- AUTH_MODE=legacy with invalid NODE_ENV ---

  describe('AUTH_MODE=legacy with invalid NODE_ENV causes termination', () => {
    it('exits when NODE_ENV is missing', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'legacy' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when NODE_ENV is empty string', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: '' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when NODE_ENV=production', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: 'production' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when NODE_ENV has wrong case (Development)', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: 'Development' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when NODE_ENV has surrounding whitespace', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: ' development ' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when NODE_ENV is unrecognized (staging)', () => {
      const { resolveAuthMode } = loadAuthConfig();
      assert.throws(
        () => resolveAuthMode({ AUTH_MODE: 'legacy', NODE_ENV: 'staging' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- Sanitized error messages ---

  describe('error messages are sanitized', () => {
    it('does not print AUTH_MODE value in error output', () => {
      const { resolveAuthMode } = loadAuthConfig();
      const logs = [];
      const originalError = console.error;
      console.error = (...args) => logs.push(args.join(' '));

      try {
        resolveAuthMode({ AUTH_MODE: 'my-secret-value-123' });
      } catch (_) {
        // expected
      }

      console.error = originalError;
      const output = logs.join(' ');
      // The error message should not contain the actual secret value
      assert.equal(output.includes('my-secret-value-123'), false);
    });
  });
});
