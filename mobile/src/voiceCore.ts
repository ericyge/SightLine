// Chooses between the ElevenLabs voice (via /api/speak) and the phone's built-in voice.
// Pure logic with injected dependencies (no Expo imports) so it can be tested from the command line.
// Same rules as web/src/phone/voice.js:
//  - reply(): chat replies use ElevenLabs when the backend says TTS is on; any failure or slowness
//    falls back to the built-in voice, so the user never hears nothing.
//  - cue(): fast guidance cues stay on the built-in voice, except when the backend setting
//    tts_guidance is "cached" AND that exact audio is already cached on the server (instant, free).

export const REPLY_TIMEOUT_MS = 2500;  // backend allows ElevenLabs 3 s; a slower reply is not worth waiting for
export const CUE_TIMEOUT_MS = 600;     // cached-only lookups are local to the server, so they must be fast
export const REPEAT_MS = 4000;         // identical guidance cues inside this window are dropped

export type VoiceConfig = { tts: string; ttsGuidance: string };
export type VoiceDeps<Audio> = {
  /** Resolves audio, or null when nothing is cached (HTTP 204); rejects on any failure or timeout. */
  fetchAudio: (text: string, opts: { cachedOnly: boolean; timeoutMs: number }) => Promise<Audio | null>;
  /** Rejects if playback cannot start. */
  play: (audio: Audio) => Promise<void>;
  stopPlayback: () => void;
  /** The built-in voice. */
  builtin: (text: string, opts: { interrupt?: boolean; force?: boolean }) => void;
  getConfig: () => VoiceConfig;
  now?: () => number;
};

export function createVoice<Audio>({
  fetchAudio, play, stopPlayback, builtin, getConfig, now = Date.now,
}: VoiceDeps<Audio>) {
  let ticket = 0;                                   // newest request wins; an older, slower one stays silent
  let lastCue = { text: '', at: -Infinity };

  /** True when the request is handled (played, or superseded); false means "use the built-in voice". */
  async function viaElevenLabs(text: string, cachedOnly: boolean, timeoutMs: number, mine: number) {
    try {
      const audio = await fetchAudio(text, { cachedOnly, timeoutMs });
      if (mine !== ticket) return true;
      if (audio == null) return false;
      stopPlayback();
      await play(audio);
      return true;
    } catch {
      return mine !== ticket;
    }
  }

  return {
    /** Speak a chat reply. */
    async reply(text: string) {
      if (!text) return;
      const mine = ++ticket;
      stopPlayback();
      if (getConfig().tts === 'elevenlabs' && (await viaElevenLabs(text, false, REPLY_TIMEOUT_MS, mine))) return;
      if (mine === ticket) builtin(text, { force: true, interrupt: true });
    },

    /** Speak a fast guidance cue ("Reach out now", "Wallet at 2 o'clock"). */
    async cue(text: string) {
      if (!text) return;
      if (text === lastCue.text && now() - lastCue.at < REPEAT_MS) return;
      lastCue = { text, at: now() };
      const mine = ++ticket;
      const cfg = getConfig();
      if (cfg.tts === 'elevenlabs' && cfg.ttsGuidance === 'cached'
          && (await viaElevenLabs(text, true, CUE_TIMEOUT_MS, mine))) return;
      if (mine === ticket) {
        stopPlayback();
        builtin(text, { interrupt: true });
      }
    },

    /** Stop any ElevenLabs audio now (e.g. when the user starts talking). */
    stop() {
      ticket += 1;
      stopPlayback();
    },
  };
}
