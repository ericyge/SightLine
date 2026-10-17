"""Run from the repository root: python -m audio_directions tts|stt."""
import argparse
import sys

from .config import AudioError, ROOT
from .stt import listen_and_transcribe
from .tts import speak_directions


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone ElevenLabs TTS / STT")
    commands = parser.add_subparsers(dest="command", required=True)
    tts = commands.add_parser("tts", help="Speak JSON direction steps in order")
    tts.add_argument("payload", nargs="?", default=ROOT / "dummy_directions.json")
    tts.add_argument("--save-dir", help="Save WAVs inside audio_directions (e.g. generated)")
    tts.add_argument("--no-play", action="store_true", help="Save only; requires --save-dir")
    commands.add_parser("stt", help="Record until Enter and print the transcript")
    args = parser.parse_args()
    try:
        if args.command == "tts":
            paths = speak_directions(args.payload, play=not args.no_play, save_dir=args.save_dir)
            for path in paths:
                print(path)
        else:
            print(listen_and_transcribe())
        return 0
    except (AudioError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
