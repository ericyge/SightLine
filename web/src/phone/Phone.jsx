import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api.js";
import { createBeeper, panFromAngle, pitchBoost, unlockAudio } from "./audio.js";
import { listen, parseCommand, recognitionSupported, speak, unlockSpeech } from "./speech.js";
import {
  compensate, getYaw, requestOrientationPermission, startOrientation, stopOrientation,
} from "./orientation.js";

const FRAME_WIDTH = 640;
const LOOP_MS = 1000;
const STALE_MS = 3000; // silence beeps if the last guide response is older than this
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Grab a 640px-wide JPEG from the video element. */
function captureFrame(video) {
  if (!video || !video.videoWidth) return Promise.reject(new Error("camera not ready"));
  const canvas = document.createElement("canvas");
  canvas.width = FRAME_WIDTH;
  canvas.height = Math.round((FRAME_WIDTH * video.videoHeight) / video.videoWidth);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode failed"))), "image/jpeg", 0.7));
}

/** Start the rear camera; returns {ready, error}. */
function useCamera(videoRef) {
  const [state, setState] = useState({ ready: false, error: "" });
  useEffect(() => {
    let stream;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: FRAME_WIDTH } },
          audio: false,
        });
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        setState({ ready: true, error: "" });
      } catch (e) {
        // TODO(C): friendlier camera-denied / insecure-context (needs HTTPS) guidance.
        setState({ ready: false, error: `Camera unavailable: ${e.message}` });
      }
    })();
    return () => stream?.getTracks().forEach((t) => t.stop());
  }, [videoRef]);
  return state;
}

