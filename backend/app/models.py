"""Pydantic models for every request/response. Field names match docs/CONTRACT.md exactly."""
from typing import Literal

from pydantic import BaseModel, Field

from .memory.models import Place

GuidanceStyle = Literal["clock_face", "left_right", "beeps_only"]
Distance = Literal["far", "near", "reach"]


class Item(BaseModel):
    """A watched item."""
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""


class Room(BaseModel):
    """A room the user can be in."""
    name: str


class Sighting(BaseModel):
    """One saved sighting. box is [x1, y1, x2, y2] normalized 0..1."""
    id: int
    item: str
    room: str
    spot: str
    box: list[float]
    confidence: float | None
    ts: str
    thumb: str
    place_id: str | None = None
    place_source: Literal["none", "manual", "automatic"] = "none"
    place_reason: str = ""


class RememberedItem(BaseModel):
    item: str
    last: Sighting | None = None
    destination: Place | None = None


class ObserveResponse(BaseModel):
    sightings: list[Sighting]


class WhereLast(BaseModel):
    room: str
    spot: str
    minutes_ago: float
    thumb: str
    confidence: float | None


class WhereResponse(BaseModel):
    """found=False means never seen: only item, found and say are meaningful."""
    item: str
    found: bool = True
    last: WhereLast | None = None
    history: list[Sighting] = Field(default_factory=list)
    destination: Place | None = None
    say: str


GuideMode = Literal["visual", "navigate", "lost"]


class GuideResponse(BaseModel):
    """When found=False, geometry fields are null and beep_ms is 0 (silence).

    mode visual: item in frame (existing beep/clock guidance).
    mode navigate: room-to-room cue toward the newest sighting's destination.
    mode lost: cannot see the item and cannot offer a route cue.
    """
    found: bool
    point: list[float] | None = None
    box: list[float] | None = None
    angle: float | None = None
    clock: int | None = None
    distance: Distance | None = None
    pan: float = 0.0
    say: str = ""
    beep_ms: int = 0
    mode: GuideMode = "visual"
    current_place: str | None = None
    destination: Place | None = None
    next_instruction: str | None = None


class WatchItem(BaseModel):
    """Profile watchlist entry (what the Purple setup agent produces)."""
    name: str
    description: str = ""
    home_spot: str = ""


class Profile(BaseModel):
    watchlist: list[WatchItem]
    rooms: list[str]
    guidance_style: GuidanceStyle = "clock_face"


class OkResponse(BaseModel):
    ok: bool = True


class HealthResponse(BaseModel):
    ok: bool = True
    mode: str
    mock: bool
