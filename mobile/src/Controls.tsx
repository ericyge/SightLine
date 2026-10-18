// Overlay on the camera: talk to Sightline, hear answers, get directional beeps.
// Same flow as web/src/phone/Phone.jsx. Every call goes to the backend (docs/CONTRACT.md).
import type { CameraView } from 'expo-camera';
import {
  RecordingPresets, requestRecordingPermissionsAsync, setAudioModeAsync, useAudioRecorder,
} from 'expo-audio';
import { manipulateAsync, SaveFormat } from 'expo-image-manipulator';
import * as Speech from 'expo-speech';
import { type RefObject, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, Vibration, View } from 'react-native';
import { Api, type CalibrationStatus, type ChatResponse, type ChatTurn, type GuideResponse, type UploadFile } from './api';
import { calibrationCommand, observationSession, prepareLearningReply } from './calibration';
import { playBeep, prepareBeeps, releaseBeeps } from './beeps';
import { CameraWatchdog, SingleFlight } from './capture';
import {
  describeFirstFailure, describeLast, emptyStats, errorText, type LoopStats, record, type Result, StageError, summarize,
} from './frameStats';
import { compensate, panFromAngle, pitchBoost } from './guidance';
import { getYaw, startOrientation, stopOrientation } from './orientation';
import { loadServer, saveServer } from './serverStore';
import { configureVoice, isVoicePlaying, releaseVoice, voice } from './voice';
import { useWake } from './useWake';
import { File, Paths } from 'expo-file-system';
import { guideIntervalMs, waitMs } from './pacing';

const LOOP_MS = 1000;
const STALE_MS = 3000;       // silence beeps if the last guide response is older than this
const HISTORY_TURNS = 6;     // matches the backend limit
const MIN_HOLD_MS = 300;     // shorter presses are treated as accidental taps
const DEFAULT_SERVER = process.env.EXPO_PUBLIC_API_BASE ?? '';
// Expo says to wait for the camera's onCameraReady before taking a photo. If it never fires, try anyway
// after this long and show whatever error comes back in the debug counter.
const CAMERA_WAIT_MS = 4000;
// iOS refuses a new photo while the previous one is unfinished (CameraNotReadyException). Photos run
// one at a time with a generous timeout (a slow photo must be allowed to finish). The camera is only
// restarted when photos keep failing for real or one has been stuck for a long time (CameraWatchdog).
const CAPTURE_TIMEOUT_MS = 15000;
const RESTART_AFTER_FAILS = 3;       // real camera errors/timeouts in a row before restarting the camera
const STUCK_RESTART_MS = 20000;      // an unfinished photo this long also triggers a restart
const RESTART_COOLDOWN_MS = 15000;   // never restart more often than this

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

type Props = {
  cameraRef: RefObject<CameraView | null>;
  cameraReady: boolean;
  onRestartCamera: () => void;   // remounts the camera view (must be a stable function)
};

