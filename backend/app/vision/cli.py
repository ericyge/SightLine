"""Recorded-video ingestion and latency measurement: python -m app.vision.cli --help."""
import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import av
from dotenv import load_dotenv

from .. import db
from . import MockVision, PerceptronVision, VisionUnavailable
from .video import sample_video


def started_at(value: str) -> datetime:
    """Require an explicit time zone so imported sightings match the recording."""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("Use an ISO timestamp with a time zone.") from None
    if result.tzinfo is None:
        raise argparse.ArgumentTypeError("Recording start time must include a time zone.")
    return result.astimezone(timezone.utc)


def _vision(args, model=None):
    mode = "mock" if args.mock else os.getenv("PERCEPTRON_MODE", "mock").lower()
    if mode not in {"mock", "perceptron"}:
        raise ValueError("PERCEPTRON_MODE must be mock or perceptron.")
    return MockVision() if mode == "mock" else PerceptronVision(model=model)


def ingest(args) -> int:
    """Process sequentially with bounded memory; emit one JSON record per frame."""
    vision = _vision(args)
    items = db.list_items()
    try:
        for offset, jpeg in sample_video(args.video, fps=args.fps, max_px=args.size, limit=args.limit):
            ts = (args.started_at + timedelta(seconds=offset)).isoformat()
            begin = time.perf_counter()
            labels = vision.detect(jpeg, items, args.room)
            elapsed = time.perf_counter() - begin
            sightings = [db.add_sighting(d.item, args.room, d.spot, d.box, d.confidence,
                                        ts, db.save_thumb(jpeg, d.box)) for d in labels]
            print(json.dumps({"mock": vision.mock, "offset_seconds": round(offset, 6), "ts": ts,
                              "inference_ms": round(elapsed * 1000, 1), "sightings": sightings}),
                  flush=True)
    finally:
        vision.close()
    return 0


def benchmark(args) -> int:
    """Time the same sampled frames for every model; never save sightings or images."""
    frames = list(sample_video(args.video, fps=args.fps, max_px=args.size, limit=args.count))
    if len(frames) != args.count:
        raise ValueError(f"Need {args.count} sampled frames; this video provided {len(frames)}.")
    items = db.list_items()
    if args.task == "guide":
        name = db.resolve_item(args.item)
        if name is None:
            raise ValueError("Guidance benchmark item must be on the watchlist.")
        items = [db.get_item(name)]
    models = args.models or [os.getenv("PERCEPTRON_MODEL", "perceptron-mk1.5")]
    failed = False
    for model in models:
        vision = _vision(args, model)
        durations = []
        errors = 0
        try:
            for _, jpeg in frames:
                begin = time.perf_counter()
                try:
                    vision.detect(jpeg, items, args.room if args.task == "observe" else None)
                except VisionUnavailable:
                    errors += 1
                else:
                    durations.append((time.perf_counter() - begin) * 1000)
        finally:
            vision.close()
        ordered = sorted(durations)
        report = {"mock": vision.mock, "model": "mock" if vision.mock else model,
                  "task": args.task, "frame_max_px": args.size,
                  "attempts": len(frames), "successes": len(durations), "errors": errors,
                  "median_ms": round(statistics.median(ordered), 1) if ordered else None,
                  "p95_ms": round(ordered[math.ceil(len(ordered) * .95) - 1], 1) if ordered else None}
        print(json.dumps(report), flush=True)
        failed = failed or bool(errors)
    return 1 if failed else 0


def list_models(args) -> int:
    vision = PerceptronVision()
    try:
        print(json.dumps({"models": vision.available_models()}))
    finally:
        vision.close()
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    for name, action in (("video", ingest), ("benchmark", benchmark)):
        command = commands.add_parser(name)
        command.set_defaults(action=action)
        command.add_argument("video", type=Path)
        command.add_argument("--fps", type=float, default=1)
        command.add_argument("--size", type=int, default=int(os.getenv("VISION_FRAME_MAX_PX", "640")))
        command.add_argument("--mock", action="store_true", help="Use fake detections; make no API calls")
        if name == "video":
            command.add_argument("--room", required=True, help="Room label for this single-room clip")
            command.add_argument("--started-at", required=True, type=started_at)
            command.add_argument("--limit", type=int)
        else:
            command.add_argument("--count", type=int, default=20)
            command.add_argument("--models", nargs="+")
            command.add_argument("--task", choices=["observe", "guide"], default="observe")
            command.add_argument("--room", default="bedroom")
            command.add_argument("--item", default="wallet")
    commands.add_parser("models").set_defaults(action=list_models)
    return p


def main(argv=None) -> int:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    args = parser().parse_args(argv)
    try:
        return args.action(args)
    except (ValueError, RuntimeError, OSError, av.error.FFmpegError) as exc:
        print(f"Sightline: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
