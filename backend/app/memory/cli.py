"""Teach and inspect saved memory: python -m app.memory.cli --help. No API key needed."""
import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .. import db
from . import store as memory
from .models import MemoryMapUpdate, SightingPlaceUpdate
from .route import directions_to_item, infer_place_for_sighting


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    teach = commands.add_parser("teach", help="Merge places and directed connections from a JSON file")
    teach.add_argument("file", type=Path)
    commands.add_parser("map", help="Show the saved map")
    commands.add_parser("items", help="Show the latest sighting and destination of watched and seen items")
    where = commands.add_parser("where", help="Show one item's five latest sightings, destination, and directions")
    where.add_argument("item")
    where.add_argument("--from-place", help="Current place ID to route from (defaults to latest place)")
    dirs = commands.add_parser("directions", help="Print step-by-step directions to reach an item")
    dirs.add_argument("item")
    dirs.add_argument("--from-place", help="Current place ID to route from (defaults to latest place)")
    assign = commands.add_parser("assign", help="Link one saved sighting to a taught place")
    assign.add_argument("sighting_id", type=int)
    target = assign.add_mutually_exclusive_group(required=True)
    target.add_argument("--place-id")
    target.add_argument("--clear", action="store_true", help="Remove the place link")
    return p


def main(argv=None) -> int:
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    args = parser().parse_args(argv)
    try:
        if args.command == "teach":
            result = memory.teach_map(MemoryMapUpdate.model_validate_json(args.file.read_text()))
        elif args.command == "map":
            result = memory.get_map()
        elif args.command == "items":
            result = memory.recalled_items()
        elif args.command == "assign":
            update = SightingPlaceUpdate(place_id=None if args.clear else args.place_id)
            result = memory.assign_sighting(args.sighting_id, update.place_id)
        elif args.command == "directions":
            result = directions_to_item(args.item, from_place_id=args.from_place)
        else:
            name = db.resolve_item(args.item)
            watched = db.is_watched(args.item)
            history = db.recent_sightings(name, 5) if name else []
            route_info = directions_to_item(args.item, from_place_id=args.from_place)
            dest = memory.destination_for(history[0] if history else None)
            result = {"item": name or args.item.strip().lower(), "watched": watched,
                      "found": bool(history), "history": history,
                      "destination": dest, "directions": route_info.get("steps", [])}
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Sightline memory: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
