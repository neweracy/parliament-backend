/**
 * Audio preprocessing for ASR upload.
 *
 * Deepgram enforces an upload deadline on prerecorded requests and returns
 * `408 SLOW_UPLOAD` when the request body does not arrive in time. On a slow
 * uplink this makes large audio files impossible to transcribe in one request,
 * regardless of how long the audio itself is.
 *
 * Two levers reduce the uploaded bytes:
 *
 * 1. Transcode to 16 kHz mono. Deepgram's models operate at 16 kHz and gain
 *    nothing from higher sample rates or a second channel, so this is a
 *    lossless-in-practice reduction for speech (~5x on typical 128 kbps
 *    stereo MP3).
 * 2. Split into time-bounded chunks so each request stays comfortably inside
 *    the upload deadline.
 *
 * Uses the bundled ffmpeg-static binary rather than a system ffmpeg, matching
 * lib/hybrid/audio-slicer.js.
 *
 * @module lib/audio-preprocess
 */

"use strict";

const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs/promises");
const os = require("os");
const path = require("path");

/** Sample rate Deepgram's models operate at. Higher rates add bytes, not accuracy. */
const ASR_SAMPLE_RATE = 16000;

/** Mono. Diarization does not require separate channels. */
const ASR_CHANNELS = 1;

/**
 * Bitrate for the transcoded MP3. 32 kbps at 16 kHz mono preserves speech
 * intelligibility for ASR while yielding roughly 235 KB per minute.
 */
const ASR_BITRATE = "32k";

/**
 * Creates an Error tagged with `type = 'AudioPreprocessError'` so callers can
 * distinguish preprocessing failures from ASR failures.
 *
 * @param {string} message
 * @returns {Error}
 */
function preprocessError(message) {
  const error = new Error(message);
  error.type = "AudioPreprocessError";
  return error;
}

/**
 * Resolves the bundled ffmpeg binary path.
 *
 * @returns {string}
 * @throws {Error} When the binary cannot be resolved or was never downloaded.
 */
function resolveFfmpeg() {
  const ffmpegPath = require("ffmpeg-static");
  if (!ffmpegPath) {
    throw preprocessError("ffmpeg binary path could not be resolved");
  }
  return ffmpegPath;
}

/**
 * Runs ffmpeg with the given arguments, capturing stderr for diagnostics.
 *
 * @param {string[]} args
 * @returns {Promise<string>} Captured stderr (ffmpeg writes progress there)
 * @throws {Error} type=AudioPreprocessError on non-zero exit or spawn failure
 */
function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(resolveFfmpeg(), args);
    } catch (err) {
      reject(preprocessError(`Could not start ffmpeg: ${err.message}`));
      return;
    }

    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });

    child.on("error", (err) => {
      // ENOENT here means the ffmpeg-static postinstall never ran.
      if (err.code === "ENOENT") {
        reject(
          preprocessError(
            "ffmpeg binary is missing. Run the ffmpeg-static install script " +
              "(pnpm blocks postinstall scripts by default)."
          )
        );
        return;
      }
      reject(preprocessError(`ffmpeg failed to start: ${err.message}`));
    });

    child.on("close", (code) => {
      if (code !== 0) {
        reject(
          preprocessError(
            `ffmpeg exited with code ${code}: ${stderr.slice(-500)}`
          )
        );
        return;
      }
      resolve(stderr);
    });
  });
}

/**
 * Reads the duration of an audio file in seconds.
 *
 * Parses ffmpeg's stderr rather than requiring ffprobe, which ffmpeg-static
 * does not ship.
 *
 * @param {string} filePath
 * @returns {Promise<number>} Duration in seconds
 * @throws {Error} type=AudioPreprocessError when duration cannot be determined
 */
async function probeDuration(filePath) {
  // ffmpeg exits non-zero when given no output, so tolerate that and parse
  // whatever it printed about the input.
  let stderr;
  try {
    stderr = await runFfmpeg(["-i", filePath, "-f", "null", "-"]);
  } catch (err) {
    // Some builds still emit the Duration line before failing.
    stderr = err.message;
  }

  const match = stderr.match(/Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d+)/);
  if (!match) {
    throw preprocessError("Could not determine audio duration");
  }

  const [, hours, minutes, seconds, fraction] = match;
  return (
    Number(hours) * 3600 +
    Number(minutes) * 60 +
    Number(seconds) +
    Number(`0.${fraction}`)
  );
}

/**
 * Transcodes audio to 16 kHz mono MP3, optionally extracting a time range.
 *
 * @param {string} inputPath
 * @param {string} outputPath
 * @param {Object} [range]
 * @param {number} [range.startSec] - Seek offset in seconds
 * @param {number} [range.durationSec] - Length to extract in seconds
 * @returns {Promise<void>}
 * @throws {Error} type=AudioPreprocessError
 */
async function transcodeForAsr(inputPath, outputPath, range = {}) {
  const args = ["-y"];

  // -ss before -i seeks by keyframe, which is fast and accurate enough at
  // chunk boundaries where we overlap nothing and stitch by wall-clock offset.
  if (typeof range.startSec === "number" && range.startSec > 0) {
    args.push("-ss", String(range.startSec));
  }

  args.push("-i", inputPath);

  if (typeof range.durationSec === "number" && range.durationSec > 0) {
    args.push("-t", String(range.durationSec));
  }

  args.push(
    "-ac", String(ASR_CHANNELS),
    "-ar", String(ASR_SAMPLE_RATE),
    "-b:a", ASR_BITRATE,
    "-f", "mp3",
    outputPath
  );

  await runFfmpeg(args);
}

/**
 * Creates a unique temp file path. The caller owns cleanup.
 *
 * @param {string} suffix
 * @returns {string}
 */
function tempPath(suffix) {
  return path.join(os.tmpdir(), `asr-${crypto.randomUUID()}${suffix}`);
}

/**
 * Removes a file, ignoring errors. Used in cleanup paths where a failure to
 * delete must not mask the original error.
 *
 * @param {string} filePath
 * @returns {Promise<void>}
 */
async function removeQuietly(filePath) {
  try {
    await fs.unlink(filePath);
  } catch {
    // Already gone, or never created.
  }
}

module.exports = {
  probeDuration,
  transcodeForAsr,
  tempPath,
  removeQuietly,
  ASR_SAMPLE_RATE,
  ASR_CHANNELS,
  ASR_BITRATE,
};
