// Expo wiring for voiceCore.ts: fetch mp3 from /api/speak, play it with expo-audio, fall back to expo-speech.
import { createAudioPlayer, type AudioPlayer } from 'expo-audio';
import { File, Paths } from 'expo-file-system';
import * as Speech from 'expo-speech';
import type { Api } from './api';
import { createVoice, type VoiceConfig } from './voiceCore';

let config: VoiceConfig = { tts: 'browser', ttsGuidance: 'cached' };
let api: Api | null = null;
let player: AudioPlayer | null = null;
let fileCounter = 0;

/** Point the voice at the backend and apply its GET /api/health fields {tts, tts_guidance}. */
export function configureVoice(backend: Api, health: { tts?: string; tts_guidance?: string }) {
  api = backend;
  config = { tts: health.tts ?? 'browser', ttsGuidance: health.tts_guidance ?? 'cached' };
}

/** The built-in voice (expo-speech). */
export function builtinSpeak(text: string, opts: { interrupt?: boolean } = {}) {
  if (!text) return;
  if (opts.interrupt) void Speech.stop();
  Speech.speak(text, { language: 'en-US' });
}

async function fetchAudio(text: string, { cachedOnly, timeoutMs }: { cachedOnly: boolean; timeoutMs: number }) {
  if (!api) throw new Error('voice not configured');
  return api.speak(text, cachedOnly, timeoutMs);
}

function stopPlayback() {
  player?.pause();
}

/** Write the mp3 to the cache directory and play it. Rejects if the file or player fails. */
async function play(bytes: Uint8Array) {
  const file = new File(Paths.cache, `sightline-tts-${fileCounter++ % 4}.mp3`);
  if (!file.exists) file.create();
  file.write(bytes);
  if (!player) player = createAudioPlayer({ uri: file.uri });
  else player.replace({ uri: file.uri });
  player.play();
  // UNVERIFIED on real phones: expo-audio reports no error from play(), so a silent playback failure
  // would not trigger the built-in-voice fallback. TODO(C): listen to the player's status for errors.
}

export const voice = createVoice<Uint8Array>({
  fetchAudio, play, stopPlayback, builtin: builtinSpeak, getConfig: () => config,
});

/** True while an ElevenLabs reply is playing (the built-in voice is checked with Speech.isSpeakingAsync). */
export function isVoicePlaying(): boolean {
  return player?.playing ?? false;
}

export function releaseVoice() {
  player?.remove();
  player = null;
  void Speech.stop();
}
