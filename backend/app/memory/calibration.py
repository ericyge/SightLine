"""One apartment's live calibration session. Metadata persists; the previous image never does."""
import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..models import Sighting
from ..vision import VisionUnavailable
from . import store
from .mapwalk import MAX_SURVEY_CANDIDATES, apply_survey, candidate_places
from .models import MemoryMapUpdate, Place, PlaceConnection, PlaceID
from .observation import observe_and_save
from .route import plan_route

KEY = "live_calibration"
MAX_CONTINUITY_SECONDS = 16
_lock = threading.Lock()  # One camera walk, one server worker; never queue old frames.


@dataclass
class PreviousView:
    database: str
    session_id: str
    place_id: str
    frame: bytes
    captured_at: datetime
    expires_at: float


_previous: PreviousView | None = None


class CalibrationStatus(BaseModel):
    session_id: str | None = None
    status: str = "idle"  # idle | mapping | saved
    available: bool = True
    mock: bool = False
    frames_processed: int = 0
    last_sequence: int = 0
    current_place: Place | None = None
    places: list[Place] = Field(default_factory=list)
    connections_total: int = 0
    rooms: list[str] = Field(default_factory=list)
    missing_paths: list[dict[str, str]] = Field(default_factory=list)
    coverage_ready: bool = False  # Graph connectivity, NOT physical navigation validation.
    action: str | None = None
    connection_added: bool = False
    sightings: list[Sighting] = Field(default_factory=list)
    memory_warning: str | None = None
    say: str = ""


class CalibrationSession(BaseModel):
    session_id: PlaceID


def _read() -> dict:
    with db.connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    return json.loads(row["value"]) if row else {}


def _save(state: dict) -> None:
    with db.connect() as c:
        c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (KEY, json.dumps(state)))


@contextmanager
def _exclusive():
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "Calibration is processing a frame. Wait for it to finish.")
    try:
        yield
    finally:
        _lock.release()


def reset_continuity() -> None:
    """Release the previous image on stop/restart/unknown view; keep the saved map."""
    global _previous
    _previous = None


def _current(state: dict) -> PreviousView | None:
    prior = _previous
    if (prior and prior.database == db.db_path() and prior.session_id == state.get("session_id")
            and time.monotonic() < prior.expires_at):
        return prior
    return None


def status(vision) -> dict:
    state = _read()
    saved = store.get_map()
    places = saved["places"]
    missing = [{"from_place_id": a["id"], "to_place_id": b["id"]}
               for a in places for b in places if a != b
               and plan_route(a["id"], b["id"], saved["connections"], places).status != "ok"]
    prior = _current(state)
    return {"session_id": state.get("session_id"), "status": state.get("status", "idle"),
            "available": vision.mode in {"mock", "perceptron"}, "mock": vision.mock,
            "frames_processed": state.get("frames_processed", 0), "last_sequence": state.get("last_sequence", 0),
            "current_place": store.get_place(prior.place_id) if prior else None,
            "places": places, "connections_total": len(saved["connections"]),
            "rooms": sorted({p["room"] for p in places if p["room"] != "unknown room"}),
            "missing_paths": missing, "coverage_ready": len(places) >= 2 and not missing,
            "say": ""}


def start(vision) -> dict:
    if vision.mode not in {"mock", "perceptron"}:
        raise HTTPException(503, "Calibration needs PERCEPTRON_MODE=perceptron (or mock for testing).")
    with _exclusive():
        state = _read()
        if state.get("status") != "mapping":
            state = {"session_id": "cal_" + uuid.uuid4().hex, "status": "mapping",
                     "frames_processed": 0, "last_sequence": 0, "last_cue": ""}
        # Starting/resuming never assumes that the user is still at the last saved place.
        reset_continuity()
        state["last_cue"] = ""
        _save(state)
        return {**status(vision), "say": "Calibration started. Slowly scan each room and its doorways, then walk the return paths too."}


def finish(vision, session_id: str) -> dict:
    with _exclusive():
        state = _read()
        if session_id != state.get("session_id"):
            raise HTTPException(404, "Unknown calibration session.")
        state["status"] = "saved"
        _save(state)
        reset_continuity()
        result = status(vision)
        if result["coverage_ready"]:
            text = "Map saved. Routes connect the recorded places in both directions. You can now ask about objects."
        else:
            text = "Map saved, but some routes are missing. Continue calibration and walk between the places in both directions."
        return {**result, "say": text}


