r"""Call the live Perceptron client on one image and print what comes back.

Use it to check, with your real key, the coordinate system and the latency:

    cd backend
    .\.venv\Scripts\python scripts\perceptron_probe.py C:\path\frame.jpg "red leather wallet"
    .\.venv\Scripts\python scripts\perceptron_probe.py frame.jpg "red leather wallet" --repeat 20

Reads PERCEPTRON_API_KEY / PERCEPTRON_MODEL / PERCEPTRON_TIMEOUT_S from the repo's .env. Prints the
raw SDK boxes (0-1000 grid), the converted 0..1 box, the center x, the box height and the elapsed
time. It never prints the key, and the image is only sent to Perceptron (nothing is stored).
Exit code 0 = the call worked (even with no box), 1 = the call failed, 2 = bad usage.
"""
import argparse
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.guidance import angle_deg, clock_hour, distance_bucket  # noqa: E402
from app.vision import PerceptronVision, box_from_sdk  # noqa: E402


def describe(box, fov_h: float) -> None:
    """Print one raw SDK box and its conversion into what the backend uses."""
    raw = (box.top_left.x, box.top_left.y, box.bottom_right.x, box.bottom_right.y)
    print(f"  raw (0-1000 grid): x1={raw[0]} y1={raw[1]} x2={raw[2]} y2={raw[3]}  mention={box.mention!r}")
    norm = box_from_sdk(box)
    if norm is None:
        print("  converted: degenerate box, would be ignored")
        return
    cx, cy = (norm[0] + norm[2]) / 2, (norm[1] + norm[3]) / 2
    angle = angle_deg(cx, fov_h)
    print(f"  converted (0..1): {norm}")
    print(f"  center x={cx:.3f}  center y={cy:.3f}  height={norm[3] - norm[1]:.3f}")
    print(f"  guidance: angle={angle:.1f} deg  clock={clock_hour(angle)}  distance={distance_bucket(norm, cy)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="JPEG/PNG/WebP file")
    ap.add_argument("description", help='what to look for, e.g. "red leather wallet"')
    ap.add_argument("--repeat", type=int, default=1, help="repeat the call N times and print p50/p95")
    ap.add_argument("--fov", type=float, default=65.0, help="camera horizontal field of view in degrees")
    args = ap.parse_args()
    path = Path(args.image)
    if not path.is_file() or args.repeat < 1:
        print("usage error: image file not found, or --repeat < 1")
        return 2
    try:
        client = PerceptronVision()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 1
    frame = path.read_bytes()
    print(f"model={client.model}  timeout={client.timeout_s}s  image={path.name} ({len(frame) // 1024} KB)")
    times, failures = [], 0
    for i in range(args.repeat):
        try:
            boxes, elapsed = client.probe(frame, [args.description])
        except Exception as e:  # noqa: BLE001 - show the class, never a body or key
            failures += 1
            print(f"call {i + 1}: FAILED ({type(e).__name__})")
            continue
        times.append(elapsed)
        if args.repeat == 1 or i == 0:
            print(f"call {i + 1}: {len(boxes)} box(es) in {elapsed * 1000:.0f} ms")
            for b in boxes:
                describe(b, args.fov)
        else:
            print(f"call {i + 1}: {len(boxes)} box(es) in {elapsed * 1000:.0f} ms")
    if len(times) > 1:
        ordered = sorted(times)
        p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
        print(f"latency over {len(times)} ok calls: p50={statistics.median(times) * 1000:.0f} ms  "
              f"p95={p95 * 1000:.0f} ms  min={ordered[0] * 1000:.0f} ms  max={ordered[-1] * 1000:.0f} ms")
    if failures:
        print(f"{failures} of {args.repeat} calls failed")
    return 1 if failures == args.repeat else 0


if __name__ == "__main__":
    sys.exit(main())
