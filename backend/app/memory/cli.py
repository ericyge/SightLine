"""Teach and inspect saved memory: python -m app.memory.cli --help. No API key needed."""
import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .. import db
from . import store as memory
from .models import MemoryMapUpdate, SightingPlaceUpdate


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    teach = commands.add_parser("teach", help="Merge places and directed connections from a JSON file")
    teach.add_argument("file", type=Path)
    commands.add_parser("map", help="Show the saved map")
    commands.add_parser("items", help="Show the latest sighting and destination of watched and seen items")
    where = commands.add_parser("where", help="Show one item's five latest sightings and destination")
    where.add_argument("item")
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
        else:
            name = db.resolve_item(args.item)
            watched = db.is_watched(args.item)
            history = db.recent_sightings(name, 5) if name else []
            result = {"item": name or args.item.strip().lower(), "watched": watched,
                      "found": bool(history), "history": history,
                      "destination": memory.destination_for(history[0] if history else None)}
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Sightline memory: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
