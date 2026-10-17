"""Local wake detection and microphone orchestration. Only activated clips upload."""
import math
import queue
import struct
import sys
import time
from contextlib import ExitStack
from dataclasses import replace

from .config import AudioError, Config, VoiceConfig
from .controls import Keys, playback_hook, playback_start
from .devices import sounddevice
from .stt import transcribe_wav
from .tts import speak_directions
from .vad import FRAME_BYTES, RATE, Utterance, WebRtcVad
from .wake import WakeDetector


class Microphone:
    """Bounded queue; fail on dropped input rather than joining discontinuous speech."""
    def __init__(self, sd, frame_length):
        self.sd, self.frame_length = sd, frame_length
        self.frames = queue.Queue(maxsize=100)
        self.failed = False

    def __enter__(self):
        def callback(data, frames, timing, status):
            if status:
                self.failed = True
            pcm = bytes(data)
            if sys.byteorder != "little":
                from array import array
                samples = array("h", pcm)
                samples.byteswap()
                pcm = samples.tobytes()
            try:
                self.frames.put_nowait(pcm)
            except queue.Full:
                self.failed = True
        self.stream = self.sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                                            blocksize=self.frame_length, callback=callback)
        self.stream.__enter__()
        return self

    def read(self, timeout=.05):
        if self.failed:
            raise AudioError("Microphone input overflow. Stop other audio tasks and retry.")
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self):
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break
        self.failed = False

    def __exit__(self, *args):
        return self.stream.__exit__(*args)


class Session:
    """Single-threaded orchestration, injectable detectors/devices for offline tests."""
    def __init__(self, config, provider_config, mic, wake, vad, keys, sd, debug=False):
        self.config, self.provider_config = config, provider_config
        self.mic, self.wake, self.vad, self.keys, self.sd = mic, wake, vad, keys, sd
        self.debug = debug
        self.pending = False
        self.last_debug = 0

    def log(self, text):
        if self.debug:
            print(f"[{time.monotonic():.3f}] {text}", file=sys.stderr)

    def detected(self, pcm):
        score = self.wake.process(pcm)
        now = time.monotonic()
        if score >= self.config.threshold or now - self.last_debug >= 1:
            self.log(f"wake_score={score:.4f} threshold={self.config.threshold}")
            self.last_debug = now
        return score >= self.config.threshold

    def interrupt(self):
        key = self.keys.poll()
        if key is not None:
            self.pending = key.lower() == "p"
            self.log("playback interrupted by key")
            return True
        if self.config.barge_in:
            pcm = self.mic.read(timeout=0)
            if pcm is not None and self.detected(pcm):
                self.pending = True
                self.log("barge-in wake detected")
                return True
        else:
            # Never call the detector on the app's own speech in half duplex.
            self.mic.flush()
        return False

    def speak(self, text):
        self.mic.flush()
        self.wake.reset()
        token = playback_hook.set(self.interrupt)
        start_token = playback_start.set(self.mic.flush)
        try:
            speak_directions({"steps": [{"text": text}]})
        except AudioError as exc:
            print(f"Spoken feedback unavailable: {exc}", file=sys.stderr)
            # Independent audible failure cue if the TTS provider itself is down.
            self.tone(frequency=330)
        finally:
            playback_hook.reset(token)
            playback_start.reset(start_token)
            self.mic.flush()

    def tone(self, frequency=880):
        # 100 ms soft sine, native-endian samples for PortAudio, no asset file.
        count = int(RATE * .1)
        pcm = b"".join(struct.pack("=h", int(5000 * math.sin(math.pi * i / count) *
                                              math.sin(2 * math.pi * frequency * i / RATE)))
                       for i in range(count))
        with self.sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16") as stream:
            stream.write(pcm)

    def wait_for_trigger(self):
        if self.pending:
            self.pending = False
            return
        self.mic.flush()
        self.wake.reset()
        self.log("waiting for wake word or P")
        last_frame = time.monotonic()
        while True:
            key = self.keys.poll()
            if key and key.lower() == "p":
                self.log("push-to-talk")
                return
            pcm = self.mic.read()
            if pcm is not None:
                last_frame = time.monotonic()
                if self.detected(pcm):
                    return
            elif time.monotonic() - last_frame > 3:
                raise AudioError("Microphone stopped delivering audio. Check device and permissions.")

    def record(self):
        self.tone()
        # Discard wake-word tail and confirmation tone; user speaks after the beep.
        self.mic.flush()
        clip = Utterance(self.config)
        buffer = b""
        previous = None
        last_frame = time.monotonic()
        while not clip.reason:
            self.keys.poll()  # Ctrl+C works on Windows too.
            pcm = self.mic.read()
            if pcm is None:
                if time.monotonic() - last_frame > 3:
                    raise AudioError("Microphone stopped during recording.")
                continue
            last_frame = time.monotonic()
            buffer += pcm
            while len(buffer) >= FRAME_BYTES and not clip.reason:
                frame, buffer = buffer[:FRAME_BYTES], buffer[FRAME_BYTES:]
                speech = self.vad.is_speech(frame)
                if speech != previous:
                    self.log(f"VAD speech={speech} elapsed={clip.elapsed:.2f}s")
                    previous = speech
                clip.feed(frame, speech)
        self.log(f"recording ended: {clip.reason}, {clip.elapsed:.2f}s")
        return clip

    def listen(self):
        while True:
            self.wait_for_trigger()
            clip = self.record()
            if clip.reason == "no_speech":
                self.speak("I didn't catch that. Please try again.")
                continue
            if clip.reason == "max_length":
                # Do not silently send a clipped command with a potentially lost suffix.
                self.speak("That was too long. Please try a shorter request.")
                continue
            started = time.monotonic()
            try:
                text = transcribe_wav(clip.wav(), self.provider_config)
            except AudioError as exc:
                print(f"Transcription failed: {exc}", file=sys.stderr)
                self.speak("I couldn't transcribe that. Please try again.")
                continue
            finally:
                self.mic.flush()
            self.log(f"transcription took {time.monotonic() - started:.2f}s")
            if text:
                return text
            self.speak("I didn't catch that. Please try again.")


