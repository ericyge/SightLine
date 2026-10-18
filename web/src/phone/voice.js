// Chooses between the ElevenLabs voice (via /api/speak) and the phone's built-in voice.
// Pure logic with injected dependencies, so it can be tested without a browser (see voice.test.js).
//
// Rules:
//  - reply(): chat replies use ElevenLabs when the backend says TTS is on; any failure or slowness
//    falls back to the built-in voice, so the user never hears nothing.
//  - cue(): fast guidance cues stay on the built-in voice, except when the backend setting
//    tts_guidance is "cached" AND that exact audio is already cached on the server (instant, free).
//  - Only the phone speaks. The dashboard never imports this module.

export const REPLY_TIMEOUT_MS = 2500;  // backend allows ElevenLabs 3 s; a slower reply is not worth waiting for
export const CUE_TIMEOUT_MS = 600;     // cached-only lookups are local to the server, so they must be fast
export const REPEAT_MS = 4000;         // identical guidance cues inside this window are dropped

/**
 * @param {object} deps
 * @param {(text:string, opts:{cachedOnly:boolean, timeoutMs:number}) => Promise<any|null>} deps.fetchAudio
 *        resolves audio, or null when nothing is cached (HTTP 204); rejects on any failure/timeout
 * @param {(audio:any) => Promise<void>} deps.play   rejects if playback cannot start
 * @param {() => void} deps.stopPlayback
 * @param {(text:string, opts:object) => void} deps.builtin   the built-in voice
 * @param {() => {tts:string, ttsGuidance:string}} deps.getConfig
 * @param {() => number} [deps.now]
 */
export function createVoice({ fetchAudio, play, stopPlayback, builtin, getConfig, now = Date.now }) {
  let ticket = 0;            // newest request wins; an older, slower reply must not talk over it
  let lastCue = { text: "", at: -Infinity };

  async function viaElevenLabs(text, cachedOnly, timeoutMs, mine) {
    try {
      const audio = await fetchAudio(text, { cachedOnly, timeoutMs });
      if (mine !== ticket) return true;          // superseded: say nothing, the newer request owns the speaker
      if (!audio) return false;                  // nothing cached
      stopPlayback();
      await play(audio);
      return true;
    } catch {
      return mine !== ticket;                    // failed (or superseded): caller falls back if still current
    }
  }

  return {
    /** Speak a chat reply. */
    async reply(text) {
      if (!text) return;
      const mine = ++ticket;
      stopPlayback();
      if (getConfig().tts === "elevenlabs" && (await viaElevenLabs(text, false, REPLY_TIMEOUT_MS, mine))) return;
      if (mine === ticket) builtin(text, { force: true, interrupt: true });
    },

    /** Speak a fast guidance cue ("Reach out now", "Wallet at 2 o'clock"). */
    async cue(text) {
      if (!text) return;
      if (text === lastCue.text && now() - lastCue.at < REPEAT_MS) return;
      lastCue = { text, at: now() };
      const mine = ++ticket;
      const cfg = getConfig();
      if (cfg.tts === "elevenlabs" && cfg.ttsGuidance === "cached"
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
