"""Explicit model download / synthetic smoke check. Does not read .env or a mic."""
import argparse
import math
import struct
import sys
from urllib.request import urlopen

from .config import AudioError, ROOT, VoiceConfig
from .wake import MODEL_DIR, STAND_INS, WakeDetector

RELEASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
FILES = ("melspectrogram.onnx", "embedding_model.onnx", STAND_INS["hey_jarvis"])


def download():
    if not MODEL_DIR.resolve().is_relative_to(ROOT):
        raise AudioError("Model download directory must remain inside audio_directions/.")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = MODEL_DIR / name
        partial = target.with_suffix(".onnx.part")
        if any(not p.resolve().is_relative_to(ROOT) for p in (target, partial)):
            raise AudioError("Model download path escapes audio_directions/.")
        if target.is_file() and target.stat().st_size:
            print(f"Already present: {name}")
            continue
        print(f"Downloading {name}...", flush=True)
        try:
            with urlopen(RELEASE + name, timeout=60) as response, partial.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if not partial.stat().st_size:
                raise AudioError(f"Empty model download: {name}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)


def smoke():
    detector = WakeDetector(VoiceConfig())  # Deliberately no .load(): no .env/key access.
    try:
        scores = []
        for n in range(40):  # Pass warmup; silence followed by a synthetic tone.
            samples = [0 if n < 20 else int(1000 * math.sin(2 * math.pi * 220 * i / 16000))
                       for i in range(detector.frame_length)]
            scores.append(detector.process(struct.pack("<1280h", *samples)))
        print(f"Loaded Hey Jarvis; scored 40 synthetic frames: min={min(scores):.6f}, max={max(scores):.6f}")
        print("Execution check only: this does not verify spoken-phrase recognition.")
    finally:
        detector.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="No downloads; load and score installed models")
    args = parser.parse_args()
    try:
        if not args.verify_only:
            download()
        smoke()
        return 0
    except (AudioError, OSError) as exc:
        print(f"Wake setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
