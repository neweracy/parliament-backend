// Exports small in-memory Buffers representing fake audio bytes plus their mimetypes.
// No filesystem access; nothing is read from test_audio.

module.exports = {
  mp3: {
    buffer: Buffer.from([0xff, 0xfb, 0x90, 0x00, 0x00, 0x00, 0x00, 0x00]),
    mimetype: "audio/mpeg"
  },
  wav: {
    buffer: Buffer.from([0x52, 0x49, 0x46, 0x46, 0x00, 0x00, 0x00, 0x00]),
    mimetype: "audio/wav"
  }
};
