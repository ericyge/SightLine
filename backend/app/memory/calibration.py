"""One apartment's live calibration session. Metadata persists; the previous image never does."""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
LIVE_CONTINUITY_SECONDS = 45
_lock = threading.Lock()  # One camera walk, one server worker; never queue old frames in mock tests.
_live_cv = threading.Condition()
_in_flight_seqs: set[int] = set()
_pool = ThreadPoolExecutor(max_workers=12)


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
def _exclusive(wait: bool = False):
    acquired = _lock.acquire(timeout=20.0) if wait else _lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(409, "Calibration is processing a frame. Wait for it to finish.")
    try:
        yield
    finally:
        _lock.release()


def reset_continuity() -> None:
    """Release the previous image on stop/restart/unknown view; keep the saved map."""
    global _previous
    _previous = None


def last_known_place_id() -> str | None:
    """Return the most recently recognized place from live calibration (within 90s) as a start-location fallback."""
    if _previous and _previous.database == db.db_path() and time.monotonic() < _previous.expires_at:
        return _previous.place_id
    state = _read()
    place_id = state.get("last_place_id")
    seen_at = state.get("last_place_mono", 0.0)
    if place_id and (time.monotonic() - float(seen_at)) <= 90.0 and store.get_place(place_id):
        return place_id
    return None


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
    with _exclusive(wait=not vision.mock):
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


def _stitch_places(from_place: dict, to_place: dict, saved_edges: list[dict],
                   fwd_instruction: str | None = None,
                   departure_view: str | None = None,
                   arrival_view: str | None = None) -> list[PlaceConnection]:
    """Build missing forward and reverse edges between two distinct places."""
    if not from_place or not to_place or from_place["id"] == to_place["id"]:
        return []
    new_edges: list[PlaceConnection] = []
    has_forward = any(
        e["from_place_id"] == from_place["id"] and e["to_place_id"] == to_place["id"]
        for e in saved_edges
    )
    if not has_forward:
        instr = (fwd_instruction or "").strip() or f"Walk straight ahead toward {to_place['name']}."
        new_edges.append(PlaceConnection(
            from_place_id=from_place["id"],
            to_place_id=to_place["id"],
            departure_view=(departure_view or f"Leaving {from_place['name']} toward {to_place['name']}.")[:1000],
            instruction=instr[:1000],
            arrival_view=(arrival_view or f"Arriving at {to_place['name']}.")[:1000],
        ))
        saved_edges.append({"from_place_id": from_place["id"], "to_place_id": to_place["id"]})
    has_reverse = any(
        e["from_place_id"] == to_place["id"] and e["to_place_id"] == from_place["id"]
        for e in saved_edges
    )
    if not has_reverse:
        new_edges.append(PlaceConnection(
            from_place_id=to_place["id"],
            to_place_id=from_place["id"],
            departure_view=f"Leaving {to_place['name']} back toward {from_place['name']}."[:1000],
            instruction="Turn around in place and walk straight ahead.",
            arrival_view=f"Arriving at {from_place['name']}."[:1000],
        ))
        saved_edges.append({"from_place_id": to_place["id"], "to_place_id": from_place["id"]})
    return new_edges


