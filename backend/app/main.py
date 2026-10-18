"""FastAPI app: REST endpoints + WebSocket /ws/events. See docs/CONTRACT.md."""
import asyncio
import base64
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# override=False: tests/CI set PERCEPTRON_MODE/LLM_PROVIDER before import; a shell export of
# PERCEPTRON_MODE=live still wins over .env — unset it or pass PERCEPTRON_MODE=perceptron on the command.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, HTTPException, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402

from . import db, guidance, llm, memory, stt, tts, wake  # noqa: E402
from .memory.api import router as memory_router  # noqa: E402
from .memory import calibration  # noqa: E402
from .memory.models import PlaceID  # noqa: E402
from .memory.observation import UNKNOWN_ROOM, observe_and_save  # noqa: E402
from .events import broadcaster  # noqa: E402
from .models import (ChatRequest, ChatResponse, GuideResponse, HealthResponse, ObserveResponse, OkResponse, Profile,  # noqa: E402
                     SpeakRequest, TranscribeResponse, WhereLast, WhereResponse)
from .vision import VisionUnavailable, get_vision, reset_vision  # noqa: E402
from .vision.live import VisionBusy  # noqa: E402
from .frames import InvalidFrame, MAX_FRAME_BYTES, prepare_frame  # noqa: E402

log = logging.getLogger("sightline")
MAX_EVENT_FRAME_BYTES = 200_000   # frames are only forwarded live to the dashboard, never stored
GUIDE_STATE_TTL_S = 10.0          # a pause this long starts a fresh guidance session
MAX_AUDIO_BYTES = 5_000_000       # 16 kHz mono WAV is ~32 KB/s, so this is over 2 minutes
CHAT_HISTORY_TURNS = 6
MAX_CHAT_SAY_CHARS = 200

_guide_states: dict[str, guidance.GuideState] = {}
_navigate_states: dict[str, guidance.NavigateState] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log loudly when mock vision is active."""
    vision = get_vision()
    log.warning("Sightline backend up. vision mode = %s%s; LLM provider = %s", vision.mode,
                " (MOCK: fake detections, no API key needed)" if vision.mock else "",
                llm.get_llm().provider)
    db.thumbs_dir().mkdir(parents=True, exist_ok=True)
    if tts.tts_enabled():   # render the fixed guidance cues in the background so they play instantly
        threading.Thread(target=tts.prewarm, daemon=True).start()
    try:
        yield
    finally:
        calibration.reset_continuity()
        reset_vision()


app = FastAPI(title="Sightline", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(memory_router)


@app.exception_handler(memory.MemoryNotFound)
@app.exception_handler(memory.MemoryConflict)
async def memory_error(request: Request, exc: ValueError):
    status = 404 if isinstance(exc, memory.MemoryNotFound) else 409
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(VisionUnavailable)
async def vision_error(request: Request, exc: VisionUnavailable):
    """Do not mistake an unavailable model for a missing item."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


async def read_frame(upload: UploadFile) -> bytes:
    """Bound uploads and keep inference, thumbnail, and dashboard geometry aligned."""
    data = await upload.read(MAX_FRAME_BYTES + 1)
    if len(data) > MAX_FRAME_BYTES:
        raise HTTPException(413, "Frame exceeds 8 MiB.")
    try:
        return await run_in_threadpool(prepare_frame, data,
                                      int(os.getenv("VISION_FRAME_MAX_PX", "640")))
    except InvalidFrame as exc:
        raise HTTPException(422, str(exc)) from None


# ---- helpers ----------------------------------------------------------------------------

def guide_max_fps() -> float:
    """Cap on /api/guide frames per second per client (env GUIDE_MAX_FPS, default 3, kept in 0.5..10)."""
    try:
        return min(10.0, max(0.5, float(os.getenv("GUIDE_MAX_FPS") or 3)))
    except ValueError:
        return 3.0


