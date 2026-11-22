// Application framing from firmware/body/main/protocols/{protocol,websocket_protocol}.
// RFC6455 transport, Opus decoding and real voice providers are separate layers.
export const UPLINK = Object.freeze({format:'opus', sample_rate:16000, channels:1, frame_duration:60});
export const DOWNLINK = Object.freeze({format:'opus', sample_rate:24000, channels:1, frame_duration:60});
export const MAX_PACKET_BYTES = 4096;
export const MAX_PACKETS = 500;
export const MAX_AUDIO_BYTES = 256 * 1024;

export class VoiceProtocolError extends Error {
  constructor(code) { super(code); this.code = code; }
}
export function requireThat(condition, code) {
  if (!condition) throw new VoiceProtocolError(code);
}
export function checkAudio(actual, expected) {
  requireThat(actual && Object.keys(expected).every(key => actual[key] === expected[key]), 'unsupported_audio');
}
export function parseHello(value) {
  requireThat(value?.type === 'hello' && value.transport === 'websocket', 'invalid_hello');
  requireThat([1,2,3].includes(value.version), 'unsupported_version');
  checkAudio(value.audio_params, UPLINK);
  return value.version;
}
function checkVersion(version) { requireThat([1,2,3].includes(version), 'unsupported_version'); }
function packetBuffer(value, maximum) {
  requireThat(Buffer.isBuffer(value) || value instanceof Uint8Array, 'invalid_audio_bytes');
  requireThat(value.byteLength > 0 && value.byteLength <= maximum, 'invalid_audio_size');
  return Buffer.from(value);
}
export function decodeAudio(value, version) {
  checkVersion(version);
  const header = version === 2 ? 16 : version === 3 ? 4 : 0;
  const bytes = packetBuffer(value,MAX_PACKET_BYTES + header);
  requireThat(bytes.length > header && bytes.length <= header + MAX_PACKET_BYTES, 'invalid_audio_size');
  let timestamp = 0;
  if (version === 2) {
    requireThat(bytes.readUInt16BE(0) === 2 && bytes.readUInt16BE(2) === 0 && bytes.readUInt32BE(4) === 0,
      'invalid_audio_header');
    requireThat(bytes.readUInt32BE(12) === bytes.length - header, 'audio_length_mismatch');
    timestamp = bytes.readUInt32BE(8);
  } else if (version === 3) {
    requireThat(bytes[0] === 0 && bytes[1] === 0, 'invalid_audio_header');
    requireThat(bytes.readUInt16BE(2) === bytes.length - header, 'audio_length_mismatch');
  }
  return {payload:Buffer.from(bytes.subarray(header)), timestamp};
}
export function encodeAudio(value, version, timestamp = 0) {
  checkVersion(version);
  const payload = packetBuffer(value,MAX_PACKET_BYTES);
  requireThat(payload.length > 0 && payload.length <= MAX_PACKET_BYTES, 'invalid_audio_size');
  requireThat(Number.isInteger(timestamp) && timestamp >= 0 && timestamp <= 0xffffffff, 'invalid_timestamp');
  const header = version === 2 ? 16 : version === 3 ? 4 : 0;
  const bytes = Buffer.alloc(header + payload.length);
  if (version === 2) {
    bytes.writeUInt16BE(2,0); bytes.writeUInt32BE(timestamp,8); bytes.writeUInt32BE(payload.length,12);
  } else if (version === 3) bytes.writeUInt16BE(payload.length,2);
  payload.copy(bytes,header);
  return bytes;
}
