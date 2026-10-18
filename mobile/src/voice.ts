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
  // Do not mark the voice "active" during the HTTP fetch: that kept Hey Jarvis disabled for
  // the whole network round-trip even when nothing was playing yet.
  return api.speak(text, cachedOnly, timeoutMs);
}

function disposePlayer() {
  try {
    player?.pause();
  } catch { /* ignore */ }
  try {
    player?.remove();
  } catch { /* ignore */ }
  player = null;
}

/** Silence the ElevenLabs player AND the built-in voice, right now. */
function stopAll() {
  finishPlayback?.();
  disposePlayer();
  void Speech.stop();
  finishSpeech?.();
  isVoiceActive = false;
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
  disposePlayer();
  // keepAudioSessionActive:false so Hey Jarvis can reclaim the mic after each cue.
  player = createAudioPlayer({ uri: file.uri }, { keepAudioSessionActive: false });
  const current = player;
  isVoiceActive = true;
  try {
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const startedAt = Date.now();
      let sawPlaying = false;
      const subscription = current.addListener('playbackStatusUpdate', (status) => {
        if (status.playing) sawPlaying = true;
        if (status.didJustFinish) done();
      });
      // Safety poll: if play() silently failed to start on iOS (e.g. audio session race), reject so
      // voiceCore falls back to the built-in Speech synthesizer instead of dropping the instruction.
      const poll = setInterval(() => {
        if (current.playing) {
          sawPlaying = true;
        } else if (sawPlaying) {
          done();
        } else if (Date.now() - startedAt > 1500) {
          fail(new Error('AudioPlayer did not start playing'));
        }
      }, 150);
      const timer = setTimeout(() => done(), MAX_PLAY_MS);
      function cleanup() {
        clearTimeout(timer);
        clearInterval(poll);
        subscription.remove();
        if (finishPlayback === done) finishPlayback = null;
      }
      function done() {
        if (settled) return;
        settled = true;
        cleanup();
        resolve();
      }
      function fail(err: Error) {
        if (settled) return;
        settled = true;
        cleanup();
        reject(err);
      }
      finishPlayback = done;
      try {
        current.play();
      } catch (e) {
        fail(e instanceof Error ? e : new Error(String(e)));
      }
    });
  } finally {
    isVoiceActive = false;
    // Release the player so AVAudioSession is free for the wake-word microphone stream.
    if (player === current) disposePlayer();
  }
}

let isVoiceActive = false;

export const voice = createVoice<Uint8Array>({
  fetchAudio, play, stopAll, builtin, getConfig: () => config,
});

/** True while an ElevenLabs reply/cue is being played (the built-in voice is checked with Speech.isSpeakingAsync). */
export function isVoicePlaying(): boolean {
  return isVoiceActive;
}

export function releaseVoice() {
  voice.stop();
  disposePlayer();
}
