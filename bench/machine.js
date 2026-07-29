'use strict';

/**
 * Reference_Machine identity capture.
 *
 * Emits a stable machine descriptor used to correlate benchmark runs.
 * The `id` is a SHA-256 hash of (cpu_model + cores + total_memory_bytes),
 * ensuring measurements are only compared across the same hardware profile.
 *
 * @module bench/machine
 * @returns {MachineIdentity}
 */

const os = require('node:os');
const crypto = require('node:crypto');
const { execSync } = require('node:child_process');

/**
 * @typedef {Object} MachineIdentity
 * @property {string} id - SHA-256 hash of cpu_model + cores + total_memory_bytes
 * @property {string} cpu_model - CPU model name
 * @property {number} cores - Logical core count
 * @property {number} total_memory_bytes - Total system memory in bytes
 * @property {string} platform - OS platform (e.g. 'linux', 'darwin', 'win32')
 * @property {string} node_version - Node.js version string
 * @property {string} python_version - Python version string or 'unavailable'
 */

/**
 * Capture the identity of the current machine.
 *
 * @returns {MachineIdentity}
 */
function getMachineIdentity() {
  const cpuModel = os.cpus()[0]?.model || 'unknown';
  const cores = os.cpus().length;
  const totalMemoryBytes = os.totalmem();
  const platform = os.platform();
  const nodeVersion = process.version;

  let pythonVersion = 'unavailable';
  try {
    pythonVersion = execSync('python3 --version', {
      encoding: 'utf8',
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    }).trim().replace(/^Python\s*/, '');
  } catch {
    // Python not available — record as unavailable
  }

  // Stable hash from invariant hardware identifiers
  const hashInput = `${cpuModel}|${cores}|${totalMemoryBytes}`;
  const id = crypto.createHash('sha256').update(hashInput).digest('hex');

  return {
    id,
    cpu_model: cpuModel,
    cores,
    total_memory_bytes: totalMemoryBytes,
    platform,
    node_version: nodeVersion,
    python_version: pythonVersion,
  };
}

module.exports = { getMachineIdentity };
