// Expo wiring for voiceCore.ts: fetch mp3 from /api/speak, play it with expo-audio, fall back to expo-speech.
// Both speakers report when they finish, and stopAll() silences both, so only one voice is ever heard.
import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from 'expo-audio';
import { File, Paths } from 'expo-file-system';
import * as Speech from 'expo-speech';
import type { Api } from './api';
import { createVoice, type VoiceConfig } from './voiceCore';

const MAX_PLAY_MS = 30000;     // safety: never wait longer than this for audio to report it finished
const MAX_SPEECH_MS = 20000;

let config: VoiceConfig = { tts: 'browser', ttsGuidance: 'always' };
let api: Api | null = null;
let player: AudioPlayer | null = null;
let fileCounter = 0;
let finishPlayback: (() => void) | null = null;   // ends the ElevenLabs utterance that is playing
let finishSpeech: (() => void) | null = null;     // ends the built-in utterance that is speaking

/** Point the voice at the backend and apply its GET /api/health fields {tts, tts_guidance}. */
export function configureVoice(backend: Api, health: { tts?: string; tts_guidance?: string }) {
  api = backend;
  config = { tts: health.tts ?? 'browser', ttsGuidance: health.tts_guidance ?? 'always' };
}

async function fetchAudio(text: string, { cachedOnly, timeoutMs }: { cachedOnly: boolean; timeoutMs: number }) {
  if (!api) throw new Error('voice not configured');
  isVoiceActive = true;
  try {
    return await api.speak(text, cachedOnly, timeoutMs);
  } finally {
    if (!player?.playing) isVoiceActive = false;
  }
}

/** Silence the ElevenLabs player AND the built-in voice, right now. */
function stopAll() {
  player?.pause();
  finishPlayback?.();
  void Speech.stop();
  finishSpeech?.();
}

/** The built-in voice. Resolves when it has finished (or was stopped). */
function builtin(text: string): Promise<void> {
  void setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false }).catch(() => {});
  return new Promise((resolve) => {
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (finishSpeech === done) finishSpeech = null;
      resolve();
    };
    const timer = setTimeout(done, MAX_SPEECH_MS);
    finishSpeech = done;
    Speech.speak(text, { language: 'en-US', onDone: done, onStopped: done, onError: done });
  });
}

/** Write the mp3 to the cache directory and play it. Resolves when it has finished playing or was stopped. */
async function play(bytes: Uint8Array): Promise<void> {
  try {
    await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false });
  } catch {
    // continue
  }
  const file = new File(Paths.cache, `sightline-tts-${fileCounter++ % 4}.mp3`);
  if (!file.exists) file.create();
  file.write(bytes);
  if (!player) player = createAudioPlayer({ uri: file.uri }, { keepAudioSessionActive: true });
  else player.replace({ uri: file.uri });
  const current = player;
  isVoiceActive = true;
  try {
    await new Promise<void>((resolve) => {
      let settled = false;
      let startedAt = Date.now();
      let sawPlaying = false;
      const subscription = current.addListener('playbackStatusUpdate', (status) => {
        if (status.playing) sawPlaying = true;
        if (status.didJustFinish) done();
      });
      // Safety poll: if play() silently failed on iOS (e.g. audio session race) or finished without firing
      // didJustFinish, don't block speechBusy for 30s.
      const poll = setInterval(() => {
        if (current.playing) {
          sawPlaying = true;
        } else if (sawPlaying || Date.now() - startedAt > 4000) {
          done();
        }
      }, 200);
      const timer = setTimeout(done, MAX_PLAY_MS);
      function done() {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        clearInterval(poll);
        subscription.remove();
        if (finishPlayback === done) finishPlayback = null;
        resolve();
      }
      finishPlayback = done;
      try {
        current.play();
      } catch {
        done();
      }
    });
  } finally {
    isVoiceActive = false;
  }
}

let isVoiceActive = false;

export const voice = createVoice<Uint8Array>({
  fetchAudio, play, stopAll, builtin, getConfig: () => config,
});

/** True while an ElevenLabs reply/cue is being fetched or played (the built-in voice is checked with Speech.isSpeakingAsync). */
export function isVoicePlaying(): boolean {
  return isVoiceActive || (player?.playing ?? false);
}

export function releaseVoice() {
  voice.stop();
  player?.remove();
  player = null;
}
