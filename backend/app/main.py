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
from fastapi.responses import FileResponse  # noqa: E402

from . import db, guidance, llm  # noqa: E402
from .events import broadcaster  # noqa: E402
from .models import (ChatRequest, ChatResponse, GuideResponse, HealthResponse, ObserveResponse, OkResponse, Profile,  # noqa: E402
                     WhereLast, WhereResponse)
from .vision import get_vision  # noqa: E402

log = logging.getLogger("sightline")
MAX_EVENT_FRAME_BYTES = 200_000   # frames are only forwarded live to the dashboard, never stored
GUIDE_STATE_TTL_S = 10.0          # a pause this long starts a fresh guidance session
CHAT_HISTORY_TURNS = 6
MAX_CHAT_SAY_CHARS = 200

_guide_states: dict[str, guidance.GuideState] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log loudly when mock vision is active."""
    vision = get_vision()
    log.warning("Sightline backend up. vision mode = %s%s; LLM provider = %s", vision.mode,
                " (MOCK: fake detections, no API key needed)" if vision.mock else "",
                llm.get_llm().provider)
    db.thumbs_dir().mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Sightline", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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
async def observe(request: Request, frame: UploadFile = File(...), room: str = Form(...),
                  ts: str | None = Form(None)):
    """Find watched items in a frame and save each sighting (frame is discarded)."""
    data = await frame.read()
    when = _parse_ts(ts).isoformat() if ts else _now_iso()
    # TODO(A): skip saving when an item hasn't moved since its last sighting (dedupe / throttle).
    detections = await run_in_threadpool(
        get_vision().detect, data, db.list_items(), room, _mock_hints(request))
    sightings = [db.add_sighting(d.item, room, d.spot, d.box, d.confidence, when,
                                 db.save_thumb(data, d.box)) for d in detections]
    broadcaster.publish(_event("sighting", sightings=sightings, frame=_frame_data_url(data)))
    return {"sightings": sightings}


def _where_answer(item: str) -> WhereResponse:
    """Answer "where is X" from stored sightings (shared by /api/where and /api/chat)."""
    name = db.resolve_item(item)
    history = db.recent_sightings(name, 5) if name else []
    if not history:
        label = name or item.strip().lower()
        text = (f"I haven't seen your {label} yet." if name
                else f"I'm not watching for {label}.")
        return WhereResponse(item=label, found=False, say=text)
    last = history[0]
    minutes = max(0.0, (datetime.now(timezone.utc) - _parse_ts(last["ts"])).total_seconds() / 60)
    return WhereResponse(
        item=name, history=history, say=where_say(last["room"], last["spot"], minutes),
        last=WhereLast(room=last["room"], spot=last["spot"], minutes_ago=round(minutes, 1),
                       thumb=last["thumb"], confidence=last["confidence"]))


@app.get("/api/where", response_model=WhereResponse)
async def where(item: str):
    """Where was this item last seen? Always 200; found=false when never seen."""
    answer = _where_answer(item)
    broadcaster.publish(_event("say", text=answer.say, source="where"))
    return answer


@app.post("/api/guide", response_model=GuideResponse)
async def guide(request: Request, frame: UploadFile = File(...), item: str = Form(...),
                yaw_deg: float | None = Form(None)):
    """One guidance step: locate the item in this frame and return pan/clock/beep info."""
    data = await frame.read()
    name = db.resolve_item(item)
    if name is None:
        return GuideResponse(found=False, say=f"I'm not watching for {item.strip().lower()}.")
    detections = await run_in_threadpool(
        get_vision().detect, data, [db.get_item(name)], None, _mock_hints(request))
    box = next((d.box for d in detections if d.item == name), None)
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
        if call.name == "find_item":
            return ChatResponse(intent="find", item=name, say=f"Looking for your {name}.",
                                provider=provider)
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