def _run(continuous=False, debug=False, barge_in=False):
    provider_config = Config.load()
    if not provider_config.voice_id or provider_config.voice_id == "your_voice_id_here":
        raise AudioError("Set ELEVENLABS_VOICE_ID for spoken feedback before starting hands-free mode.")
    config = VoiceConfig.load()
    if barge_in:
        config = replace(config, barge_in=True)
    sd = sounddevice()
    with ExitStack() as stack:
        wake = WakeDetector(config)
        stack.callback(wake.close)
        vad = WebRtcVad(config.vad_mode)
        keys = stack.enter_context(Keys())
        try:
            mic = stack.enter_context(Microphone(sd, wake.frame_length))
            session = Session(config, provider_config, mic, wake, vad, keys, sd, debug)
            label = "Hey Sightline (custom model)" if config.model_path else config.keyword
            print(f"Wake word: {label}. P: push-to-talk; any key: stop playback; Ctrl+C: exit.", file=sys.stderr)
            if config.barge_in:
                print("Barge-in enabled: use headphones.", file=sys.stderr)
            while True:
                text = session.listen()
                if not continuous:
                    return text
                print(text, flush=True)
                session.speak(text)
        except sd.PortAudioError as exc:
            raise AudioError("Audio device failed. Check default mic/speaker and microphone permissions.") from exc


def listen_for_wake_word_and_transcribe() -> str:
    """Wait locally, beep, endpoint an utterance, upload that clip, return text.

    Failed/empty captures are announced and retried. Requires local wake models
    and TTS configuration. No pre-trigger audio is uploaded.
    """
    return _run()


def demo(*, debug=False, barge_in=False):
    _run(continuous=True, debug=debug, barge_in=barge_in)
