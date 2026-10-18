"""Shared API/video observation pipeline with conservative, per-object place assignment."""
import json
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db
from . import store as memory

MAX_CANDIDATES = 40
UNKNOWN_ROOM = "unknown room"


def _normalized(value):
    return " ".join(value.casefold().split())


def _landmarks(place):
    return {_normalized(value) for value in place["landmarks"]}


def place_context(room, session_id, ts):
    """Bound model context; only recent observations in this capture session are hints."""
    places = memory.get_map()["places"]
    if room:
        places = [p for p in places if _normalized(p["room"]) == _normalized(room)]
    # Keep the exact evidence supplied to the model for later validation, including indices.
    candidates = [dict(p, description=p["description"][:500], landmarks=p["landmarks"][:6])
                  for p in places[:MAX_CANDIDATES]]
    recent = []
    if session_id:
        now = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        cutoff = (now - timedelta(seconds=20)).isoformat()
        with db.connect() as c:
            recent = [dict(r) for r in c.execute("""SELECT item,spot,place_id,ts FROM sightings
                WHERE session_id=? AND julianday(ts)>=julianday(?) AND julianday(ts)<julianday(?)
                ORDER BY julianday(ts) DESC,id DESC LIMIT 8""", (session_id, cutoff, ts))]
        allowed = {p["id"] for p in candidates}
        recent = [r for r in recent if r["place_id"] in allowed]
    return {"places": candidates, "catalog_complete": len(places) <= MAX_CANDIDATES,
            "recent_observations": recent}


def _unknown(room, reason):
    return {"place_id": None, "room": room or UNKNOWN_ROOM, "place_source": "none",
            "place_reason": reason}


def _resolve(c, evidence, room, context, current):
    observed_room = room or (evidence.room if evidence else None) or UNKNOWN_ROOM
    if evidence is None or evidence.decision == "unknown":
        return _unknown(observed_room, evidence.reason if evidence else "Resting place could not be identified.")
    landmarks = _landmarks(evidence.model_dump())
    if not evidence.local_key or len(landmarks) < 2:
        return _unknown(observed_room, "Need at least two distinct fixed landmark relationships.")
    candidates = {p["id"]: p for p in context["places"]}
    if evidence.decision == "match":
        candidate = candidates.get(evidence.candidate_id)
        indices = set(evidence.matched_landmarks)
        if candidate is None or len(indices) < 2 or any(i >= len(candidate["landmarks"]) for i in indices):
            return _unknown(observed_room, "Match did not reference two supplied landmarks of a known place.")
        stored = current.get(candidate["id"])
        if stored is None or any(stored[key] != candidate[key] for key in ("name", "room")) or \
                stored["landmarks"][:6] != candidate["landmarks"] or stored["description"][:500] != candidate["description"]:
            return _unknown(observed_room, "Place changed while this frame was being analyzed; waiting for another view.")
        matched = {_normalized(candidate["landmarks"][i]) for i in indices}
        if len(matched) < 2 or any(matched <= _landmarks(p) for p in current.values() if p["id"] != candidate["id"]):
            return _unknown(observed_room, "Visible landmarks do not distinguish this place from another candidate.")
        return {"place_id": candidate["id"], "room": candidate["room"], "place_source": "automatic",
                "place_reason": f"Inferred match using {len(matched)} stored landmarks. " + evidence.reason}
    if evidence.candidate_id or evidence.matched_landmarks or not evidence.name or not evidence.description:
        return _unknown(observed_room, "New place evidence is incomplete or contradictory.")
    if not context["catalog_complete"] or set(current) != set(candidates):
        return _unknown(observed_room, "Place catalog was incomplete or changed; no new place was created.")
    if any(len(landmarks & _landmarks(p)) >= 2 for p in current.values()):
        return _unknown(observed_room, "These landmarks may describe an existing place; waiting for a clear match.")
    place_id = "auto_" + uuid.uuid4().hex
    c.execute("INSERT INTO places(id,name,room,description,landmarks) VALUES (?,?,?,?,?)",
              (place_id, evidence.name, observed_room, evidence.description, json.dumps(evidence.landmarks)))
    return {"place_id": place_id, "room": observed_room, "place_source": "automatic",
            "place_reason": "New inferred place from visible landmarks. " + evidence.reason}


