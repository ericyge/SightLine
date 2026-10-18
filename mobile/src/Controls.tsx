// Overlay on the camera: talk to Sightline, hear answers, get directional beeps.
// Same flow as web/src/phone/Phone.jsx. Every call goes to the backend (docs/CONTRACT.md).
import type { CameraView } from 'expo-camera';
import {
  RecordingPresets, requestRecordingPermissionsAsync, setAudioModeAsync, useAudioRecorder,
} from 'expo-audio';
import { manipulateAsync, SaveFormat } from 'expo-image-manipulator';
import * as Speech from 'expo-speech';
import { type RefObject, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { Api, type ChatResponse, type ChatTurn, type GuideResponse, type UploadFile } from './api';
import { playBeep, prepareBeeps, releaseBeeps } from './beeps';
import {
  describeLast, emptyStats, errorText, type LoopStats, record, type Result, StageError, summarize,
} from './frameStats';
import { compensate, panFromAngle, pitchBoost } from './guidance';
import { getYaw, startOrientation, stopOrientation } from './orientation';
import { configureVoice, releaseVoice, voice } from './voice';
import { guideIntervalMs, waitMs } from './pacing';

const LOOP_MS = 1000;
const STALE_MS = 3000;       // silence beeps if the last guide response is older than this
const HISTORY_TURNS = 6;     // matches the backend limit
const MIN_HOLD_MS = 300;     // shorter presses are treated as accidental taps
const DEFAULT_SERVER = process.env.EXPO_PUBLIC_API_BASE ?? '';
// Expo says to wait for the camera's onCameraReady before taking a photo. If it never fires, try anyway
// after this long and show whatever error comes back in the debug counter.
const CAMERA_WAIT_MS = 4000;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function say(text: string, interrupt = false) {
  if (!text) return;
  if (interrupt) void Speech.stop();
  Speech.speak(text, { language: 'en-US' });
}

type Props = { cameraRef: RefObject<CameraView | null>; cameraReady: boolean };

export default function Controls({ cameraRef, cameraReady }: Props) {
  const [server, setServer] = useState(DEFAULT_SERVER);
  const [showSettings, setShowSettings] = useState(!DEFAULT_SERVER);
  const api = useMemo(() => new Api(server), [server]);
  const [connected, setConnected] = useState(false);
  const [mock, setMock] = useState(false);
  const [stt, setStt] = useState<'elevenlabs' | 'browser'>('browser');
  const [rooms, setRooms] = useState<string[]>(['bedroom', 'kitchen', 'living room']);
  const [room, setRoom] = useState('bedroom');
  const [status, setStatus] = useState('Set the server address to begin.');
  const [cue, setCue] = useState('');
  const [guiding, setGuiding] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [typed, setTyped] = useState('');
  const [memStats, setMemStats] = useState<LoopStats>(emptyStats);     // /api/observe frames
  const [guideStats, setGuideStats] = useState<LoopStats>(emptyStats);   // /api/guide frames
  const [showDebug, setShowDebug] = useState(true);
  const [now, setNow] = useState(0);                                      // ticks so the 'Ns ago' text stays fresh

  const roomRef = useRef(room);
  const cameraReadyRef = useRef(cameraReady);
  const mountedAt = useRef(0);
  useEffect(() => { roomRef.current = room; }, [room]);
  useEffect(() => { cameraReadyRef.current = cameraReady; }, [cameraReady]);
  useEffect(() => {
    mountedAt.current = Date.now();
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const history = useRef<ChatTurn[]>([]);
  const guideRef = useRef<{ last: GuideResponse | null; yawAtLast: number | null; at: number }>(
    { last: null, yawAtLast: null, at: 0 });
  const guideMs = useRef(guideIntervalMs()); // min ms between /api/guide requests (backend GUIDE_MAX_FPS)
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recording = useRef<{ ready: Promise<boolean>; startedAt: number } | null>(null);

  // One-time audio setup: render the beep files, allow sound with the silent switch on.
  useEffect(() => {
    prepareBeeps();
    void setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false });
    void startOrientation();
    return () => { releaseBeeps(); releaseVoice(); stopOrientation(); void Speech.stop(); };
  }, []);

  const [attempt, setAttempt] = useState(0);
  const connect = () => setAttempt((n) => n + 1);
  useEffect(() => {
    if (!server) return;
    let cancelled = false;
    (async () => {
      try {
        const [health, profile] = await Promise.all([api.health(), api.profile()]);
        if (cancelled) return;
        setConnected(true);
        setMock(health.mock);
        setStt(health.stt);
        configureVoice(api, health);   // ElevenLabs voice on/off and the guidance-cue setting
        guideMs.current = guideIntervalMs(health.guide_max_fps);
        setRooms(profile.rooms);
        setRoom((r) => (profile.rooms.includes(r) ? r : profile.rooms[0] ?? r));
        setStatus(health.stt === 'elevenlabs'
          ? 'Ready. Hold the button and speak.'
          : 'Ready. Voice needs the server speech key; type your request.');
      } catch {
        if (cancelled) return;
        setConnected(false);
        setStatus(`Cannot reach ${server}. Same Wi-Fi? Is the backend running?`);
      }
    })();
    return () => { cancelled = true; };
  }, [api, server, attempt]);

  /** One 640 px JPEG from the camera (no shutter sound). Failures say which step broke. */
  const captureFrame = useCallback(async (): Promise<UploadFile> => {
    let pic;
    try {
      pic = await cameraRef.current?.takePictureAsync({ quality: 0.5, shutterSound: false });
    } catch (e) {
      throw new StageError('camera', errorText(e));
    }
    if (!pic) throw new StageError('camera', cameraRef.current ? 'no picture returned' : 'camera ref is empty');
    try {
      const small = await manipulateAsync(pic.uri, [{ resize: { width: 640 } }],
        { compress: 0.6, format: SaveFormat.JPEG });
      return { uri: small.uri, name: 'frame.jpg', type: 'image/jpeg' };
    } catch (e) {
      throw new StageError('resize', errorText(e));
    }
  }, [cameraRef]);

  /** Wait for the camera (see CAMERA_WAIT_MS). */
  const cameraUsable = () => cameraReadyRef.current || Date.now() - mountedAt.current > CAMERA_WAIT_MS;

  // Memory: about one frame a second to /api/observe while not guiding. Starts as soon as the backend
  // connects and the camera is ready. Every outcome is counted so a failure shows up on screen.
  useEffect(() => {
    if (!connected || guiding) return;
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        if (!cameraUsable()) { await sleep(250); continue; }
        const t0 = Date.now();
        let result: Result;
        try {
          const r = await api.observe(await captureFrame(), roomRef.current);
          result = { kind: r ? 'ok' : 'dropped', at: Date.now(), ms: Date.now() - t0 };
        } catch (e) {
          result = {
            kind: 'failed', at: Date.now(), ms: Date.now() - t0, error: errorText(e),
            stage: e instanceof StageError ? e.stage : 'upload',
          };
        }
        if (cancelled) break;
        setMemStats((st) => record(st, result));
        await sleep(Math.max(0, LOOP_MS - (Date.now() - t0)));
      }
    })();
    return () => { cancelled = true; };
  }, [api, connected, guiding, captureFrame]);

  // Guidance: as fast as the backend answers, capped (GUIDE_MAX_FPS), one request in flight and never
  // queued; plus a fast local loop that plays the beeps and keeps the pan right between answers.
  useEffect(() => {
    if (!guiding) return;
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        if (!cameraUsable()) { await sleep(250); continue; }
        const t0 = Date.now();
        let result: Result;
        try {
          const yaw = getYaw();
          const r = await api.guide(await captureFrame(), guiding, yaw);
          if (cancelled) break;
          // null = 204: the backend dropped this frame (still busy). Keep the last result.
          if (r) {
            guideRef.current = { last: r, yawAtLast: yaw, at: Date.now() };
            if (r.say) { void voice.cue(r.say); setCue(r.say); }
          }
          result = { kind: r ? 'ok' : 'dropped', at: Date.now(), ms: Date.now() - t0 };
        } catch (e) {
          setStatus('Connection problem. Still trying.');
          result = {
            kind: 'failed', at: Date.now(), ms: Date.now() - t0, error: errorText(e),
            stage: e instanceof StageError ? e.stage : 'upload',
          };
        }
        if (cancelled) break;
        setGuideStats((st) => record(st, result));
        await sleep(waitMs(guideMs.current, Date.now() - t0));
      }
    })();
    (async () => {
      while (!cancelled) {
        const { last, yawAtLast, at } = guideRef.current;
        if (last?.found && last.angle != null && Date.now() - at < STALE_MS) {
          // Between vision updates, keep the pan right as the phone turns.
          const angle = compensate(last.angle, getYaw(), yawAtLast);
          playBeep(panFromAngle(angle), pitchBoost(angle));
          await sleep(last.beep_ms);
        } else {
          await sleep(100);
        }
      }
    })();
    return () => { cancelled = true; guideRef.current = { last: null, yawAtLast: null, at: 0 }; };
  }, [api, guiding, captureFrame]);

  const stopGuidance = useCallback(() => {
    setGuiding(null);
    setCue('');
    setStatus('Guidance stopped.');
    void voice.cue('Stopped.');
  }, []);

  const apply = useCallback((r: ChatResponse) => {
    history.current = [...history.current, { role: 'assistant' as const, content: r.say.slice(0, 500) }]
      .slice(-HISTORY_TURNS);
    if (r.intent === 'stop') return stopGuidance();
    if (r.intent === 'find' && r.item) setGuiding(r.item);
    if (r.intent === 'set_room' && r.room) setRoom(r.room);
    setStatus(r.say);
    void voice.reply(r.say);   // ElevenLabs voice when enabled, built-in voice on any failure
  }, [stopGuidance]);

  const runCommand = useCallback(async (text: string) => {
    history.current = [...history.current, { role: 'user' as const, content: text.slice(0, 500) }]
      .slice(-HISTORY_TURNS);
    try {
      apply(await api.chat(text, history.current.slice(0, -1), roomRef.current));
    } catch {
      setStatus('Could not reach the server.');
      say('I could not reach the server.', true);
    }
  }, [api, apply]);

  // ---- hold to talk (server speech-to-text) ----
  async function beginTalk() {
    if (listening || stt !== 'elevenlabs') return;
    setListening(true);
    setStatus('Listening…');
    Speech.stop();
    voice.stop();   // do not talk over the user
    const ready = (async () => {
      const perm = await requestRecordingPermissionsAsync();
      if (!perm.granted) { setStatus('Microphone permission is needed to talk.'); return false; }
      await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      return true;
    })().catch(() => { setStatus('Microphone unavailable.'); return false; });
    recording.current = { ready, startedAt: Date.now() };
  }

  async function endTalk() {
    const current = recording.current;
    recording.current = null;
    if (!current) return;
    const ok = await current.ready;
    if (!ok) { setListening(false); return; }
    try {
      await recorder.stop();
      await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false });
      setListening(false);
      if (Date.now() - current.startedAt < MIN_HOLD_MS || !recorder.uri) {
        setStatus('Hold the button while you speak.');
        return say('Hold the button while you speak.');
      }
      setStatus('Thinking…');
      // m4a/AAC from expo-audio's preset. UNVERIFIED that ElevenLabs accepts it (docs say "all major formats").
      const { text } = await api.transcribe({ uri: recorder.uri, name: 'recording.m4a', type: 'audio/mp4' });
      if (!text) { setStatus("I didn't hear anything. Try again."); return say("I didn't hear anything. Try again."); }
      await runCommand(text);
    } catch {
      setListening(false);
      setStatus('Speech service unavailable. Type your request instead.');
      say('Speech service unavailable. Type your request instead.');
    }
  }

  const submitTyped = () => {
    const text = typed.trim();
    if (!text) return;
    setTyped('');
    void runCommand(text);
  };

  return (
    <View style={styles.overlay} pointerEvents="box-none">
      <View style={styles.panel}>
        {mock && <Text style={styles.badge}>MOCK MODE: fake detections</Text>}
        <Text style={styles.status} accessibilityLiveRegion="polite">{status}</Text>
        {guiding && <Text style={styles.cue}>{cue || `Searching for ${guiding}…`}</Text>}
        <View style={styles.rooms} accessibilityRole="radiogroup">
          {rooms.map((r) => (
            <Pressable key={r} onPress={() => setRoom(r)} accessibilityRole="radio"
              accessibilityState={{ selected: r === room }} accessibilityLabel={`Room ${r}`}
              style={[styles.chip, r === room && styles.chipOn]}>
              <Text style={[styles.chipText, r === room && styles.chipTextOn]}>{r}</Text>
            </Pressable>
          ))}
        </View>
        {showDebug && (
          // Debug counters for sighted helpers; hidden from screen readers so they are not read aloud.
          <View style={styles.debug} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
            <Text style={styles.debugText}>
              Camera: {cameraReady ? 'ready' : 'starting...'} | Backend: {connected ? 'connected' : 'NOT connected'}
            </Text>
            <Text style={styles.debugText}>{summarize('Memory', memStats)}</Text>
            <Text style={[styles.debugText, memStats.last?.kind === 'failed' && styles.debugBad]}>
              Last memory frame: {describeLast(memStats, now)}
            </Text>
            {guiding && <Text style={styles.debugText}>{summarize('Guide', guideStats)}</Text>}
            {guiding && (
              <Text style={[styles.debugText, guideStats.last?.kind === 'failed' && styles.debugBad]}>
                Last guide frame: {describeLast(guideStats, now)}
              </Text>
            )}
          </View>
        )}
        <Pressable onPress={() => setShowDebug((d) => !d)} accessibilityRole="button"
          accessibilityLabel={showDebug ? 'Hide debug counters' : 'Show debug counters'} style={styles.link}>
          <Text style={styles.linkText}>{showDebug ? 'Hide' : 'Show'} debug counters</Text>
        </Pressable>
        <Pressable onPress={() => setShowSettings((s) => !s)} accessibilityRole="button"
          accessibilityLabel="Server settings" style={styles.link}>
          <Text style={styles.linkText}>{connected ? 'Connected' : 'Not connected'} · settings</Text>
        </Pressable>
        {showSettings && (
          <View style={styles.settings}>
            <TextInput value={server} onChangeText={setServer} autoCapitalize="none" autoCorrect={false}
              placeholder="http://192.168.x.x:8000 or https://….ngrok-free.app" placeholderTextColor="#999"
              accessibilityLabel="Server address" style={styles.input} onSubmitEditing={connect} />
            <Pressable onPress={connect} accessibilityRole="button" accessibilityLabel="Connect" style={styles.small}>
              <Text style={styles.smallText}>Connect</Text>
            </Pressable>
          </View>
        )}
      </View>

      <View style={styles.bottom}>
        {stt !== 'elevenlabs' && (
          <View style={styles.settings}>
            <TextInput value={typed} onChangeText={setTyped} onSubmitEditing={submitTyped} returnKeyType="send"
              placeholder="where's my wallet" placeholderTextColor="#999"
              accessibilityLabel="Type a request" style={styles.input} />
            <Pressable onPress={submitTyped} accessibilityRole="button" accessibilityLabel="Send" style={styles.small}>
              <Text style={styles.smallText}>Send</Text>
            </Pressable>
          </View>
        )}
        {guiding && (
          <Pressable onPress={stopGuidance} accessibilityRole="button" accessibilityLabel="Stop guidance"
            style={styles.stop}>
            <Text style={styles.stopText}>Stop</Text>
          </Pressable>
        )}
        {/* TODO(C): press-and-hold is awkward with VoiceOver/TalkBack; add a double-tap toggle. */}
        <Pressable onPressIn={beginTalk} onPressOut={endTalk} disabled={stt !== 'elevenlabs'}
          accessibilityRole="button" accessibilityLabel="Hold to talk"
          accessibilityHint="Press and hold, speak, then release"
          style={[styles.talk, listening && styles.talkOn, stt !== 'elevenlabs' && styles.talkOff]}>
          <Text style={styles.bigText}>{listening ? 'Listening…' : 'Hold to talk'}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: { ...StyleSheet.absoluteFill, justifyContent: 'space-between', padding: 12 },
  panel: { backgroundColor: 'rgba(0,0,0,0.72)', borderRadius: 14, padding: 14, gap: 10, marginTop: 36 },
  badge: { alignSelf: 'flex-start', backgroundColor: '#ffe14d', color: '#000', fontWeight: '800', padding: 6, borderRadius: 6 },
  status: { color: '#fff', fontSize: 22, lineHeight: 30 },
  cue: { color: '#ffe14d', fontSize: 30, fontWeight: '800' },
  rooms: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { minHeight: 48, paddingHorizontal: 16, borderRadius: 24, borderWidth: 2, borderColor: '#fff', justifyContent: 'center' },
  chipOn: { backgroundColor: '#fff' },
  chipText: { color: '#fff', fontSize: 18 },
  chipTextOn: { color: '#000', fontWeight: '700' },
  link: { minHeight: 44, justifyContent: 'center' },
  linkText: { color: '#9cf', fontSize: 16 },
  debug: { backgroundColor: 'rgba(0,40,0,0.85)', borderRadius: 8, padding: 8, gap: 2 },
  debugText: { color: '#9f9', fontSize: 14, lineHeight: 19 },
  debugBad: { color: '#ff9b9b', fontWeight: '700' },
  settings: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  input: { flex: 1, minHeight: 52, backgroundColor: '#fff', borderRadius: 10, paddingHorizontal: 12, fontSize: 18, color: '#000' },
  small: { minHeight: 52, minWidth: 80, backgroundColor: '#2d6cff', borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  smallText: { color: '#fff', fontSize: 18, fontWeight: '700' },
  bottom: { gap: 12 },
  talk: { minHeight: 180, borderRadius: 24, backgroundColor: '#2d6cff', alignItems: 'center', justifyContent: 'center' },
  talkOn: { backgroundColor: '#4dff88' },
  talkOff: { opacity: 0.45 },
  stop: { minHeight: 90, borderRadius: 24, backgroundColor: '#ff6b6b', alignItems: 'center', justifyContent: 'center' },
  stopText: { color: '#000', fontSize: 34, fontWeight: '800' },
  bigText: { color: '#fff', fontSize: 34, fontWeight: '800' },
});
