// Build a short stereo beep as WAV bytes with the left/right volume baked in.
// expo-audio has no stereo-pan control, so beeps are pre-rendered at a few pan positions.

const PEAK = 0.5;

/** 16-bit stereo PCM WAV of a sine beep. pan: -1 (left) .. 1 (right), equal-power. */
export function makeBeepWav(pan: number, hz: number, ms = 90, rate = 22050): Uint8Array {
  const frames = Math.round((rate * ms) / 1000);
  const p = Math.max(-1, Math.min(1, pan));
  const left = Math.cos(((p + 1) * Math.PI) / 4);
  const right = Math.sin(((p + 1) * Math.PI) / 4);
  const bytes = new Uint8Array(44 + frames * 4);
  const view = new DataView(bytes.buffer);
  const tag = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  tag(0, 'RIFF');
  view.setUint32(4, 36 + frames * 4, true);
  tag(8, 'WAVE');
  tag(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 2, true); // stereo
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 4, true);
  view.setUint16(32, 4, true);
  view.setUint16(34, 16, true);
  tag(36, 'data');
  view.setUint32(40, frames * 4, true);
  for (let i = 0; i < frames; i++) {
    const envelope = Math.sin((Math.PI * i) / frames); // fade in and out: no clicks
    const sample = Math.sin((2 * Math.PI * hz * i) / rate) * envelope * PEAK;
    view.setInt16(44 + i * 4, Math.round(sample * left * 0x7fff), true);
    view.setInt16(46 + i * 4, Math.round(sample * right * 0x7fff), true);
  }
  return bytes;
}
