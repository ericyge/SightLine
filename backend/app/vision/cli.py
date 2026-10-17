"""Recorded-video ingestion and latency measurement: python -m app.vision.cli --help."""
import argparse
import json
import math
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import av
from dotenv import load_dotenv

from .. import db, memory
from ..memory.observation import observe_and_save
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
    memory.validate_place(args.place_id, args.room)
    vision = _vision(args)
    session_id = uuid.uuid4().hex
    try:
        for offset, jpeg in sample_video(args.video, fps=args.fps, max_px=args.size, limit=args.limit):
            ts = (args.started_at + timedelta(seconds=offset)).isoformat()
            sightings, inference_ms = observe_and_save(vision, jpeg, args.room, ts,
                place_id=args.place_id, auto_place=not args.no_auto_places, session_id=session_id)
            print(json.dumps({"mock": vision.mock, "offset_seconds": round(offset, 6), "ts": ts,
                              "inference_ms": inference_ms, "sightings": sightings}),
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
        target = db.target_item(args.item)
        if target is None:
            raise ValueError("Guidance benchmark item must be a valid object name.")
        items = [target]
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
            command.add_argument("--room", help="Optional room label for a single-room clip; omit for automatic inference")
            command.add_argument("--place-id", help="Taught destination of ALL detected items in this clip; omit for a room scan")
            command.add_argument("--no-auto-places", action="store_true", help="Save detections without inferring places")
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
