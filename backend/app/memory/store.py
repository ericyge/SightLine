"""Persistent location memory for a taught or discovered environment."""
import json

from .. import db
from .models import MemoryMapUpdate


class MemoryNotFound(ValueError):
    """The referenced place or observation is absent."""


class MemoryConflict(ValueError):
    """A place assignment contradicts the recorded room or stable place identity."""


def _place(row) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "room": row["room"],
            "description": row["description"], "landmarks": json.loads(row["landmarks"])}


def get_place(place_id: str) -> dict:
    with db.connect() as c:
        place = _place(c.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone())
    if place is None:
        raise MemoryNotFound(f"Unknown place: {place_id}")
    return place


def validate_place(place_id: str | None, room: str | None) -> dict | None:
    if place_id is None:
        return None
    place = get_place(place_id)
    if room and place["room"].casefold() != room.strip().casefold():
        raise MemoryConflict("Place belongs to a different room than this observation.")
    return place


def get_map() -> dict:
    with db.connect() as c:
        places = [_place(r) for r in c.execute("SELECT * FROM places ORDER BY id")]
        connections = [dict(r) for r in c.execute(
            "SELECT * FROM place_connections ORDER BY from_place_id, to_place_id")]
    return {"places": places, "connections": connections}


def teach_map(update: MemoryMapUpdate) -> dict:
    """Atomically merge a taught map without erasing omitted places or sightings."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = {r["id"]: r["room"] for r in c.execute("SELECT id, room FROM places")}
        available = set(existing) | {p.id for p in update.places}
        for edge in update.connections:
            if edge.from_place_id not in available or edge.to_place_id not in available:
                raise MemoryNotFound("Both ends of every connection must be taught places.")
        for place in update.places:
            if place.id in existing and existing[place.id].casefold() != place.room.casefold():
                raise MemoryConflict("A place ID cannot move to another room; teach a new place ID.")
            c.execute("""INSERT INTO places (id,name,room,description,landmarks) VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
                landmarks=excluded.landmarks""",
                (place.id, place.name, place.room, place.description, json.dumps(place.landmarks)))
        for edge in update.connections:
            c.execute("""INSERT INTO place_connections
                (from_place_id,to_place_id,departure_view,instruction,arrival_view) VALUES (?,?,?,?,?)
                ON CONFLICT(from_place_id,to_place_id) DO UPDATE SET
                departure_view=excluded.departure_view, instruction=excluded.instruction,
                arrival_view=excluded.arrival_view""",
                (edge.from_place_id, edge.to_place_id, edge.departure_view, edge.instruction, edge.arrival_view))
    return get_map()


def assign_sighting(sighting_id: int, place_id: str | None) -> dict:
    """Confirm or clear a single observed item's destination without changing when it was seen."""
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM sightings WHERE id=?", (sighting_id,)).fetchone()
        if row is None:
            raise MemoryNotFound("Unknown sighting ID.")
        if place_id is not None:
            place = _place(c.execute("SELECT * FROM places WHERE id=?", (place_id,)).fetchone())
            if place is None:
                raise MemoryNotFound(f"Unknown place: {place_id}")
            if place["room"].casefold() != row["room"].strip().casefold():
                raise MemoryConflict("Place belongs to a different room than this observation.")
        c.execute("UPDATE sightings SET place_id=?,place_source=?,place_reason=? WHERE id=?",
                  (place_id, "manual" if place_id else "none",
                   "Manually assigned." if place_id else "Manually cleared.", sighting_id))
        result = c.execute("SELECT * FROM sightings WHERE id=?", (sighting_id,)).fetchone()
    return db.sighting_record(result)


def destination_for(sighting: dict | None) -> dict | None:
    """Only the newest observation's place is a destination; never revive an older link."""
    if sighting is None or sighting["place_id"] is None:
        return None
    return get_place(sighting["place_id"])


def recalled_items() -> list[dict]:
    """Return watched items plus any open-vocabulary items observed in sightings."""
    result = []
    seen_names = set()
    for item in db.list_items():
        name = item["name"]
        seen_names.add(db.canonicalize_item(name) or name)
        history = db.recent_sightings(name, 1)
        last = history[0] if history else None
        result.append({"item": name, "last": last, "destination": destination_for(last)})
    for name in db.seen_items():
        canon = db.canonicalize_item(name) or name
        if canon in seen_names:
            continue
        seen_names.add(canon)
        history = db.recent_sightings(canon, 1)
        last = history[0] if history else None
        result.append({"item": canon, "last": last, "destination": destination_for(last)})
    return result
