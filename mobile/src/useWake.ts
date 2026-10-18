// "Hey Jarvis": while `enabled`, stream the microphone to the backend's /ws/wake, and when it answers "wake"
// capture the request that follows (until the user stops talking) and hand it over as a WAV.
//
// Audio stays in memory: wake audio goes only to YOUR backend, and nothing is stored. Pure parts are in
// wakeCore.ts. iOS caveat (read from expo-audio's native code): AudioStream.start() puts the phone's audio
// session in RECORD-only mode, so nothing can play while this is on. The caller must therefore turn
// `enabled` off whenever the app plays sound (guidance beeps, spoken replies), and this hook restores the
// playback mode when it stops. UNVERIFIED on real phones.
import { requestRecordingPermissionsAsync, setAudioModeAsync, useAudioStream } from 'expo-audio';
import { useEffect, useRef, useState } from 'react';
import { errorText } from './frameStats';
import {
  type EndpointOptions, Endpointer, encodeWav16k, levelDb, parseWakeMessage, PcmConverter, streamEncodingFor,
  toArrayBuffer, wakeUrl,
} from './wakeCore';

export type WakeState = 'off' | 'connecting' | 'listening' | 'capturing' | 'error';

type Props = {
  enabled: boolean;
  server: string;
  onWake: () => void;                                   // "Hey Jarvis" was heard: show/feel that we are listening
  onUtterance: (wav: Uint8Array, reason: string) => void;   // the request after it, as a 16 kHz mono WAV
  endpoint?: Partial<EndpointOptions>;
};

type Session = {
  mode: 'wake' | 'capturing' | 'done';
  ws: WebSocket | null;
  ready: boolean;
  fatal: boolean;              // the backend said wake cannot run; do not keep retrying
  conv: PcmConverter | null;
  rate: number;
  chunks: Int16Array[];
  endpointer: Endpointer | null;
  levelAt: number;
};

const RETRY_MS = 3000;
const LEVEL_EVERY_MS = 400;

export function useWake({ enabled, server, onWake, onUtterance, endpoint }: Props) {
  const [state, setState] = useState<WakeState>('connecting');
  const [detail, setDetail] = useState('');
  const [level, setLevel] = useState(-120);
  const callbacks = useRef({ onWake, onUtterance, endpoint });
  useEffect(() => { callbacks.current = { onWake, onUtterance, endpoint }; });
  const session = useRef<Session>({
    mode: 'done', ws: null, ready: false, fatal: false, conv: null, rate: 0, chunks: [], endpointer: null, levelAt: 0,
  });

  const { stream } = useAudioStream({
    sampleRate: 16000, channels: 1, encoding: 'int16',
    onBuffer: (buf) => {
      const s = session.current;
      if (s.mode === 'done') return;
      const data = toArrayBuffer(buf.data);
      if (!data) return;
      if (!s.conv || s.rate !== buf.sampleRate) {
        s.conv = new PcmConverter(buf.sampleRate);
        s.rate = buf.sampleRate;
      }
      const pcm = s.conv.push(data, buf.channels, streamEncodingFor(buf.sampleRate));
      if (pcm.length === 0) return;
      const now = Date.now();
      if (now - s.levelAt > LEVEL_EVERY_MS) {
        s.levelAt = now;
        setLevel(Math.round(levelDb(pcm)));
      }
      if (s.mode === 'wake') {
        if (s.ready && s.ws?.readyState === 1) {
          s.ws.send(pcm);   // React Native sends a typed array's own bytes (offset and length respected)
        }
        return;
      }
      // capturing the request that follows "Hey Jarvis"
      s.chunks.push(pcm);
      const result = s.endpointer?.push(pcm);
      if (result?.stop) {
        s.mode = 'done';
        const total = s.chunks.reduce((n, c) => n + c.length, 0);
        const all = new Int16Array(total);
        let at = 0;
        for (const c of s.chunks) { all.set(c, at); at += c.length; }
        s.chunks = [];
        callbacks.current.onUtterance(encodeWav16k(all), result.reason);
      }
    },
  });

  useEffect(() => {
    if (!enabled) return;
    const url = wakeUrl(server);
    const s = session.current;
    Object.assign(s, { mode: 'wake', ready: false, fatal: false, conv: null, rate: 0, chunks: [], endpointer: null });
    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const open = () => {
      if (cancelled || s.fatal || !url) return;
      const ws = new WebSocket(url);
      ws.binaryType = 'arraybuffer';
      s.ws = ws;
      ws.onmessage = (e) => {
        if (typeof e.data !== 'string') return;
        const m = parseWakeMessage(e.data);
        if (!m || cancelled) return;
        if (m.type === 'ready') {
          s.ready = true;
          setState('listening');
          setDetail('');
        } else if (m.type === 'wake' && s.mode === 'wake') {
          s.mode = 'capturing';
          s.chunks = [];
          s.endpointer = new Endpointer(callbacks.current.endpoint);
          setState('capturing');
          callbacks.current.onWake();
        } else if (m.type === 'error') {
          s.fatal = true;
          setState('error');
          setDetail(m.reason);
        }
      };
      ws.onclose = () => {
        s.ready = false;
        if (cancelled || s.fatal) return;
        setState('connecting');
        retry = setTimeout(open, RETRY_MS);
      };
      ws.onerror = () => { /* onclose follows and retries */ };
    };

    (async () => {
      const permission = await requestRecordingPermissionsAsync();
      if (cancelled) return;
      if (!permission.granted) {
        setState('error');
        setDetail('Microphone permission is needed for the wake word.');
        return;
      }
      if (!url) {
        setState('error');
        setDetail('Set the server address first.');
        return;
      }
      setState('connecting');
      open();
      try {
        await stream.start();
      } catch (e) {
        if (!cancelled) {
          setState('error');
          setDetail(`Microphone stream failed: ${errorText(e)}`);
        }
      }
    })();

    return () => {
      cancelled = true;
      clearTimeout(retry);
      s.mode = 'done';
      s.ws?.close();
      s.ws = null;
      try { stream.stop(); } catch { /* not running */ }
      // stream.start() left the phone in record-only mode: put playback back
      void setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false }).catch(() => {});
    };
  }, [enabled, server, stream]);

  return { state: enabled ? state : ('off' as WakeState), detail, level };
}
