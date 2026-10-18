import { File } from 'expo-file-system';
import { makeFilePart } from './filePart';

// Backend wrappers. The app never talks to Gemini, Perceptron or ElevenLabs directly: the backend
// owns every key. Field names match docs/CONTRACT.md.

export type Intent = 'find' | 'where' | 'stop' | 'set_room' | 'chitchat';
export type ChatTurn = { role: 'user' | 'assistant'; content: string };
export type ChatResponse = { intent: Intent; item: string | null; room: string | null; say: string; provider: string };
export type Health = {
  ok: boolean; mode: string; mock: boolean; stt: 'elevenlabs' | 'browser';
  vision: { calls: number; p50_ms: number | null; p95_ms: number | null; errors: number; dropped: number } | null;
  guide_max_fps: number;
  tts: 'elevenlabs' | 'browser';          // elevenlabs: speak chat replies via /api/speak
  tts_guidance: 'cached' | 'never';       // whether fixed guidance cues may use cached ElevenLabs audio
  wake?: boolean;                          // true when WS /ws/wake can run (WAKE_ENABLED, packages, models)
};
export type Profile = { watchlist: { name: string }[]; rooms: string[]; guidance_style: string };
export type GuideResponse = {
  found: boolean; point: number[] | null; box: number[] | null; angle: number | null; clock: number | null;
  distance: 'far' | 'near' | 'reach' | null; pan: number; say: string; beep_ms: number;
};

/** A local file to upload (React Native's FormData takes {uri, name, type}). */
export type UploadFile = { uri: string; name: string; type: string };

/** A local file as a multipart part Expo SDK 57's fetch accepts (it rejects the old {uri, name, type} form). */
const filePart = (f: UploadFile) => makeFilePart(new File(f.uri), f.name, f.type);

export class Api {
  constructor(public base: string) {}

  private url(path: string) {
    return `${this.base.replace(/\/+$/, '')}${path}`;
  }

  private async json<T>(res: Response): Promise<T> {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    // 204 = the backend dropped this frame because Perceptron was still busy; callers get null
    if (res.status === 204) return null as T;
    return (await res.json()) as T;
  }

  private async send<T>(path: string, init: RequestInit = {}, timeoutMs = 8000): Promise<T> {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      // ngrok's free tier shows an interstitial page unless this header is set
      const headers = { 'ngrok-skip-browser-warning': '1', ...(init.headers ?? {}) };
      return await this.json<T>(await fetch(this.url(path), { ...init, headers, signal: ctl.signal }));
    } finally {
      clearTimeout(timer);
    }
  }

  health = () => this.send<Health>('/api/health', {}, 4000);
  profile = () => this.send<Profile>('/api/profile', {}, 4000);

  chat(text: string, history: ChatTurn[], currentRoom?: string) {
    return this.send<ChatResponse>('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, history, current_room: currentRoom }),
    }, 6000);
  }

  observe(frame: UploadFile, room: string) {
    const body = new FormData();
    body.append('frame', filePart(frame));
    body.append('room', room);
    return this.send<{ sightings: unknown[] } | null>('/api/observe', { method: 'POST', body });
  }

  guide(frame: UploadFile, item: string, yawDeg?: number | null) {
    const body = new FormData();
    body.append('frame', filePart(frame));
    body.append('item', item);
    if (typeof yawDeg === 'number') body.append('yaw_deg', String(yawDeg));
    return this.send<GuideResponse | null>('/api/guide', { method: 'POST', body });
  }

  /**
   * mp3 bytes for a reply from POST /api/speak. Resolves null when `cachedOnly` and nothing is cached
   * (HTTP 204). Rejects on any failure or timeout so the caller can use the built-in voice.
   */
  async speak(text: string, cachedOnly: boolean, timeoutMs: number): Promise<Uint8Array | null> {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(this.url(`/api/speak${cachedOnly ? '?cached_only=true' : ''}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' },
        body: JSON.stringify({ text }),
        signal: ctl.signal,
      });
      if (res.status === 204) return null;
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return new Uint8Array(await res.arrayBuffer());
    } finally {
      clearTimeout(timer);
    }
  }

  transcribe(audio: UploadFile) {
    const body = new FormData();
    body.append('audio', filePart(audio));
    return this.send<{ text: string; provider: string }>('/api/transcribe', { method: 'POST', body }, 15000);
  }
}
