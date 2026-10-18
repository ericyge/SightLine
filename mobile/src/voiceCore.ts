// Chooses between the ElevenLabs voice (via /api/speak) and the phone's built-in voice, and makes sure only
// ONE voice speaks at a time. Pure logic with injected dependencies (no Expo imports), so it is tested from
// the command line. Rules:
//  - reply(): a chat reply. It interrupts anything that is speaking. ElevenLabs when TTS is on; any failure
//    or slowness falls back to the built-in voice, so the user never hears nothing.
//  - cue(): a guidance cue ("Wallet at 2 o'clock"). It NEVER cuts off a reply: it waits for the reply to
//    finish, and only the newest waiting cue is then spoken. A newer cue interrupts an older cue.
//    tts_guidance decides the voice: "always" = ElevenLabs (built-in only if that fails or is slow),
//    "cached" = ElevenLabs only for audio the server already has, "never" = built-in.
//  - Starting any speech silences BOTH engines first (stopAll), so two voices never overlap.

export const REPLY_TIMEOUT_MS = 4500;      // give ElevenLabs enough time for full sentence replies
export const CUE_CACHED_TIMEOUT_MS = 800;  // cached-only lookups are local to the server, so they must be fast
export const CUE_FETCH_TIMEOUT_MS = 4000;  // "always" mode: let ElevenLabs synthesize uncached cues without premature Apple TTS fallback
export const REPEAT_MS = 4000;             // identical guidance cues inside this window are dropped
export const MAX_REPLY_WAIT_MS = 12000;    // a stuck reply must not silence guidance forever

export type VoiceConfig = { tts: string; ttsGuidance: string };
export type VoiceDeps<Audio> = {
  /** Resolves audio, or null when nothing is cached (HTTP 204); rejects on any failure or timeout. */
  fetchAudio: (text: string, opts: { cachedOnly: boolean; timeoutMs: number }) => Promise<Audio | null>;
  /** Resolves when playback FINISHES (or is stopped); rejects if it cannot start. */
  play: (audio: Audio) => Promise<void>;
  /** Silence the ElevenLabs player AND the built-in voice, now. Pending play()/builtin() calls resolve. */
  stopAll: () => void;
  /** The built-in voice. Resolves when it finishes or is stopped. */
  builtin: (text: string) => Promise<void>;
  getConfig: () => VoiceConfig;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
};

export function createVoice<Audio>({
  fetchAudio, play, stopAll, builtin, getConfig, now = Date.now,
  sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms)),
}: VoiceDeps<Audio>) {
  let replyTicket = 0;                       // newest reply wins
  let cueTicket = 0;                         // newest cue wins
  let activeReply: Promise<void> | null = null;
  let lastCue = { text: '', at: -Infinity };
  let pendingCue = '';

  /**
   * Speak `text` in the right voice and resolve when it has finished. `current()` says whether this request
   * is still wanted; a superseded request says nothing (the newer one owns the speaker).
   */
  async function speak(text: string, cachedOnly: boolean, timeoutMs: number, current: () => boolean) {
    if (getConfig().tts === 'elevenlabs') {
      let audio: Audio | null = null;
      try {
        audio = await fetchAudio(text, { cachedOnly, timeoutMs });
      } catch {
        audio = null;                        // failed or too slow: use the built-in voice
      }
      if (!current()) return;
      if (audio != null) {
        try {
          stopAll();
          await play(audio);
          return;
        } catch {
          if (!current()) return;            // could not start: fall back below
        }
      }
    }
    if (!current()) return;
    stopAll();
    await builtin(text);
  }

  return {
    /** Speak a chat reply. Resolves when it has finished speaking. */
    async reply(text: string) {
      if (!text) return;
      const mine = ++replyTicket;
      cueTicket += 1;                        // cues that were waiting are stale now
      stopAll();
      const run = speak(text, false, REPLY_TIMEOUT_MS, () => mine === replyTicket);
      activeReply = run;
      try {
        await run;
      } finally {
        if (activeReply === run) activeReply = null;
      }
    },

    /** Speak a fast guidance cue. Never interrupts a reply. */
    async cue(text: string) {
      if (!text) return;
      if (text === pendingCue) return;
      if (text === lastCue.text && now() - lastCue.at < REPEAT_MS) return;
      const mine = ++cueTicket;
      const repliesAtStart = replyTicket;
      pendingCue = text;
      const current = () => mine === cueTicket && repliesAtStart === replyTicket;
      try {
        const reply = activeReply;
        if (reply) await Promise.race([reply.catch(() => {}), sleep(MAX_REPLY_WAIT_MS)]);
        if (!current()) return;
        lastCue = { text, at: now() };
        const cfg = getConfig();
        if (cfg.tts === 'elevenlabs' && cfg.ttsGuidance !== 'never') {
          const cachedOnly = cfg.ttsGuidance === 'cached';
          await speak(text, cachedOnly, cachedOnly ? CUE_CACHED_TIMEOUT_MS : CUE_FETCH_TIMEOUT_MS, current);
        } else {
          stopAll();
          await builtin(text);
        }
      } finally {
        if (pendingCue === text) pendingCue = '';
      }
    },

    /** Silence everything now and forget what was waiting (e.g. when the user starts talking). */
    stop() {
      replyTicket += 1;
      cueTicket += 1;
      activeReply = null;
      pendingCue = '';
      stopAll();
    },
  };
}
