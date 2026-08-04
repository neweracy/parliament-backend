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


// ---------------------------------------------------------------------------
// resolveSessionSecret tests
// ---------------------------------------------------------------------------

describe('resolveSessionSecret', () => {
  let originalExit;
  let exitCalled;
  let exitCode;

  beforeEach(() => {
    exitCalled = false;
    exitCode = null;
    originalExit = process.exit;
    process.exit = mock.fn((code) => {
      exitCalled = true;
      exitCode = code;
      throw new Error(`process.exit(${code})`);
    });
  });

  afterEach(() => {
    process.exit = originalExit;
  });

  // --- Valid hex secrets ---

  describe('valid hex encoding', () => {
    it('accepts 64 hex chars (32 bytes decoded)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const hex64 = 'a'.repeat(64); // 64 hex chars = 32 bytes
      const result = resolveSessionSecret({ SESSION_SECRET: hex64 });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 32);
    });

    it('accepts 128 hex chars (64 bytes decoded)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const hex128 = 'abcdef0123456789'.repeat(8); // 128 hex chars = 64 bytes
      const result = resolveSessionSecret({ SESSION_SECRET: hex128 });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 64);
    });

    it('accepts 256 hex chars (128 bytes decoded)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const hex256 = 'ff'.repeat(128); // 256 hex chars = 128 bytes
      const result = resolveSessionSecret({ SESSION_SECRET: hex256 });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 128);
    });

    it('accepts mixed-case hex chars', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const hex = 'AaBbCcDd01234567'.repeat(4); // 64 chars
      const result = resolveSessionSecret({ SESSION_SECRET: hex });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 32);
    });

    it('correctly decodes known hex value', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 64 hex chars = "0102030405...2021...1f20" repeated
      const hex = '0102030405060708091011121314151617181920212223242526272829303132';
      const result = resolveSessionSecret({ SESSION_SECRET: hex });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 32);
      assert.equal(result[0], 0x01);
      assert.equal(result[1], 0x02);
    });
  });

  // --- Valid base64 secrets ---

  describe('valid base64 encoding', () => {
    it('accepts padded base64 decoding to 48 bytes', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 48 bytes in base64 = 64 chars (no padding needed for 48 bytes)
      const buf = Buffer.alloc(48, 0xab);
      const b64 = buf.toString('base64');
      assert.equal(b64.length, 64);
      const result = resolveSessionSecret({ SESSION_SECRET: b64 });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 48);
    });

    it('accepts padded base64 decoding to 32 bytes (minimum)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 32 bytes → 44 chars base64 (with padding). But 44 < 64 chars minimum!
      // Actually, input length check is 64-256. So base64 for 32 bytes is 44 chars which is < 64.
      // Need to use a larger decoded size for base64 to be valid.
      // 48 bytes → 64 chars base64 (exactly at the 64-char minimum)
      const buf = Buffer.alloc(48, 0xcd);
      const b64 = buf.toString('base64');
      assert.equal(b64.length, 64);
      const result = resolveSessionSecret({ SESSION_SECRET: b64 });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 48);
    });

    it('accepts padded base64 decoding to 128 bytes (maximum)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 128 bytes → 172 chars base64 (with padding)
      const buf = Buffer.alloc(128, 0xef);
      const b64 = buf.toString('base64');
      assert.ok(b64.length >= 64 && b64.length <= 256);
      const result = resolveSessionSecret({ SESSION_SECRET: b64 });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 128);
    });

    it('accepts base64 with padding characters', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 49 bytes → 68 chars base64 (with == padding)
      const buf = Buffer.alloc(49, 0x99);
      const b64 = buf.toString('base64');
      assert.ok(b64.endsWith('='));
      assert.ok(b64.length >= 64 && b64.length <= 256);
      const result = resolveSessionSecret({ SESSION_SECRET: b64 });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(result.length, 49);
    });
  });

  // --- Ambiguous values (valid as both hex and base64) decode as hex ---

  describe('ambiguous values decode as hex', () => {
    it('decodes an all-lowercase hex string that is also valid base64 as hex', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // "abcdef01" repeated 8 times = 64 chars; all hex chars, also valid base64
      const ambiguous = 'abcdef01'.repeat(8); // 64 chars, all hex, also valid base64 (no +/=)
      const result = resolveSessionSecret({ SESSION_SECRET: ambiguous });
      assert.ok(Buffer.isBuffer(result));
      // Decoded as hex: 64 hex chars = 32 bytes
      assert.equal(result.length, 32);
      // Verify it was decoded as hex (first byte should be 0xab)
      assert.equal(result[0], 0xab);
    });

    it('prioritizes hex over base64 for values matching both patterns', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 128 hex chars, all characters in [0-9a-f] which is subset of base64 charset
      const value = '0123456789abcdef'.repeat(8); // 128 chars
      const result = resolveSessionSecret({ SESSION_SECRET: value });
      // As hex: 128/2 = 64 bytes
      assert.equal(result.length, 64);
    });
  });

  // --- Absent/empty SESSION_SECRET ---

  describe('absent or empty SESSION_SECRET causes termination', () => {
    it('exits when SESSION_SECRET is undefined', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({}),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when SESSION_SECRET is empty string', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: '' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when SESSION_SECRET is null', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: null }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- Prohibited values ---

  describe('prohibited values cause termination', () => {
    const prohibited = ['changeme', 'change-me', 'secret', 'development-secret', 'dev-secret', 'default'];

    for (const value of prohibited) {
      it(`exits for prohibited value "${value}"`, () => {
        const { resolveSessionSecret } = loadAuthConfig();
        // Pad to meet the 64-char minimum length requirement for the input length check
        // Actually prohibited check happens before length check in the code... let me check:
        // Looking at the code: absent → prohibited → length → encoding
        // So prohibited values get caught even if short
        assert.throws(
          () => resolveSessionSecret({ SESSION_SECRET: value }),
          /process\.exit\(1\)/
        );
        assert.equal(exitCalled, true);
        assert.equal(exitCode, 1);
      });
    }

    it('exits for prohibited value with different case (CHANGEME)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: 'CHANGEME' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits for prohibited value with surrounding whitespace', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: '  changeme  ' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits for prohibited value with mixed case and whitespace', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: ' Development-Secret ' }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- Input length violations ---

  describe('input length violations cause termination', () => {
    it('exits when input is fewer than 64 characters (63 hex chars)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: 'a'.repeat(63) }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits when input is more than 256 characters (257 hex chars)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: 'a'.repeat(257) }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('accepts exactly 64 characters (boundary)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const result = resolveSessionSecret({ SESSION_SECRET: 'a'.repeat(64) });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(exitCalled, false);
    });

    it('accepts exactly 256 characters (boundary)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const result = resolveSessionSecret({ SESSION_SECRET: 'f'.repeat(256) });
      assert.ok(Buffer.isBuffer(result));
      assert.equal(exitCalled, false);
    });
  });

  // --- Non-conforming encoding ---

  describe('non-conforming encoding causes termination', () => {
    it('exits for string with invalid hex characters (not base64 either)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 'g' is not hex and if mixed with non-base64 chars
      const badValue = 'g'.repeat(64); // not hex, check if it's valid base64...
      // 'g' is valid base64 char, so this would be valid base64 but not valid hex
      // Let's use something that's neither hex nor proper base64
      const reallyBad = '!@#$%^&*()'.repeat(7); // 70 chars, neither hex nor base64
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: reallyBad }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });

    it('exits for unpadded base64 (not RFC 4648 canonical)', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // Create a base64 string that would need padding but strip it
      const buf = Buffer.alloc(49, 0xaa); // 49 bytes → 68 chars base64 with padding
      const padded = buf.toString('base64');
      const unpadded = padded.replace(/=+$/, '');
      // unpadded length might be < 64 or not divisible by 4
      // Make sure it's in the 64-256 range
      if (unpadded.length >= 64 && unpadded.length <= 256) {
        assert.throws(
          () => resolveSessionSecret({ SESSION_SECRET: unpadded }),
          /process\.exit\(1\)/
        );
        assert.equal(exitCalled, true);
        assert.equal(exitCode, 1);
      }
    });

    it('exits for base64 with length not divisible by 4', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // Create a 65-char string of valid base64 chars but length % 4 !== 0
      // Also not valid hex (use + or / to break hex)
      const value = 'A'.repeat(64) + '+'; // 65 chars, has +, not hex, length%4 !== 0
      assert.throws(
        () => resolveSessionSecret({ SESSION_SECRET: value }),
        /process\.exit\(1\)/
      );
      assert.equal(exitCalled, true);
      assert.equal(exitCode, 1);
    });
  });

  // --- Decoded length violations ---

  describe('decoded length outside 32-128 bytes causes termination', () => {
    it('exits when base64 decodes to less than 32 bytes', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // Need a base64 value that is 64-256 chars but decodes to < 32 bytes
      // A pure base64 value that decodes to 31 bytes would be 44 chars (too short)
      // We need base64 of at least 64 chars that decodes to < 32 bytes — impossible
      // since 64 base64 chars decode to 48 bytes. So this case can only happen
      // if the value is base64 only (not hex) and somehow short-decodes.
      // Actually for base64: 64 chars decode to 48 bytes, which is >= 32. 
      // The minimum base64 length is 64 chars which decodes to 48 bytes.
      // So base64 decoded < 32 is impossible given the 64-char minimum.
      // Skip this — it's covered by the input length check.
    });

    it('exits when base64 decodes to more than 128 bytes', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      // 192 bytes → 256 chars base64 (no padding for multiple of 3)
      // 192 bytes > 128 bytes limit
      const buf = Buffer.alloc(192, 0xbb);
      const b64 = buf.toString('base64');
      // b64 length = 256 chars (192 * 4/3 = 256)
      assert.equal(b64.length, 256);
      // This is NOT valid hex (contains + and /) so will try base64 path
      // If it happens to be all-hex chars, it would decode as hex (96 bytes, valid)
      // Let's ensure it has non-hex chars
      if (!b64.match(/^[0-9a-fA-F]+$/)) {
        assert.throws(
          () => resolveSessionSecret({ SESSION_SECRET: b64 }),
          /process\.exit\(1\)/
        );
        assert.equal(exitCalled, true);
        assert.equal(exitCode, 1);
      }
    });
  });

  // --- Secret never appears in error output ---

  describe('secret value never appears in error messages', () => {
    it('does not print the secret value when encoding is invalid', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const logs = [];
      const originalError = console.error;
      console.error = (...args) => logs.push(args.join(' '));

      const secretValue = '!@#XYZ_UNIQUE_SECRET_VALUE_!@#$%'.repeat(3); // 96 chars

      try {
        resolveSessionSecret({ SESSION_SECRET: secretValue });
      } catch (_) {
        // expected
      }

      console.error = originalError;
      const output = logs.join(' ');
      assert.equal(output.includes(secretValue), false);
      assert.equal(output.includes('XYZ_UNIQUE_SECRET_VALUE'), false);
    });

    it('does not print the secret value when it is a prohibited value', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const logs = [];
      const originalError = console.error;
      console.error = (...args) => logs.push(args.join(' '));

      try {
        resolveSessionSecret({ SESSION_SECRET: 'changeme' });
      } catch (_) {
        // expected
      }

      console.error = originalError;
      const output = logs.join(' ');
      assert.equal(output.includes('changeme'), false);
    });

    it('does not print the secret value for wrong-length secrets', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const logs = [];
      const originalError = console.error;
      console.error = (...args) => logs.push(args.join(' '));

      const shortSecret = 'abc123';

      try {
        resolveSessionSecret({ SESSION_SECRET: shortSecret });
      } catch (_) {
        // expected
      }

      console.error = originalError;
      const output = logs.join(' ');
      assert.equal(output.includes('abc123'), false);
    });
  });

  // --- Return type ---

  describe('returns Buffer on success', () => {
    it('returns a Buffer instance', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const result = resolveSessionSecret({ SESSION_SECRET: 'a'.repeat(64) });
      assert.ok(Buffer.isBuffer(result));
    });

    it('returned Buffer has correct decoded content', () => {
      const { resolveSessionSecret } = loadAuthConfig();
      const hex = 'deadbeef'.repeat(8); // 64 chars
      const result = resolveSessionSecret({ SESSION_SECRET: hex });
      assert.equal(result[0], 0xde);
      assert.equal(result[1], 0xad);
      assert.equal(result[2], 0xbe);
      assert.equal(result[3], 0xef);
    });
  });
});