export default function Phone() {
  const videoRef = useRef(null);
  const { ready, error } = useCamera(videoRef);
  const [rooms, setRooms] = useState(["bedroom", "kitchen", "living room"]);
  const [room, setRoom] = useState("bedroom");
  const [status, setStatus] = useState("Ready. Hold the button and ask where something is.");
  const [cue, setCue] = useState("");
  const [guiding, setGuiding] = useState(null); // item name while in guidance mode
  const [listening, setListening] = useState(false);
  const [mock, setMock] = useState(false);
  const roomRef = useRef(room);
  const guideRef = useRef({ last: null, yawAtLast: null, at: 0 });
  const stopListenRef = useRef(null);
  roomRef.current = room;

  useEffect(() => {
    api.getProfile().then((p) => { setRooms(p.rooms); setRoom(p.rooms[0] ?? "room"); }).catch(() => {});
    api.getHealth().then((h) => setMock(h.mock)).catch(() => {});
  }, []);

  // Memory loop: ~1 frame/second to /api/observe while not guiding.
  useEffect(() => {
    if (!ready || guiding) return;
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        const t0 = performance.now();
        try {
          await api.observe(await captureFrame(videoRef.current), roomRef.current);
        } catch {
          // TODO(C): offline fallback / error state (queue sightings, show "offline").
        }
        await sleep(Math.max(0, LOOP_MS - (performance.now() - t0)));
      }
    })();
    return () => { cancelled = true; };
  }, [ready, guiding]);

  // Guidance loop: ~1 frame/second to /api/guide, plus a fast local loop updating the beeps.
  useEffect(() => {
    if (!guiding) return;
    let cancelled = false;
    const beeper = createBeeper();
    beeper.start();
    const refresh = setInterval(() => {
      const { last, yawAtLast, at } = guideRef.current;
      if (!last || !last.found || performance.now() - at > STALE_MS) {
        beeper.update({ intervalMs: 0 });
        return;
      }
      // Between vision updates, keep the pan correct as the phone turns.
      const angle = compensate(last.angle, getYaw(), yawAtLast);
      beeper.update({ pan: panFromAngle(angle), intervalMs: last.beep_ms, boost: pitchBoost(angle) });
    }, 100);
    (async () => {
      while (!cancelled) {
        const t0 = performance.now();
        try {
          const yaw = getYaw();
          const r = await api.guide(await captureFrame(videoRef.current), guiding, yaw ?? undefined);
          if (cancelled) break;
          guideRef.current = { last: r, yawAtLast: yaw, at: performance.now() };
          if (r.say) { speak(r.say, { interrupt: true }); setCue(r.say); }
        } catch {
          setStatus("Connection problem. Still trying.");
        }
        await sleep(Math.max(0, LOOP_MS - (performance.now() - t0)));
      }
    })();
    return () => {
      cancelled = true;
      clearInterval(refresh);
      beeper.stop();
      guideRef.current = { last: null, yawAtLast: null, at: 0 };
    };
  }, [guiding]);

  const stopGuidance = useCallback(() => {
    setGuiding(null);
    setCue("");
    setStatus("Guidance stopped.");
    speak("Stopped", { interrupt: true, force: true });
  }, []);

  const runCommand = useCallback(async (text) => {
    const cmd = parseCommand(text);
    if (!cmd) {
      setStatus(`Heard "${text}". Try "where's my wallet" or "find my keys".`);
      speak("Sorry, try: where's my wallet, or find my keys.", { force: true });
    } else if (cmd.type === "stop") {
      stopGuidance();
    } else if (cmd.type === "where") {
      try {
        const r = await api.where(cmd.item);
        setStatus(r.say);
        speak(r.say, { force: true });
      } catch {
        setStatus("Could not reach the server.");
        speak("I could not reach the server.", { force: true });
      }
    } else {
      setGuiding(cmd.item);
      setStatus(`Guiding you to your ${cmd.item}.`);
      speak(`Looking for your ${cmd.item}`, { force: true });
    }
  }, [stopGuidance]);

  async function unlock() {
    unlockAudio();
    unlockSpeech();
    if (await requestOrientationPermission()) startOrientation();
  }
  useEffect(() => () => stopOrientation(), []);

  function startTalk() {
    if (listening) return;
    unlock();
    setListening(true);
    setStatus("Listening…");
    stopListenRef.current = listen({
      onResult: runCommand,
      onEnd: () => setListening(false),
      onError: (e) => { setListening(false); setStatus(`Could not hear you: ${e.error ?? e.message}`); },
    });
    if (!stopListenRef.current) setListening(false);
  }
  function endTalk() {
    stopListenRef.current?.();
    stopListenRef.current = null;
  }

  function onKey(e, down) {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) {
      e.preventDefault();
      down ? startTalk() : endTalk();
    }
  }

  const [typed, setTyped] = useState("");
  const canListen = recognitionSupported();

  return (
    <main className="phone">
      {mock && <div className="mock-badge" role="status">MOCK MODE: fake detections</div>}
      <video ref={videoRef} className="preview" playsInline muted aria-label="Camera preview" />
      <label className="room">
        Room
        <select value={room} onChange={(e) => setRoom(e.target.value)} aria-label="Current room">
          {rooms.map((r) => <option key={r}>{r}</option>)}
        </select>
      </label>

      <div className="status" role="status" aria-live="polite">{error || status}</div>
      {/* The cue is spoken by SpeechSynthesis already, so no aria-live here (avoids double audio). */}
      {guiding && <div className="cue" aria-live="off">{cue || `Searching for ${guiding}…`}</div>}

      {guiding && (
        <button className="stop" onClick={stopGuidance} aria-label="Stop guidance">Stop</button>
      )}
      <button
        className={`talk ${listening ? "active" : ""}`}
        aria-label="Hold to talk"
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); startTalk(); }}
        onPointerUp={endTalk}
        onPointerCancel={endTalk}
        onContextMenu={(e) => e.preventDefault()}
        onKeyDown={(e) => onKey(e, true)}
        onKeyUp={(e) => onKey(e, false)}
      >
        {listening ? "Listening…" : "Hold to talk"}
      </button>

      {!canListen && (
        // Fallback for browsers without SpeechRecognition (e.g. desktop Firefox).
        <form className="typed" onSubmit={(e) => { e.preventDefault(); unlock(); runCommand(typed); setTyped(""); }}>
          <label>Type a command
            <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="where's my wallet" />
          </label>
          <button type="submit">Go</button>
        </form>
      )}
      {/* TODO(C): error states (no network, server down), offline fallback. */}
    </main>
  );
}
