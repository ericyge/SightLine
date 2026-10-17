"""Check a Sightline profile produced by the Purple agent, and optionally send it to the backend.

Usage (from the repo root, using the backend venv so pydantic is available):
    .\\backend\\.venv\\Scripts\\python purple\\validate_profile.py profile.json
    Get-Clipboard | .\\backend\\.venv\\Scripts\\python purple\\validate_profile.py -
    .\\backend\\.venv\\Scripts\\python purple\\validate_profile.py profile.json --post

The input can be raw JSON or the agent's whole reply: the JSON is pulled out of a ```json block.
Exit code 0 = valid (warnings are fine), 1 = errors, 2 = could not read or find JSON.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from pydantic import ValidationError  # noqa: E402

from app.models import Profile  # noqa: E402

MAX_ITEMS = 8
MAX_ROOMS = 6


def extract_json(text: str) -> dict:
    """Return the profile dict from raw JSON or from an agent reply containing a code block."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    if not candidate:
        raise ValueError("no JSON object found in the input")
    return json.loads(candidate)


def check(data: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block use; warnings are quality hints."""
    try:
        profile = Profile.model_validate(data)
    except ValidationError as e:
        return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()], []
    errors, warnings = [], []
    extra = set(data) - {"watchlist", "rooms", "guidance_style"}
    if extra:
        warnings.append(f"unexpected keys will be ignored: {sorted(extra)}")
    if not profile.watchlist:
        errors.append("watchlist is empty")
    if not profile.rooms:
        errors.append("rooms is empty")
    if len(profile.watchlist) > MAX_ITEMS:
        warnings.append(f"more than {MAX_ITEMS} items; detection gets slower and less accurate")
    if len(profile.rooms) > MAX_ROOMS:
        warnings.append(f"more than {MAX_ROOMS} rooms")
    names = [w.name.strip().lower() for w in profile.watchlist]
    for dup in {n for n in names if names.count(n) > 1}:
        errors.append(f"duplicate item name: {dup}")
    rooms = [r.strip().lower() for r in profile.rooms]
    for w in profile.watchlist:
        if not w.name.strip():
            errors.append("an item has an empty name")
        if w.name != w.name.lower():
            warnings.append(f"{w.name}: name should be lowercase (the backend lowercases it)")
        if len(w.description.split()) < 2:
            warnings.append(f"{w.name}: description is thin; add color/material for the vision model")
        if not w.home_spot:
            warnings.append(f"{w.name}: no home_spot")
        elif not any(r in w.home_spot.lower() for r in rooms):
            warnings.append(f"{w.name}: home_spot '{w.home_spot}' does not mention any listed room")
    return errors, warnings


def post_profile(data: dict, base_url: str) -> str:
    """POST the profile to the backend and return a one-line result."""
    req = urllib.request.Request(f"{base_url.rstrip('/')}/api/profile", method="POST",
                                 data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return f"POST /api/profile -> {r.status} {r.read().decode()}"
    except urllib.error.HTTPError as e:
        return f"POST /api/profile -> HTTP {e.code}: {e.read().decode()[:300]}"
    except urllib.error.URLError as e:
        return f"POST failed: {e.reason} (is the backend running?)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default="-", help="profile file, or - for stdin")
    ap.add_argument("--post", action="store_true", help="POST to the backend if valid")
    ap.add_argument("--url", default="http://localhost:8000", help="backend base URL")
    args = ap.parse_args()
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8-sig")
    try:
        data = extract_json(text)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: could not read JSON: {e}")
        return 2
    errors, warnings = check(data)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        return 1
    print(f"OK: {len(data['watchlist'])} item(s), {len(data['rooms'])} room(s), "
          f"style {data['guidance_style']}")
    if args.post:
        print(post_profile(data, args.url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