export default function Controls({ cameraRef, cameraReady, onRestartCamera }: Props) {
  // The address survives reloads: the last one that connected is read back from a small file.
  const [initialServer] = useState(() => DEFAULT_SERVER || loadServer());
  const [server, setServer] = useState(initialServer);
  const [showSettings, setShowSettings] = useState(!initialServer);
  const api = useMemo(() => new Api(server), [server]);
  const [connected, setConnected] = useState(false);
  const [mock, setMock] = useState(false);
  const [stt, setStt] = useState<'elevenlabs' | 'browser'>('browser');
  const [wakeAvailable, setWakeAvailable] = useState(false);   // backend can run the wake word
  const [wakeOn, setWakeOn] = useState(true);                   // the user's switch
  const [wakeBusy, setWakeBusy] = useState(false);              // handling a wake request, or its reply is playing
  const [speechBusy, setSpeechBusy] = useState(false);          // TTS/spoken cue playing (wake must yield the mic)
  const speechHoldRef = useRef(0);                              // >0 while a reply or guidance cue is actively speaking
  const hushBeeps = useRef(false);                              // true while wake or speech owns the audio session
  const [rooms, setRooms] = useState<string[]>(['bedroom', 'kitchen', 'living room']);
  const [room, setRoom] = useState('');  // Automatic: never constrain a walk to the first profile room.
  const [sessionId] = useState(observationSession);
  const [calibration, setCalibration] = useState<CalibrationStatus | null>(null);
  const calibrationRef = useRef<CalibrationStatus | null>(null);
  const [calibrationBusy, setCalibrationBusy] = useState(false);
  const calibrationActionRef = useRef(false);
  const commandInFlight = useRef(false);
  const [commandBusy, setCommandBusy] = useState(false);
  const resumeLearning = useRef(false);
  const pendingFrame = useRef<Promise<unknown> | null>(null);
  const calibrationSequence = useRef(0);
  const calibrationFailed = useRef(false);
  const memoryWarned = useRef(false);
  const [status, setStatus] = useState('Set the server address to begin.');
  const [cue, setCue] = useState('');
  const [guiding, setGuiding] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [typed, setTyped] = useState('');
  const [memStats, setMemStats] = useState<LoopStats>(emptyStats);     // /api/observe frames
  const [guideStats, setGuideStats] = useState<LoopStats>(emptyStats);   // /api/guide frames
  const [showDebug, setShowDebug] = useState(false);
  const [testResult, setTestResult] = useState('');                   // result of the one-tap camera test
  const pausedRef = useRef(false);                                    // loops pause while the test runs
  const [restarts, setRestarts] = useState(0);                            // automatic camera restarts
  const [flight] = useState(() => new SingleFlight());                    // one photo at a time
  const [watchdog] = useState(() => new CameraWatchdog(RESTART_AFTER_FAILS, STUCK_RESTART_MS, RESTART_COOLDOWN_MS));
  const memBeat = useRef(0);                                          // last time the memory loop ticked
  const [beat, setBeat] = useState(0);
  const [testing, setTesting] = useState(false);
  const [now, setNow] = useState(0);                                      // ticks so the 'Ns ago' text stays fresh

  const roomRef = useRef(room);
  const cameraReadyRef = useRef(cameraReady);
  const cameraStartedAt = useRef(0);   // when the camera view was (re)mounted, for the ready timeout
  useEffect(() => { roomRef.current = room; }, [room]);
  useEffect(() => { cameraReadyRef.current = cameraReady; }, [cameraReady]);
  useEffect(() => {
    cameraStartedAt.current = Date.now();
    const t = setInterval(() => { setNow(Date.now()); setBeat(memBeat.current); }, 1000);
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
  const resetConnection = () => {
    setConnected(false);
    calibrationRef.current = null;
    setCalibration(null);
  };
  const changeServer = (value: string) => { resetConnection(); setServer(value); };
  const connect = () => { resetConnection(); setAttempt((n) => n + 1); };
  useEffect(() => {
    if (!server) return;
    let cancelled = false;
    (async () => {
      try {
        const [health, profile, savedCalibration] = await Promise.all([api.health(), api.profile(), api.calibration()]);
        if (cancelled) return;
        const restored = savedCalibration.available
          ? await api.startCalibration() : savedCalibration;
        if (cancelled) return;
        setConnected(true);
        saveServer(server);   // remember it for the next reload
        setMock(health.mock);
        setStt(health.stt);
        setWakeAvailable(Boolean(health.wake));
        configureVoice(api, health);   // ElevenLabs voice on/off and the guidance-cue setting
        guideMs.current = guideIntervalMs(health.guide_max_fps);
        setRooms(profile.rooms);
        setRoom((r) => (profile.rooms.includes(r) ? r : ''));
        calibrationRef.current = restored;
        calibrationSequence.current = restored.last_sequence;
        setCalibration(restored);
        // Blind-first: announce readiness out loud. Screen text is for helpers only.
        const wakeReady = health.stt === 'elevenlabs' && Boolean(health.wake);
        let readySay: string;
        if (restored.status === 'mapping' && wakeReady) {
          readySay = 'Connected. I am learning your space. Say Hey Jarvis, then ask about an object.';
        } else if (restored.status === 'mapping') {
          readySay = 'Connected and learning, but Hey Jarvis is not available. Check the speech and wake settings on the server.';
        } else if (wakeReady) {
          readySay = 'Connected. Apartment learning needs full Perceptron mode. Say Hey Jarvis when you are ready.';
        } else {
          readySay = 'Connected, but voice control is not ready. Check speech and wake settings on the server.';
        }
        setStatus(readySay);
        void voice.reply(readySay);
      } catch {
        if (cancelled) return;
        setConnected(false);
        const fail = 'Cannot reach the server. Check that the phone and laptop are on the same Wi-Fi, and that the backend is running.';
        setStatus(fail);
        void voice.reply(fail);
      }
    })();
    return () => { cancelled = true; };
  }, [api, server, attempt]);

  /** One 640 px JPEG from the camera (no shutter sound). Failures say which step broke. */
  const captureFrame = useCallback(async (): Promise<UploadFile> => {
    const camera = cameraRef.current;
    if (!camera) throw new StageError('camera', 'camera ref is empty');
    let pic;
    try {
      pic = await flight.run(() => camera.takePictureAsync({ quality: 0.5, shutterSound: false }), CAPTURE_TIMEOUT_MS);
    } catch (e) {
      throw new StageError('camera', errorText(e));
    }
    if (!pic) throw new StageError('camera', 'no picture returned');
    try {
      const small = await manipulateAsync(pic.uri, [{ resize: { width: 640 } }],
        { compress: 0.6, format: SaveFormat.JPEG });
      return { uri: small.uri, name: 'frame.jpg', type: 'image/jpeg' };
    } catch (e) {
      throw new StageError('resize', errorText(e));
    }
  }, [cameraRef, flight]);

  /** Wait for the camera (see CAMERA_WAIT_MS). */
  const cameraUsable = () => cameraReadyRef.current || Date.now() - cameraStartedAt.current > CAMERA_WAIT_MS;

  /** After each frame: restart the camera if photos keep failing or one is stuck (see CameraWatchdog). */
  const afterAttempt = useCallback((r: Result) => {
    const cameraFailed = r.kind === 'failed' && r.stage === 'camera';
    if (watchdog.note(cameraFailed, r.error, Date.now())) {
      cameraStartedAt.current = Date.now();   // give the new camera view time to report ready
      flight.reset();
      setRestarts((n) => n + 1);
      onRestartCamera();
    }
  }, [watchdog, flight, onRestartCamera]);

  // Memory: pipelined capture (~1 frame/s) while not guiding. Captures the photo and fires the upload
  // without waiting for the previous HTTP response to return (up to 3 in flight), saving responses as they arrive.
  useEffect(() => {
    if (!connected || guiding) return;
    let cancelled = false;
    const inFlight = new Set<Promise<unknown>>();
    const syncPending = () => {
      pendingFrame.current = inFlight.size ? Promise.allSettled([...inFlight]) : null;
    };
    (async () => {
      while (!cancelled) {
        memBeat.current = Date.now();
        if (pausedRef.current || !cameraUsable()
            || (calibrationRef.current?.status === 'idle' && !calibrationRef.current.places.length)) {
          await sleep(250); continue;
        }
        if (inFlight.size >= 4) {
          await Promise.race([...inFlight]).catch(() => undefined);
          continue;
        }
        const t0 = Date.now();
        let frame: { uri: string; name: string; type: string };
        try {
          frame = await captureFrame();
        } catch (e) {
          if (cancelled) break;
          const failRes: Result = {
            kind: 'failed', at: Date.now(), ms: Date.now() - t0, error: errorText(e),
            stage: e instanceof StageError ? e.stage : 'camera',
          };
          setMemStats((st) => record(st, failRes));
          afterAttempt(failRes);
          await sleep(400);
          continue;
        }
        if (cancelled) break;
        const capturedAt = new Date().toISOString();
        const active = calibrationRef.current;
        const seq = active?.status === 'mapping' && active.session_id ? ++calibrationSequence.current : 0;
        const sessionForFrame = active?.session_id;
        const roomForFrame = roomRef.current;

        const uploadTask = (async () => {
          let result: Result;
          try {
            let r: unknown;
            if (seq > 0 && sessionForFrame) {
              const next = await api.calibrationFrame(frame, sessionForFrame, seq, capturedAt);
              if (!cancelled && next.last_sequence >= (calibrationRef.current?.last_sequence ?? 0)) {
                calibrationRef.current = next;
                setCalibration(next);
                if (next.memory_warning) {
                  const warning = 'Mapping continues, but object memory could not process this frame.';
                  setStatus(warning);
                  if (!memoryWarned.current) void voice.cue(warning);
                }
                if (!next.memory_warning && calibrationFailed.current) void voice.cue('Apartment learning has resumed.');
                memoryWarned.current = !!next.memory_warning;
                calibrationFailed.current = false;
              }
              r = next;
            } else {
              r = await api.observe(frame, roomForFrame, sessionId);
            }
            result = { kind: r ? 'ok' : 'dropped', at: Date.now(), ms: Date.now() - t0 };
          } catch (e) {
            if (!cancelled && calibrationRef.current?.status === 'mapping') {
              setStatus(`Apartment learning failed: ${errorText(e)}. Still trying.`);
              if (!calibrationFailed.current) void voice.cue('Apartment learning could not process this view. I’ll keep trying.');
              calibrationFailed.current = true;
            }
            result = {
              kind: 'failed', at: Date.now(), ms: Date.now() - t0, error: errorText(e),
              stage: e instanceof StageError ? e.stage : 'upload',
            };
          }
          if (!cancelled) {
            setMemStats((st) => record(st, result));
            afterAttempt(result);
          }
        })();

        inFlight.add(uploadTask);
        syncPending();
        void uploadTask.finally(() => {
          inFlight.delete(uploadTask);
          syncPending();
        });

        await sleep(Math.max(150, 550 - (Date.now() - t0)));
      }
    })();
    return () => { cancelled = true; };
  }, [api, connected, guiding, captureFrame, afterAttempt, sessionId]);

  // ---- "Hey Jarvis": primary hands-free path (blind-first; hold-to-talk is backup only) ----
  // iOS: AudioStream forces record-only mode while active. Yield the mic whenever TTS speech
  // (`speechBusy`), a wake utterance (`wakeBusy`), or push-to-talk (`listening`) is active, and
  // immediately re-arm "Hey Jarvis" in between guidance cues so the user can say
  // "Hey Jarvis, I got the wallet" or "Hey Jarvis, stop" hands-free.
  const wakeEnabled = (wakeAvailable && wakeOn && connected && stt === 'elevenlabs'
    && !listening && !wakeBusy && !speechBusy);
  // Beep loop and guidance cue queue read these refs so speech/wake state toggles do not
  // remount the guidance effect or interrupt an active "Hey Jarvis, I got the [object]" capture.
  const wakeEnabledRef = useRef(wakeEnabled);
  useEffect(() => { wakeEnabledRef.current = wakeEnabled; }, [wakeEnabled]);
  const voiceCaptureActiveRef = useRef(false);

  // Guidance: as fast as the backend answers, capped (GUIDE_MAX_FPS), one request in flight and never
  // queued; plus a fast local loop that plays the beeps and keeps the pan right between answers.
  useEffect(() => {
    if (!guiding) return;
    let cancelled = false;
    let guidanceFailed = false;
    let activeCuePromise: Promise<void> | null = null;
    let queuedCueText = '';

    const releaseCueHold = () => {
      speechHoldRef.current = Math.max(0, speechHoldRef.current - 1);
      if (speechHoldRef.current === 0) setSpeechBusy(false);
    };

    const enqueueCue = (text: string) => {
      if (voiceCaptureActiveRef.current) return;
      queuedCueText = text;
      if (activeCuePromise) return;
      const drain = async () => {
        speechHoldRef.current += 1;
        setSpeechBusy(true);
        try {
          await sleep(120);
          while (!cancelled && queuedCueText && !voiceCaptureActiveRef.current) {
            const nextText = queuedCueText;
            queuedCueText = '';
            setCue(nextText);
            // Cap so a hung TTS path cannot leave Hey Jarvis muted forever.
            await Promise.race([voice.cue(nextText), sleep(12000)]);
          }
        } finally {
          await sleep(350);
          releaseCueHold();
          activeCuePromise = null;
          if (!cancelled && queuedCueText && !voiceCaptureActiveRef.current) {
            activeCuePromise = drain();
          }
        }
      };
      activeCuePromise = drain();
    };

    // Guarantee iOS playback mode so 3D spatial beeps and voice cues are never silenced by record-only mode.
    void setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false }).catch(() => {});
    (async () => {
      while (!cancelled) {
        if (testing || calibrationActionRef.current || voiceCaptureActiveRef.current || !cameraUsable()) {
          await sleep(250);
          continue;
        }
        const t0 = Date.now();
        let result: Result;
        try {
          const yaw = getYaw();
          const r = await api.guide(await captureFrame(), guiding, yaw);
          if (cancelled) break;
          // null = 204: the backend dropped this frame (still busy). Keep the last result.
          if (r && !voiceCaptureActiveRef.current) {
            guideRef.current = { last: r, yawAtLast: yaw, at: Date.now() };
            const spoken = r.say || (guidanceFailed ? r.next_instruction || 'Camera updates have resumed.' : '');
            if (spoken) {
              setCue(spoken);
              enqueueCue(spoken);
            }
            guidanceFailed = false;
          }
          result = { kind: r ? 'ok' : 'dropped', at: Date.now(), ms: Date.now() - t0 };
        } catch (e) {
          if (cancelled) break;
          guideRef.current = { last: null, yawAtLast: null, at: 0 };
          const warning = 'Pause walking. I cannot check the camera view. Retrying.';
          if (!guidanceFailed) void voice.cue(warning);
          guidanceFailed = true;
          setCue(warning);
          setStatus('Connection problem. Still trying.');
          result = {
            kind: 'failed', at: Date.now(), ms: Date.now() - t0, error: errorText(e),
            stage: e instanceof StageError ? e.stage : 'upload',
          };
        }
        if (cancelled) break;
        setGuideStats((st) => record(st, result));
        afterAttempt(result);
        await sleep(waitMs(guideMs.current, Date.now() - t0));
      }
    })();
    (async () => {
      while (!cancelled) {
        // Hands-free wake owns the mic on iOS (record-only). Only play spatial beeps while TTS
        // is active (`speechBusy`) or when wake listening is turned off (`!wakeEnabled`);
        // otherwise `playBeep` and `AudioStream` fight over AVAudioSession (OSStatus 561017449)
        // and kill the "Hey Jarvis" microphone stream.
        const { last, yawAtLast, at } = guideRef.current;
        if (hushBeeps.current || wakeEnabledRef.current || last?.distance === 'reach') { await sleep(100); continue; }
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
    return () => {
      cancelled = true;
      queuedCueText = '';
      guideRef.current = { last: null, yawAtLast: null, at: 0 };
      // Drop any cue hold so stopping guidance always re-arms Hey Jarvis.
      speechHoldRef.current = 0;
      setSpeechBusy(false);
    };
  }, [api, guiding, captureFrame, afterAttempt]);

  // Poll spoken output so wake can release the mic while replies/cues play (iOS record-only conflict).
  useEffect(() => {
    if (!connected) { speechHoldRef.current = 0; setSpeechBusy(false); return; }
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        const busy = speechHoldRef.current > 0 || (await Speech.isSpeakingAsync()) || isVoicePlaying();
        if (!cancelled) setSpeechBusy(busy);
        await sleep(200);
      }
    })();
    return () => { cancelled = true; };
  }, [connected]);

  /** Take ONE photo with default options and report exactly what happened (isolates the camera). */
  async function testPhoto() {
    const camera = cameraRef.current;
    if (!camera) { setTestResult('Test photo: camera ref is empty'); return; }
    pausedRef.current = true;
    setTesting(true);
    const t0 = Date.now();
    setTestResult('Test photo: taking...');
    try {
      const pic = await flight.run(() => camera.takePictureAsync(), CAPTURE_TIMEOUT_MS);
      setTestResult(`Test photo: OK ${pic.width}x${pic.height} in ${Date.now() - t0} ms`);
    } catch (e) {
      setTestResult(`Test photo: FAILED after ${Date.now() - t0} ms: ${errorText(e)}`);
    } finally {
      pausedRef.current = false;
      setTesting(false);
    }
  }

  const stopGuidance = useCallback(async (say?: unknown) => {
    const spoken = typeof say === 'string' && say ? say : 'Stopped.';
    voiceCaptureActiveRef.current = false;
    setGuiding(null);
    setCue('');
    setStatus('Guidance stopped.');
    speechHoldRef.current += 1;
    setSpeechBusy(true);
    try {
      await Promise.race([voice.reply(spoken), sleep(10000)]);
    } finally {
      await sleep(300);
      speechHoldRef.current = Math.max(0, speechHoldRef.current - 1);
      if (speechHoldRef.current === 0) setSpeechBusy(false);
    }
  }, []);

  const apply = useCallback(async (r: ChatResponse) => {
    history.current = [...history.current, { role: 'assistant' as const, content: r.say.slice(0, 500) }]
      .slice(-HISTORY_TURNS);
    if (r.intent === 'stop') return stopGuidance(r.say || 'Stopped.');
    if (r.intent === 'where') { setGuiding(null); setCue(''); }
    if (r.intent === 'set_room' && r.room) setRoom(r.room);
    setStatus(r.say);
    speechHoldRef.current += 1;
    setSpeechBusy(true);
    try {
      // Cap so a hung TTS path cannot leave Hey Jarvis muted forever.
      await Promise.race([voice.reply(r.say), sleep(15000)]);
    } finally {
      if (r.intent === 'find' && r.item) {
        setGuiding(r.item);
        pausedRef.current = false; // Start guidance loop cleanly right after the initial reply finishes speaking.
      }
      await sleep(300);           // let room echo decay before re-arming the wake mic
      speechHoldRef.current = Math.max(0, speechHoldRef.current - 1);
      if (speechHoldRef.current === 0) setSpeechBusy(false);
    }
  }, [stopGuidance]);

  const changeCalibration = useCallback(async (action: 'start' | 'finish', quiet = false): Promise<boolean> => {
    if (!connected || calibrationActionRef.current || guiding || testing) return false;
    calibrationActionRef.current = true;
    pausedRef.current = true;
    setCalibrationBusy(true);
    try {
      // Let the single frame already in flight settle before starting/finishing its session.
      await pendingFrame.current?.catch(() => undefined);
      const active = calibrationRef.current;
      if (action === 'finish' && (!active?.session_id || active.status !== 'mapping')) return true;
      const next = action === 'start' ? await api.startCalibration()
        : await api.finishCalibration(active!.session_id!);
      calibrationRef.current = next;
      calibrationSequence.current = next.last_sequence;
      setCalibration(next);
      setRoom('');
      if (!quiet) { setStatus(next.say); void voice.reply(next.say); }
      return true;
    } catch (e) {
      const message = `Apartment learning: ${errorText(e)}`;
      setStatus(message);
      void voice.reply(message);
      return false;
    } finally {
      pausedRef.current = commandInFlight.current;
      calibrationActionRef.current = false;
      setCalibrationBusy(false);
    }
  }, [api, connected, guiding, testing]);

  // Guidance owns the camera while active; restart learning with fresh continuity on stop.
  useEffect(() => {
    if (!resumeLearning.current || guiding || commandBusy || !connected) return;
    resumeLearning.current = false;
    void changeCalibration('start', true);
  }, [guiding, commandBusy, connected, changeCalibration]);

  const runCommand = useCallback(async (text: string) => {
    if (commandInFlight.current || calibrationActionRef.current) return;
    const command = calibrationCommand(text);
    if (command) return changeCalibration(command);
    commandInFlight.current = true;
    setCommandBusy(true);
    pausedRef.current = true;
    history.current = [...history.current, { role: 'user' as const, content: text.slice(0, 500) }]
      .slice(-HISTORY_TURNS);
    try {
      const reply = await prepareLearningReply(pendingFrame.current,
        () => api.chat(text, history.current.slice(0, -1), roomRef.current),
        async () => {
          if (calibrationRef.current?.status !== 'mapping') return true;
          const saved = await changeCalibration('finish', true);
          if (saved) resumeLearning.current = true;
          return saved;
        });
      await apply(reply);
    } catch {
      setStatus('Could not complete the request. Please try again.');
      void voice.reply('I could not complete that request. Please try again.');
    } finally {
      commandInFlight.current = false;
      pausedRef.current = false;
      setCommandBusy(false);
    }
  }, [api, apply, changeCalibration]);

  /** Wait until the spoken reply has finished, so the microphone stream does not cut it off. */
  const waitUntilQuiet = useCallback(async () => {
    await sleep(500);   // let the reply start
    let quietPolls = 0;
    for (let waited = 0; waited < 20000 && quietPolls < 2; waited += 300) {
      const speaking = (await Speech.isSpeakingAsync()) || isVoicePlaying();
      quietPolls = speaking ? 0 : quietPolls + 1;
      await sleep(300);
    }
    await sleep(500);
  }, []);

  const handleWakeRequest = useCallback(async (wav: Uint8Array, reason: string) => {
    setWakeBusy(true);   // turns the stream off and restores the speaker before anything is played
    try {
      if (reason === 'no-speech') {
        const say = "I didn't hear a request. Say Hey Jarvis again.";
        setStatus(say);
        void voice.reply(say);
        return;
      }
      setStatus('Thinking…');
      const file = new File(Paths.cache, 'sightline-request.wav');
      if (!file.exists) file.create();
      file.write(wav);
      const { text } = await api.transcribe({ uri: file.uri, name: 'request.wav', type: 'audio/wav' });
      if (!text) {
        const say = "I didn't catch that. Say Hey Jarvis again.";
        setStatus(say);
        void voice.reply(say);
        return;
      }
      await runCommand(text);
      await waitUntilQuiet();
    } catch {
      const say = 'Speech service unavailable. Say Hey Jarvis again in a moment.';
      setStatus(say);
      void voice.reply(say);
    } finally {
      setWakeBusy(false);
    }
  }, [api, runCommand, waitUntilQuiet]);

  const wake = useWake({
    enabled: wakeEnabled,
    server,
    onWake: () => {
      voiceCaptureActiveRef.current = true;
      Vibration.vibrate(150);
      setStatus('Listening…');
    },   // a buzz: nothing can play while listening
    onUtterance: (wav, reason) => { void handleWakeRequest(wav, reason); },
  });

  useEffect(() => {
    voiceCaptureActiveRef.current = wakeBusy || listening || commandBusy || wake.state === 'capturing';
    // Hush beeps while capturing a command, while TTS owns the speaker, or when the item is in
    // reach ("Stop. Reach straight in front of you now") so the mic stays open in record mode for
    // "Hey Jarvis, I got the wallet" without playBeep fighting AVAudioSession.
    const reached = guideRef.current.last?.distance === 'reach';
    hushBeeps.current = speechBusy || wakeBusy || listening || wake.state === 'capturing' || reached;
  }, [speechBusy, wakeBusy, listening, commandBusy, wake.state]);

  const wakeLine = ((): { text: string; bad: boolean } => {
    if (!wakeAvailable) return { text: 'not available on the server (WAKE_ENABLED=true and the wake packages/models)', bad: true };
    if (stt !== 'elevenlabs') return { text: 'needs STT_PROVIDER=elevenlabs for Hey Jarvis', bad: true };
    if (!wakeOn) return { text: 'off — tap Hey Jarvis: ON', bad: true };
    if (!connected) return { text: 'waiting for the backend', bad: false };
    if (speechBusy) return { text: 'paused while speaking (will resume)', bad: false };
    if (listening || wakeBusy) return { text: 'paused while handling a request', bad: false };
    if (wake.state === 'error') return { text: wake.detail || 'error', bad: true };
    if (wake.state === 'capturing') return { text: `heard you, capturing your request (${wake.level} dB)`, bad: false };
    if (wake.state === 'listening') {
      return { text: `listening for "Hey Jarvis" (mic ${wake.level} dB)`, bad: false };
    }
    return { text: 'connecting...', bad: false };
  })();

  // ---- hold to talk (server speech-to-text) ----
  async function beginTalk() {
    if (listening || stt !== 'elevenlabs') return;
    setListening(true);
    setStatus('Listening…');
    voice.stop();   // silence every voice: do not talk over the user
    const ready = (async () => {
      const perm = await requestRecordingPermissionsAsync();
      if (!perm.granted) {
        const say = 'Microphone permission is needed. Enable the microphone in Settings, then say Hey Jarvis.';
        setStatus(say);
        void voice.reply(say);
        return false;
      }
      await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      return true;
    })().catch(() => {
      const say = 'Microphone unavailable. Check phone settings, then say Hey Jarvis again.';
      setStatus(say);
      void voice.reply(say);
      return false;
    });
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
        const say = 'I need a longer request. Say Hey Jarvis, then speak your request.';
        setStatus(say);
        return void voice.reply(say);
      }
      setStatus('Thinking…');
      // m4a/AAC from expo-audio's preset. UNVERIFIED that ElevenLabs accepts it (docs say "all major formats").
      const { text } = await api.transcribe({ uri: recorder.uri, name: 'recording.m4a', type: 'audio/mp4' });
      if (!text) {
        const say = "I didn't hear anything. Say Hey Jarvis again.";
        setStatus(say);
        return void voice.reply(say);
      }
      await runCommand(text);
    } catch {
      setListening(false);
      const say = 'Speech service unavailable. Say Hey Jarvis again in a moment.';
      setStatus(say);
      void voice.reply(say);
    }
  }

  const submitTyped = () => {
    const text = typed.trim();
    if (!text) return;
    setTyped('');
    void runCommand(text);
  };

  // Why the memory loop is (not) taking photos, in one line.
  const loopLine = ((): { text: string; bad: boolean } => {
    if (guiding) return { text: 'off while guiding', bad: false };
    if (!connected) return { text: 'NOT STARTED: backend not connected', bad: true };
    if (testing) return { text: 'paused for the test photo', bad: false };
    if (calibration?.status === 'idle' && !calibration.places.length) return { text: 'waiting for calibration', bad: false };
    if (beat === 0) return { text: 'starting...', bad: false };
    const idle = Math.round((now - beat) / 1000);
    return idle > 3 ? { text: `STOPPED (last tick ${idle}s ago)`, bad: true } : { text: 'running', bad: false };
  })();

  return (
    <View style={styles.overlay} pointerEvents="box-none">
      <ScrollView style={styles.panelScroll} contentContainerStyle={styles.panel}>
        {mock && <Text style={styles.badge}>MOCK MODE: fake detections</Text>}
        <Text style={styles.status} accessibilityLiveRegion="polite">{status}</Text>
        <Text style={[styles.wakeHint, wakeLine.bad && styles.debugBad]} accessibilityLiveRegion="polite">
          {wakeEnabled && wake.state === 'listening'
            ? (guiding
              ? 'Hands-free: say Hey Jarvis, then stop — or another request.'
              : 'Hands-free: say Hey Jarvis, then your request.')
            : `Hey Jarvis: ${wakeLine.text}`}
        </Text>
        <View style={styles.calibration}>
          <Text style={styles.calibrationText}>
            {guiding ? 'Learning paused while guiding' : calibration?.status === 'mapping' ? 'Learning in the background' : 'Apartment memory'}
          </Text>
          <Text style={styles.chipText}>
            {calibration ? `${calibration.places.length} places · ${calibration.connections_total} connections` : 'Connect to begin.'}
          </Text>
          {calibration?.status === 'mapping' && <Text style={styles.chipText}>
            Objects in latest frame: {calibration.sightings.map((s) => s.item).join(', ') || 'none'}
          </Text>}
          {calibration?.places.length ? <Text style={styles.chipText}>
            {calibration.coverage_ready ? 'Return paths recorded between mapped places.' : 'More views or return paths needed.'}
          </Text> : null}
          {showDebug && <Pressable onPress={() => void changeCalibration(calibration?.status === 'mapping' ? 'finish' : 'start')}
            disabled={!connected || calibrationBusy || testing || !!guiding || !calibration?.available}
            accessibilityRole="button" accessibilityState={{ disabled: !connected || calibrationBusy || testing || !!guiding || !calibration?.available, busy: calibrationBusy }}
            accessibilityLabel={calibration?.status === 'mapping' ? 'Finish calibration' : calibration?.places.length ? 'Continue calibration' : 'Start calibration'}
            style={styles.small}>
            <Text style={styles.smallText}>{calibrationBusy ? 'Saving…' : calibration?.status === 'mapping'
              ? 'Finish calibration' : calibration?.places.length ? 'Continue calibration' : 'Start calibration'}</Text>
          </Pressable>}
          {calibration && !calibration.available && <Text style={styles.chipText}>
            Calibration is unavailable with the current server vision mode.
          </Text>}
        </View>
        {guiding && (
          <Pressable onPress={stopGuidance} accessibilityRole="button"
            accessibilityLabel={`Guiding to ${guiding}. ${cue || 'Searching'}. Tap to stop guidance.`}>
            <Text style={styles.cue}>{cue || `Searching for ${guiding}…`}</Text>
          </Pressable>
        )}
        {calibration?.status !== 'mapping' && <View style={styles.rooms} accessibilityRole="radiogroup">
          {['', ...rooms].map((r) => (
            <Pressable key={r} onPress={() => setRoom(r)} accessibilityRole="radio"
              accessibilityState={{ selected: r === room }} accessibilityLabel={r ? `Room ${r}` : 'Automatic room recognition'}
              style={[styles.chip, r === room && styles.chipOn]}>
              <Text style={[styles.chipText, r === room && styles.chipTextOn]}>{r || 'Automatic'}</Text>
            </Pressable>
          ))}
        </View>}
        {showDebug && (
          // Debug counters for sighted helpers; hidden from screen readers so they are not read aloud.
          <View style={styles.debug} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
            <Text style={styles.debugText}>
              Camera: {cameraReady ? 'ready' : 'starting...'}{restarts > 0 ? ` (restarted ${restarts}x)` : ''} | Backend: {connected ? 'connected' : 'NOT connected'}
            </Text>
            {!connected && (
              <Text style={[styles.debugText, styles.debugBad]}>
                Photos only start after the backend connects. Open settings, enter the server address and press Connect.
              </Text>
            )}
            <Text style={[styles.debugText, loopLine.bad && styles.debugBad]}>Memory loop: {loopLine.text}</Text>
            <Text style={[styles.debugText, wakeLine.bad && styles.debugBad]}>Wake word: {wakeLine.text}</Text>
            <Text style={styles.debugText}>
              Photo: {flight.inProgress ? `in progress for ${Math.round(flight.ageMs(now) / 1000)}s` : 'idle'}
            </Text>
            <Text style={styles.debugText}>{summarize('Memory', memStats)}</Text>
            <Text style={[styles.debugText, memStats.last?.kind === 'failed' && styles.debugBad]}>
              Last memory frame: {describeLast(memStats, now)}
            </Text>
            {memStats.firstFailure && memStats.firstFailure !== memStats.last && (
              <Text style={[styles.debugText, styles.debugBad]}>First failure: {describeFirstFailure(memStats, now)}</Text>
            )}
            {testResult !== '' && <Text style={styles.debugText}>{testResult}</Text>}
            {guiding && <Text style={styles.debugText}>{summarize('Guide', guideStats)}</Text>}
            {guiding && (
              <Text style={[styles.debugText, guideStats.last?.kind === 'failed' && styles.debugBad]}>
                Last guide frame: {describeLast(guideStats, now)}
              </Text>
            )}
          </View>
        )}
        {wakeAvailable && (
          <Pressable onPress={() => setWakeOn((w) => !w)} accessibilityRole="switch" accessibilityState={{ checked: wakeOn }}
            accessibilityLabel="Hey Jarvis wake word" style={styles.link}>
            <Text style={styles.linkText}>Hey Jarvis: {wakeOn ? 'ON (tap to turn off)' : 'OFF (tap to turn on)'}</Text>
          </Pressable>
        )}
        <Pressable onPress={testPhoto} disabled={calibrationBusy || testing} accessibilityRole="button" accessibilityLabel="Test one photo" style={styles.link}>
          <Text style={styles.linkText}>Test one photo (camera check)</Text>
        </Pressable>
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
            <TextInput value={server} onChangeText={changeServer} editable={!calibrationBusy} autoCapitalize="none" autoCorrect={false}
              placeholder="http://192.168.x.x:8000 or https://….ngrok-free.app" placeholderTextColor="#999"
              accessibilityLabel="Server address" style={styles.input} onSubmitEditing={connect} />
            <Pressable onPress={connect} disabled={calibrationBusy} accessibilityRole="button" accessibilityLabel="Connect" style={styles.small}>
              <Text style={styles.smallText}>Connect</Text>
            </Pressable>
          </View>
        )}
      </ScrollView>

      <View style={styles.bottom}>
        {/* Sighted-helper fallbacks only. Blind users: Hey Jarvis is the only supported path. */}
        {showDebug && stt !== 'elevenlabs' && (
          <View style={styles.settings} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
            <TextInput value={typed} onChangeText={setTyped} onSubmitEditing={submitTyped} returnKeyType="send"
              placeholder="where's my wallet" placeholderTextColor="#999"
              accessibilityLabel="Type a request (helper only)" style={styles.input} />
            <Pressable onPress={submitTyped} accessibilityRole="button" accessibilityLabel="Send" style={styles.small}>
              <Text style={styles.smallText}>Send</Text>
            </Pressable>
          </View>
        )}
        {showDebug && guiding && (
          <Pressable onPress={stopGuidance} accessibilityRole="button" accessibilityLabel="Stop guidance"
            accessibilityHint="Or say Hey Jarvis, then stop"
            style={styles.stop}>
            <Text style={styles.stopText}>Stop</Text>
          </Pressable>
        )}
        {showDebug && (
          <Pressable onPressIn={beginTalk} onPressOut={endTalk} disabled={stt !== 'elevenlabs'}
            accessibilityRole="button" accessibilityLabel="Hold to talk helper backup"
            accessibilityHint="Helper only. Blind users should say Hey Jarvis."
            style={[styles.talkBackup, listening && styles.talkOn, stt !== 'elevenlabs' && styles.talkOff]}>
            <Text style={styles.backupText}>{listening ? 'Listening…' : 'Hold to talk (helper)'}</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  overlay: { ...StyleSheet.absoluteFill, justifyContent: 'space-between', padding: 12 },
  panel: { backgroundColor: 'rgba(0,0,0,0.72)', borderRadius: 14, padding: 14, gap: 10, marginTop: 36 },
  panelScroll: { flexShrink: 1, marginBottom: 12 },
  calibration: { gap: 8, paddingVertical: 8 },
  calibrationText: { color: '#fff', fontSize: 22, fontWeight: '700' },
  badge: { alignSelf: 'flex-start', backgroundColor: '#ffe14d', color: '#000', fontWeight: '800', padding: 6, borderRadius: 6 },
  status: { color: '#fff', fontSize: 22, lineHeight: 30 },
  wakeHint: { color: '#9cf', fontSize: 20, lineHeight: 28, fontWeight: '600' },
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
  talkBackup: { minHeight: 64, borderRadius: 16, backgroundColor: '#333', alignItems: 'center', justifyContent: 'center' },
  talkOn: { backgroundColor: '#4dff88' },
  talkOff: { opacity: 0.45 },
  stop: { minHeight: 90, borderRadius: 24, backgroundColor: '#ff6b6b', alignItems: 'center', justifyContent: 'center' },
  stopText: { color: '#000', fontSize: 34, fontWeight: '800' },
  backupText: { color: '#ccc', fontSize: 18, fontWeight: '600' },
});
