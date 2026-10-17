"""FastAPI app: REST endpoints + WebSocket /ws/events. See docs/CONTRACT.md."""
import asyncio
import base64
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from fastapi import FastAPI, HTTPException, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

from . import db, guidance, memory  # noqa: E402
from .memory.api import router as memory_router  # noqa: E402
from .memory.models import PlaceID  # noqa: E402
from .memory.observation import UNKNOWN_ROOM, observe_and_save  # noqa: E402
from .events import broadcaster  # noqa: E402
from .models import (GuideResponse, HealthResponse, ObserveResponse, OkResponse, Profile,  # noqa: E402
                     WhereLast, WhereResponse)
from .vision import VisionUnavailable, get_vision, reset_vision  # noqa: E402
from .frames import InvalidFrame, MAX_FRAME_BYTES, prepare_frame  # noqa: E402

log = logging.getLogger("sightline")
MAX_EVENT_FRAME_BYTES = 200_000   # frames are only forwarded live to the dashboard, never stored
GUIDE_STATE_TTL_S = 10.0          # a pause this long starts a fresh guidance session

_guide_states: dict[str, guidance.GuideState] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log loudly when mock vision is active."""
    vision = get_vision()
    log.warning("Sightline backend up. vision mode = %s%s", vision.mode,
                " (MOCK: fake detections, no API key needed)" if vision.mock else "")
    db.thumbs_dir().mkdir(parents=True, exist_ok=True)
    try:
        yield
    finally:
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

def fov_h() -> float:
    return float(os.getenv("FOV_H_DEGREES", "65"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime:
    """Parse an ISO timestamp; naive values are treated as UTC."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mock_hints(request: Request) -> dict:
    """Forced mock position/size from X-Mock-Item-X / X-Mock-Item-H headers or mock_x / mock_h query."""
    hints = {}
    for key, header in (("x", "x-mock-item-x"), ("h", "x-mock-item-h")):
        raw = request.headers.get(header) or request.query_params.get(f"mock_{key}")
        try:
            if raw is not None:
                hints[key] = float(raw)
        except ValueError:
            pass
    return hints


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

@app.get("/api/health", response_model=HealthResponse)
def health():
    """Mode check so the UI can show a MOCK badge."""
    v = get_vision()
    return HealthResponse(mode=v.mode, mock=v.mock)


@app.get("/api/profile", response_model=Profile)
def get_profile():
    return db.get_profile()


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
    sightings, _ = await run_in_threadpool(observe_and_save, get_vision(), data, room, when,
        place_id=place_id, auto_place=auto_place, session_id=session_id, hints=_mock_hints(request))
    broadcaster.publish(_event("sighting", sightings=sightings, frame=_frame_data_url(data)))
    return {"sightings": sightings}


@app.get("/api/where", response_model=WhereResponse)
async def where(item: str):
    """Where was this item last seen? Always 200; found=false when never seen."""
    name = db.resolve_item(item)
    history = db.recent_sightings(name, 5) if name else []
    if not history:
        label = name or item.strip().lower()
        text = (f"I haven't seen your {label} yet." if name
                else "Please tell me which item you're looking for.")
        broadcaster.publish(_event("say", text=text, source="where"))
        return WhereResponse(item=label, found=False, say=text)
    last = history[0]
    destination = memory.destination_for(last)
    minutes = max(0.0, (datetime.now(timezone.utc) - _parse_ts(last["ts"])).total_seconds() / 60)
    text = where_say(last["room"], last["spot"], minutes)
    if destination:
        room_phrase = f" in your {destination['room']}" if destination["room"] != UNKNOWN_ROOM else ""
        text = f"Last seen at {destination['name']}{room_phrase}, {ago_phrase(minutes)}."
    broadcaster.publish(_event("say", text=text, source="where"))
    return WhereResponse(
        item=name, history=history, destination=destination, say=text,
        last=WhereLast(room=last["room"], spot=last["spot"], minutes_ago=round(minutes, 1),
                       thumb=last["thumb"], confidence=last["confidence"]))


@app.post("/api/guide", response_model=GuideResponse)
async def guide(request: Request, frame: UploadFile = File(...), item: str = Form(...),
                yaw_deg: float | None = Form(None)):
    """One guidance step: locate the item in this frame and return pan/clock/beep info."""
    data = await read_frame(frame)
    target = db.target_item(item)
    if target is None:
        return GuideResponse(found=False, say="Please tell me which item to find.")
    name = target["name"]
    vision = get_vision()
    hints = _mock_hints(request)
    if vision.mock and not target["watched"] and not hints and not db.recent_sightings(name, 1):
        detections = []
    else:
        detections = await run_in_threadpool(vision.detect, data, [target], None, hints)
    box = next((d.box for d in detections if (db.canonicalize_item(d.item) or d.item) == name), None)
    now = time.monotonic()
    state = _guide_states.get(name)
    if state is None or now - state.touched > GUIDE_STATE_TTL_S:
        state = _guide_states[name] = guidance.GuideState(since=now)
    # TODO(B): yaw_deg is accepted but unused server-side; yaw compensation runs in the browser
    #          (web/src/phone/orientation.js). Use it here if you want server-side smoothing.
    result = guidance.compute_guide(name, box, state, db.guidance_style(), fov_h(), now)
    broadcaster.publish(_event("guide", item=name, frame=_frame_data_url(data), **result))
    if result["say"]:
        broadcaster.publish(_event("say", text=result["say"], source="guide"))
    return result


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