def process_frame(vision, frame: bytes, session_id: str, sequence: int,
                  captured_at: datetime, hints: dict | None = None) -> dict:
    global _previous
    with _exclusive():
        state = _read()
        if session_id != state.get("session_id"):
            raise HTTPException(404, "Unknown calibration session.")
        if state.get("status") != "mapping":
            raise HTTPException(409, "Start calibration before sending frames.")
        if vision.mode not in {"mock", "perceptron"}:
            raise HTTPException(503, "Calibration needs the full Perceptron client.")
        if sequence <= state["last_sequence"]:
            raise HTTPException(409, "Calibration frame already processed or out of order.")
        age = (datetime.now(timezone.utc) - captured_at).total_seconds()
        if age < -5 or age > 30:
            reset_continuity()
            raise HTTPException(422, "Calibration frame timestamp is stale or in the future.")
        if state.get("last_captured_at") and captured_at <= datetime.fromisoformat(state["last_captured_at"]):
            reset_continuity()
            raise HTTPException(409, "Calibration capture timestamp is out of order.")
        prior = _current(state)
        if prior and (sequence != state["last_sequence"] + 1
                      or not 0 < (captured_at - prior.captured_at).total_seconds() <= MAX_CONTINUITY_SECONDS):
            prior = None
        # Reserve the sequence before inference; retries cannot insert the same frame twice.
        state["last_sequence"] = sequence
        state["last_captured_at"] = captured_at.isoformat()
        _save(state)
        previous_place = store.get_place(prior.place_id) if prior else None
        try:
            survey = vision.survey_place(frame, candidate_places(), previous=previous_place,
                                        previous_frame=prior.frame if prior else None, hints=hints)
        except Exception:
            reset_continuity()
            raise
        if survey.decision == "new" and len(store.get_map()["places"]) >= MAX_SURVEY_CANDIDATES:
            reset_continuity()
            raise HTTPException(409, "Calibration map reached its place limit; inspect the map before continuing.")
        applied = apply_survey(survey, previous_place_id=prior.place_id if prior else None)
        reset_continuity()
        if applied.place_id:
            _previous = PreviousView(db.db_path(), session_id, applied.place_id, frame,
                                     captured_at, time.monotonic() + MAX_CONTINUITY_SECONDS)
        state["frames_processed"] += 1
        current = store.get_place(applied.place_id) if applied.place_id else None
        if not vision.mock and applied.connection_added and prior and current and previous_place:
            saved_edges = store.get_map()["connections"]
            has_reverse = any(
                e["from_place_id"] == current["id"] and e["to_place_id"] == previous_place["id"]
                for e in saved_edges
            )
            if not has_reverse:
                store.teach_map(MemoryMapUpdate(connections=[PlaceConnection(
                    from_place_id=current["id"],
                    to_place_id=previous_place["id"],
                    departure_view=f"Leaving {current['name']} back toward {previous_place['name']}.",
                    instruction="Turn around in place and walk straight ahead.",
                    arrival_view=f"Arriving at {previous_place['name']}.",
                )]))
        if current is None:
            cue_key, text = "unknown", "I can't recognize this view yet. Hold the camera steady and slowly scan nearby landmarks."
        elif prior and prior.place_id != current["id"] and not applied.connection_added:
            cue_key = f"transition:{prior.place_id}:{current['id']}"
            text = f"Recognized {current['name']}. If the route is missing, slowly retrace the doorway between these places."
        else:
            cue_key = current["id"]
            text = f"{'Added' if applied.action == 'new' else 'Recognized'} {current['name']}."
        say = text if state.get("last_cue") != cue_key else ""
        state["last_cue"] = cue_key
        _save(state)
        # Object destinations are inferred independently; camera position is never assigned to every object.
        sightings, warning = [], None
        try:
            sightings, _ = observe_and_save(vision, frame, None, captured_at.isoformat(),
                                            session_id=session_id, hints=hints, coalesce=True)
            if not vision.mock and current is not None:
                anchored = []
                for s in sightings:
                    carried = any(w in (s.get("spot") or "").lower() for w in ("inside a bag", "in hand", "carried"))
                    if not carried and s.get("place_id") != current["id"]:
                        try:
                            if s.get("room") != current["room"]:
                                with db.connect() as c:
                                    c.execute("UPDATE sightings SET room=? WHERE id=?", (current["room"], s["id"]))
                            s = store.assign_sighting(s["id"], current["id"])
                        except Exception:
                            pass
                    anchored.append(s)
                sightings = anchored
        except VisionUnavailable as exc:
            warning = str(exc)
        return {**status(vision), "action": applied.action, "connection_added": applied.connection_added,
                "sightings": sightings, "memory_warning": warning,
                "say": say}
