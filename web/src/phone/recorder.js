// Record the microphone as 16 kHz mono 16-bit WAV for POST /api/transcribe.
// WAV (not MediaRecorder webm/mp4) because it is the input the ElevenLabs STT call is known to take
// and it is the same on every browser. Audio stays in memory; the backend never stores it.
// TODO(C): replace ScriptProcessorNode (deprecated, but works everywhere) with an AudioWorklet.

export const TARGET_RATE = 16000;
export const MIN_SECONDS = 0.3;  // shorter holds are treated as accidental taps
export const MAX_SECONDS = 30;   // backend accepts far more; this bounds memory on the phone

/** Average-downsample mono float samples from fromRate to toRate. */
export function downsample(samples, fromRate, toRate = TARGET_RATE) {
  if (fromRate <= toRate) return samples;
  const ratio = fromRate / toRate;
  const out = new Float32Array(Math.floor(samples.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(samples.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += samples[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

/** Encode mono float samples (-1..1) as a 16-bit PCM WAV file. */
export function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);          // fmt chunk size
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

/**
 * Start recording. Resolves once the microphone is open (may show a permission prompt).
 * Returns { stop(): Promise<Blob|null> }; null means the recording was too short.
 */
export async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC();
  if (ctx.state === "suspended") await ctx.resume();
  const source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  let total = 0;
  processor.onaudioprocess = (e) => {
    if (total >= MAX_SECONDS * ctx.sampleRate) return;
    const data = new Float32Array(e.inputBuffer.getChannelData(0));
    chunks.push(data);
    total += data.length;
  };
  source.connect(processor);
  processor.connect(ctx.destination); // some browsers only run the processor when connected; output is silent

  return {
    async stop() {
      processor.disconnect();
      source.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      const rate = ctx.sampleRate;
      await ctx.close();
      if (total / rate < MIN_SECONDS) return null;
      const merged = new Float32Array(total);
      let offset = 0;
      for (const c of chunks) { merged.set(c, offset); offset += c.length; }
      const wav = encodeWav(downsample(merged, rate), Math.min(rate, TARGET_RATE));
      return new Blob([wav], { type: "audio/wav" });
    },
  };
}
