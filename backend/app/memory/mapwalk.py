"""Walkthrough mapping: build places and connections from a camera walk, no hand labels.

Owner: A. Vision returns place surveys; this module persists places/edges.
"""
import re
import uuid
from dataclasses import dataclass

from . import store as memory
from .models import MemoryMapUpdate, Place, PlaceConnection
from .observation import UNKNOWN_ROOM

MAX_SURVEY_CANDIDATES = 40


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _landmarks(values) -> set[str]:
    return {_normalized(v) for v in values}


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")[:40] or "place"
    if re.match(r"^[a-z0-9]", base):
        return base
    return "p_" + base


@dataclass
class SurveyApplyResult:
    """Outcome of applying one frame's place survey to the map."""
    place_id: str | None
    action: str  # match | new | unknown | skipped
    connection_added: bool
    reason: str


def candidate_places(room: str | None = None) -> list[dict]:
    places = memory.get_map()["places"]
    if room:
        places = [p for p in places if _normalized(p["room"]) == _normalized(room)]
    return places[:MAX_SURVEY_CANDIDATES]


def apply_survey(survey, *, previous_place_id: str | None = None,
                 room_hint: str | None = None) -> SurveyApplyResult:
    """Persist a match/new place and optionally a directed edge from the previous place."""
    if survey is None or survey.decision == "unknown":
        return SurveyApplyResult(None, "unknown", False,
                                 getattr(survey, "reason", None) or "Place unclear in this frame.")

    current = {p["id"]: p for p in memory.get_map()["places"]}
    place_id = None
    action = "unknown"
    reason = survey.reason

    if survey.decision == "match":
        candidate = current.get(survey.place_id)
        idxs = sorted(set(survey.matched_landmarks or []))
        if candidate is None or len(idxs) < 2 or any(i < 0 or i >= len(candidate["landmarks"]) for i in idxs):
            return SurveyApplyResult(None, "unknown", False,
                                     "Match lacked two valid landmarks of a known place.")
        matched = {_normalized(candidate["landmarks"][i]) for i in idxs}
        if len(matched) < 2:
            return SurveyApplyResult(None, "unknown", False, "Matched landmarks were not distinct.")
        if any(matched <= _landmarks(p["landmarks"]) for p in current.values() if p["id"] != candidate["id"]):
            return SurveyApplyResult(None, "unknown", False, "Matched landmarks do not uniquely identify this place.")
        place_id, action = candidate["id"], "match"
        reason = f"Matched taught place. {survey.reason}"
    elif survey.decision == "new":
        landmarks = [x for x in (survey.landmarks or []) if x and x.strip()]
        uniq = _landmarks(landmarks)
        if not survey.name or not survey.description or len(uniq) < 2:
            return SurveyApplyResult(None, "unknown", False,
                                     "New place needs a name, description, and two distinct landmarks.")
        if any(len(uniq & _landmarks(p["landmarks"])) >= 2 for p in current.values()):
            return SurveyApplyResult(None, "unknown", False,
                                     "Landmarks overlap an existing place; waiting for a clear match.")
        room = (room_hint or survey.room or UNKNOWN_ROOM).strip() or UNKNOWN_ROOM
        place_id = f"auto_{_slug(survey.name)}_{uuid.uuid4().hex[:10]}"
        if len(place_id) > 80:
            place_id = "auto_" + uuid.uuid4().hex
        memory.teach_map(MemoryMapUpdate(places=[Place(
            id=place_id, name=survey.name.strip(), room=room,
            description=survey.description.strip(), landmarks=landmarks[:20])]))
        action = "new"
        reason = f"Created place from walkthrough. {survey.reason}"
    else:
        return SurveyApplyResult(None, "unknown", False, "Unrecognized survey decision.")

    connection_added = False
    if (place_id and previous_place_id and previous_place_id != place_id
            and survey.transition_observed and survey.instruction_from_previous
            and survey.departure_view and survey.arrival_view):
        saved = memory.get_map()
        if previous_place_id in {p["id"] for p in saved["places"]}:
            exists = any(e["from_place_id"] == previous_place_id and e["to_place_id"] == place_id
                         for e in saved["connections"])
            if not exists:
                instruction = (survey.instruction_from_previous or "").strip()
                memory.teach_map(MemoryMapUpdate(connections=[PlaceConnection(
                    from_place_id=previous_place_id, to_place_id=place_id,
                    departure_view=survey.departure_view[:1000],
                    instruction=instruction[:1000],
                    arrival_view=survey.arrival_view[:1000])]))
                connection_added = True
    return SurveyApplyResult(place_id, action, connection_added, reason)


def mapwalk_video(vision, frames, *, room: str | None = None, hints_for=None):
    """Yield per-frame JSON-serializable records while growing the map.

    frames: iterable of (offset_seconds, jpeg_bytes)
    hints_for: optional callable(offset, index) -> hints dict for MockVision
    """
    previous = None
    previous_frame = None
    previous_offset = None
    for index, (offset, jpeg) in enumerate(frames):
        if previous_offset is not None and not 0 < offset - previous_offset <= 10:
            previous = previous_frame = None
        candidates = candidate_places(room)
        hints = hints_for(offset, index) if hints_for else None
        previous_info = None
        if previous:
            try:
                previous_info = memory.get_place(previous)
            except memory.MemoryNotFound:
                previous_info = None
        begin_places = len(memory.get_map()["places"])
        survey = vision.survey_place(jpeg, candidates, previous=previous_info, hints=hints,
                                     previous_frame=previous_frame)
        applied = apply_survey(survey, previous_place_id=previous, room_hint=room)
        previous = applied.place_id
        previous_frame = jpeg if previous else None
        previous_offset = offset
        yield {
            "offset_seconds": round(float(offset), 6),
            "place_id": applied.place_id,
            "action": applied.action,
            "connection_added": applied.connection_added,
            "reason": applied.reason,
            "places_total": len(memory.get_map()["places"]),
            "connections_total": len(memory.get_map()["connections"]),
            "places_created_this_frame": max(0, len(memory.get_map()["places"]) - begin_places),
        }
