// Pure pieces of the "Hey Jarvis" feature (no Expo imports, so they are tested from the command line):
// converting the microphone stream to the 16 kHz mono int16 the backend wants, deciding when the user has
// finished speaking, packaging the request as a WAV, and the WebSocket address.

export const WAKE_RATE = 16000;

export type StreamEncoding = 'int16' | 'float32';

/**
 * Converts microphone buffers (any sample rate, 1 or more channels, int16 or float32) to mono int16 at
 * 16 kHz. Stateful, because buffers arrive in pieces: leftover samples are carried to the next call.
 * Resamples by averaging the input samples under each output sample (a simple low-pass).
 */
export class PcmConverter {
  private ratio: number;          // input samples per output sample
  private carry: number[] = [];   // mono samples (-1..1) not yet consumed
  private pos = 0;                // read position inside `carry`, in input samples

  constructor(inputRate: number) {
    this.ratio = inputRate / WAKE_RATE;
  }

  push(data: ArrayBuffer, channels: number, encoding: StreamEncoding): Int16Array {
    const raw = encoding === 'int16' ? new Int16Array(data) : new Float32Array(data);
    const scale = encoding === 'int16' ? 32768 : 1;
    const ch = Math.max(1, channels);
    const buffer = this.carry;
    for (let i = 0; i + ch <= raw.length; i += ch) {      // downmix to mono
      let sum = 0;
      for (let c = 0; c < ch; c++) sum += raw[i + c];
      buffer.push(sum / ch / scale);
    }
    const out: number[] = [];
    let pos = this.pos;
    while (pos + this.ratio <= buffer.length) {
      const start = Math.floor(pos);
      const end = Math.max(start + 1, Math.floor(pos + this.ratio));
      let sum = 0;
      for (let i = start; i < end; i++) sum += buffer[i];
      out.push(sum / (end - start));
      pos += this.ratio;
    }
    const used = Math.floor(pos);
    this.carry = buffer.slice(used);
    this.pos = pos - used;
    const pcm = new Int16Array(out.length);
    for (let i = 0; i < out.length; i++) {
      pcm[i] = Math.max(-32768, Math.min(32767, Math.round(out[i] * 32767)));
    }
    return pcm;
  }
}

/** Loudness of a block of int16 samples in dBFS (0 = full scale, about -90 = silence). */
export function levelDb(samples: Int16Array): number {
  if (samples.length === 0) return -120;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  const rms = Math.sqrt(sum / samples.length) / 32768;
  return 20 * Math.log10(Math.max(rms, 1e-9));
}

export type EndpointOptions = {
  speechDb: number;     // louder than this counts as speech
  ignoreMs: number;     // ignore the first part: it is the tail of "Hey Jarvis" itself
  silenceMs: number;    // stop this long after the last speech
  noSpeechMs: number;   // give up if nothing is said within this long
  maxMs: number;        // never record longer than this
};

// UNVERIFIED on real phones: -45 dBFS and these timings are starting points to tune with the debug level.
export const DEFAULT_ENDPOINT: EndpointOptions = {
  speechDb: -45, ignoreMs: 700, silenceMs: 1200, noSpeechMs: 5000, maxMs: 12000,
};

export type EndpointResult = { stop: false } | { stop: true; reason: 'silence' | 'no-speech' | 'max' };

/** Decides when the user has finished speaking. Time comes from the audio itself, so it is deterministic. */
export class Endpointer {
  private opts: EndpointOptions;
  private elapsedMs = 0;
  private lastSpeechMs = -1;
  speechSeen = false;
  lastDb = -120;

  constructor(opts: Partial<EndpointOptions> = {}) {
    this.opts = { ...DEFAULT_ENDPOINT, ...opts };
  }

  push(frame: Int16Array): EndpointResult {
    this.elapsedMs += (frame.length / WAKE_RATE) * 1000;
    this.lastDb = levelDb(frame);
    if (this.elapsedMs > this.opts.ignoreMs && this.lastDb >= this.opts.speechDb) {
      this.speechSeen = true;
      this.lastSpeechMs = this.elapsedMs;
    }
    if (this.elapsedMs >= this.opts.maxMs) return { stop: true, reason: 'max' };
    if (this.speechSeen && this.elapsedMs - this.lastSpeechMs >= this.opts.silenceMs) {
      return { stop: true, reason: 'silence' };
    }
    if (!this.speechSeen && this.elapsedMs >= this.opts.noSpeechMs) return { stop: true, reason: 'no-speech' };
    return { stop: false };
  }
}

/** A 16 kHz mono 16-bit WAV file (what /api/transcribe is known to accept). */
export function encodeWav16k(samples: Int16Array): Uint8Array {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const tag = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  tag(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  tag(8, 'WAVE');
  tag(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);          // PCM
  view.setUint16(22, 1, true);          // mono
  view.setUint32(24, WAKE_RATE, true);
  view.setUint32(28, WAKE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  tag(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) view.setInt16(44 + i * 2, samples[i], true);
  return bytes;
}

/** WebSocket address of the backend's wake endpoint, or null if the server address is not usable. */
export function wakeUrl(server: string): string | null {
  const m = server.trim().replace(/\/+$/, '').match(/^(https?):\/\/(.+)$/i);
  if (!m) return null;
  return `${m[1].toLowerCase() === 'https' ? 'wss' : 'ws'}://${m[2]}/ws/wake`;
}

export type WakeMessage =
  | { type: 'ready' }
  | { type: 'wake'; score: number }
  | { type: 'error'; reason: string };

/** Parse a text message from the backend; null if it is not one we know. */
export function parseWakeMessage(text: string): WakeMessage | null {
  try {
    const m = JSON.parse(text);
    if (m?.type === 'ready') return { type: 'ready' };
    if (m?.type === 'wake') return { type: 'wake', score: Number(m.score) || 0 };
    if (m?.type === 'error') return { type: 'error', reason: String(m.reason ?? 'unknown error') };
  } catch {
    // not JSON
  }
  return null;
}

/**
 * What the data in a microphone buffer really is. expo-audio (iOS) converts to the requested 16 kHz int16,
 * but if it cannot build that converter it sends the hardware's own audio (usually 32-bit float at 44.1 or
 * 48 kHz) while the buffer metadata still looks the same. So: exactly 16 kHz means int16, anything else float32.
 * UNVERIFIED on real devices.
 */
export function streamEncodingFor(sampleRate: number): StreamEncoding {
  return Math.round(sampleRate) === WAKE_RATE ? 'int16' : 'float32';
}

/** The bytes of a stream buffer as an ArrayBuffer, whatever typed wrapper the native module used; null if unusable. */
export function toArrayBuffer(data: unknown): ArrayBuffer | null {
  if (data instanceof ArrayBuffer) return data;
  if (ArrayBuffer.isView(data)) {
    return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
  }
  return null;
}