def process_frame(vision, frame: bytes, session_id: str, sequence: int,
                  captured_at: datetime, hints: dict | None = None) -> dict:
    global _previous
    real_live = not vision.mock and not hints
    max_cont = LIVE_CONTINUITY_SECONDS if real_live else MAX_CONTINUITY_SECONDS

    if real_live:
        with _exclusive(wait=True):
            state = _read()
            if session_id != state.get("session_id"):
                raise HTTPException(404, "Unknown calibration session.")
            if state.get("status") != "mapping":
                raise HTTPException(409, "Start calibration before sending frames.")
            if vision.mode not in {"mock", "perceptron"}:
                raise HTTPException(503, "Calibration needs the full Perceptron client.")
            age = (datetime.now(timezone.utc) - captured_at).total_seconds()
            if age < -5 or age > 60:
                raise HTTPException(422, "Calibration frame timestamp is stale or in the future.")
            prior = _current(state)
            if prior:
                dt = (captured_at - prior.captured_at).total_seconds()
                if not (-5 <= dt <= max_cont):
                    prior = None
            previous_place = store.get_place(prior.place_id) if prior else None
            candidates = candidate_places()

        # Run Perceptron place survey and object observation concurrently OUTSIDE _lock so
        # overlapping frames from the phone execute in parallel against Perceptron.
        obs_future = _pool.submit(
            observe_and_save, vision, frame, None, captured_at.isoformat(),
            session_id=session_id, hints=hints, coalesce=True, allow_concurrent_catalog=True
        )
        survey = vision.survey_place(
            frame, candidates, previous=previous_place,
            previous_frame=prior.frame if prior else None, hints=hints
        )
        sightings, warning = [], None
        try:
            sightings, _ = obs_future.result()
        except VisionUnavailable as exc:
            warning = str(exc)

        with _exclusive(wait=True):
            state = _read()
            if session_id != state.get("session_id") or state.get("status") != "mapping":
                return {**status(vision), "action": "skipped", "connection_added": False,
                        "sightings": sightings, "memory_warning": warning, "say": ""}
            latest_prior = _current(state) or prior
            latest_prev_place = store.get_place(latest_prior.place_id) if latest_prior else previous_place
            if survey.decision == "new" and len(store.get_map()["places"]) >= MAX_SURVEY_CANDIDATES:
                survey = survey  # cap reached; fall back to existing places if any
            applied = apply_survey(survey, previous_place_id=latest_prior.place_id if latest_prior else None)
            current = store.get_place(applied.place_id) if applied.place_id else None

            # If survey_place returned unknown (e.g. close-up of a table), promote any place inferred by observe_and_save.
            if current is None:
                for s in sightings:
                    if s.get("place_id"):
                        inferred = store.get_place(s["place_id"])
                        if inferred is not None:
                            current = inferred
                            break

            connection_added = applied.connection_added
            saved_edges = list(store.get_map()["connections"])
            new_edges: list[PlaceConnection] = []

            if latest_prev_place and current and latest_prev_place["id"] != current["id"]:
                new_edges.extend(_stitch_places(
                    latest_prev_place, current, saved_edges,
                    fwd_instruction=survey.instruction_from_previous,
                    departure_view=survey.departure_view,
                    arrival_view=survey.arrival_view,
                ))

            anchor_place = current or latest_prev_place
            anchored = []
            for s in sightings:
                carried = any(w in (s.get("spot") or "").lower() for w in ("inside a bag", "in hand", "carried"))
                s_place_id = s.get("place_id")
                if not carried and s_place_id is None and anchor_place is not None:
                    try:
                        if s.get("room") != anchor_place["room"] and anchor_place["room"] != "unknown room":
                            with db.connect() as c:
                                c.execute("UPDATE sightings SET room=? WHERE id=?", (anchor_place["room"], s["id"]))
                        s = store.assign_sighting(s["id"], anchor_place["id"])
                        s_place_id = s.get("place_id")
                    except Exception:
                        pass
                if not carried and s_place_id:
                    s_place = store.get_place(s_place_id)
                    if s_place is not None:
                        if current and s_place["id"] != current["id"]:
                            new_edges.extend(_stitch_places(
                                current, s_place, saved_edges,
                                fwd_instruction=f"Step forward toward {s_place['name']}.",
                            ))
                        if latest_prev_place and s_place["id"] != latest_prev_place["id"]:
                            new_edges.extend(_stitch_places(
                                latest_prev_place, s_place, saved_edges,
                                fwd_instruction=survey.instruction_from_previous or f"Walk straight ahead toward {s_place['name']}.",
                            ))
                anchored.append(s)
            sightings = anchored

            if new_edges:
                store.teach_map(MemoryMapUpdate(connections=new_edges))
                connection_added = True

            if current is not None and sequence >= state.get("last_sequence", 0):
                _previous = PreviousView(db.db_path(), session_id, current["id"], frame,
                                         captured_at, time.monotonic() + max_cont)
                state["last_place_id"] = current["id"]
                state["last_place_mono"] = time.monotonic()
            if sequence > state.get("last_sequence", 0):
                state["last_sequence"] = sequence
                state["last_captured_at"] = captured_at.isoformat()
            state["frames_processed"] = state.get("frames_processed", 0) + 1
            if current is None:
                cue_key, text = "unknown", "I can't recognize this view yet. Hold the camera steady and slowly scan nearby landmarks."
            else:
                cue_key = current["id"]
                text = f"{'Added' if applied.action == 'new' else 'Recognized'} {current['name']}."
            say = text if state.get("last_cue") != cue_key else ""
            state["last_cue"] = cue_key
            _save(state)
            return {**status(vision), "action": applied.action, "connection_added": connection_added,
                    "sightings": sightings, "memory_warning": warning, "say": say}

    with _exclusive(wait=False):
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
        if prior:
            dt = (captured_at - prior.captured_at).total_seconds()
            if sequence != state["last_sequence"] + 1 or not (0 < dt <= max_cont):
                prior = None
        # Reserve the sequence before inference; retries cannot insert the same frame twice.
        state["last_sequence"] = sequence
        state["last_captured_at"] = captured_at.isoformat()
        _save(state)
        previous_place = store.get_place(prior.place_id) if prior else None
        candidates = candidate_places()
        try:
            survey = vision.survey_place(frame, candidates, previous=previous_place,
                                        previous_frame=prior.frame if prior else None, hints=hints)
        except Exception:
            reset_continuity()
            raise
        if survey.decision == "new" and len(store.get_map()["places"]) >= MAX_SURVEY_CANDIDATES:
            reset_continuity()
            raise HTTPException(409, "Calibration map reached its place limit; inspect the map before continuing.")
        applied = apply_survey(survey, previous_place_id=prior.place_id if prior else None)
        if applied.place_id:
            _previous = PreviousView(db.db_path(), session_id, applied.place_id, frame,
                                     captured_at, time.monotonic() + max_cont)
            state["last_place_id"] = applied.place_id
            state["last_place_mono"] = time.monotonic()
        else:
            reset_continuity()
        state["frames_processed"] += 1
        current = store.get_place(applied.place_id) if applied.place_id else None
        connection_added = applied.connection_added
        if current is None:
            cue_key, text = "unknown", "I can't recognize this view yet. Hold the camera steady and slowly scan nearby landmarks."
        elif prior and prior.place_id != current["id"] and not connection_added:
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
        except VisionUnavailable as exc:
            warning = str(exc)
        return {**status(vision), "action": applied.action, "connection_added": connection_added,
                "sightings": sightings, "memory_warning": warning,
                "say": say}