def resolve_frame(c, detections, room, context):
    """Reject conflicting claims within a frame instead of silently merging surfaces."""
    current = {r["id"]: memory._place(r) for r in c.execute("SELECT * FROM places")}
    if room:
        current = {k: p for k, p in current.items() if _normalized(p["room"]) == _normalized(room)}
    groups = defaultdict(list)
    for index, d in enumerate(detections):
        key = d.location.local_key if d.location and d.location.local_key else f"unresolved_{index}"
        groups[key].append(d)
    results = {}
    for key, group in groups.items():
        evidence = group[0].location
        others = [d.location for other, values in groups.items() if other != key for d in values if d.location]
        conflict = any(d.location != evidence for d in group)
        if evidence:
            # Distinct frame surfaces cannot share a persistent match or indistinguishable new evidence.
            conflict |= any((evidence.candidate_id is not None and e.candidate_id == evidence.candidate_id) or
                            (e.decision == evidence.decision == "new" and
                             _landmarks(e.model_dump()) == _landmarks(evidence.model_dump())) for e in others)
        result = (_unknown(room, "Conflicting place descriptions within this frame.") if conflict
                  else _resolve(c, evidence, room, context, current))
        for d in group:
            results[d.item] = result
    return results


REPEAT_WINDOW_SECONDS = 15


def refresh_repeat(c, item, assignment, spot, box, confidence, ts, session_id):
    """Refresh only the newest, confidently assigned sighting in this live capture session."""
    if not session_id or not assignment["place_id"] or not spot.strip():
        return None
    row = c.execute("SELECT * FROM sightings WHERE item=? ORDER BY julianday(ts) DESC,id DESC LIMIT 1",
                    (item,)).fetchone()
    if row is None or any(row[key] != value for key, value in {
        "session_id": session_id, "place_id": assignment["place_id"],
        "place_source": assignment["place_source"], "room": assignment["room"],
    }.items()) or _normalized(row["spot"]) != _normalized(spot):
        return None
    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    previous = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
    previous = previous if previous.tzinfo else previous.replace(tzinfo=timezone.utc)
    if not 0 <= (when - previous).total_seconds() <= REPEAT_WINDOW_SECONDS:
        return None
    # Keep the first crop as a representative image; do not create one JPEG per repeat.
    c.execute("""UPDATE sightings SET ts=?,spot=?,x1=?,y1=?,x2=?,y2=?,confidence=?,place_reason=?
                 WHERE id=?""", (when.astimezone(timezone.utc).isoformat(), spot, *box, confidence,
                                 assignment["place_reason"], row["id"]))
    return db.sighting_record(c.execute("SELECT * FROM sightings WHERE id=?", (row["id"],)).fetchone())


def observe_and_save(vision, frame, room, ts, *, place_id=None, auto_place=True, session_id=None, hints=None, coalesce=False):
    """One inference call; atomically save new places and sightings; persist only object crops."""
    room = room.strip() if room and room.strip() else None
    explicit = memory.validate_place(place_id, room)
    if explicit:
        room = explicit["room"]
    context = place_context(room, session_id, ts) if auto_place and not explicit else None
    begin = time.perf_counter()
    detections = (vision.observe(frame, db.list_items(), room, context, hints) if context is not None
                  else vision.detect(frame, db.list_items(), room or UNKNOWN_ROOM, hints))
    inference_ms = round((time.perf_counter() - begin) * 1000, 1)
    if not detections:
        return [], inference_ms
    crops = []
    try:
        with db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            assignments = resolve_frame(c, detections, room, context) if context is not None else {}
            sightings = []
            for d in detections:
                item_name = db.canonicalize_item(d.item) or d.item
                assignment = assignments.get(d.item) or assignments.get(item_name) or {
                    "place_id": place_id, "room": room or UNKNOWN_ROOM,
                    "place_source": "manual" if explicit else "none",
                    "place_reason": "Explicit place override." if explicit else "Automatic assignment disabled."}
                repeated = refresh_repeat(c, item_name, assignment, d.spot, d.box, d.confidence,
                                          ts, session_id) if coalesce else None
                if repeated is not None:
                    sightings.append(repeated)
                    continue
                thumb = db.save_thumb(frame, d.box)
                if thumb:
                    crops.append(db.thumbs_dir() / Path(thumb).name)
                sightings.append(db.insert_sighting(c, item_name, assignment["room"], d.spot, d.box,
                    d.confidence, ts, thumb, place_id=assignment["place_id"],
                    place_source=assignment["place_source"], place_reason=assignment["place_reason"],
                    session_id=session_id))
        return sightings, inference_ms
    except Exception:
        for path in crops:
            path.unlink(missing_ok=True)
        raise