def fov_h() -> float:
    return float(os.getenv("FOV_H_DEGREES", "65"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO timestamp; naive values are treated as UTC."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mock_hints(request: Request) -> dict:
    """Forced mock position/size/place from headers or query params (mock mode only)."""
    hints = {}
    for key, header in (("x", "x-mock-item-x"), ("h", "x-mock-item-h"),
                        ("hand_x", "x-mock-hand-x"), ("hand_y", "x-mock-hand-y")):
        raw = request.headers.get(header) or request.query_params.get(f"mock_{key}")
        try:
            if raw is not None:
                hints[key] = float(raw)
        except ValueError:
            pass
    place = request.headers.get("x-mock-place") or request.query_params.get("mock_place")
    if place is not None:
        hints["place"] = place
    facing = request.headers.get("x-mock-facing") or request.query_params.get("mock_facing")
    if facing is not None:
        hints["facing"] = facing
    hide = request.headers.get("x-mock-hide") or request.query_params.get("mock_hide")
    if hide is not None and hide.strip().lower() in {"1", "true", "yes"}:
        hints["hide"] = True
    return hints


def _nav_state(name: str, now: float) -> guidance.NavigateState:
    state = _navigate_states.get(name)
    if state is None or now - state.touched > GUIDE_STATE_TTL_S:
        state = _navigate_states[name] = guidance.NavigateState(since=now)
    state.touched = now
    return state


def _guide_payload(*, found: bool, say: str = "", beep_ms: int = 0, mode: str = "lost",
                   current_place: str | None = None, destination: dict | None = None,
                   next_instruction: str | None = None, point=None, box=None, angle=None,
                   clock=None, distance=None, pan: float = 0.0, departure_status: str | None = None) -> dict:
    return {"found": found, "point": point, "box": box, "angle": angle, "clock": clock,
            "distance": distance, "pan": pan, "say": say, "beep_ms": beep_ms, "mode": mode,
            "current_place": current_place, "destination": destination,
            "next_instruction": next_instruction, "departure_status": departure_status}


LIVE_REPEAT_S = 10.0      # re-speak an unchanged instruction after 10s
LIVE_MIN_CUE_GAP_S = 5.5  # minimum cooldown between different spoken cues in real live mode


def _nav_say(nav: guidance.NavigateState, text: str, key: str, now: float,
             repeat_s: float = guidance.NAVIGATE_REPEAT_S, min_gap_s: float = 0.0) -> str:
    """Speak a navigate cue on change (after min_gap_s), or re-prompt the same cue after repeat_s."""
    if min_gap_s > 0 and nav.last_spoken_at > 0 and (now - nav.last_spoken_at) < min_gap_s:
        if not key.endswith(":blocked"):
            return ""
    spoken, nav.last_key, nav.last_spoken_at = guidance.navigate_say(
        text, key, nav.last_key, now=now, last_spoken_at=nav.last_spoken_at, repeat_s=repeat_s)
    return spoken


_BODY_ACTION_PREFIXES = (
    "turn left", "turn right", "turn slightly", "turn around", "walk straight",
    "step forward", "go straight", "stop", "reach ",
)


def _is_body_relative(instruction: str) -> bool:
    low = instruction.strip().lower()
    return low.startswith(_BODY_ACTION_PREFIXES) and "look" not in low and "scan" not in low


def _body_relative_hop_cue(hop_instruction: str, facing: str) -> str:
    """Convert a route hop + camera facing into a blind-accessible physical movement cue."""
    if facing == "blocked":
        return "Stop walking. Something is blocking the path ahead. Step or turn to the side."
    if facing == "left":
        return "Stop walking. Turn left in place until you face the path ahead."
    if facing == "right":
        return "Stop walking. Turn right in place until you face the path ahead."
    if facing == "aligned":
        if _is_body_relative(hop_instruction):
            return hop_instruction
        return "Walk straight ahead a few steps."
    if _is_body_relative(hop_instruction):
        return hop_instruction
    return "Stop walking. Turn slowly right in place until you face the path ahead."


def _body_relative_arrival_cue(facing: str, history: list[dict]) -> str:
    """Blind-accessible physical reach cue when at the destination place."""
    if facing == "left":
        return "Stop walking. Turn slightly left and reach in front of you."
    if facing == "right":
        return "Stop walking. Turn slightly right and reach in front of you."
    if facing == "blocked":
        return "Stop walking. Reach carefully in front of you."
    box = history[0].get("box") if history else None
    if isinstance(box, list) and len(box) == 4:
        cx = ( float(box[0]) + float(box[2]) ) / 2.0
        if cx < 0.38:
            return "Stop walking. Reach in front of you, slightly to your left."
        if cx > 0.62:
            return "Stop walking. Reach in front of you, slightly to your right."
    return "Stop walking. Reach straight in front of you."


def _body_relative_visual_cue(angle: float | None, distance: str | None) -> str:
    """Blind-accessible physical movement/reach cue when the target object is visible in frame."""
    if angle is None or distance is None:
        return ""
    if distance == "reach":
        if angle < -8:
            return "Stop. Reach in front of you to your left."
        if angle > 8:
            return "Stop. Reach in front of you to your right."
        return "Stop. Reach straight in front of you now."
    if distance == "near":
        if angle < -20:
            return "Turn left and step forward."
        if angle < -8:
            return "Turn slightly left and step forward."
        if angle <= 8:
            return "Step forward and reach straight in front of you."
        if angle <= 20:
            return "Turn slightly right and step forward."
        return "Turn right and step forward."
    if angle < -20:
        return "Turn left."
    if angle < -8:
        return "Turn slightly left and walk straight."
    if angle <= 8:
        return "Walk straight ahead."
    if angle <= 20:
        return "Turn slightly right and walk straight."
    return "Turn right."


def _localization_candidates(places: list[dict], destination: dict, connections: list[dict], real_live: bool) -> list[dict]:
    candidates = []
    dest_lms = ", ".join(destination.get("landmarks", [])[:3])
    dest_dep_view = f"Centered directly in front of {destination['name']}" + (f" ({dest_lms})" if dest_lms else "")
    for place in places:
        route = memory.plan_route(place["id"], destination["id"], connections, places)
        hop = route.next_hop
        if hop and hop.departure_view.strip():
            departure = {"to_place_id": hop.to_place_id, "departure_view": hop.departure_view}
        elif real_live:
            departure = {"to_place_id": destination["id"], "departure_view": dest_dep_view}
        else:
            departure = None
        candidates.append({**place, "departure": departure})
    return candidates


def _navigate_guide(name: str, frame: bytes, hints: dict, now: float, yaw: float | None = None,
                    loc_future=None) -> dict:
    """Room-to-room cue when the target item is not visible in this frame."""
    history = db.recent_sightings(name, 1)
    saved_map = memory.get_map()
    places = saved_map["places"]
    destination = memory.navigable_destination(name)
    nav = _nav_state(name, now)
    vis_state = _guide_states.get(name)
    if vis_state and vis_state.last_spoken_at > nav.last_spoken_at:
        nav.last_spoken_at = vis_state.last_spoken_at
    real_live = not get_vision().mock and not hints
    repeat_s = LIVE_REPEAT_S if real_live else guidance.NAVIGATE_REPEAT_S
    min_gap_s = LIVE_MIN_CUE_GAP_S if real_live else 0.0
    if destination is None:
        if history:
            cue = (
                "Stop walking. Turn slowly right in place with your phone in front of you."
                if real_live
                else f"I last saw your {name}, but couldn't identify its place. Please slowly scan your surroundings."
            )
            text = _nav_say(nav, cue, f"unassigned:{history[0]['id']}", now, repeat_s=repeat_s, min_gap_s=min_gap_s)
            return _guide_payload(found=False, say=text, mode="lost")
        nav.last_key = None
        state = _guide_states.get(name)
        if state is None or now - state.touched > GUIDE_STATE_TTL_S:
            state = _guide_states[name] = guidance.GuideState(since=now)
        result = guidance.compute_guide(name, None, state, db.guidance_style(), fov_h(), now, yaw)
        return _guide_payload(found=False, say=result["say"], mode="lost")

    candidates = _localization_candidates(places, destination, saved_map["connections"], real_live)
    try:
        loc = loc_future.result() if loc_future is not None else get_vision().localize(frame, candidates, hints)
    except VisionUnavailable:
        nav.last_key = None  # Repeat the instruction after recovery from a failed check.
        nav.last_spoken_at = 0.0
        raise
    current_id = loc.place_id
    if current_id is not None:
        nav.last_place_id = current_id
        nav.last_place_at = now
    elif real_live:
        if nav.last_place_id and (now - nav.last_place_at) <= 3.5:
            current_id = nav.last_place_id
        elif nav.last_place_id is None:
            seeded = calibration.last_known_place_id()
            if seeded:
                current_id = nav.last_place_id = seeded
                nav.last_place_at = now
    plan = memory.plan_route(current_id, destination["id"], saved_map["connections"], places)

    if plan.status == "already_there":
        if real_live:
            cue = _body_relative_arrival_cue(loc.departure_status, history)
            text = _nav_say(nav, cue, f"at:{destination['id']}:{loc.departure_status}", now,
                            repeat_s=repeat_s, min_gap_s=min_gap_s)
            return _guide_payload(found=False, say=text, mode="navigate",
                                  current_place=current_id, destination=destination,
                                  next_instruction=cue, departure_status=loc.departure_status)
        text = _nav_say(nav, f"You're at {destination['name']}. Look around slowly.",
                        f"at:{destination['id']}", now, repeat_s=repeat_s)
        return _guide_payload(found=False, say=text, mode="navigate",
                              current_place=current_id, destination=destination,
                              next_instruction=None)

    if plan.status == "ok" and plan.next_hop is not None:
        hop = plan.next_hop
        key = f"{hop.from_place_id}->{hop.to_place_id}"
        facing = loc.departure_status if hop.departure_view.strip() else "unknown"
        if real_live:
            spoken_instruction = _body_relative_hop_cue(hop.instruction, facing)
            key = f"{key}:{facing}:{spoken_instruction}"
            text = _nav_say(nav, spoken_instruction, key, now, repeat_s=repeat_s, min_gap_s=min_gap_s)
            return _guide_payload(found=False, say=text, mode="navigate",
                                  current_place=current_id, destination=destination,
                                  next_instruction=spoken_instruction if facing != "blocked" else None,
                                  departure_status=facing)
        if facing != "aligned":
            cue = {
                "left": "Pause walking. Turn slowly left in place so I can check the route.",
                "right": "Pause walking. Turn slowly right in place so I can check the route.",
                "blocked": "Stop. The route appears blocked. Keep still while I check again.",
            }.get(facing, "Pause walking. Slowly scan in place so I can check which way you're facing.")
            text = _nav_say(nav, cue, f"{key}:{facing}", now, repeat_s=repeat_s)
            return _guide_payload(found=False, say=text, mode="navigate",
                                  current_place=current_id, destination=destination,
                                  departure_status=facing)
        key = f"{key}:aligned:{hop.instruction}"
        text = _nav_say(nav, hop.instruction, key, now, repeat_s=repeat_s)
        return _guide_payload(found=False, say=text, mode="navigate",
                              current_place=current_id, destination=destination,
                              next_instruction=hop.instruction, departure_status="aligned")

    if plan.status == "unknown_current":
        if real_live:
            cue = "Stop walking. Turn slowly right in place so I can check where you are."
            text = _nav_say(nav, cue, f"noloc:{destination['id']}", now, repeat_s=repeat_s, min_gap_s=min_gap_s)
            return _guide_payload(found=False, say=text, mode="navigate",
                                  current_place=None, destination=destination,
                                  next_instruction=cue)
        text = _nav_say(
            nav,
            f"Pause walking. Last seen at {destination['name']}, but I can't tell where you are yet. Please slowly scan your surroundings.",
            f"noloc:{destination['id']}", now, repeat_s=repeat_s)
        return _guide_payload(found=False, say=text, mode="lost",
                              current_place=None, destination=destination)

    if plan.status == "no_path":
        cur_place = memory.get_place(current_id)
        if real_live and cur_place:
            cue = _body_relative_hop_cue("", loc.departure_status)
            text = _nav_say(nav, cue, f"nopath:{current_id}:{destination['id']}:{loc.departure_status}", now,
                            repeat_s=repeat_s, min_gap_s=min_gap_s)
            return _guide_payload(found=False, say=text, mode="navigate",
                                  current_place=current_id, destination=destination,
                                  next_instruction=cue, departure_status=loc.departure_status)
        text = _nav_say(
            nav,
            f"You're at {cur_place['name']}, but I don't know a path to "
            f"{destination['name']}.",
            f"nopath:{current_id}:{destination['id']}", now)
        return _guide_payload(found=False, say=text, mode="lost",
                              current_place=current_id, destination=destination)

    text = _nav_say(nav, f"I can't navigate to your {name} right now.", f"bad:{destination['id']}", now)
    return _guide_payload(found=False, say=text, mode="lost", destination=destination)


def ago_phrase(minutes: float) -> str:
    """"just now" / "about 4 minutes ago" / "about 2 hours ago"."""
    if minutes < 1:
        return "just now"
    if minutes < 60:
        n = round(minutes)
        return f"about {n} minute{'s' if n != 1 else ''} ago"
    n = round(minutes / 60)
    return f"about {n} hour{'s' if n != 1 else ''} ago"


def where_say(room: str, spot: str, minutes_ago: float) -> str:
    """Spoken answer for /where."""
    place = f"In your {room}, {spot}" if spot else f"In your {room}"
    if room == UNKNOWN_ROOM:
        place = f"Last seen {spot}" if spot else "Last seen in an unidentified place"
    ago = ago_phrase(minutes_ago)
    return f"{place}, {ago}." if ago != "just now" else f"{place}, just now."


def _frame_data_url(frame: bytes) -> str | None:
    """Data URL of the frame for the live dashboard, or None if too big."""
    if len(frame) > MAX_EVENT_FRAME_BYTES:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(frame).decode()


def _event(type_: str, **payload) -> dict:
    return {"type": type_, "mock": get_vision().mock, **payload}


# ---- routes -----------------------------------------------------------------------------

@app.get("/api/calibration", response_model=calibration.CalibrationStatus)
def calibration_status():
    return calibration.status(get_vision())


@app.post("/api/calibration/start", response_model=calibration.CalibrationStatus)
def calibration_start():
    return calibration.start(get_vision())


@app.post("/api/calibration/finish", response_model=calibration.CalibrationStatus)
def calibration_finish(session: calibration.CalibrationSession):
    return calibration.finish(get_vision(), session.session_id)


@app.post("/api/calibration/frame", response_model=calibration.CalibrationStatus)
async def calibration_frame(request: Request, frame: UploadFile = File(...),
                            session_id: PlaceID = Form(...), sequence: int = Form(..., ge=1),
                            captured_at: str = Form(...)):
    data = await read_frame(frame)
    try:
        when = _parse_ts(captured_at).astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(422, "captured_at must be an ISO timestamp.") from None
    result = await run_in_threadpool(calibration.process_frame, get_vision(), data, session_id,
                                     sequence, when, _mock_hints(request))
    broadcaster.publish(_event("calibration", **result))
    if result["sightings"]:
        broadcaster.publish(_event("sighting", sightings=result["sightings"], frame=_frame_data_url(data)))
    return result

@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Mode check so the UI can show a MOCK badge. async so a busy Perceptron pool cannot starve it."""
    v = get_vision()
    client = stt.get_stt()
    return HealthResponse(mode=v.mode, mock=v.mock, stt=client.provider if client else "browser",
                          vision=v.stats(), guide_max_fps=guide_max_fps(),
                          tts="elevenlabs" if tts.tts_enabled() else "browser",
                          tts_guidance=tts.guidance_cues_mode(), wake=wake.availability()[0])


@app.get("/api/profile", response_model=Profile)
async def get_profile():
    return await run_in_threadpool(db.get_profile)


@app.post("/api/profile", response_model=OkResponse)
def post_profile(profile: Profile):
    """Store the watchlist/rooms/style produced by the Purple setup agent."""
    db.set_profile(profile.model_dump())
    return OkResponse()


@app.post("/api/observe", response_model=ObserveResponse)
async def observe(request: Request, frame: UploadFile = File(...), room: str | None = Form(None, max_length=160),
                  ts: str | None = Form(None), place_id: PlaceID | None = Form(None),
                  auto_place: bool = Form(True), session_id: PlaceID | None = Form(None)):
    """Find watched items in a frame and save each sighting (frame is discarded)."""
    await run_in_threadpool(memory.validate_place, place_id, room)
    data = await read_frame(frame)
    try:
        when = _parse_ts(ts).astimezone(timezone.utc).isoformat() if ts else _now_iso()
    except ValueError:
        raise HTTPException(422, "ts must be an ISO timestamp.") from None
    # TODO(A): skip saving when an item hasn't moved since its last sighting (dedupe / throttle).
    try:
        sightings, _ = await run_in_threadpool(observe_and_save, get_vision(), data, room, when,
            place_id=place_id, auto_place=auto_place, session_id=session_id, hints=_mock_hints(request), coalesce=True)
    except VisionBusy:
        return Response(status_code=204)   # the fast live client is busy with an earlier frame: drop this one
    broadcaster.publish(_event("sighting", sightings=sightings, frame=_frame_data_url(data)))
    return {"sightings": sightings}


def _where_answer(item: str, from_place: str | None = None) -> WhereResponse:
    """Answer "where is X" from stored sightings (shared by /api/where and /api/chat)."""
    name = db.resolve_item(item)
    history = db.recent_sightings(name, 5) if name else []
    if not history:
        label = name or item.strip().lower()
        text = (f"I don't know where your {label} is yet. I haven't seen it." if name
                else "Please tell me which item you're looking for.")
        return WhereResponse(item=label, found=False, say=text)
    last = history[0]
    newest_dest = memory.destination_for(last)
    destination = newest_dest or memory.navigable_destination(name)
    route_info = memory.directions_to_item(name, from_place_id=from_place)
    minutes = max(0.0, (datetime.now(timezone.utc) - _parse_ts(last["ts"])).total_seconds() / 60)
    text = where_say(last["room"], last["spot"], minutes)
    if newest_dest:
        room_phrase = f" in your {newest_dest['room']}" if newest_dest["room"] != UNKNOWN_ROOM else ""
        text = f"Last seen at {newest_dest['name']}{room_phrase}, {ago_phrase(minutes)}."
    elif destination:
        text = (f"{text} I can guide you to {destination['name']}, "
                f"where I last mapped it.")
    return WhereResponse(
        item=name, history=history, destination=destination,
        directions=route_info.get("steps", []), say=text,
        last=WhereLast(room=last["room"], spot=last["spot"], minutes_ago=round(minutes, 1),
                       thumb=last["thumb"], confidence=last["confidence"]))


@app.get("/api/where", response_model=WhereResponse)
async def where(item: str, from_place: str | None = None):
    """Where was this item last seen? Always 200; found=false when never seen."""
    answer = _where_answer(item, from_place=from_place)
    broadcaster.publish(_event("say", text=answer.say, source="where"))
    return answer


@app.post("/api/guide", response_model=GuideResponse)
async def guide(request: Request, frame: UploadFile = File(...), item: str = Form(...),
                yaw_deg: float | None = Form(None)):
    """One guidance step: visual beeps when the item is visible, else room-to-room navigate."""
    data = await read_frame(frame)
    target = db.target_item(item)
    if target is None:
        return GuideResponse(found=False, say="Please tell me which item to find.", mode="lost")
    name = target["name"]
    vision = get_vision()
    hints = _mock_hints(request)
    # Hide overrides other mock detect hints so navigate can be tested without the item in frame.
    detect_hints = {k: v for k, v in hints.items() if k not in {"place", "facing"}}
    real_live = not vision.mock and not hints
    loc_future = None
    if real_live and vision.mode == "perceptron":
        dest_for_loc = memory.navigable_destination(name)
        if dest_for_loc is not None:
            saved_map = memory.get_map()
            cands = _localization_candidates(saved_map["places"], dest_for_loc, saved_map["connections"], True)
            loc_future = calibration._pool.submit(vision.localize, data, cands, hints)
    if vision.mock and not target["watched"] and not detect_hints and not db.recent_sightings(name, 1):
        detections = []
    else:
        try:
            detections = await run_in_threadpool(vision.detect, data, [target], None, detect_hints)
        except VisionBusy:
            return Response(status_code=204)   # drop; the client keeps its last result and the local beeps
    box = next((d.box for d in detections if (db.canonicalize_item(d.item) or d.item) == name), None)
    hand_box = next((d.box for d in detections if d.item == "hand"), None)
    now = time.monotonic()
    prev_state = _guide_states.get(name)
    # If the user's hand is visible and just occluded the target object that was seen moments ago,
    # keep the last object box so close-range hand beeping continues smoothly instead of dropping out.
    if box is None and hand_box is not None and prev_state is not None and prev_state.last_box is not None:
        if now - prev_state.since <= guidance.SMOOTH_WINDOW_S:
            box = prev_state.last_box
    if box is None:
        result = await run_in_threadpool(_navigate_guide, name, data, hints, now, yaw_deg, loc_future)
    else:
        nav_prev = _navigate_states.get(name)
        state = _guide_states.get(name)
        if state is None or now - state.touched > GUIDE_STATE_TTL_S:
            state = _guide_states[name] = guidance.GuideState(since=now)
        if nav_prev and nav_prev.last_spoken_at > state.last_spoken_at:
            state.last_spoken_at = nav_prev.last_spoken_at
        prev_vis_key = state.last_key
        # yaw_deg (the phone's heading) is remembered so a later "Lost it" cue can point the shorter way;
        # beep panning between updates is still compensated in the apps.
        result = guidance.compute_guide(name, box, state, db.guidance_style(), fov_h(), now, yaw_deg,
                                        hand_box=hand_box, dynamic=real_live)
        if not vision.mock and not hints:
            cue = _body_relative_visual_cue(result.get("angle"), result.get("distance"))
            vis_key = f"vis:{cue}"
            is_new_reach = result.get("distance") == "reach" and not (prev_vis_key or "").startswith("vis:Stop. Reach")
            # While the hand is actively in frame or already in reach, let the hot/cold beeps own the
            # speaker instead of interrupting with frequent voice cues.
            min_gap = 16.0 if (hand_box is not None or (prev_vis_key or "").startswith("vis:Stop. Reach")) else LIVE_MIN_CUE_GAP_S
            if not is_new_reach and state.last_spoken_at > 0 and (now - state.last_spoken_at) < min_gap:
                spoken = ""
                state.last_key = prev_vis_key
            else:
                spoken, state.last_key, state.last_spoken_at = guidance.navigate_say(
                    cue, vis_key, prev_vis_key, now=now,
                    last_spoken_at=state.last_spoken_at, repeat_s=max(LIVE_REPEAT_S, min_gap))
            result["say"] = spoken
        destination = memory.navigable_destination(name)
        result = {**result, "mode": "visual", "current_place": None,
                  "destination": destination, "next_instruction": result.get("say") or None}
    broadcaster.publish(_event("guide", item=name, frame=_frame_data_url(data), **result))
    if result["say"]:
        broadcaster.publish(_event("say", text=result["say"], source="guide"))
    return result


def _chitchat(say: str, provider: str) -> ChatResponse:
    return ChatResponse(intent="chitchat", say=say, provider=provider)


def _ask_item(provider: str) -> ChatResponse:
    """One short clarifying question listing a few watched items."""
    names = [i["name"] for i in db.list_items()][:3]
    return _chitchat(f"Which item? I can find {', '.join(names)}." if names
                     else "Which item do you mean?", provider)


def _chat_response(result: llm.LLMResult, provider: str) -> ChatResponse:
    """Turn the model's tool call into an intent. Item/room are validated against the profile
    and spoken text for find/where/stop/set_room comes from the app, never from the model."""
    if not result.tool_calls:
        say = " ".join(result.text.split())[:MAX_CHAT_SAY_CHARS]
        return _chitchat(say or "Sorry, I didn't catch that.", provider)
    call = result.tool_calls[0]
    args = call.arguments
    if call.name == "stop_guidance":
        return ChatResponse(intent="stop", say="Stopped.", provider=provider)
    if call.name == "set_room":
        room = db.resolve_room(str(args.get("room", "")))
        if room is None:
            known = ", ".join(db.get_profile()["rooms"][:3])
            return _chitchat(f"Which room? I know {known}.", provider)
        return ChatResponse(intent="set_room", room=room, say=f"Okay, you're in the {room}.",
                            provider=provider)
    if call.name in ("find_item", "where_is_item"):
        name = db.resolve_item(str(args.get("item", "")))
        if name is None:
            return _ask_item(provider)
        # Both "where's X" and "find X" start live guidance when the item has been seen.
        if db.recent_sightings(name, 1):
            answer = _where_answer(name)
            say = (f"{answer.say} I'll guide you there." if call.name == "where_is_item"
                   else f"Looking for your {name}.")
            return ChatResponse(intent="find", item=name, say=say, provider=provider)
        return ChatResponse(intent="where", item=name, say=_where_answer(name).say,
                            provider=provider)
    return _chitchat("Sorry, I didn't catch that.", provider)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Work out what the user wants from natural speech (see docs/CONTRACT.md)."""
    profile = db.get_profile()
    system = llm.build_system_prompt(db.list_items(), profile["rooms"], req.current_room)
    messages = [{"role": "system", "content": system},
                *[t.model_dump() for t in req.history[-CHAT_HISTORY_TURNS:]],
                {"role": "user", "content": req.text}]
    result, provider = await run_in_threadpool(llm.chat_with_fallback, messages)
    resp = _chat_response(result, provider)
    # The dashboard sees the text live; nothing here is stored in the database.
    broadcaster.publish(_event("chat", user_text=req.text, intent=resp.intent, item=resp.item,
                               say=resp.say, provider=provider))
    return resp


@app.post("/api/transcribe", response_model=TranscribeResponse)
async def transcribe(audio: UploadFile = File(...)):
    """Speech-to-text for a short recording. 503 when STT_PROVIDER is not elevenlabs."""
    client = stt.get_stt()
    if client is None:
        raise HTTPException(503, "Speech-to-text is not configured; use browser recognition")
    data = await audio.read(MAX_AUDIO_BYTES + 1)   # held in memory only, never stored or logged
    if not data:
        raise HTTPException(400, "Empty recording")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Recording too long")
    try:
        text = await run_in_threadpool(client.transcribe, data, audio.content_type or "audio/wav")
    except stt.STTError as e:
        log.warning("STT %s failed (%s)", client.provider, e)
        raise HTTPException(502, "Speech-to-text failed") from None
    return TranscribeResponse(text=text, provider=client.provider)


@app.post("/api/speak")
async def speak(req: SpeakRequest, cached_only: bool = False):
    """Reply text -> mp3 in the configured ElevenLabs voice. 503 when TTS is off, 502 on any provider
    failure (the app then uses the phone's own voice). With ?cached_only=true a cache miss answers 204
    immediately without calling ElevenLabs: that is how fast guidance cues use the voice only when free."""
    client = tts.get_tts()
    if client is None:
        raise HTTPException(503, "Text-to-speech is not enabled; use the built-in voice")
    audio = client.cached(req.text)
    hit = audio is not None
    if audio is None:
        if cached_only:
            return Response(status_code=204)
        try:
            audio = await run_in_threadpool(client.speak, req.text)
        except tts.TTSError as e:   # the text being spoken is never logged
            log.warning("TTS %s failed (%s)", client.provider, e)
            raise HTTPException(502, "Text-to-speech failed") from None
    return Response(audio, media_type=tts.MEDIA_TYPE, headers={"X-TTS-Cache": "hit" if hit else "miss"})


@app.websocket("/ws/wake")
async def ws_wake(ws: WebSocket):
    """Wake word. The phone sends binary 16 kHz mono int16 audio; replies {"type":"wake"} on "Hey Jarvis".
    Text "reset" clears the detector (send it before resuming after the phone captured a request).
    Replies {"type":"ready"} once the model is loaded, or {"type":"error","reason":...} and closes."""
    await ws.accept()
    if not wake.acquire_slot():
        await ws.send_json({"type": "error", "reason": "Too many wake listeners are connected."})
        await ws.close(code=1013)
        return
    try:
        try:
            session = await run_in_threadpool(wake.new_session)
        except wake.WakeUnavailable as e:
            await ws.send_json({"type": "error", "reason": str(e)})
            await ws.close(code=1011)
            return
        await ws.send_json({"type": "ready"})
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") == "reset":
                session.reset()
            elif message.get("bytes") is not None:
                chunk = message["bytes"]
                if len(chunk) > wake.MAX_CHUNK_BYTES or len(chunk) % 2:
                    await ws.send_json({"type": "error", "reason": "Send 16 kHz mono 16-bit audio in chunks under 64 KB."})
                    await ws.close(code=1003)
                    return
                event = await run_in_threadpool(session.process, chunk, time.monotonic())
                if event:   # only the event leaves; the audio is already gone
                    await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        wake.release_slot()


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Push dashboard events: {type: sighting|guide|say, ...}."""
    await ws.accept()
    q = broadcaster.subscribe()
    try:
        while True:
            await ws.send_json(await q.get())
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        broadcaster.unsubscribe(q)


@app.get("/api/thumbs/{name}")
def thumb(name: str):
    """Serve a saved thumbnail (under /api so the Vite proxy covers it)."""
    path = db.thumbs_dir() / Path(name).name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg")
