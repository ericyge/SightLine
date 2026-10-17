// Backend wrappers. The app never talks to Gemini, Perceptron or ElevenLabs directly: the backend
// owns every key. Field names match docs/CONTRACT.md.

export type Intent = 'find' | 'where' | 'stop' | 'set_room' | 'chitchat';
export type ChatTurn = { role: 'user' | 'assistant'; content: string };
export type ChatResponse = { intent: Intent; item: string | null; room: string | null; say: string; provider: string };
export type Health = { ok: boolean; mode: string; mock: boolean; stt: 'elevenlabs' | 'browser' };
export type Profile = { watchlist: { name: string }[]; rooms: string[]; guidance_style: string };
export type GuideResponse = {
  found: boolean; point: number[] | null; box: number[] | null; angle: number | null; clock: number | null;
  distance: 'far' | 'near' | 'reach' | null; pan: number; say: string; beep_ms: number;
};

/** A local file to upload (React Native's FormData takes {uri, name, type}). */
export type UploadFile = { uri: string; name: string; type: string };

export class Api {
  constructor(public base: string) {}

  private url(path: string) {
    return `${this.base.replace(/\/+$/, '')}${path}`;
  }

  private async json<T>(res: Response): Promise<T> {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
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
    body.append('frame', frame as unknown as Blob);
    body.append('room', room);
    return this.send<{ sightings: unknown[] }>('/api/observe', { method: 'POST', body });
  }

  guide(frame: UploadFile, item: string, yawDeg?: number | null) {
    const body = new FormData();
    body.append('frame', frame as unknown as Blob);
    body.append('item', item);
    if (typeof yawDeg === 'number') body.append('yaw_deg', String(yawDeg));
    return this.send<GuideResponse>('/api/guide', { method: 'POST', body });
  }

  transcribe(audio: UploadFile) {
    const body = new FormData();
    body.append('audio', audio as unknown as Blob);
    return this.send<{ text: string; provider: string }>('/api/transcribe', { method: 'POST', body }, 15000);
  }
}
