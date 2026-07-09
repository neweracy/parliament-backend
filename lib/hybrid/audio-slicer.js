'use strict';

const { spawn } = require('child_process');
const fs = require('fs').promises;
const os = require('os');
const path = require('path');
const crypto = require('crypto');

/**
 * Creates an Error tagged with `type = 'AudioSliceError'`.
 *
 * @param {string} message
 * @returns {Error}
 */
function audioSliceError(message) {
  const error = new Error(message);
  error.type = 'AudioSliceError';
  return error;
}

/**
 * Extracts an audio slice between two timestamps using the bundled
 * ffmpeg-static binary (no system ffmpeg on PATH). Writes to a temp file in
 * os.tmpdir(), reads it back as a Buffer, and always removes the temp file.
 *
 * ffmpeg args: ['-y', '-ss', startSec, '-to', endSec, '-i', <input temp>,
 *               '-f', 'mp3', '-acodec', 'libmp3lame', <output temp>]
 *
 * @param {Buffer} buffer      Source audio bytes.
 * @param {number} startSec    Padded slice start.
 * @param {number} endSec      Padded slice end.
 * @param {string} [ffmpegPath=require('ffmpeg-static')]  Resolved binary path.
 * @returns {Promise<{ buffer: Buffer, mimetype: 'audio/mpeg' }>}
 * @throws {Error} type=AudioSliceError when ffmpeg exits non-zero or the
 *   binary path is unresolved.
 */
async function sliceAudio(buffer, startSec, endSec, ffmpegPath = require('ffmpeg-static')) {
  if (!ffmpegPath) {
    throw audioSliceError('ffmpeg binary path could not be resolved');
  }

  const tmpDir = os.tmpdir();
  const inputPath = path.join(tmpDir, `hybrid-slice-in-${crypto.randomUUID()}`);
  const outputPath = path.join(tmpDir, `hybrid-slice-out-${crypto.randomUUID()}.mp3`);

  try {
    await fs.writeFile(inputPath, buffer);

    await new Promise((resolve, reject) => {
      const args = [
        '-y',
        '-ss',
        String(startSec),
        '-to',
        String(endSec),
        '-i',
        inputPath,
        '-f',
        'mp3',
        '-acodec',
        'libmp3lame',
        outputPath,
      ];

      const child = spawn(ffmpegPath, args);

      let stderr = '';
      if (child.stderr) {
        child.stderr.on('data', (chunk) => {
          stderr += chunk.toString();
        });
      }

      child.on('error', (error) => {
        reject(audioSliceError(`ffmpeg failed to start: ${error.message}`));
      });

      child.on('close', (code) => {
        if (code === 0) {
          resolve();
        } else {
          reject(
            audioSliceError(
              `ffmpeg exited with code ${code}${stderr ? `: ${stderr.trim()}` : ''}`
            )
          );
        }
      });
    });

    const output = await fs.readFile(outputPath);
    return { buffer: output, mimetype: 'audio/mpeg' };
  } finally {
    await Promise.allSettled([fs.unlink(inputPath), fs.unlink(outputPath)]);
  }
}

/**
 * Runs the resolved ffmpeg binary with the given args, rejecting with an
 * AudioSliceError on a non-zero exit or spawn failure.
 *
 * @param {string} ffmpegPath
 * @param {string[]} args
 * @returns {Promise<void>}
 */
function runFfmpeg(ffmpegPath, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(ffmpegPath, args);

    let stderr = '';
    if (child.stderr) {
      child.stderr.on('data', (chunk) => {
        stderr += chunk.toString();
      });
    }

    child.on('error', (error) => {
      reject(audioSliceError(`ffmpeg failed to start: ${error.message}`));
    });

    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(
          audioSliceError(
            `ffmpeg exited with code ${code}${stderr ? `: ${stderr.trim()}` : ''}`
          )
        );
      }
    });
  });
}

/**
 * Extracts multiple timestamp ranges from the source audio and concatenates
 * them, in order, into a single `audio/mpeg` clip using one ffmpeg pass
 * (atrim + concat via filter_complex). This lets the correction stage send the
 * combined low-confidence audio to the Correction_Engine in a single request
 * per language instead of one request per segment.
 *
 * @param {Buffer} buffer      Source audio bytes.
 * @param {Array<{ start: number, end: number }>} ranges  Ordered ranges (seconds).
 * @param {string} [ffmpegPath=require('ffmpeg-static')]  Resolved binary path.
 * @returns {Promise<{ buffer: Buffer, mimetype: 'audio/mpeg' }>}
 * @throws {Error} type=AudioSliceError when no ranges are given, ffmpeg exits
 *   non-zero, or the binary path is unresolved.
 */
async function sliceAndConcatAudio(buffer, ranges, ffmpegPath = require('ffmpeg-static')) {
  if (!ffmpegPath) {
    throw audioSliceError('ffmpeg binary path could not be resolved');
  }
  if (!Array.isArray(ranges) || ranges.length === 0) {
    throw audioSliceError('sliceAndConcatAudio requires at least one range');
  }

  const tmpDir = os.tmpdir();
  const inputPath = path.join(tmpDir, `hybrid-concat-in-${crypto.randomUUID()}`);
  const outputPath = path.join(tmpDir, `hybrid-concat-out-${crypto.randomUUID()}.mp3`);

  try {
    await fs.writeFile(inputPath, buffer);

    // Build a filter_complex that trims each range to its own label, resets its
    // timestamps, then concatenates all labels into a single audio stream.
    const trims = ranges
      .map(
        (r, i) =>
          `[0:a]atrim=start=${r.start}:end=${r.end},asetpts=PTS-STARTPTS[a${i}]`
      )
      .join(';');
    const labels = ranges.map((_, i) => `[a${i}]`).join('');
    const filter = `${trims};${labels}concat=n=${ranges.length}:v=0:a=1[out]`;

    const args = [
      '-y',
      '-i',
      inputPath,
      '-filter_complex',
      filter,
      '-map',
      '[out]',
      '-f',
      'mp3',
      '-acodec',
      'libmp3lame',
      outputPath,
    ];

    await runFfmpeg(ffmpegPath, args);

    const output = await fs.readFile(outputPath);
    return { buffer: output, mimetype: 'audio/mpeg' };
  } finally {
    await Promise.allSettled([fs.unlink(inputPath), fs.unlink(outputPath)]);
  }
}

module.exports = { sliceAudio, sliceAndConcatAudio };
